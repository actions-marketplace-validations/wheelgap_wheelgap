"""--badge: a shields.io endpoint file (https://shields.io/badges/endpoint-badge) with how many targets have wheels."""

from __future__ import annotations

import json
from pathlib import Path

from .check import Row, Status
from .targets import Target


def badge(rows: list[Row], targets: list[Target]) -> dict[str, object]:
    """A target counts when no package on it builds from source or is missing. Any unknown result makes the message
    `unknown`: a count would be a guess. As in the rest of the report, a gap --ignore accepts (NAME, or NAME:TARGET
    on that target) doesn't count; an --advisory target's gaps do, since the badge is about what pip installs."""
    out: dict[str, object] = {"schemaVersion": 1, "label": "wheels"}
    if any(s is Status.UNKNOWN for r in rows for t, s in r.results.items() if t not in r.ignored):
        return out | {"message": "unknown", "color": "lightgrey"}
    gap = (Status.SOURCE, Status.MISSING)
    covered = sum(1 for t in targets if not any(r.results[t] in gap and t not in r.ignored for r in rows))
    color = "brightgreen" if covered == len(targets) else "red" if covered == 0 else "yellow"
    plural = "s" if len(targets) != 1 else ""
    return out | {"message": f"{covered}/{len(targets)} target{plural}", "color": color}


def write_badge(path: str, rows: list[Row], targets: list[Target]) -> None:
    Path(path).write_text(json.dumps(badge(rows, targets)) + "\n", encoding="utf-8")
