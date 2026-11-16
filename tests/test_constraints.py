"""Tests for --write-constraints: --suggest's answers as a pip/uv constraints file (#378)."""

from __future__ import annotations

import pytest
from conftest import FAKE_INDEX
from test_cli import run

from wheelgap.cli import main

FLAGS = ["-p", "3.13", "--explain", "--suggest", "--color", "never"]
WHEELSTOPPED = {  # no newer release has a Windows ARM wheel, but 1.1 did
    "1.0": ["wheelstopped-1.0-cp313-cp313-win_arm64.whl", "wheelstopped-1.0.tar.gz"],
    "1.1": ["wheelstopped-1.1-cp313-cp313-win_arm64.whl", "wheelstopped-1.1.tar.gz"],
    "2.0": ["wheelstopped-2.0.tar.gz"],
}
OLDWIN = {  # 1.0 has the Windows ARM wheel 2.0 lacks, but not 2.0's Linux one
    "1.0": ["oldwin-1.0-cp313-cp313-win_arm64.whl", "oldwin-1.0.tar.gz"],
    "2.0": ["oldwin-2.0-cp313-cp313-manylinux_2_17_x86_64.whl", "oldwin-2.0.tar.gz"],
}
NEWDROP = {  # 1.1 has the Windows ARM wheel 1.0 lacks, but not 1.0's Linux one
    "1.0": ["newdrop-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", "newdrop-1.0.tar.gz"],
    "1.1": ["newdrop-1.1-cp313-cp313-win_arm64.whl", "newdrop-1.1.tar.gz"],
}


def written(tmp_path):
    return (tmp_path / "constraints.txt").read_text(encoding="utf-8").splitlines()


@pytest.mark.usefixtures("fake_pypi")
def test_one_pin_per_package_from_a_newer_or_an_older_release(capsys, tmp_path, monkeypatch):
    monkeypatch.setitem(FAKE_INDEX, "wheelstopped", WHEELSTOPPED)
    path = tmp_path / "constraints.txt"
    args = ["laggard==1.0", "wheelstopped==2.0", "-t", "win-amd64,win-arm64", "--write-constraints", str(path)]
    code, out, err = run(capsys, *args, *FLAGS)
    assert code == 1  # the gaps still fail the run
    lines = written(tmp_path)
    assert lines[1:3] == [
        f"# Not checked against the rest of the dependency tree: try `pip install -c {path} ...` or",
        f"# `uv pip compile -c {path} ...` to see that it still resolves.",
    ]
    assert lines[3:] == [
        "laggard==1.1  # wheels for win-amd64; 1.0 has none",
        "wheelstopped==1.1  # wheels for win-arm64; 2.0 has none",
    ]
    assert f"wheelgap: note: wrote 2 pins to {path}, not checked against the rest of the dependency tree" in err
    assert "laggard 1.0 -> 1.1 has wheels for win-amd64" in out  # --suggest's own lines don't change


@pytest.mark.usefixtures("fake_pypi")
def test_one_release_must_cover_every_gap_it_can(capsys, tmp_path):
    path = str(tmp_path / "constraints.txt")
    # 1.2 has Windows and musl wheels: the lowest release with both, though 1.1 is nearer for Windows.
    run(capsys, "laggard==1.0", "-t", "win-amd64,musl-x86_64", "--write-constraints", path, *FLAGS)
    assert written(tmp_path)[3:] == ["laggard==1.2  # wheels for win-amd64, musl-x86_64; 1.0 has none"]
    # No release has wheels for both Windows and macOS: leave it out rather than pin one and keep the other's gap.
    run(capsys, "laggard==1.0", "-t", "win-amd64,macos-arm64", "--write-constraints", path, *FLAGS)
    assert written(tmp_path)[3:] == [
        "# left out laggard: its gaps need different releases (1.1 for win-amd64; 1.3 for macos-arm64)"
    ]


@pytest.mark.usefixtures("fake_pypi")
def test_never_a_pin_that_loses_a_wheel_elsewhere(capsys, tmp_path, monkeypatch):
    monkeypatch.setitem(FAKE_INDEX, "oldwin", OLDWIN)
    monkeypatch.setitem(FAKE_INDEX, "newdrop", NEWDROP)
    path = str(tmp_path / "constraints.txt")
    code, out, err = run(
        capsys, "oldwin==2.0", "newdrop==1.0", "-t", "linux-x86_64,win-arm64", "--write-constraints", path, *FLAGS
    )
    assert "oldwin 2.0: no newer release has a wheel for win-arm64; the last one that did is 1.0" in out
    assert written(tmp_path)[3:] == [
        "# left out newdrop for win-arm64: a release with a wheel there loses one elsewhere",
        "# left out oldwin for win-arm64: a release with a wheel there loses one elsewhere",
    ]
    assert "wrote 0 pins" in err


@pytest.mark.usefixtures("fake_pypi")
def test_needs_suggest_and_a_writable_file(capsys, tmp_path):
    with pytest.raises(SystemExit):
        main(["laggard==1.0", "--explain", "--write-constraints", str(tmp_path / "c.txt")])
    assert (
        "--write-constraints writes what --suggest finds; pass --explain --suggest (or --deps --explain) too"
        in capsys.readouterr().err
    )
    code, _, err = run(capsys, "laggard==1.0", "-t", "win-amd64", "--write-constraints", str(tmp_path), *FLAGS)
    assert code == 2 and "wheelgap: error:" in err
    # A run without gaps still writes the file, with no pins.
    code, _, err = run(capsys, "purewheel", "-t", "win-amd64", "--write-constraints", str(tmp_path / "c.txt"), *FLAGS)
    assert code == 0 and "wrote 0 pins" in err
    assert (tmp_path / "c.txt").read_text(encoding="utf-8").count("\n") == 3  # the header alone
