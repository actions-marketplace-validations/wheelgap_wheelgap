import io
import json
import threading
from collections.abc import Callable, Iterator

import pytest

from wheelgap import __version__, mcp


def run(*messages: object, raw: bytes = b"") -> list:
    """Send messages to a fresh server over in-memory stdio; return the parsed replies, one per output line."""
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages) + raw)
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout)
    lines = stdout.getvalue().splitlines()
    return [json.loads(line) for line in lines]


def init(version: str = "2025-06-18") -> dict:
    return {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": version}}


def call(tool: str, msg_id: int = 1, **arguments: object) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}


@pytest.mark.parametrize(
    ("asked", "agreed"),
    [("2024-11-05", "2024-11-05"), ("2025-11-25", "2025-11-25"), ("2099-01-01", "2025-11-25"), (None, "2025-11-25")],
)
def test_initialize_negotiates_version(asked, agreed):
    (reply,) = run(init(asked))
    result = reply["result"]
    assert reply["id"] == 0
    assert result["protocolVersion"] == agreed
    assert result["serverInfo"] == {"name": "wheelgap", "version": __version__}
    assert result["capabilities"] == {"tools": {"listChanged": False}}


def test_notifications_get_no_reply_and_unknown_methods_are_errors():
    replies = run(
        init(),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 9}},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        # Clients on the 2026 revisions probe with server/discover and fall back to initialize on any error.
        {"jsonrpc": "2.0", "id": 3, "method": "server/discover", "params": {}},
    )
    assert [r["id"] for r in replies] == [0, 2, 3]
    assert replies[1]["result"] == {}
    assert replies[2]["error"]["code"] == mcp.METHOD_NOT_FOUND


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"{not json\n", mcp.PARSE_ERROR),
        (b"\xff\xfe\n", mcp.PARSE_ERROR),
        (b'{"id": 1, "method": "ping"}\n', mcp.INVALID_REQUEST),
        (b'{"jsonrpc": "2.0", "id": true, "method": "ping"}\n', mcp.INVALID_REQUEST),
        (b'{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": []}\n', mcp.INVALID_PARAMS),
        (b"[]\n", mcp.INVALID_REQUEST),
        (b'{"jsonrpc": "2.0", "id": ' + b"9" * 5000 + b', "method": "ping"}\n', mcp.PARSE_ERROR),
        (b"[" * 100_000 + b"\n", mcp.PARSE_ERROR),
        (b'{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": ["x"]}}\n', mcp.INVALID_PARAMS),
    ],
)
def test_bad_messages_get_errors_and_the_server_keeps_going(raw, code):
    replies = run(raw=raw + b'\n{"jsonrpc": "2.0", "id": 7, "method": "ping"}\r\n')
    assert replies[0]["error"]["code"] == code
    assert replies[-1] == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_responses_from_the_client_get_no_reply():
    replies = run({"jsonrpc": "2.0", "id": 3, "result": {}}, {"jsonrpc": "2.0", "id": 4, "method": "ping"})
    assert [r["id"] for r in replies] == [4]


def test_utf8_bom_on_the_first_line():
    (reply,) = run(raw=b"\xef\xbb\xbf" + json.dumps(init()).encode() + b"\n")
    assert reply["result"]["serverInfo"]["name"] == "wheelgap"


def test_batches_reply_with_a_list():
    (reply,) = run([{"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"jsonrpc": "2.0", "method": "x"}])
    assert reply == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_tools_list_has_valid_schemas():
    (_, reply) = run(init(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in reply["result"]["tools"]}
    assert set(tools) == {"check_package", "check_lockfile", "check_dockerfile", "list_targets"}
    for tool in tools.values():
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True
        assert set(tool["inputSchema"].get("required", [])) <= set(tool["inputSchema"]["properties"])


def test_check_package(fake_pypi):
    (_, reply) = run(init(), call("check_package", name="winonly", python="3.13", targets=["win-amd64", "linux/arm64"]))
    result = reply["result"]
    data = json.loads(result["content"][0]["text"])
    assert result["isError"] is False
    assert result["structuredContent"] == data
    assert data["results"] == {"3.13 win-amd64": "wheel", "3.13 linux-aarch64": "missing"}
    assert data["ok"] is False
    assert data["gaps"] == ["3.13 linux-aarch64"]
    assert data["version"] == "311"
    assert data["assumes"] == {"glibc": "2.36", "musl": "1.2", "macos": "14.0"}
    assert fake_pypi == ["https://pypi.org/pypi/winonly/json"]


def test_check_package_pinned_version_and_glibc(fake_pypi):
    (_, reply) = run(
        init(),
        call("check_package", name="fastlib", version="2.0.0", python="3.13", targets=["linux-x86_64"], glibc="2.17"),
    )
    data = reply["result"]["structuredContent"]
    assert fake_pypi == ["https://pypi.org/pypi/fastlib/2.0.0/json"]
    assert data["results"] == {"3.13 linux-x86_64": "wheel"}  # manylinux_2_17 still fits glibc 2.17
    assert data["assumes"]["glibc"] == "2.17"


def test_structured_content_only_from_2025_06_18(fake_pypi):
    (_, reply) = run(init("2025-03-26"), call("check_package", name="purewheel", python="3.13"))
    assert "structuredContent" not in reply["result"]
    assert json.loads(reply["result"]["content"][0]["text"])["ok"] is True


def test_check_package_lookup_failure_is_unknown_not_ok(fake_pypi):
    (_, reply) = run(init(), call("check_package", name="no-such-package", python="3.13", targets=["linux-x86_64"]))
    data = reply["result"]["structuredContent"]
    assert data["ok"] is False
    assert data["results"] == {"3.13 linux-x86_64": "unknown"}
    assert data["error"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "'name' is required"),
        ({"name": ""}, "'name' must be a non-empty string"),
        ({"name": "numpy", "targets": "linux-aarch64"}, "'targets' must be an array of strings"),
        ({"name": "numpy", "targets": ["linux-sparc"]}, "linux-sparc"),
        ({"name": "numpy", "python": "three"}, "three"),
        ({"name": "numpy", "platforms": ["win32"]}, "unknown argument(s): platforms"),
        ({"name": "numpy==2.0"}, "isn't a package name"),
        ({"name": "numpy", "version": ">=2"}, "exact version"),
        ({"name": "numpy", "glibc": "new"}, "glibc"),
    ],
)
def test_bad_arguments_are_tool_errors_without_lookups(fake_pypi, arguments, message):
    (_, reply) = run(init(), call("check_package", **arguments))
    assert reply["result"]["isError"] is True
    assert message in reply["result"]["content"][0]["text"]
    assert not fake_pypi


def test_unknown_tool_is_a_protocol_error():
    (_, reply) = run(init(), call("rm_rf"))
    assert reply["error"]["code"] == mcp.INVALID_PARAMS


def test_check_lockfile_reports_only_gaps(fake_pypi, fixtures):
    (_, reply) = run(
        init(), call("check_lockfile", path=str(fixtures / "uvproj"), python="3.13", targets=["linux-x86_64"])
    )
    data = reply["result"]["structuredContent"]
    assert data["targets"] == ["3.13 linux-x86_64"]
    assert data["ok"] is False
    assert data["packages"] == 5
    assert {g["name"]: g["results"] for g in data["gaps"]} == {
        "nativeext": {"3.13 linux-x86_64": "source"},
        "puresdist": {"3.13 linux-x86_64": "source"},
    }


def test_check_lockfile_lists_skipped_packages(fake_pypi, fixtures):
    (_, reply) = run(
        init(), call("check_lockfile", path=str(fixtures / "uvproj"), python="3.13", targets=["linux-x86_64"])
    )
    data = reply["result"]["structuredContent"]
    assert {s["name"]: s["reason"] for s in data["skipped"]} == {"demo": "editable", "gitdep": "git"}


def test_check_lockfile_uses_project_config_and_arguments_win(fake_pypi, fixtures):
    path = str(fixtures / "configproj")
    (_, configured, explicit) = run(
        init(),
        call("check_lockfile", path=path),
        call("check_lockfile", 2, path=path, python="3.13", targets=["linux-x86_64"]),
    )
    assert configured["result"]["structuredContent"]["targets"] == ["3.13 win-amd64", "3.14t win-amd64"]
    assert explicit["result"]["structuredContent"]["targets"] == ["3.13 linux-x86_64"]


def test_check_lockfile_offline_config_means_no_lookups(fake_pypi, tmp_path):
    (tmp_path / "requirements.txt").write_text("purewheel==3.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[tool.wheelgap]\noffline = true\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_lockfile", path=str(tmp_path / "requirements.txt"), python="3.13"))
    assert reply["result"]["isError"] is True
    assert "offline" in reply["result"]["content"][0]["text"]
    assert not fake_pypi


def test_check_lockfile_ignores_from_config(fake_pypi, tmp_path, fixtures):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / "pyproject.toml").write_text(
        '[tool.wheelgap]\nignore = ["nativeext:linux-x86_64", "puresdist"]\n', encoding="utf-8"
    )
    (_, reply) = run(
        init(),
        call("check_lockfile", path=str(tmp_path), python="3.13", targets=["linux-x86_64", "win-amd64"], offline=True),
    )
    data = reply["result"]["structuredContent"]
    assert data["ignored"] == ["puresdist"]
    # nativeext's linux gap is accepted; its Windows gap still counts.
    assert [(g["name"], g["gaps"]) for g in data["gaps"]] == [("nativeext", ["3.13 win-amd64"])]


def test_check_lockfile_glibc_argument(fake_pypi, fixtures):
    path = str(fixtures / "uvproj")
    (_, default, old) = run(
        init(),
        call("check_lockfile", path=path, python="3.13", targets=["linux-x86_64"], offline=True),
        call("check_lockfile", 2, path=path, python="3.13", targets=["linux-x86_64"], offline=True, glibc="2.12"),
    )
    assert "fastlib" not in {g["name"] for g in default["result"]["structuredContent"]["gaps"]}
    # fastlib's only wheel is manylinux_2_17, too new for glibc 2.12.
    assert "fastlib" in {g["name"] for g in old["result"]["structuredContent"]["gaps"]}


def test_check_lockfile_counts_unknown(fake_pypi, tmp_path):
    (tmp_path / "requirements.txt").write_text("purewheel==3.1\nno-such-package==1.0\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_lockfile", path=str(tmp_path / "requirements.txt"), python="3.13"))
    data = reply["result"]["structuredContent"]
    assert data["unknown"] == 1 and data["ok"] is False


def test_check_lockfile_path_starting_with_a_dash(fake_pypi, tmp_path, monkeypatch, fixtures):
    (tmp_path / "-p").mkdir()
    (tmp_path / "-p" / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    monkeypatch.chdir(tmp_path)
    (_, reply) = run(init(), call("check_lockfile", path="-p", python="3.13", offline=True))
    assert reply["result"]["isError"] is False
    assert reply["result"]["structuredContent"]["path"].endswith("-p")


def test_check_lockfile_argparse_exit_is_a_tool_error(monkeypatch, fixtures):
    from wheelgap import cli

    def exits(argv):
        raise SystemExit(2)

    monkeypatch.setattr(cli, "main", exits)
    replies = run(
        init(), call("check_lockfile", path=str(fixtures / "uvproj")), {"jsonrpc": "2.0", "id": 9, "method": "ping"}
    )
    reply, ping = (next(r for r in replies if r["id"] == i) for i in (1, 9))  # the call runs on the worker
    assert reply["result"]["isError"] is True
    assert ping["result"] == {}


def test_check_lockfile_passes_on_notes(fake_pypi, fixtures):
    (_, reply) = run(
        init(),
        call("check_lockfile", path=str(fixtures / "uvproj"), python="3.11", targets=["linux-x86_64"], offline=True),
    )
    notes = reply["result"]["structuredContent"]["notes"]
    assert "may show false gaps" in notes[0]
    assert notes[1] == "2 packages weren't checked (see skipped); ok covers the others only"
    assert not fake_pypi


def test_check_lockfile_explicit_python_beats_python_version_file(fake_pypi, tmp_path, fixtures):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / ".python-version").write_text("3.12\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_lockfile", path=str(tmp_path), python="3.13", offline=True))
    assert {t.split()[0] for t in reply["result"]["structuredContent"]["targets"]} == {"3.13"}


def test_check_lockfile_uses_python_version_file(fake_pypi, tmp_path, fixtures):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / ".python-version").write_text("3.12.4\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_lockfile", path=str(tmp_path), targets=["linux-x86_64"], offline=True))
    assert reply["result"]["structuredContent"]["targets"] == ["3.12 linux-x86_64"]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"path": "/no/such/uv.lock"}, "no such file or directory"),
        ({"path": "."}, None),
        ({"path": "x", "offline": "yes"}, "'offline' must be true or false"),
    ],
)
def test_check_lockfile_errors(fake_pypi, tmp_path, monkeypatch, arguments, message):
    monkeypatch.chdir(tmp_path)  # an empty directory: no lockfile to find
    (tmp_path / "x").write_text("", encoding="utf-8")
    (_, reply) = run(init(), call("check_lockfile", **arguments))
    assert reply["result"]["isError"] is True
    if message:
        assert message in reply["result"]["content"][0]["text"]


def dockerfile_project(tmp_path, fixtures, text: str):
    (tmp_path / "uv.lock").write_bytes((fixtures / "uvproj" / "uv.lock").read_bytes())
    (tmp_path / "Dockerfile").write_text(text, encoding="utf-8")
    return tmp_path / "Dockerfile"


def test_check_dockerfile_alpine_image_gives_musl_targets(fake_pypi, tmp_path, fixtures):
    dockerfile = dockerfile_project(tmp_path, fixtures, "FROM python:3.12 AS build\nFROM python:3.13-alpine\n")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(dockerfile)))
    data = reply["result"]["structuredContent"]
    assert data["image"] == {"python": "3.13", "platform": "musl-x86_64", "glibc": None, "musl": "1.2"}
    assert data["targets"] == ["3.13 musl-x86_64"]
    assert data["path"] == str(tmp_path.resolve())  # default: the Dockerfile's folder
    assert {g["name"] for g in data["gaps"]} >= {"nativeext", "puresdist"}
    assert {s["name"] for s in data["skipped"]} == {"demo", "gitdep"}


def test_check_dockerfile_matches_the_cli(fake_pypi, tmp_path, fixtures, capsys):
    from wheelgap import cli

    dockerfile = dockerfile_project(tmp_path, fixtures, "FROM --platform=linux/arm64 python:3.13-slim\n")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(dockerfile), path=str(tmp_path / "uv.lock")))
    data = reply["result"]["structuredContent"]
    assert data["image"]["platform"] == "linux-aarch64" and data["image"]["musl"] is None
    capsys.readouterr()
    cli.main(["--json", f"--dockerfile={dockerfile}", str(tmp_path / "uv.lock")])
    expected = json.loads(capsys.readouterr().out)
    assert data["targets"] == [f"{t['python']} {t['platform']}" for t in expected["targets"]]
    assert data["packages"] == expected["summary"]["packages"]


def test_check_dockerfile_targets_override_the_image_platform(fake_pypi, tmp_path, fixtures):
    dockerfile = dockerfile_project(tmp_path, fixtures, "FROM python:3.13-alpine\n")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(dockerfile), targets=["linux/arm64"]))
    data = reply["result"]["structuredContent"]
    # With an Alpine image, a Docker platform means musl, as with `wheelgap --dockerfile ... -t linux/arm64`.
    assert data["targets"] == ["3.13 musl-aarch64"]
    assert data["image"]["platform"] == "musl-x86_64"


@pytest.mark.parametrize(
    ("text", "arguments", "message"),
    [
        ("FROM ubuntu:24.04\n", {}, "supported, use check_lockfile with python and targets instead"),
        ("RUN echo hi\n", {}, "no FROM line found"),
        ("FROM python:3.13\n", {"path": "/no/such/uv.lock"}, "no such file or directory"),
        ("FROM python:3.13\n", {"dockerfile": "/no/such/Dockerfile"}, "no such file or directory"),
        ("FROM python:3.13\n", {"python": "3.12"}, "unknown argument(s): python"),
    ],
)
def test_check_dockerfile_errors(fake_pypi, tmp_path, fixtures, text, arguments, message):
    dockerfile = dockerfile_project(tmp_path, fixtures, text)
    (_, reply) = run(init(), call("check_dockerfile", **({"dockerfile": str(dockerfile)} | arguments)))
    assert reply["result"]["isError"] is True
    assert message in reply["result"]["content"][0]["text"]
    assert not fake_pypi


def test_check_dockerfile_directory_is_a_tool_error(fake_pypi, tmp_path, fixtures):
    dockerfile_project(tmp_path, fixtures, "FROM python:3.13\n")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(tmp_path)))
    assert reply["result"]["isError"] is True
    assert "is a directory" in reply["result"]["content"][0]["text"]


def test_check_package_pyodide_lock_marks_bundled(fake_pypi, fixtures):
    lock = str(fixtures / "pyodide-lock.json")  # Pyodide 0.29.0, Python 3.13: ships fastlib 2.0.0
    (_, without, with_lock) = run(
        init(),
        call("check_package", name="fastlib", python="3.13", targets=["pyodide"]),
        call("check_package", 2, name="fastlib", python="3.13", targets=["pyodide"], pyodide_lock=lock),
    )
    assert without["result"]["structuredContent"]["results"] == {"3.13 pyodide": "missing"}
    data = with_lock["result"]["structuredContent"]
    assert with_lock["result"]["isError"] is False
    assert data["results"] == {"3.13 pyodide": "bundled"}
    assert data["ok"] is True
    assert data["gaps"] == []
    assert data["bundled"] == "2.0.0"


def test_check_lockfile_pyodide_lock_marks_bundled(fake_pypi, fixtures, tmp_path):
    (tmp_path / "requirements.txt").write_text("fastlib==2.0.0\nnowheels==0.9\n", encoding="utf-8")
    (_, reply) = run(
        init(),
        call(
            "check_lockfile",
            path=str(tmp_path / "requirements.txt"),
            python="3.13",
            targets=["pyodide"],
            pyodide_lock=str(fixtures / "pyodide-lock.json"),
        ),
    )
    data = reply["result"]["structuredContent"]
    assert reply["result"]["isError"] is False
    assert {g["name"]: g["results"] for g in data["gaps"]} == {"nowheels": {"3.13 pyodide": "missing"}}


def test_check_lockfile_says_when_pyodide_ships_another_version(fake_pypi, fixtures, tmp_path):
    lock = json.loads((fixtures / "pyodide-lock.json").read_text(encoding="utf-8"))
    lock["packages"]["fastlib"]["version"] = "3.0.0"
    (tmp_path / "pyodide-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastlib==2.0.0\n", encoding="utf-8")
    (_, reply) = run(
        init(),
        call(
            "check_lockfile",
            path=str(tmp_path / "requirements.txt"),
            python="3.13",
            targets=["pyodide"],
            pyodide_lock=str(tmp_path / "pyodide-lock.json"),
        ),
    )
    data = reply["result"]["structuredContent"]
    assert data["ok"] is True  # bundled still passes, as in the CLI
    assert data["bundled_version_differs"] == [{"name": "fastlib", "version": "2.0.0", "bundled": "3.0.0"}]
    assert any("Pyodide ships 3.0.0, not the 2.0.0" in note for note in data["notes"])


def test_check_package_non_utf8_lock_is_a_tool_error(fake_pypi, tmp_path):
    (tmp_path / "pyodide-lock.json").write_bytes(b"\xff\xfe{}")
    (_, reply) = run(
        init(),
        call(
            "check_package",
            name="fastlib",
            python="3.13",
            targets=["pyodide"],
            pyodide_lock=str(tmp_path / "pyodide-lock.json"),
        ),
    )
    assert reply["result"]["isError"] is True
    assert "internal error" not in reply["result"]["content"][0]["text"]


@pytest.mark.parametrize("tool", ["check_package", "check_lockfile"])
@pytest.mark.parametrize(
    ("lock", "message"),
    [
        ("pyodide-lock.json", "is for Python 3.13"),  # the fixture's Python, but the targets use 3.12
        ("no-such-lock.json", "no-such-lock.json"),
        ("http://example.com/pyodide-lock.json", "plain http:// URLs are not allowed"),
    ],
)
def test_pyodide_lock_problems_are_tool_errors(fake_pypi, fixtures, tmp_path, tool, lock, message):
    (tmp_path / "requirements.txt").write_text("fastlib==2.0.0\n", encoding="utf-8")
    spec = lock if "://" in lock else str(fixtures / lock)
    where = {"name": "fastlib"} if tool == "check_package" else {"path": str(tmp_path / "requirements.txt")}
    (_, reply) = run(init(), call(tool, **where, python="3.12", targets=["pyodide"], pyodide_lock=spec))
    assert reply["result"]["isError"] is True
    text = reply["result"]["content"][0]["text"]
    assert message in text
    assert "internal error" not in text
    assert not fake_pypi


def test_list_targets():
    (_, reply) = run(init(), call("list_targets"))
    data = reply["result"]["structuredContent"]
    assert "musl-aarch64" in data["platforms"]
    assert data["groups"]["all"] and set(data["groups"]["all"]) <= set(data["platforms"])
    assert set(data["aliases"].values()) <= set(data["platforms"])


def test_stray_prints_stay_off_the_protocol_stream(monkeypatch):
    def noisy(args):
        print("progress...")
        return {"done": True}

    monkeypatch.setitem(mcp._HANDLERS, "list_targets", noisy)
    (_, reply) = run(init(), call("list_targets"))
    assert reply["result"]["structuredContent"] == {"done": True}


def test_crash_in_a_tool_is_reported_not_fatal(monkeypatch):
    def broken(args):
        raise KeyError("boom")

    monkeypatch.setitem(mcp._HANDLERS, "list_targets", broken)
    replies = run(init(), call("list_targets"), {"jsonrpc": "2.0", "id": 5, "method": "ping"})
    reply, ping = (next(r for r in replies if r["id"] == i) for i in (1, 5))  # the call runs on the worker
    assert reply["result"]["isError"] is True
    assert "KeyError" in reply["result"]["content"][0]["text"]
    assert ping["result"] == {}


def test_non_ascii_output_stays_on_one_line(monkeypatch):
    monkeypatch.setitem(mcp._HANDLERS, "list_targets", lambda args: {"text": "naïve\nline"})
    stdin = io.BytesIO(json.dumps(call("list_targets")).encode() + b"\n")
    stdout = io.BytesIO()
    mcp.serve(stdin, stdout)
    assert stdout.getvalue().count(b"\n") == 1
    assert json.loads(stdout.getvalue())["result"]["structuredContent"]["text"] == "naïve\nline"


def test_real_stdio(fixtures):
    import subprocess
    import sys

    messages = [init(), {"jsonrpc": "2.0", "method": "notifications/initialized"}, call("list_targets")]
    proc = subprocess.run(
        [sys.executable, "-m", "wheelgap.mcp"],
        input=b"".join(json.dumps(m).encode() + b"\r\n" for m in messages),
        capture_output=True,
        timeout=60,
        check=True,
    )
    replies = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in replies] == [0, 1]
    assert "linux-aarch64" in replies[1]["result"]["structuredContent"]["platforms"]


@pytest.mark.parametrize(("argv", "code"), [(["--version"], 0), (["--help"], 0), (["--bogus"], 2)])
def test_main_flags(argv, code, capsys):
    assert mcp.main(argv) == code


def with_token(message: dict, token: object) -> dict:
    message["params"]["_meta"] = {"progressToken": token}
    return message


def ping(msg_id: int) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "method": "ping"}


def is_progress(m: dict) -> bool:
    return m.get("method") == "notifications/progress"


class Recorder:
    """A stdout for serve that keeps the messages and lets the test wait for one."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._changed = threading.Condition()

    def write(self, data: bytes) -> None:
        with self._changed:
            self.messages += [json.loads(line) for line in data.splitlines()]
            self._changed.notify_all()

    def flush(self) -> None:
        pass

    def wait_for(self, predicate: Callable[[dict], bool]) -> Callable[[], None]:
        def wait() -> None:
            with self._changed:
                assert self._changed.wait_for(lambda: any(predicate(m) for m in self.messages), timeout=10)

        return wait


def live(*steps: object) -> Iterator[bytes]:
    """A stdin for serve: messages are sent one per line, and callables run before the next line is read."""
    for step in steps:
        if callable(step):
            step()
        else:
            yield json.dumps(step).encode() + b"\n"


@pytest.fixture
def slow_pypi(fake_pypi, monkeypatch):
    """fake_pypi, but every lookup waits until the test sets the returned event."""
    from wheelgap import pypi

    release = threading.Event()
    fast = pypi.fetch_json

    def fetch_json(url, timeout=20.0):
        assert release.wait(10), "the test never released the lookups"
        return fast(url, timeout)

    monkeypatch.setattr(pypi, "fetch_json", fetch_json)
    return release


@pytest.fixture
def three_pins(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("purewheel==3.1\nnowheels==0.9\nwinonly==311\n", encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(("version", "token"), [("2025-06-18", "tok"), ("2024-11-05", 7)])
def test_check_lockfile_sends_progress_with_a_token(fake_pypi, three_pins, tmp_path, version, token):
    other = tmp_path / "other.txt"  # not yet cached, so the call without a token really looks it up
    other.write_text("fastlib==2.0.0\n", encoding="utf-8")
    replies = run(
        init(version),
        with_token(call("check_lockfile", path=three_pins, python="3.13"), token),
        call("check_lockfile", 2, path=str(other), python="3.13"),  # no token, no progress
    )
    progress = [r["params"] for r in replies if is_progress(r)]
    assert [(p["progressToken"], p["progress"], p["total"]) for p in progress] == [(token, n, 3) for n in range(4)]
    # message arrived in 2025-03-26.
    assert all(("message" in p) == (version != "2024-11-05") for p in progress)
    assert [r.get("id") for r in replies if not is_progress(r)] == [0, 1, 2]
    assert replies.index(next(r for r in replies if r.get("id") == 1)) > replies.index(
        next(filter(is_progress, replies))
    )


def test_check_package_sends_progress_with_a_token(fake_pypi):
    replies = run(init(), with_token(call("check_package", name="purewheel", python="3.13"), "t"))
    assert [r["params"]["progress"] for r in replies if is_progress(r)] == [0, 1]
    assert replies[-1]["id"] == 1 and replies[-1]["result"]["isError"] is False


def test_progress_counts_up_over_several_lookup_runs():
    sent: list[object] = []
    server = mcp.Server(sent.append)
    call_ = mcp._Call("t")
    for done, total in [(0, 2), (1, 2), (2, 2), (0, 3), (1, 3), (2, 3), (3, 3)]:  # e.g. a lock re-check
        server._progress(call_, done, total)
    server.close()
    assert [(m["params"]["progress"], m["params"]["total"]) for m in sent] == [
        (0, 2),
        (1, 2),
        (2, 2),
        (3, 5),
        (4, 5),
        (5, 5),
    ]


def test_ping_is_answered_while_a_call_runs(slow_pypi, three_pins):
    out = Recorder()
    mcp.serve(
        live(
            init(),
            with_token(call("check_lockfile", path=three_pins, python="3.13"), "t"),
            out.wait_for(is_progress),  # the call is running, its lookups held
            ping(5),
            out.wait_for(lambda m: m.get("id") == 5),
            slow_pypi.set,
        ),
        out,
    )
    assert [m["id"] for m in out.messages if "id" in m] == [0, 5, 1]
    assert out.messages[-1]["result"]["structuredContent"]["packages"] == 3
    assert [m["params"]["progress"] for m in out.messages if is_progress(m)] == [0, 1, 2, 3]


def test_cancelled_call_sends_no_result_or_more_progress(slow_pypi, three_pins):
    out = Recorder()
    mcp.serve(
        live(
            init(),
            with_token(call("check_lockfile", path=three_pins, python="3.13"), "t"),
            out.wait_for(is_progress),
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1, "reason": "user"}},
            ping(5),
            out.wait_for(lambda m: m.get("id") == 5),  # the cancel has been handled
            slow_pypi.set,
            call("list_targets", 2),  # the server carries on
        ),
        out,
    )
    assert [m["id"] for m in out.messages if "id" in m] == [0, 5, 2]
    assert [m["params"]["progress"] for m in out.messages if is_progress(m)] == [0]


def test_call_cancelled_while_queued_never_runs(fake_pypi, slow_pypi, three_pins):
    out = Recorder()
    mcp.serve(
        live(
            init(),
            with_token(call("check_lockfile", path=three_pins, python="3.13"), "t"),
            out.wait_for(is_progress),
            call("check_package", 2, name="fastlib", python="3.13"),  # waits for the worker
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 2}},
            ping(5),
            out.wait_for(lambda m: m.get("id") == 5),
            slow_pypi.set,
        ),
        out,
    )
    assert [m["id"] for m in out.messages if "id" in m] == [0, 5, 1]
    assert fake_pypi and not any("fastlib" in url for url in fake_pypi)


def test_batch_with_a_call_still_gets_one_list(fake_pypi):
    (reply,) = run([call("list_targets", 1), ping(2), {"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert [r["id"] for r in reply] == [1, 2]
    assert reply[0]["result"]["isError"] is False


def test_a_result_that_isnt_json_is_still_answered(monkeypatch):
    monkeypatch.setitem(mcp._HANDLERS, "list_targets", lambda args: {"x": {1}})
    (_, reply) = run(init(), call("list_targets"))
    assert reply["id"] == 1
    assert reply["result"]["isError"] is True
    assert "TypeError" in reply["result"]["content"][0]["text"]


def test_a_crash_on_the_worker_is_still_answered(monkeypatch):
    def broken(self, name, arguments):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp.Server, "_call", broken)
    (_, reply) = run(init(), call("list_targets"))
    assert reply["id"] == 1
    assert reply["error"]["code"] == mcp.INTERNAL_ERROR


def test_check_dockerfile_without_a_lockfile_next_to_it_says_to_pass_path(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.13-alpine\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(tmp_path / "Dockerfile")))
    assert reply["result"]["isError"] is True
    assert "pass 'path'" in reply["result"]["content"][0]["text"]


def test_check_dockerfile_errors_carry_no_cli_advice_where_none_applies(tmp_path):
    (tmp_path / "Dockerfile").write_text("RUN echo hi\n", encoding="utf-8")
    (_, reply) = run(init(), call("check_dockerfile", dockerfile=str(tmp_path / "Dockerfile"), path=str(tmp_path)))
    text = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True and "check_lockfile" not in text
