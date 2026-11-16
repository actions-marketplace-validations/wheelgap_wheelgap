"""--write-constraints: --suggest's answers as a pip/uv constraints file, one pin per package."""

from __future__ import annotations

from pathlib import Path

from packaging.version import Version

from .check import Row
from .suggest import Suggestion
from .targets import Target


def constraints(
    rows: list[Row], targets: list[Target], suggestions: dict[int, Suggestion], path: str
) -> tuple[list[str], int]:
    """The file's lines and how many pins it has. A package gets a pin only when one release covers every gap target
    --suggest named a release for, and that release keeps every wheel the package has now; the others get a comment
    saying why they were left out. Nothing here is checked against the rest of the dependency tree."""
    multi_py = len({t.python for t in targets}) > 1

    def labels(ts: list[Target]) -> str:
        return ", ".join(t.label(multi_py) for t in ts)

    by_key: dict[str, list[Row]] = {}
    for r in rows:
        by_key.setdefault(r.package.key, []).append(r)
    pins: list[str] = []
    left_out: list[str] = []
    for group in by_key.values():
        asked = [(r, suggestions[id(r.package)]) for r in group if id(r.package) in suggestions]
        if not asked:
            continue
        name = asked[0][0].package.name
        if len({r.package.version for r in group}) > 1:  # a range resolved per Python: one pin would move them all
            left_out.append(f"# left out {name}: it resolves to different releases on different Pythons")
            continue
        named: dict[Target, str] = {}
        losing: list[Target] = []
        for _, s in asked:
            named |= s.found
            named |= {t: v for t, v in s.last.items() if t not in s.last_loses}
            losing += [t for t in (*s.loses, *s.last_loses) if t not in losing]
        if len(versions := sorted(set(named.values()), key=Version)) > 1:
            by_version = {v: [t for t, n in named.items() if n == v] for v in versions}
            needs = "; ".join(f"{v} for {labels(ts)}" for v, ts in by_version.items())
            left_out.append(f"# left out {name}: its gaps need different releases ({needs})")
        elif versions:
            pinned = (asked[0][0].package.version or "").strip()  # a lockfile's text: keep the comment on its line
            pins.append(f"{name}=={versions[0]}  # wheels for {labels(list(named))}; {pinned} has none")
        if losing:
            left_out.append(f"# left out {name} for {labels(losing)}: a release with a wheel there loses one elsewhere")
    header = [
        "# Written by wheelgap --suggest: one pin per package, to a release with wheels for its gap targets.",
        f"# Not checked against the rest of the dependency tree: try `pip install -c {path} ...` or",
        f"# `uv pip compile -c {path} ...` to see that it still resolves.",
    ]
    return [*header, *pins, *left_out], len(pins)


def write_constraints(path: str, rows: list[Row], targets: list[Target], suggestions: dict[int, Suggestion]) -> int:
    lines, count = constraints(rows, targets, suggestions, path)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return count
