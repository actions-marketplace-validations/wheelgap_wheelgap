"""--dist DIR (#374): check the files about to be uploaded, and flag a target the last release had a wheel for.

The fake index serves `distpkg` 1.0 with wheels for Linux and Windows on ARM; the dist folders below name 1.1,
as a real release would.
"""

import json
from pathlib import Path

import pytest

from wheelgap.cli import main

LINUX_WHEEL = "distpkg-1.1-cp313-cp313-manylinux_2_17_x86_64.whl"
WIN_ARM_WHEEL = "distpkg-1.1-cp313-cp313-win_arm64.whl"
MACOS_WHEEL = "distpkg-1.1-cp313-cp313-macosx_14_0_arm64.whl"
SDIST = "distpkg-1.1.tar.gz"


def dist(tmp_path: Path, *filenames: str) -> Path:
    folder = tmp_path / "dist"
    folder.mkdir()
    for filename in filenames:
        (folder / filename).write_bytes(b"")
    return folder


def check(folder: Path, *args: str) -> int:
    return main(["--dist", str(folder), "-p", "3.13", "-t", "linux-x86_64", "-t", "win-arm64", "--no-cache", *args])


def test_every_wheel_still_ships(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL, WIN_ARM_WHEEL, SDIST)
    assert check(folder, "--all") == 0
    out = capsys.readouterr().out
    assert "had a wheel" not in out
    assert "wheel" in out  # every target installs a wheel, not the sdist


def test_sdist_only_dist_builds_everywhere(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, SDIST)
    assert check(folder) == 1
    out = capsys.readouterr().out
    assert "source" in out  # falls back to the sdist, same as a lockfile row


def test_a_dropped_target_is_named_and_fails(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)  # no win_arm64 wheel this time
    assert check(folder) == 1
    out = capsys.readouterr().out
    assert "win-arm64: 1.0 had a wheel, this release has none" in out


def test_a_dropped_target_fails_even_with_fail_on_missing(fake_pypi, capsys, tmp_path):
    # The missing wheel builds from the sdist, which --fail-on missing would allow; the drop still fails.
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)
    assert check(folder, "--fail-on", "missing") == 1
    assert "win-arm64: 1.0 had a wheel, this release has none" in capsys.readouterr().out


def test_dropped_targets_are_in_json(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)
    assert check(folder, "--json") == 1
    assert json.loads(capsys.readouterr().out)["dropped"] == {"win-arm64": "1.0"}


def test_a_new_target_is_not_a_drop(fake_pypi, capsys, tmp_path):
    # macOS on ARM is new in this release; it isn't a target the last release lost.
    folder = dist(tmp_path, LINUX_WHEEL, WIN_ARM_WHEEL, MACOS_WHEEL, SDIST)
    assert check(folder, "-t", "macos-arm64") == 0
    assert "had a wheel" not in capsys.readouterr().out


def test_a_first_release_compares_nothing(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, "brandnew-1.0-py3-none-any.whl")  # not on the index
    assert check(folder) == 0
    assert "brandnew has no earlier release on the index" in capsys.readouterr().err


@pytest.mark.parametrize(
    "filenames",
    [
        (LINUX_WHEEL, "otherlib-1.1-py3-none-any.whl"),  # two projects
        (LINUX_WHEEL, "distpkg-1.0-cp313-cp313-manylinux_2_17_x86_64.whl"),  # two versions
    ],
)
def test_a_mixed_dist_is_refused(fake_pypi, capsys, tmp_path, filenames):
    folder = dist(tmp_path, *filenames)
    assert check(folder) == 2
    assert "more than one project or version" in capsys.readouterr().err


def test_an_empty_dist_is_refused(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, "README.txt")
    assert check(folder) == 2
    assert "no wheels or sdists" in capsys.readouterr().err


def test_dist_takes_no_other_inputs(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL)
    with pytest.raises(SystemExit):
        main(["--dist", str(folder), "somepkg"])
    assert "--dist DIR takes no other inputs" in capsys.readouterr().err


def test_dist_needs_the_json_api(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL)
    assert check(folder, "--offline") == 2
    assert "--offline: --dist looks the last release up on PyPI" in capsys.readouterr().err
    assert check(folder, "--index-url", "https://example.org/simple") == 2
    assert "a simple index" in capsys.readouterr().err


def test_a_maintenance_release_is_compared_with_the_release_before_it(fake_pypi, capsys, tmp_path):
    # 1.1 comes after 1.0, which had no Windows on ARM wheel: the newer 2.0 having one isn't a drop.
    folder = dist(tmp_path, "distline-1.1-cp313-cp313-manylinux_2_17_x86_64.whl", "distline-1.1.tar.gz")
    check(folder, "--json")
    assert json.loads(capsys.readouterr().out)["dropped"] == {}


def test_an_index_variable_skips_the_comparison(fake_pypi, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_INDEX_URL", "https://example.invalid/simple")
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)  # would drop win-arm64 against PyPI's distpkg
    check(folder, "--json")
    captured = capsys.readouterr()
    assert "dropped" not in json.loads(captured.out)
    assert "PIP_INDEX_URL is set, so --dist didn't compare distpkg" in captured.err


def test_dist_refuses_online(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL)
    assert check(folder, "--online") == 2
    assert "--online would check PyPI's instead" in capsys.readouterr().err


@pytest.mark.parametrize("args", [("--advisory", "win-arm64"), ("--ignore", "distpkg:win-arm64")])
def test_an_advisory_or_ignored_target_isnt_a_drop(fake_pypi, capsys, tmp_path, args):
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)  # drops win-arm64, which these runs don't hold against it
    code = check(folder, *args)
    assert "had a wheel" not in capsys.readouterr().out
    assert code == 0


def test_a_drop_with_fail_on_missing_doesnt_say_the_run_passes(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, LINUX_WHEEL, SDIST)
    assert check(folder, "--fail-on", "missing") == 1
    assert "this run passes" not in capsys.readouterr().out


def test_a_drop_in_markdown(fake_pypi, capsys, tmp_path):
    folder = dist(tmp_path, WIN_ARM_WHEEL, SDIST)  # drops linux-x86_64, whose underscore Markdown mustn't escape
    assert check(folder, "--markdown") == 1
    assert "- `linux-x86_64`: `1.0` had a wheel, this release has none" in capsys.readouterr().out


def test_a_failed_lookup_is_noted_even_with_quiet(fake_pypi, capsys, tmp_path, monkeypatch):
    from wheelgap import cli

    def fail(name, version):
        raise OSError("no route")

    monkeypatch.setattr(cli, "previous_release", fail)
    folder = dist(tmp_path, LINUX_WHEEL, WIN_ARM_WHEEL, SDIST)
    assert check(folder, "-q") == 0
    assert "--dist's dropped-target check didn't run" in capsys.readouterr().err


def test_a_wheel_the_python_couldnt_use_isnt_a_drop(fake_pypi, capsys, tmp_path, monkeypatch):
    import conftest

    old = {"filename": "pyonly-1.0-py3-none-win_arm64.whl", "requires_python": ">=3.14"}
    monkeypatch.setitem(conftest.FAKE_INDEX, "pyonly", {"1.0": [old, "pyonly-1.0.tar.gz"]})
    folder = dist(tmp_path, "pyonly-1.1.tar.gz")  # checked on 3.13, which that wheel never served
    assert check(folder, "--json") == 1  # the source build is still a gap
    assert json.loads(capsys.readouterr().out)["dropped"] == {}
