"""Python API: check packages and lockfiles without going through the command line.

import wheelgap

targets = wheelgap.make_targets(python="3.13", platforms=["linux-aarch64", "musl-aarch64"])
result = wheelgap.check_package("numpy", "2.3.4", targets=targets)
result.ok                   # True if every target has a wheel
result.statuses[targets[1]] # Status.WHEEL / SOURCE / MISSING / ...
result.to_dict()            # JSON-friendly, keyed by "3.13 musl-aarch64"
"""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .check import Row, Status, check, status_for, wheel_tags
from .offline import require_offline
from .pyodide import PyodideLock, bundled_version_differs, bundles, check_locks
from .pypi import fill_all, recheck_lock
from .sources import Package, Source, SourceError, find_lockfile, load_file, pythons_outside_lock
from .targets import (
    DEFAULT_ANDROID_API,
    DEFAULT_GLIBC,
    DEFAULT_IOS,
    DEFAULT_MACOS,
    DEFAULT_MUSL,
    DEFAULT_PLATFORMS,
    Target,
    build_targets,
    single_python,
)

_OK = (Status.WHEEL, Status.NOT_NEEDED, Status.BUNDLED)


def running_python() -> str:
    """The Python this code runs on, e.g. '3.13' or '3.14t'."""
    ft = "t" if sysconfig.get_config_var("Py_GIL_DISABLED") else ""
    return f"{sys.version_info.major}.{sys.version_info.minor}{ft}"


def make_targets(
    python: str | Iterable[str] | None = None,
    platforms: str | Iterable[str] | None = None,
    *,
    glibc: str = DEFAULT_GLIBC,
    musl: str = DEFAULT_MUSL,
    macos: str = DEFAULT_MACOS,
    ios: str = DEFAULT_IOS,
    android_api: str | int = DEFAULT_ANDROID_API,
) -> list[Target]:
    """Targets from the same names the CLI takes: python="3.13,3.14t", platforms=["all", "pyodide", "mobile"]."""
    pythons = [python] if isinstance(python, str) else list(python or [running_python()])
    plats = [platforms] if isinstance(platforms, str) else list(platforms or DEFAULT_PLATFORMS)
    if not any(p.strip() for item in pythons for p in item.split(",")):
        pythons = [running_python()]
    return build_targets(pythons, plats, glibc, musl, macos, ios, android_api)


@dataclass(frozen=True)
class Result:
    """One package's status on each target."""

    name: str
    version: str | None
    statuses: dict[Target, Status] = field(default_factory=dict)
    files: tuple[str, ...] = ()
    error: str | None = None
    # With pyodide_locks: the version Pyodide ships, set when any target is BUNDLED, and whether it's outside the
    # pin or range asked for (it still counts as ok, as in the CLI).
    bundled_version: str | None = None
    bundled_version_differs: bool = False

    @property
    def ok(self) -> bool:
        return all(s in _OK for s in self.statuses.values())

    @property
    def gaps(self) -> list[Target]:
        return [t for t, s in self.statuses.items() if s not in _OK]

    @property
    def pure(self) -> bool:
        """Has a Python 3 wheel that works on every platform (e.g. py3-none-any, but not py2-none-any)."""
        return any(
            t.platform == "any" and t.abi == "none" and t.interpreter.startswith(("py3", "cp3"))
            for tags in wheel_tags(list(self.files))
            for t in tags
        )

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "name": self.name,
            "version": self.version,
            "error": self.error,
            "results": {t.label(True): s.value for t, s in self.statuses.items()},
        }
        if self.bundled_version is not None:  # the same keys as the CLI's --json
            out["bundled"] = self.bundled_version
            out["bundled_version_differs"] = self.bundled_version_differs
        return out


def _result(pkg: Package, statuses: dict[Target, Status], pyodide_locks: Sequence[PyodideLock] = ()) -> Result:
    applied = [lock for lock in pyodide_locks if any(bundles(lock, pkg.key, t, s) for t, s in statuses.items())]
    statuses = {
        t: Status.BUNDLED if any(bundles(lock, pkg.key, t, s) for lock in applied) else s for t, s in statuses.items()
    }
    return Result(
        pkg.name,
        pkg.version,
        statuses,
        tuple(pkg.files or ()),
        pkg.error,
        # Locks for different Pythons can ship different versions: report the first, and a difference in any.
        applied[0].packages[pkg.key] if applied else None,
        any(bundled_version_differs(pkg, lock) for lock in applied),
    )


def _package_targets(targets: Sequence[Target] | None) -> list[Target]:
    resolved = list(targets) if targets is not None else make_targets()
    if not resolved:
        raise ValueError("package checks require at least one target")
    return resolved


def check_packages(
    packages: Iterable[str | tuple[str, str | None]],
    targets: Sequence[Target] | None = None,
    *,
    workers: int = 16,
    pyodide_locks: Iterable[PyodideLock] = (),
) -> list[Result]:
    """Check several packages, looking their files up on the index in parallel.

    Each package is a name ("numpy", the latest release) or a (name, version) pair.
    pyodide_locks (from wheelgap.pyodide.load_pyodide_lock, one per Python) mark a package that a lock's Pyodide
    ships itself as BUNDLED on pyodide targets for that lock's Python, where it would otherwise be SOURCE or MISSING.
    As with --pyodide-lock, a lock whose Python no pyodide target uses raises PyodideLockError (a ValueError) before
    any lookup, and so do two locks for the same Python. A result with a BUNDLED target has bundled_version (the
    version Pyodide ships) and bundled_version_differs (True when that's outside the pin or range asked for).
    """
    targets = _package_targets(targets)
    pyodide_locks = tuple(pyodide_locks)
    if workers < 1:
        raise ValueError("workers must be at least 1")
    check_locks(pyodide_locks, targets)
    pkgs = [Package(p, None) if isinstance(p, str) else Package(p[0], p[1]) for p in packages]
    for p in pkgs:
        p.origin = "api"
    fill_all(pkgs, workers=workers, python=single_python(targets))
    return [
        _result(p, {t: status_for(None if p.error else p.files, t, yanked=p.yanked) for t in targets}, pyodide_locks)
        for p in pkgs
    ]


def check_package(
    name: str,
    version: str | None = None,
    targets: Sequence[Target] | None = None,
    *,
    files: Iterable[str] | None = None,
    pyodide_locks: Iterable[PyodideLock] = (),
) -> Result:
    """Check one package. Pass `files` (distribution filenames) to skip the index lookup.

    pyodide_locks work as in check_packages().
    """
    pyodide_locks = tuple(pyodide_locks)
    if files is None:
        return check_packages([(name, version)], targets, pyodide_locks=pyodide_locks)[0]
    targets = _package_targets(targets)
    check_locks(pyodide_locks, targets)
    pkg = Package(name, version)
    pkg.files = list(files)
    return _result(pkg, {t: status_for(pkg.files, t) for t in targets}, pyodide_locks)


def check_lockfile(
    path: str | Path,
    targets: Sequence[Target] | None = None,
    *,
    online: bool = False,
    offline: bool = False,
    pyodide_locks: Iterable[PyodideLock] = (),
) -> list[Result]:
    """Check every package a lockfile (or a project directory) installs on at least one target.

    Environment markers and the uv.lock dependency graph are respected, as in the CLI. A uv.lock whose
    requires-python leaves out a target's Python may be missing wheels for it, so its PyPI packages are also
    looked up on the index (keeping the lock's files if a lookup fails).
    With offline=True, only locked files are used: inputs needing an index lookup raise SourceError, and the
    other-Python recheck is skipped. Locked files may therefore omit wheels for those Python versions.
    online=True and offline=True cannot be combined.
    pyodide_locks work as in check_packages(), and are checked before the lockfile is read.
    """
    return _check_lockfile(path, targets, online=online, offline=offline, pyodide_locks=pyodide_locks)[0]


def _check_lockfile(
    path: str | Path,
    targets: Sequence[Target] | None,
    *,
    online: bool,
    offline: bool,
    pyodide_locks: Iterable[PyodideLock] = (),
) -> tuple[list[Result], Source]:
    """check_lockfile(), also returning the loaded source (diff.py needs its skipped packages)."""
    targets = _package_targets(targets)
    pyodide_locks = tuple(pyodide_locks)
    if online and offline:
        raise ValueError("online=True and offline=True cannot be combined")
    check_locks(pyodide_locks, targets)
    p = Path(path)
    lockfile = find_lockfile(p) if p.is_dir() else p
    if lockfile.name == "pyproject.toml":  # direct dependencies only; the command line says so in a note
        raise SourceError(
            f"{lockfile}: a pyproject.toml lists direct dependencies only, so check_lockfile() and "
            "wheelgap.diff don't read it; pass a lockfile (the wheelgap command checks a pyproject.toml)"
        )
    source: Source = load_file(lockfile)
    if offline:
        require_offline([source], "offline=True", undo="set offline=False")
    fill_all(source.packages, refresh=online, python=single_python(targets))
    if not online and not offline and pythons_outside_lock(source, targets):
        recheck_lock(source.packages)
    rows: list[Row] = [r for r in check([source], targets) if r.needed]
    return [_result(r.package, dict(r.results), pyodide_locks) for r in rows], source
