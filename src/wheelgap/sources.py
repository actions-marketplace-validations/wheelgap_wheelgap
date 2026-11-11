"""Read packages (and, where the lockfile records them, their distribution files) from inputs."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name, canonicalize_version
from packaging.version import Version

if TYPE_CHECKING:
    from .targets import Target

LOCKFILES = (
    "uv.lock",
    "pylock.toml",
    "poetry.lock",
    "pdm.lock",
    "Pipfile.lock",
    "requirements.txt",
    "requirements.lock",
)


class SourceError(ValueError):
    pass


@dataclass
class Package:
    name: str
    version: str | None
    # Distribution filenames. None means "unknown, ask the index".
    files: list[str] | None = None
    # Set for packages that don't come from an index (editable, git, path, ...).
    skip: str | None = None
    error: str | None = None
    origin: str = ""
    # Environment markers; the package is installed if any of them is true. None means always.
    markers: list[str] | None = None
    # A version range that isn't an exact pin (numpy>=1.26,<2): the index picks the newest match.
    spec: str | None = None
    # Files of a pinned release that the index marks as yanked (see check.status_for).
    yanked: frozenset[str] = frozenset()
    # uv.lock / pylock.toml / poetry.lock / Pipfile.lock: the index the lock says the package came from, e.g.
    # https://pypi.org/simple.
    registry: str | None = None
    # The index picked `version` (an unpinned or ranged requirement): it isn't a pin from the input.
    version_from_index: bool = False

    @property
    def key(self) -> str:
        return canonicalize_name(self.name)


# A dependency edge in a lockfile graph: (name, version or None, marker or None)
Edge = tuple[str, str | None, str | None]


@dataclass(frozen=True)
class UvSelection:
    """Explicit installation selection. None on a Source keeps the conservative all-dependencies walk."""

    extras: frozenset[str] = frozenset()
    all_extras: bool = False
    groups: frozenset[str] = frozenset()
    no_dev: bool = False
    only_groups: frozenset[str] = frozenset()


@dataclass
class Source:
    label: str
    packages: list[Package] = field(default_factory=list)
    # uv.lock only: which packages each package depends on, and where the walk starts.
    # When set, a package applies to a target only if it's reachable along edges whose markers hold.
    edges: dict[int, list[Edge]] | None = None
    roots: list[int] = field(default_factory=list)
    root_edges: list[Edge] = field(default_factory=list)
    # Index into root_edges -> name of the dependency group it comes from, e.g. {0: "dev"}.
    root_groups: dict[int, str] = field(default_factory=dict)
    # uv.lock only: the project's requires-python. uv keeps only the wheels that can serve it.
    requires_python: str | None = None
    # Retain uv edge provenance for an explicit installation selection. The existing flattened
    # graph remains untouched, preserving conservative behavior when no selectors are supplied.
    uv_packages: list[dict] | None = None
    uv_manifest: dict = field(default_factory=dict)
    uv_pyproject: Path | None = None
    uv_default_groups: list[str] = field(default_factory=lambda: ["dev"])
    uv_selection: UvSelection | None = None
    # A requirements file (requirements.txt, .in, requirements.lock): unpinned lines get the newest release.
    requirements: bool = False
    # pyproject.toml only: [project] optional-dependencies by canonical extra name, applied with --extra.
    # None when the input isn't a pyproject.toml.
    pyproject_extras: dict[str, list[str]] | None = None
    # pyproject.toml only: optional-dependencies is listed in [project] dynamic, so the extras can't be read.
    pyproject_dynamic_extras: bool = False
    # pyproject.toml only: the canonical [project] name, to spot `myproject[extra]` self-references.
    pyproject_name: str | None = None
    # poetry.lock only: each package's [[package]] table, kept so --group/--only-group/--no-dev can pick packages
    # by the groups Poetry records on them.
    poetry_tables: list[dict] | None = None
    poetry_pyproject: Path | None = None
    # pdm.lock only: each package's `groups` list (None for a package that records none, as before PDM 2.x),
    # kept so --group/--only-group/--no-dev can pick packages. None when the input isn't a pdm.lock.
    pdm_groups: list[list[str] | None] | None = None
    # pdm.lock only: its [metadata] groups (canonical; None if it has none), and whether its [metadata] strategy has
    # inherit_metadata, without which PDM doesn't go by package groups.
    pdm_locked_groups: list[str] | None = None
    pdm_inherit_metadata: bool = False
    pdm_pyproject: Path | None = None
    # poetry.lock after a group selection: the packages it left out, which were still read (for --ignore notes).
    unselected_keys: set[str] = field(default_factory=set)


def pythons_outside_lock(source: Source, targets: Iterable[Target], *, bounds_only: bool = True) -> list[str]:
    """Target Pythons the source's requires-python leaves out, e.g. ['3.14t'] for `==3.13.*`.

    For a lockfile, uv keeps only the wheels that can serve requires-python (abi3 and pure wheels survive), so
    the lock may be missing wheels for those Pythons. Like uv, only the bounds count there: `!=3.12.*` doesn't
    exclude 3.12. With bounds_only=False (a library's pyproject.toml, which pip checks in full against the
    running Python), every clause counts, so `!=3.12.*` leaves out 3.12 but `!=3.12.0` doesn't.
    """
    if not source.requires_python:
        return []
    try:
        clauses = SpecifierSet(source.requires_python)
        spec = SpecifierSet(",".join(str(s) for s in clauses if not (bounds_only and s.operator == "!=")))
    except InvalidSpecifier:
        return []
    out = []
    for t in targets:
        # A minor is served if any of its releases fits: its first and last patch, or a version the spec names.
        tries = [Version(f"{t.major}.{t.minor}.0"), Version(f"{t.major}.{t.minor}.999")]
        for s in spec:
            try:
                v = Version(s.version.removesuffix(".*"))
            except ValueError:
                continue
            if v.release[:2] == (t.major, t.minor):
                tries.append(v)
        if not any(spec.contains(v, prereleases=True) for v in tries):
            out.append(t.python)
    return list(dict.fromkeys(out))


def _filename(entry: dict) -> str | None:
    for k in ("filename", "file", "name"):
        if isinstance(entry.get(k), str) and entry[k].endswith((".whl", ".tar.gz", ".zip", ".tar.bz2", ".tgz")):
            return entry[k]
    url = entry.get("url")
    if isinstance(url, str):
        return unquote(Path(urlparse(url).path).name)
    path = entry.get("path")
    if isinstance(path, str):
        return Path(path).name
    return None


def _uv_edges(p: dict) -> list[Edge]:
    groups = [p.get("dependencies", [])]
    for table in ("optional-dependencies", "dev-dependencies"):
        groups.extend(p.get(table, {}).values())
    return [(d["name"], d.get("version"), d.get("marker")) for g in groups for d in g]


def parse_uv_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    manifest = data.get("manifest", {})
    members = {canonicalize_name(m) for m in manifest.get("members", [])}
    edges: dict[int, list[Edge]] = {}
    source = Source(path.name, edges=edges, uv_packages=data.get("package", []), uv_manifest=manifest)
    source.uv_pyproject = path.with_name("pyproject.toml")
    for p in data.get("package", []):
        src = p.get("source", {})
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        kind = next((k for k in ("editable", "virtual", "git", "path", "directory", "url") if k in src), None)
        if kind and "registry" not in src:
            pkg.skip = kind
        else:
            pkg.registry = src.get("registry")
            files = [f for f in (_filename(w) for w in p.get("wheels", [])) if f]
            if p.get("sdist"):
                files.append(_filename(p["sdist"]) or f"{p['name']}-{p.get('version')}.tar.gz")
            pkg.files = files
        i = len(source.packages)
        source.packages.append(pkg)
        edges[i] = _uv_edges(p)
        # Roots are the project itself (source "."), plus workspace members. A path dependency elsewhere
        # (directory = "libs/x") is reached through its dependents' edges and their markers instead.
        if (kind in ("editable", "virtual") and src.get(kind) == ".") or pkg.key in members:
            source.roots.append(i)
    for name, group in manifest.get("dependency-groups", {}).items():
        for d in group:
            source.root_groups[len(source.root_edges)] = name
            source.root_edges.append((d["name"], None, d.get("marker")))
    source.root_edges.extend((d["name"], None, d.get("marker")) for d in manifest.get("requirements", []))
    if not source.roots and not source.root_edges:
        source.edges = None  # no idea where the graph starts; treat every package as needed
    if isinstance(data.get("requires-python"), str):
        source.requires_python = data["requires-python"]
    return source


def uv_selected_roots(source: Source) -> list[int]:
    # A rooted workspace syncs its project, not every sibling member. With no project
    # (a virtual workspace), all member roots remain eligible, as with uv sync.
    packages = source.uv_packages or []
    project = [
        i
        for i in source.roots
        if any(packages[i].get("source", {}).get(kind) == "." for kind in ("editable", "virtual"))
    ]
    return project or source.roots


def uv_extra_names(package: dict) -> frozenset[str]:
    # uv omits empty optional-dependency groups from the graph, but records their names here.
    return frozenset(canonicalize_name(name) for name in package.get("optional-dependencies", {})) | frozenset(
        canonicalize_name(name) for name in package.get("metadata", {}).get("provides-extras", [])
    )


def uv_group_names(package: dict) -> frozenset[str]:
    return frozenset(canonicalize_name(name) for name in package.get("dev-dependencies", {})) | frozenset(
        canonicalize_name(name) for name in package.get("metadata", {}).get("requires-dev", {})
    )


def select_uv_dependencies(
    sources: list[Source],
    selection: UvSelection,
    strict: bool = True,
    *,
    configured_extras: bool = False,
    poetry: bool = True,
) -> None:
    """Apply `selection` to the uv.lock sources, and its groups to Poetry 2.x poetry.lock and pdm.lock sources. With
    strict (selectors from the command line), other inputs are an error; otherwise (from [tool.wheelgap]) they're left
    as they are. configured_extras: the extras come from [tool.wheelgap], which apply to uv.lock inputs only, so a
    poetry.lock or pdm.lock doesn't reject them. poetry=False leaves poetry.lock and pdm.lock inputs as they are (only
    extras configured)."""
    if selection.only_groups and (selection.extras or selection.all_extras):
        raise SourceError("--only-group cannot be combined with --extra or --all-extras")
    if selection.no_dev and "dev" in selection.groups | selection.only_groups:
        raise SourceError("--no-dev cannot be combined with an explicit dev group")
    if strict and (not sources or any(source.uv_packages is None for source in sources)):
        others = [source for source in sources if source.uv_packages is None]
        if sources and all(source.poetry_tables is not None or source.pdm_groups is not None for source in others):
            if selection.all_extras or (selection.extras and not configured_extras):
                raise SourceError(
                    "extra selection requires uv.lock inputs (poetry.lock and pdm.lock support groups only)"
                )
        else:
            raise SourceError("extra/group selection requires uv.lock inputs")
    for source in sources:
        if source.pdm_groups is not None:
            # A lock without package groups or inherit_metadata: configured groups leave it as it is.
            if poetry and (strict or (source.pdm_inherit_metadata and all(g is not None for g in source.pdm_groups))):
                _select_pdm_groups(source, selection)
            continue
        if source.poetry_tables is not None:
            # A Poetry 1.x lock doesn't record groups per package; configured groups leave it as it is.
            if poetry and (strict or source.edges is None):
                _select_poetry_groups(source, selection)
            continue
        if source.uv_packages is None:
            continue
        pyproject = source.uv_pyproject
        if pyproject is not None and pyproject.is_file():
            uv = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}).get("uv", {})
            defaults = uv.get("default-groups", ["dev"])
            if defaults == "all":
                source.uv_default_groups = ["*"]
            elif isinstance(defaults, list) and all(isinstance(g, str) for g in defaults):
                source.uv_default_groups = [canonicalize_name(g) for g in defaults]
            else:
                raise SourceError(f"{pyproject}: [tool.uv] default-groups must be a list of strings or 'all'")
        if source.edges is None:
            raise SourceError(f"{source.label}: cannot select extras/groups without project or manifest roots")
        packages = source.uv_packages or []
        roots = uv_selected_roots(source)
        extras = frozenset(name for i in roots for name in uv_extra_names(packages[i]))
        groups: set[str] = {canonicalize_name(name) for name in source.uv_manifest.get("dependency-groups", {})}
        groups.update(frozenset().union(*(uv_group_names(packages[i]) for i in roots)))
        unknown_defaults = set(source.uv_default_groups) - groups - {"dev", "*"}
        if unknown_defaults:
            raise SourceError(f"{source.label}: unknown default groups: {', '.join(sorted(unknown_defaults))}")
        for label, unknown in (
            ("extras", selection.extras - extras),
            ("groups", (selection.groups | selection.only_groups) - groups),
        ):
            if unknown:
                raise SourceError(f"{source.label}: unknown {label}: {', '.join(sorted(unknown))}")
        source.uv_selection = selection


def _poetry_markers(p: dict, groups: Iterable[str]) -> list[str] | None:
    """A Poetry 2.x package's markers for the given groups: None (always installed) if a group has no marker."""
    markers = p.get("markers")
    if isinstance(markers, str):
        return [markers]
    if not isinstance(markers, dict):
        return None
    # Poetry leaves a group out of the dict when the package is always installed for it.
    if any(g not in markers for g in groups):
        return None
    return [m for g in groups if isinstance(m := markers.get(g), str)] or None


def _select_poetry_groups(source: Source, selection: UvSelection) -> None:
    """Keep the poetry.lock packages that Poetry would install for `selection`, with Poetry's meanings: `main` and
    every non-optional group by default, `--group` adds a group (like `poetry install --with`), `--no-dev` leaves
    out `dev`, and `--only-group` installs only the named groups (`main` too only if named)."""
    tables = source.poetry_tables or []
    if source.edges is not None or any(not isinstance(t.get("groups"), list) for t in tables):
        raise SourceError(f"{source.label}: group selection needs a poetry.lock from Poetry 2.x (with package groups)")
    known = {"main"} | {canonicalize_name(g) for t in tables for g in t["groups"]}
    optional: set[str] = set()
    pyproject = source.poetry_pyproject
    if pyproject is not None and pyproject.is_file():
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        # PEP 735 groups (Poetry 2.2+): an empty one is in no package's groups, but it's still a known group.
        pep735 = data.get("dependency-groups")
        known.update(canonicalize_name(name) for name in (pep735 if isinstance(pep735, dict) else {}))
        declared = data.get("tool", {}).get("poetry", {})
        for name, group in (declared.get("group") or {}).items():
            known.add(canonicalize_name(name))
            if isinstance(group, dict) and group.get("optional") is True:
                optional.add(canonicalize_name(name))
    unknown = (selection.groups | selection.only_groups) - known
    if unknown:
        raise SourceError(f"{source.label}: unknown groups: {', '.join(sorted(unknown))}")
    if selection.only_groups:
        chosen = set(selection.only_groups | selection.groups)
    else:
        chosen = (known - optional) | selection.groups
    if selection.no_dev:
        chosen.discard("dev")
    kept = []
    for pkg, table in zip(source.packages, tables, strict=True):
        groups = [g for g in table["groups"] if canonicalize_name(g) in chosen]
        if groups:
            pkg.markers = _poetry_markers(table, groups)
            kept.append(pkg)
        else:
            source.unselected_keys.add(pkg.key)
    source.packages = kept
    source.poetry_tables = None


def _select_pdm_groups(source: Source, selection: UvSelection) -> None:
    """Keep the pdm.lock packages that `pdm install` would install for `selection`, with PDM's meanings: every group
    the lock was made for ([metadata] groups) by default, `--no-dev` leaves out `dev` (like `pdm install --without
    dev`), `--group` only checks that a group is locked, and `--only-group` installs only the named groups (`default`
    too only if named). A package counts if any of its groups is selected."""
    per_package = source.pdm_groups or []
    if not source.pdm_inherit_metadata or any(g is None for g in per_package):
        raise SourceError(
            f"{source.label}: group selection needs a pdm.lock with package groups "
            "(locked with the inherit_metadata strategy)"
        )
    package_groups = [[canonicalize_name(g) for g in groups or []] for groups in per_package]
    if source.pdm_locked_groups is not None:
        locked = set(source.pdm_locked_groups)
    else:  # no [metadata] groups: the groups its packages record
        locked = {"default"} | {g for groups in package_groups for g in groups}
    asked = selection.groups | selection.only_groups
    if asked - locked:
        declared: set[str] = set()
        pyproject = source.pdm_pyproject
        if pyproject is not None and pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            tables = (
                data.get("project", {}).get("optional-dependencies"),
                data.get("dependency-groups"),
                data.get("tool", {}).get("pdm", {}).get("dev-dependencies"),
            )
            declared = {canonicalize_name(name) for table in tables if isinstance(table, dict) for name in table}
        unknown = asked - locked - declared
        if unknown:
            raise SourceError(f"{source.label}: unknown groups: {', '.join(sorted(unknown))}")
        unlocked = sorted(asked - locked)
        raise SourceError(
            f"{source.label}: groups not in the lock: {', '.join(unlocked)} "
            f"(lock them with `pdm lock {' '.join(f'-G {name}' for name in unlocked)}`)"
        )
    chosen = set(selection.only_groups | selection.groups) if selection.only_groups else locked
    if selection.no_dev:
        chosen.discard("dev")
    kept = []
    for pkg, groups in zip(source.packages, package_groups, strict=True):
        if any(g in chosen for g in groups):
            kept.append(pkg)
        else:
            source.unselected_keys.add(pkg.key)
    source.packages = kept
    source.pdm_groups = None


def parse_pylock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    pkgs = []
    for p in data.get("packages", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name, registry=p.get("index"))
        if p.get("marker"):
            pkg.markers = [p["marker"]]
        if any(k in p for k in ("vcs", "directory", "archive")) and "wheels" not in p and "sdist" not in p:
            pkg.skip = next(k for k in ("vcs", "directory", "archive") if k in p)
        else:
            files = [f for f in (_filename(w) for w in p.get("wheels", [])) if f]
            if p.get("sdist"):
                files.append(_filename(p["sdist"]) or f"{p['name']}-{p.get('version')}.tar.gz")
            pkg.files = files
        pkgs.append(pkg)
    requires = data.get("requires-python")
    return Source(path.name, pkgs, requires_python=requires if isinstance(requires, str) else None)


def _poetry_edges(p: dict) -> list[Edge]:
    """A poetry.lock package's dependencies as edges. A value is a version string, a table, or a list of
    tables for different markers (numpy = [{version = ">=2", markers = "python_version >= '3.10'"}, ...])."""
    out: list[Edge] = []
    for name, spec in p.get("dependencies", {}).items():
        for s in spec if isinstance(spec, list) else [spec]:
            version = s.get("version") if isinstance(s, dict) else s
            marker = s.get("markers") if isinstance(s, dict) else None
            out.append((name, version if isinstance(version, str) and version != "*" else None, marker))
    return out


def parse_poetry_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    legacy_files = metadata.get("files", {})
    pkgs: list[Package] = []
    edges: dict[int, list[Edge]] = {}
    for p in data.get("package", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        markers = p.get("markers")
        if isinstance(markers, str):
            pkg.markers = [markers]
        elif isinstance(markers, dict):
            # Poetry leaves a group out of the dict when the package is always installed for it.
            always = any(g not in markers for g in p.get("groups", []))
            pkg.markers = None if always else ([m for m in markers.values() if isinstance(m, str)] or None)
        src_type = p.get("source", {}).get("type")
        if src_type in ("git", "hg", "svn", "bzr", "directory", "file", "url"):
            pkg.skip = src_type
        else:
            # A "legacy" source is a simple index: remember it so the package isn't looked up on PyPI.
            url = p.get("source", {}).get("url")
            pkg.registry = url if isinstance(url, str) else None
            entries = p.get("files")
            if entries is None:
                entries = legacy_files.get(p["name"])
            # No files recorded at all (old [metadata.hashes] locks): PyPI packages are looked up on the index,
            # packages from another index are skipped (fill_all).
            pkg.files = None if entries is None else [f for f in (_filename(e) for e in entries) if f]
        edges[len(pkgs)] = _poetry_edges(p)
        pkgs.append(pkg)
    # Lock files before 2.1 (all of Poetry 1.x) only record markers on dependency edges, not on
    # packages, so walk the graph from the packages nothing depends on, as for uv.lock.
    if _version_tuple(str(metadata.get("lock-version", "2.1"))) < (2, 1) and pkgs:
        roots = set(_graph_roots(pkgs, edges))
        # The project's own dependencies are roots too, even if another package also depends on them
        # under a marker (colorama directly, and via click only on Windows).
        declared = _project_dependencies(path.parent / "pyproject.toml")
        roots |= {i for i, p in enumerate(pkgs) if p.key in declared}
        return Source(path.name, pkgs, edges=edges, roots=sorted(roots), poetry_tables=data.get("package", []))
    return Source(
        path.name, pkgs, poetry_tables=data.get("package", []), poetry_pyproject=path.parent / "pyproject.toml"
    )


def _graph_roots(pkgs: list[Package], edges: dict[int, list[Edge]]) -> list[int]:
    """Where to start walking a lock that doesn't record its project: every package in a group of
    packages (a strongly connected component, usually just one package) that nothing outside the group
    depends on. That covers dependency cycles such as sphinx <-> sphinxcontrib-*, which no package
    "depended on by nothing" would reach."""
    by_name: dict[str, list[int]] = {}
    for i, p in enumerate(pkgs):
        by_name.setdefault(p.key, []).append(i)
    succ = {
        i: sorted({j for name, _, _ in edges.get(i, []) for j in by_name.get(canonicalize_name(name), [])})
        for i in range(len(pkgs))
    }
    comp = _components(len(pkgs), succ)
    has_outside_parent = {comp[j] for i in succ for j in succ[i] if comp[i] != comp[j]}
    return [i for i in range(len(pkgs)) if comp[i] not in has_outside_parent]


def _components(n: int, succ: dict[int, list[int]]) -> list[int]:
    """Tarjan's strongly connected components, iteratively (lockfiles can be deep)."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    comp = [-1] * n
    stack: list[int] = []
    on_stack: set[int] = set()
    counter = count = 0
    for root in range(n):
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            v, child = work.pop()
            if child == 0:
                index[v] = low[v] = counter
                counter += 1
                stack.append(v)
                on_stack.add(v)
            if child < len(succ[v]):
                work.append((v, child + 1))
                w = succ[v][child]
                if w not in index:
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
                continue
            if low[v] == index[v]:
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp[w] = count
                    if w == v:
                        break
                count += 1
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
    return comp


def _project_dependencies(pyproject: Path) -> set[str]:
    """Names a project declares itself (PEP 621 and Poetry tables), to use as roots for poetry.lock.
    A missing or odd pyproject.toml just means no extra roots: it must never break the run."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return set()

    def table(value: object) -> dict:
        return value if isinstance(value, dict) else {}

    def strings(value: object) -> list[str]:
        return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

    project = table(data.get("project"))
    specs = strings(project.get("dependencies"))
    for extra in table(project.get("optional-dependencies")).values():
        specs += strings(extra)
    for group in table(data.get("dependency-groups")).values():
        specs += strings(group)
    names: set[str] = set()
    for spec in specs:
        try:
            names.add(canonicalize_name(Requirement(spec).name))
        except InvalidRequirement:
            continue
    poetry = table(table(data.get("tool")).get("poetry"))
    tables = [table(poetry.get("dependencies")), table(poetry.get("dev-dependencies"))]
    tables += [table(table(g).get("dependencies")) for g in table(poetry.get("group")).values()]
    names |= {canonicalize_name(name) for t in tables for name in t if name.lower() != "python"}
    return names


def _version_tuple(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in text.split("."))
    except ValueError:
        return (0,)


def _add_once(pkgs: list[Package], seen: dict[tuple[str, str | None], Package], pkg: Package) -> None:
    """Append `pkg` unless the same name and version is already there; then widen that entry's markers.

    Pipenv lists a package used by several categories (default, develop, docs...) in each of them, and PDM
    writes one entry per extras variant (`coverage` and `coverage[toml]`). Either way it's one install,
    needed if any entry is.
    """
    key = (pkg.key, pkg.version)
    first = seen.get(key)
    if first is None:
        seen[key] = pkg
        pkgs.append(pkg)
        return
    if pkg.skip == "private index":
        first.skip = pkg.skip  # Pipenv may pin the index in one category only: keep the name off pypi.org anyway
    if pkg.registry is not None and pkg.registry != first.registry:
        if first.registry is None:
            first.registry = pkg.registry  # the other entry is from PyPI: this index decides for both
        else:
            first.skip = "private index"  # pinned to two indexes: no single lookup is safe for both
    if first.markers is not None:
        first.markers = None if pkg.markers is None else first.markers + pkg.markers


def parse_pdm_lock(path: Path) -> Source:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    metadata = data.get("metadata", {})
    legacy_files = metadata.get("files", {})
    locked_groups = metadata.get("groups")
    strategy = metadata.get("strategy")
    pkgs: list[Package] = []
    seen: dict[tuple[str, str | None], Package] = {}
    # Each package's groups, merged over its entries (PDM writes one per extras variant).
    groups: dict[tuple[str, str | None], list[str] | None] = {}
    for p in data.get("package", []):
        pkg = Package(p["name"], p.get("version"), origin=path.name)
        recorded = p.get("groups")
        recorded = [g for g in recorded if isinstance(g, str)] if isinstance(recorded, list) else None
        key = (pkg.key, pkg.version)
        before = groups.get(key, [])
        groups[key] = None if recorded is None or (key in groups and before is None) else [*(before or []), *recorded]
        if p.get("marker"):
            pkg.markers = [p["marker"]]
        src_type = next((k for k in ("git", "hg", "svn", "bzr", "path", "url", "file") if k in p), None)
        if src_type:
            pkg.skip = src_type
        else:
            entries = p.get("files")
            if entries is None:
                # Older lockfiles (PDM before 2.8) keep files in one [metadata.files] table
                # keyed by "name version", with the name as PDM wrote it, which isn't always
                # normalized ("Jinja2 3.1.2" for jinja2): try the exact key, then match names.
                entries = legacy_files.get(f"{p['name']} {p.get('version')}")
                if entries is None:
                    target_name = canonicalize_name(p["name"])
                    target_version = p.get("version")
                    for key, value in legacy_files.items():
                        key_name, _, key_version = key.rpartition(" ")
                        if key_version == target_version and canonicalize_name(key_name) == target_name:
                            entries = value
                            break
            if entries is not None:
                pkg.files = [f for f in (_filename(e) for e in entries) if f]
            # Not in either place: leave files as None so the PyPI lookup fills them in.
        _add_once(pkgs, seen, pkg)
    return Source(
        path.name,
        pkgs,
        pdm_groups=[groups[(pkg.key, pkg.version)] for pkg in pkgs],
        pdm_locked_groups=(
            [canonicalize_name(g) for g in locked_groups if isinstance(g, str)]
            if isinstance(locked_groups, list)
            else None
        ),
        pdm_inherit_metadata=isinstance(strategy, list) and "inherit_metadata" in strategy,
        pdm_pyproject=path.parent / "pyproject.toml",
    )


def parse_pipfile_lock(path: Path) -> Source:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SourceError(f"{path.name}: not a Pipfile.lock (expected a JSON object)")
    pkgs: list[Package] = []
    seen: dict[tuple[str, str | None], Package] = {}
    for key, val in data.items():
        if key != "_meta" and not isinstance(val, dict):
            raise SourceError(f"{path.name}: {key} isn't a package section")
    sections = [s for s in ("default", "develop") if s in data]
    sections += [key for key in data if key not in ("_meta", "default", "develop")]
    meta = data.get("_meta")
    sources = meta.get("sources") if isinstance(meta, dict) else None
    # Pipenv writes "index": "<source name>" for a package pinned to one of the Pipfile's [[source]] entries.
    source_urls = {
        s["name"]: s["url"]
        for s in (sources if isinstance(sources, list) else [])
        if isinstance(s, dict) and isinstance(s.get("name"), str) and isinstance(s.get("url"), str)
    }
    for section in sections:
        for name, p in data[section].items():
            if not isinstance(p, dict):
                raise SourceError(f"{path.name}: {section}.{name} isn't a package entry")
            version = p.get("version")
            if version is not None and not isinstance(version, str):
                raise SourceError(f"{path.name}: {section}.{name} has a version that isn't a string")
            pkg = Package(name, version.removeprefix("==") if version else None, origin=path.name)
            if p.get("markers"):
                pkg.markers = [p["markers"]]
            pkg.skip = next((kind for kind in ("git", "hg", "svn", "bzr", "path", "file") if kind in p), None)
            if pkg.skip is None and p.get("editable"):
                pkg.skip = "editable"
            index = p.get("index")
            if pkg.skip is None and index is not None:
                url = source_urls.get(str(index))
                if url is None:  # an index the lock doesn't describe: no way to tell where the name may go
                    pkg.skip = "private index"
                elif not _is_public_pypi(url):
                    pkg.registry = url  # pypi.fill_all skips it, unless WHEELGAP_INDEX_URL is that index
            _add_once(pkgs, seen, pkg)
    return Source(path.name, pkgs)


def _is_public_pypi(url: str | None) -> bool:
    try:
        return url is not None and urlparse(url).hostname in ("pypi.org", "pypi.python.org")
    except ValueError:  # e.g. "https://[bad/simple": not something we can call PyPI
        return False


def _pinned_version(req: Requirement) -> str | None:
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        return specs[0].version
    return None


def parse_pyproject(path: Path) -> Source:
    """A library's direct dependencies: [project] dependencies, read as requirement lines. Their own
    dependencies aren't known without a lockfile, so they aren't checked."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise SourceError(f"{path}: invalid TOML: {e}") from None
    project = data.get("project")
    if not isinstance(project, dict):
        raise SourceError(f"{path}: no [project] table, so there are no dependencies to read")
    dynamic = project.get("dynamic", [])
    if not isinstance(dynamic, list):
        raise SourceError(f"{path}: [project] dynamic must be a list of strings")
    if "dependencies" in dynamic:
        raise SourceError(
            f"{path}: [project] dependencies is dynamic (set by the build backend), so wheelgap can't read it; "
            "check a lockfile instead"
        )
    if not project.get("dependencies") and not project.get("optional-dependencies"):
        # poetry-core 2 builds [tool.poetry.dependencies] into the package whenever [project] lists no dependencies
        # or optional-dependencies (missing or empty: DependencyGroup.dependencies' "legacy mode").
        tool = data.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        poetry_deps = poetry.get("dependencies") if isinstance(poetry, dict) else None
        if isinstance(poetry_deps, dict) and any(dep.lower() != "python" for dep in poetry_deps):
            raise SourceError(
                f"{path}: the dependencies are in [tool.poetry.dependencies], not [project] dependencies, "
                "so wheelgap can't read them; check poetry.lock instead"
            )
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        raise SourceError(f"{path}: [project] dependencies must be a list of strings")
    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict) or not all(
        isinstance(v, list) and all(isinstance(d, str) for d in v) for v in optional.values()
    ):
        raise SourceError(f"{path}: [project] optional-dependencies must be a table of lists of strings")
    extras: dict[str, list[str]] = {}
    for extra_name, lines in optional.items():
        key = canonicalize_name(extra_name)
        if key in extras:
            raise SourceError(
                f"{path}: [project] optional-dependencies has two extras that are both {key!r} once normalized "
                "(PEP 685)"
            )
        extras[key] = list(lines)
    source = Source(path.name, pyproject_extras=extras, pyproject_dynamic_extras="optional-dependencies" in dynamic)
    if isinstance(requires_python := project.get("requires-python"), str):
        source.requires_python = requires_python
    if isinstance(name := project.get("name"), str):
        source.pyproject_name = canonicalize_name(name)
    self_extras: list[str] = []
    for line in dependencies:
        pkg = requirement_to_package(line, path.name)
        if pkg.key != source.pyproject_name:
            source.packages.append(pkg)
            continue
        # `myproject[extra]` in its own dependencies: installing the project installs that extra too.
        self_extras.extend(_self_reference_extras(source, line, Requirement(line)))
    _add_extras(source, self_extras)
    return source


def select_pyproject_extras(source: Source, extras: Iterable[str], all_extras: bool) -> None:
    """Add the requirements of the chosen [project] optional-dependencies groups to a pyproject.toml source.
    A self-reference such as `myproject[test]` inside an extra pulls in that extra, as pip does."""
    available = source.pyproject_extras
    if available is None:
        return
    wanted: list[str] = list(available) if all_extras else [canonicalize_name(e) for e in extras]
    if source.pyproject_dynamic_extras and (all_extras or wanted):
        raise SourceError(
            f"{source.label}: [project] optional-dependencies is dynamic (set by the build backend), "
            "so its extras can't be read"
        )
    if unknown := sorted(set(wanted) - set(available)):
        raise SourceError(f"{source.label}: unknown extras: {', '.join(unknown)}")
    _add_extras(source, wanted)


def _self_reference_extras(source: Source, line: str, req: Requirement) -> list[str]:
    """The extras that `myproject[extra]`, a requirement on the project itself, pulls in."""
    if req.marker or req.url or req.specifier:
        raise SourceError(
            f"{source.label}: {line!r} refers to the project itself with a marker, URL or version, "
            "which isn't supported"
        )
    wanted: list[str] = [canonicalize_name(e) for e in req.extras]
    if wanted and source.pyproject_dynamic_extras:
        raise SourceError(
            f"{source.label}: {line!r} names extras, but [project] optional-dependencies is dynamic "
            "(set by the build backend), so its extras can't be read"
        )
    missing = sorted(set(wanted) - set(source.pyproject_extras or {}))
    if missing:
        raise SourceError(f"{source.label}: {line!r} names unknown extras: {', '.join(missing)}")
    return wanted


def _add_extras(source: Source, wanted: list[str]) -> None:
    """Add each wanted extra's requirements, following self-references, and each identical requirement once."""
    available = source.pyproject_extras or {}
    seen: set[str] = set()
    while wanted:
        extra = wanted.pop(0)
        if extra in seen:
            continue
        seen.add(extra)
        for line in available.get(extra, []):
            try:
                req = Requirement(line)
            except InvalidRequirement as e:
                raise SourceError(f"{source.label}: can't parse requirement {line!r}: {e}") from None
            if source.pyproject_name is not None and canonicalize_name(req.name) == source.pyproject_name:
                wanted.extend(_self_reference_extras(source, line, req))
                continue
            pkg = requirement_to_package(line, source.label)
            if pkg not in source.packages:  # also in another extra or in dependencies: one row
                source.packages.append(pkg)


def requirement_to_package(text: str, origin: str) -> Package:
    try:
        req = Requirement(text)
    except InvalidRequirement as e:
        raise SourceError(f"{origin}: can't parse requirement {text!r}: {e}") from None
    markers = [str(req.marker)] if req.marker else None
    if req.url:
        return Package(req.name, None, skip="url", origin=origin, markers=markers)
    version = _pinned_version(req)
    spec = str(req.specifier) if req.specifier and version is None else None
    return Package(req.name, version, origin=origin, markers=markers, spec=spec)


# "name @ url", "name@url" and "name[extra] @ url": PEP 508 direct references, which Requirement parses.
# A bare URL can't match: its scheme ("git+ssh:") has characters a package name can't.
_NAMED_URL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?\s*@")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
_COMMENT_RE = re.compile(r"\s#")
_INCLUDE_RE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)?(\S.*)$")
_CONSTRAINT_RE = re.compile(r"^(?:-c(?:\s+|=)?|--constraint(?:\s+|=))(\S.*)$")


def parse_requirements(path: Path) -> Source:
    constraints: list[Package] = []
    seen: dict[bool, set[Path]] = {False: set(), True: set()}  # requirement files, constraint files
    pkgs = _read_requirements(path, seen, constraints)
    by_name: dict[str, list[Package]] = {}
    for entry in constraints:
        by_name.setdefault(entry.key, []).append(entry)
    for pkg in pkgs:
        if pkg.skip or pkg.version or not (entries := by_name.get(pkg.key)):
            continue
        if any(entry.markers for entry in entries):
            pkg.error = "constraint has an environment marker; target-dependent constraints aren't supported"
        elif any(entry.skip for entry in entries):
            pkg.error = "constraint isn't an index version or range"
        # 1.9 and 1.9.0 are one version; reversed() keeps the first spelling.
        elif len(pins := {canonicalize_version(e.version): e.version for e in reversed(entries) if e.version}) > 1:
            pkg.error = "constraints specify conflicting exact versions"
        elif pins:
            pkg.version, pkg.spec = next(iter(pins.values())), None
        else:
            spec = SpecifierSet(pkg.spec or "")
            for entry in entries:
                spec &= SpecifierSet(entry.spec or "")
            pkg.spec = str(spec) or None
    return Source(path.name, pkgs, requirements=True)


def _read_requirements(
    path: Path, seen: dict[bool, set[Path]], constraints: list[Package], constraint: bool = False
) -> list[Package]:
    path = path.resolve()
    if path in seen[constraint]:
        return []
    seen[constraint].add(path)
    pkgs: list[Package] = []
    append = constraints.append if constraint else pkgs.append
    lines = path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines()
    for raw in lines:
        line = _COMMENT_RE.split(raw, 1)[0].strip()  # " # note" or "<TAB># note"
        if not line or line.startswith("#"):
            continue
        if m := _CONSTRAINT_RE.match(line):
            target = m.group(1).strip()
            # A URL (-c https://releases.openstack.org/...) or ${VAR} is left to pip: it's skipped, as before.
            if "://" not in target and "${" not in target:
                _read_requirements(path.parent / target, seen, constraints, True)
            continue
        if m := _INCLUDE_RE.match(line):  # -r file, -rfile, --requirement file, --requirement=file
            pkgs.extend(_read_requirements(path.parent / m.group(1).strip(), seen, constraints, constraint))
            continue
        if line.startswith("-e ") or line.startswith("--editable"):
            name = line.split("#egg=", 1)[1] if "#egg=" in line else line.split(None, 1)[-1]
            append(Package(name, None, skip="editable", origin=path.name))
            continue
        if line.startswith("-"):
            continue  # pip options: --index-url, --hash continuation, ...
        tokens = line.split()
        # Drop --hash=sha256:... and "--hash sha256:..." (option and value as two tokens).
        line = " ".join(
            t for k, t in enumerate(tokens) if not t.startswith("--hash") and (k == 0 or tokens[k - 1] != "--hash")
        )
        if line.startswith((".", "/")):
            append(Package(line, None, skip="path", origin=path.name))
            continue
        if "://" in line and not _NAMED_URL_RE.match(line):
            # A bare URL (git+ssh://git@github.com/org/repo.git#egg=foo): never from the index.
            # "name @ url" lines are left to Requirement, which knows the name and markers.
            egg = _EGG_RE.search(line)
            name = egg.group(1) if egg else line.split(";", 1)[0].strip()
            append(Package(name, None, skip="url", origin=path.name))
            continue
        append(requirement_to_package(line, path.name))
    return pkgs


PARSERS = {
    "uv.lock": parse_uv_lock,
    "pylock.toml": parse_pylock,
    "poetry.lock": parse_poetry_lock,
    "pdm.lock": parse_pdm_lock,
    "Pipfile.lock": parse_pipfile_lock,
}


def load_file(path: Path) -> Source:
    name = path.name
    if name == "pyproject.toml":
        return parse_pyproject(path)
    if name in PARSERS:
        return PARSERS[name](path)
    if name.startswith("pylock.") and name.endswith(".toml"):
        return parse_pylock(path)
    if name.startswith("requirements") and name.endswith(".lock"):
        return parse_requirements(path)
    if name.endswith(".lock") and "uv" in name:
        return parse_uv_lock(path)
    if name.endswith((".txt", ".in")):
        return parse_requirements(path)
    raise SourceError(
        f"don't know how to read {path} "
        "(expected uv.lock, pylock.toml, poetry.lock, pdm.lock, Pipfile.lock, requirements.lock, requirements.txt "
        "or pyproject.toml)"
    )


def find_lockfile(directory: Path) -> Path:
    for name in LOCKFILES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    hint = "Pass a file, or package names like `wheelgap numpy psycopg2`."
    if _has_project_table(directory / "pyproject.toml"):
        pyproject = directory / "pyproject.toml"
        hint = f"To check the direct dependencies in its pyproject.toml, run `wheelgap {pyproject}`. " + hint
    raise SourceError(f"no lockfile found in {directory} (looked for {', '.join(LOCKFILES)}). {hint}")


def _has_project_table(pyproject: Path) -> bool:
    """For the "no lockfile" hint only: a missing or unreadable file just means no hint."""
    try:
        return isinstance(tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project"), dict)
    except (OSError, ValueError):
        return False


def load_inputs(inputs: list[str]) -> list[Source]:
    """Each input is a file, a directory (lockfile auto-detected) or a requirement like 'numpy==2.3.4'."""
    sources: list[Source] = []
    adhoc = Source("command line")
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            sources.append(load_file(find_lockfile(p)))
        elif p.is_file():
            sources.append(load_file(p))
        elif "/" in item or item.endswith((".lock", ".txt", ".toml")):
            raise SourceError(f"{item}: no such file")
        else:
            adhoc.packages.append(requirement_to_package(item, "command line"))
    if adhoc.packages:
        sources.append(adhoc)
    return sources
