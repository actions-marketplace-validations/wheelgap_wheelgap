from copy import deepcopy

import pytest
from packaging.version import InvalidVersion

from wheelgap.suggest import nearest_with_wheel
from wheelgap.targets import build_targets


def wheel(version, tag="py3-none-any", **metadata):
    return {"filename": f"demo-{version}-{tag}.whl", **metadata}


@pytest.mark.parametrize(
    "releases,pinned,expected",
    [
        ({"1.1": [wheel("1.1")]}, "1.0", "1.1"),
        ({"1.1": [{"filename": "demo-1.1.tar.gz"}], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": [{"filename": "demo-1.1.tar.gz"}]}, "1.0", None),
        ({}, "1.0", None),
        ({"1.1": []}, "1.0", None),
        ({"1.1": [wheel("1.1", yanked=True)]}, "1.0", None),
        ({"1.1": [wheel("1.1", yanked=True), {"filename": "demo-1.1.tar.gz"}]}, "1.0", None),
        ({"1.1": [wheel("1.1", yanked=True)], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": [wheel("1.1", "cp313-cp313-win_amd64", yanked=True), wheel("1.1")]}, "1.0", "1.1"),
        ({"1.1rc1": [wheel("1.1rc1")]}, "1.0", None),
        ({"1.1.dev1": [wheel("1.1.dev1")], "1.1": [wheel("1.1")]}, "1.0", "1.1"),
        ({"1.1rc2": [wheel("1.1rc2")], "1.1": [wheel("1.1")]}, "1.1rc1", "1.1rc2"),
        ({"1.1": [wheel("1.1")]}, "1.1rc1", "1.1"),
        ({"1.1.dev2": [wheel("1.1.dev2")]}, "1.1.dev1", "1.1.dev2"),
        ({"1.0": [wheel("1.0")], "0.9": [wheel("0.9")]}, "1.0", None),
        ({"1.0.0": [wheel("1.0.0")]}, "1.0", None),
        ({"1.10": [wheel("1.10")], "1.9": [wheel("1.9")]}, "1.8", "1.9"),
        ({"not-a-version": [wheel("2.0")], "2.0": [wheel("2.0")]}, "1.0", "2.0"),
        ({"1.1": [{"filename": "broken.whl"}], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": None, "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": [{"url": "x"}], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": ["demo-1.1-py3-none-any.whl"], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.1": [{"filename": None}], "1.2": [wheel("1.2")]}, "1.0", "1.2"),
        ({"1.0.post1": [wheel("1.0.post1")], "1.1": [wheel("1.1")]}, "1.0", "1.0.post1"),
        ({"v1.1": [wheel("1.1")]}, "1.0", "v1.1"),
    ],
)
def test_nearest_with_wheel(releases, pinned, expected):
    (target,) = build_targets(["3.13"], ["linux-x86_64"])
    original = deepcopy(releases)
    assert nearest_with_wheel(releases, pinned, target) == expected
    assert releases == original


@pytest.mark.parametrize(
    "python,platform,tag,expected",
    [
        ("3.13", "linux-x86_64", "cp313-cp313-win_amd64", None),
        ("3.13", "win-amd64", "cp313-cp313-win_amd64", "1.1"),
        ("3.13", "musl-aarch64", "cp313-cp313-manylinux_2_17_aarch64", None),
        ("3.13", "musl-aarch64", "cp313-cp313-musllinux_1_2_aarch64", "1.1"),
        ("3.13", "linux-x86_64", "cp312-cp312-manylinux_2_17_x86_64", None),
        ("3.13", "linux-x86_64", "cp312-abi3-manylinux_2_17_x86_64", "1.1"),
        ("3.13t", "linux-x86_64", "cp312-abi3-manylinux_2_17_x86_64", None),
        ("3.13t", "linux-x86_64", "cp313-cp313t-manylinux_2_17_x86_64", "1.1"),
    ],
)
def test_nearest_with_wheel_uses_target_compatibility(python, platform, tag, expected):
    (target,) = build_targets([python], [platform])
    assert nearest_with_wheel({"1.1": [wheel("1.1", tag)]}, "1.0", target) == expected


def test_nearest_with_wheel_invalid_pin():
    (target,) = build_targets(["3.13"], ["linux-x86_64"])
    with pytest.raises(InvalidVersion):
        nearest_with_wheel({"1.1": [wheel("1.1")]}, "not-a-version", target)


@pytest.mark.parametrize("python", ["3.13", "3.13t"])
@pytest.mark.parametrize(
    "requires_python,expected",
    [(">=3.14", "1.2"), ("<3.13", "1.2"), (">=3.13.1", "1.1"), (None, "1.1"), ("not a specifier", "1.1")],
)
def test_nearest_with_wheel_requires_python(python, requires_python, expected):
    (target,) = build_targets([python], ["linux-x86_64"])
    releases = {
        "1.1": [wheel("1.1", requires_python=requires_python)],
        "1.2": [wheel("1.2", requires_python=">=3.13")],
    }
    assert nearest_with_wheel(releases, "1.0", target) == expected


@pytest.mark.parametrize("compatible_file", [False, True])
def test_nearest_with_wheel_requires_python_per_file(compatible_file):
    (target,) = build_targets(["3.13"], ["linux-x86_64"])
    files = [wheel("1.1", requires_python=">=3.14"), {"filename": "demo-1.1.tar.gz", "requires_python": ">=3.8"}]
    if compatible_file:
        files.append(wheel("1.1", "cp313-cp313-manylinux_2_17_x86_64", requires_python=">=3.13"))
    assert nearest_with_wheel({"1.1": files}, "1.0", target) == ("1.1" if compatible_file else None)


def test_nearest_with_wheel_never_fetches(fake_pypi):
    (target,) = build_targets(["3.13"], ["linux-x86_64"])
    assert nearest_with_wheel({"1.1": [wheel("1.1")]}, "1.0", target) == "1.1"
    assert fake_pypi == []
