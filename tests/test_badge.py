"""Tests for --badge: a shields.io endpoint file with how many targets have wheels (#359)."""

from __future__ import annotations

import json

from wheelgap.badge import badge
from wheelgap.check import Row, Status
from wheelgap.cli import main
from wheelgap.sources import Package
from wheelgap.targets import build_targets

LIN, MUSL, WIN = build_targets(["3.13"], ["linux-x86_64", "musl-x86_64", "win-amd64"])
TARGETS = [LIN, MUSL, WIN]


def row(name, **statuses):
    by_label = {"lin": LIN, "musl": MUSL, "win": WIN}
    return Row(Package(name, "1.0"), {t: Status.WHEEL for t in TARGETS} | {by_label[k]: s for k, s in statuses.items()})


def test_counts_targets_where_nothing_builds_from_source_or_is_missing():
    assert badge([row("a"), row("b", win=Status.NOT_NEEDED, musl=Status.BUNDLED)], TARGETS) == {
        "schemaVersion": 1,
        "label": "wheels",
        "message": "3/3 targets",
        "color": "brightgreen",
    }
    some = badge([row("a", musl=Status.SOURCE), row("b", win=Status.MISSING)], TARGETS)
    assert (some["message"], some["color"]) == ("1/3 targets", "yellow")
    none = badge([row("a", lin=Status.SOURCE, musl=Status.SOURCE, win=Status.MISSING)], TARGETS)
    assert (none["message"], none["color"]) == ("0/3 targets", "red")
    assert badge([], [LIN])["message"] == "1/1 target"


def test_a_gap_ignored_on_a_target_does_not_count_there():
    ignored = row("a", musl=Status.SOURCE, win=Status.UNKNOWN)
    ignored.ignored = frozenset({MUSL, WIN})  # --ignore a:musl-x86_64 --ignore a:win-amd64
    assert badge([ignored], TARGETS)["message"] == "3/3 targets"


def test_an_unknown_result_is_unknown_not_a_count():
    # Even when another package already has a gap there: the count would be a guess either way.
    assert badge([row("a", musl=Status.SOURCE), row("b", win=Status.UNKNOWN)], TARGETS) == {
        "schemaVersion": 1,
        "label": "wheels",
        "message": "unknown",
        "color": "lightgrey",
    }


def test_cli_writes_the_badge_even_when_the_check_fails(capsys, fixtures, tmp_path, fake_pypi):
    path = tmp_path / "wheels.json"
    args = [str(fixtures / "armgap"), "-p", "3.13", "-t", "linux-aarch64,musl-aarch64"]
    assert main(args) == 1
    plain = capsys.readouterr().out
    assert main([*args, "--badge", str(path)]) == 1
    assert capsys.readouterr().out == plain  # the usual report, unchanged
    # newglibc is missing on both targets
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "schemaVersion": 1,
        "label": "wheels",
        "message": "0/2 targets",
        "color": "red",
    }


def test_cli_badge_write_error_is_exit_2(capsys, fixtures, tmp_path, fake_pypi):
    args = [str(fixtures / "armgap"), "-p", "3.13", "-t", "linux-aarch64"]
    for path in (str(tmp_path / "nodir" / "wheels.json"), ""):  # "": an unset CI variable, not "no badge"
        assert main([*args, "--badge", path]) == 2
        assert "wheelgap: error:" in capsys.readouterr().err
