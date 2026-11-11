import json

import pytest

import wheelgap
from wheelgap import Status
from wheelgap.pyodide import PyodideLockError, load_pyodide_lock
from wheelgap.sources import SourceError


def test_make_targets_takes_cli_style_names():
    targets = wheelgap.make_targets(python="3.13,3.14t", platforms=["linux-aarch64", "alpine"])
    assert [t.label(True) for t in targets] == [
        "3.13 linux-aarch64",
        "3.13 musl-x86_64",
        "3.14t linux-aarch64",
        "3.14t musl-x86_64",
    ]
    assert len(wheelgap.make_targets(python="3.13", platforms="all")) == 8
    with pytest.raises(wheelgap.TargetError):
        wheelgap.make_targets(python="3.13", platforms="solaris")


@pytest.mark.parametrize("python", ["", " , "])
def test_make_targets_uses_running_python_for_empty_input(python):
    assert wheelgap.make_targets(python=python) == wheelgap.make_targets()


def test_make_targets_empty_python_keeps_other_options():
    kwargs = dict(platforms="linux-x86_64", glibc="2.28")
    assert wheelgap.make_targets(python="", **kwargs) == wheelgap.make_targets(**kwargs)


def test_package_checks_reject_empty_targets_before_lookup(fake_pypi, fixtures):
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_packages(["purewheel"], targets=[])
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_package("purewheel", targets=[])
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_package("purewheel", targets=[], files=["purewheel-1.0-py3-none-any.whl"])
    with pytest.raises(ValueError, match="at least one target"):
        wheelgap.check_lockfile(fixtures / "uvproj", targets=[])
    assert not fake_pypi


@pytest.mark.parametrize("packages", [["purewheel"], []])
def test_check_packages_rejects_non_positive_workers_before_lookup(fake_pypi, packages):
    with pytest.raises(ValueError, match="workers must be at least 1"):
        wheelgap.check_packages(packages, workers=0)
    assert not fake_pypi


def test_check_package_offline_with_files():
    targets = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "musl-aarch64"])
    files = ["x-1.0-cp313-cp313-manylinux_2_17_x86_64.whl", "x-1.0.tar.gz"]
    result = wheelgap.check_package("x", "1.0", targets, files=files)
    assert result.statuses == {targets[0]: Status.WHEEL, targets[1]: Status.SOURCE}
    assert not result.ok and result.gaps == [targets[1]]
    assert not result.pure
    assert result.to_dict() == {
        "name": "x",
        "version": "1.0",
        "error": None,
        "results": {"3.13 linux-x86_64": "wheel", "3.13 musl-aarch64": "source"},
    }
    json.dumps(result.to_dict())  # JSON-friendly


def test_check_package_from_index(fake_pypi):
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    result = wheelgap.check_package("purewheel", targets=targets)
    assert result.version == "3.1" and result.ok and result.pure
    assert fake_pypi == ["https://pypi.org/pypi/purewheel/json"]


def test_check_packages_mixes_names_and_pins(fake_pypi):
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    results = wheelgap.check_packages(["purewheel", ("nowheels", "0.9"), "does-not-exist"], targets)
    by_name = {r.name: r for r in results}
    assert by_name["purewheel"].ok
    assert by_name["nowheels"].statuses[targets[0]] is Status.SOURCE
    assert by_name["does-not-exist"].error == "not on index"
    assert by_name["does-not-exist"].statuses[targets[0]] is Status.UNKNOWN


def test_check_packages_counts_what_pyodide_bundles(fake_pypi, fixtures):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))  # Python 3.13
    linux, pyodide = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "pyodide"])
    results = {
        r.name: r for r in wheelgap.check_packages(["fastlib", "markupsafe"], [linux, pyodide], pyodide_locks=[lock])
    }
    assert results["fastlib"].statuses == {linux: Status.WHEEL, pyodide: Status.BUNDLED}
    assert results["fastlib"].ok
    # In the lock but not on the index: the failed lookup stays a gap.
    assert results["markupsafe"].statuses[pyodide] is Status.UNKNOWN


def test_check_package_counts_what_pyodide_bundles(fake_pypi, fixtures):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    linux, pyodide = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "pyodide"])
    looked_up = wheelgap.check_package("fastlib", targets=[linux, pyodide], pyodide_locks=[lock])
    assert looked_up.statuses == {linux: Status.WHEEL, pyodide: Status.BUNDLED}
    offline = wheelgap.check_package(
        "MarkupSafe", "3.0.2", [linux, pyodide], files=["markupsafe-3.0.2.tar.gz"], pyodide_locks=[lock]
    )
    assert offline.statuses == {linux: Status.SOURCE, pyodide: Status.BUNDLED}
    assert offline.files == ("markupsafe-3.0.2.tar.gz",) and offline.gaps == [linux]


def test_check_lockfile_counts_what_pyodide_bundles(fake_pypi, fixtures, tmp_path):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    (tmp_path / "requirements.txt").write_text("fastlib==2.0.0\nnowheels==0.9\n")
    linux, pyodide = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "pyodide"])
    results = {
        r.name: r
        for r in wheelgap.check_lockfile(tmp_path / "requirements.txt", [linux, pyodide], pyodide_locks=[lock])
    }
    assert results["fastlib"].statuses == {linux: Status.WHEEL, pyodide: Status.BUNDLED}
    assert results["nowheels"].statuses == {linux: Status.SOURCE, pyodide: Status.MISSING}  # not in the lock


@pytest.mark.parametrize(
    ("version", "differs"),
    [("3.0.2", False), ("3.0.2.0", False), ("2.1.5", True), (None, False)],
)
def test_result_reports_the_version_pyodide_bundles(fixtures, version, differs):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))  # markupsafe 3.0.2
    linux, pyodide = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "pyodide"])
    files = [f"markupsafe-{version or '2.1.5'}.tar.gz"]
    result = wheelgap.check_package("MarkupSafe", version, [linux, pyodide], files=files, pyodide_locks=[lock])
    assert result.statuses[pyodide] is Status.BUNDLED and result.gaps == [linux]  # a different version still passes
    assert result.bundled_version == "3.0.2" and result.bundled_version_differs is differs
    assert result.to_dict()["bundled"] == "3.0.2" and result.to_dict()["bundled_version_differs"] is differs


def test_result_without_a_bundled_target_has_no_bundled_version(fake_pypi, fixtures):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    linux, pyodide = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "pyodide"])
    by_name = {
        r.name: r for r in wheelgap.check_packages(["fastlib", "nowheels"], [linux, pyodide], pyodide_locks=[lock])
    }
    assert by_name["fastlib"].bundled_version == "2.0.0" and not by_name["fastlib"].bundled_version_differs
    assert by_name["nowheels"].bundled_version is None
    assert (
        "bundled" not in by_name["nowheels"].to_dict()
        and "bundled_version_differs" not in by_name["nowheels"].to_dict()
    )


@pytest.mark.parametrize(
    "check",
    [
        lambda **kw: wheelgap.check_packages(["fastlib"], **kw)[0],
        lambda **kw: wheelgap.check_package("fastlib", **kw),
        lambda **kw: wheelgap.check_package("fastlib", "2.0.0", files=["fastlib-2.0.0.tar.gz"], **kw),
    ],
)
def test_pyodide_locks_can_be_a_generator(fake_pypi, fixtures, check):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    (pyodide,) = wheelgap.make_targets(python="3.13", platforms="pyodide")
    result = check(targets=[pyodide], pyodide_locks=(x for x in [lock]))
    assert result.statuses == {pyodide: Status.BUNDLED}


def test_check_lockfile_pyodide_locks_can_be_a_generator(fake_pypi, fixtures, tmp_path):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    (tmp_path / "requirements.txt").write_text("fastlib==2.0.0\n")
    (pyodide,) = wheelgap.make_targets(python="3.13", platforms="pyodide")
    (result,) = wheelgap.check_lockfile(tmp_path / "requirements.txt", [pyodide], pyodide_locks=(x for x in [lock]))
    assert result.statuses == {pyodide: Status.BUNDLED}


@pytest.mark.parametrize(
    "check",
    [
        lambda **kw: wheelgap.check_packages(["fastlib"], **kw),
        lambda **kw: wheelgap.check_package("fastlib", **kw),
        lambda **kw: wheelgap.check_package("fastlib", files=["fastlib-2.0.0.tar.gz"], **kw),
        lambda **kw: wheelgap.check_lockfile("missing-uv.lock", **kw),
    ],
)
@pytest.mark.parametrize(
    ("python", "copies", "message"),
    [
        ("3.12", 1, "no pyodide target uses Python 3.13 .pyodide targets: 3.12"),
        ("3.13", 2, "are both for Python 3.13"),
    ],
)
def test_pyodide_locks_that_cant_apply_raise_before_lookup(fake_pypi, fixtures, check, python, copies, message):
    lock = load_pyodide_lock(str(fixtures / "pyodide-lock.json"))
    targets = wheelgap.make_targets(python=python, platforms=["linux-x86_64", "pyodide"])
    with pytest.raises(PyodideLockError, match=message):
        check(targets=targets, pyodide_locks=[lock] * copies)
    assert not fake_pypi


def test_check_lockfile_respects_markers_and_graph(fixtures):
    targets = wheelgap.make_targets(python="3.13", platforms=["linux-x86_64", "win-amd64"])
    results = {r.name: r for r in wheelgap.check_lockfile(fixtures / "uvproj", targets)}
    assert results["winonly"].statuses == {targets[0]: Status.NOT_NEEDED, targets[1]: Status.WHEEL}
    assert "orphan" not in results  # installed on no target
    assert results["puresdist"].gaps == targets


def test_default_targets_use_running_python():
    targets = wheelgap.make_targets()
    assert {t.python for t in targets} == {wheelgap.running_python()}


def test_pinned_yanked_release_keeps_its_files(fake_pypi):
    # pip installs a release pinned with == even if it has been yanked (PEP 592).
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    assert wheelgap.check_package("yanked", "1.0", targets).statuses[targets[0]] is Status.WHEEL


@pytest.mark.parametrize(
    ("files", "pure"),
    [
        (["x-1-py3-none-any.whl"], True),
        (["x-1-py2.py3-none-any.whl"], True),
        (["x-1-py2-none-any.whl", "x-1.tar.gz"], False),  # e.g. futures: Python 2 only
        (["x-1-cp313-cp313-manylinux_2_17_x86_64.whl"], False),
    ],
)
def test_pure_means_a_python3_wheel_for_every_platform(files, pure):
    assert wheelgap.check_package("x", "1", wheelgap.make_targets("3.13", "linux"), files=files).pure is pure


def test_pinned_release_ignores_yanked_files_when_others_remain(fake_pypi):
    # pip only falls back to yanked files when every file of the pinned release is yanked.
    targets = wheelgap.make_targets(python="3.13", platforms="linux")
    assert wheelgap.check_package("halfyanked", "2.0", targets).statuses[targets[0]] is Status.SOURCE


# requires-python ==3.12.*, so uv kept only fastlib's cp312 wheel; PyPI (fake_pypi) has a cp313 one.
LOCK_312_ONLY = (
    'version = 1\nrequires-python = "==3.12.*"\n\n'
    '[[package]]\nname = "app"\nversion = "0"\nsource = { editable = "." }\ndependencies = [{ name = "fastlib" }]\n\n'
    '[[package]]\nname = "fastlib"\nversion = "2.0.0"\nsource = { registry = "https://pypi.org/simple" }\n'
    'sdist = { url = "https://files.pythonhosted.org/p/fastlib-2.0.0.tar.gz" }\n'
    'wheels = [{ url = "https://files.pythonhosted.org/p/fastlib-2.0.0-cp312-cp312-manylinux_2_17_x86_64.whl" }]\n'
)


def test_check_lockfile_rechecks_a_lock_for_another_python(fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    targets = wheelgap.make_targets(python="3.13", platforms="linux-x86_64")
    (result,) = wheelgap.check_lockfile(tmp_path, targets)
    assert result.name == "fastlib" and result.statuses == {targets[0]: Status.WHEEL}
    assert fake_pypi == ["https://pypi.org/pypi/fastlib/2.0.0/json"]


def test_check_lockfile_offline_uses_locked_files(fake_pypi, fixtures):
    targets = wheelgap.make_targets(python="3.13", platforms="linux-x86_64")
    results = wheelgap.check_lockfile(fixtures / "uvproj", targets, offline=True)
    assert results
    assert not fake_pypi


def test_check_lockfile_offline_skips_other_python_recheck(fake_pypi, tmp_path):
    (tmp_path / "uv.lock").write_text(LOCK_312_ONLY, encoding="utf-8")
    targets = wheelgap.make_targets(python="3.13", platforms="linux-x86_64")
    (result,) = wheelgap.check_lockfile(tmp_path, targets, offline=True)
    assert result.statuses == {targets[0]: Status.SOURCE}
    assert not fake_pypi


@pytest.mark.parametrize("kind", ["requirements.txt", "poetry.lock"])
def test_check_lockfile_offline_refuses_missing_files(fake_pypi, tmp_path, kind):
    text = "purewheel==3.1\n" if kind == "requirements.txt" else ('[[package]]\nname = "purewheel"\nversion = "3.1"\n')
    path = tmp_path / kind
    path.write_text(text, encoding="utf-8")
    with pytest.raises(SourceError, match="needs the index.*purewheel"):
        wheelgap.check_lockfile(path, offline=True)
    assert not fake_pypi


def test_check_lockfile_rejects_online_and_offline_before_loading(fake_pypi, tmp_path):
    with pytest.raises(ValueError, match="online.*offline"):
        wheelgap.check_lockfile(tmp_path / "absent.lock", online=True, offline=True)
    assert not fake_pypi
