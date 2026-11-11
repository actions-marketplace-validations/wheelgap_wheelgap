"""An MCP server, so coding agents can ask wheelgap whether a package has a wheel for a platform.

Run it with `python -m wheelgap.mcp` (or the `wheelgap-mcp` command). It speaks the Model Context Protocol over
stdio: one JSON-RPC 2.0 message per line on stdin and stdout, using the initialize handshake of the 2024-11-05 to
2025-11-25 protocol revisions. Newer clients that first probe with server/discover get "method not found" and fall
back to that handshake. Like the rest of wheelgap it needs only the standard library and packaging.

Tool calls run on one worker thread, one at a time, while the main thread keeps reading stdin: it answers ping and
handles notifications/cancelled during a long check_lockfile. One worker, because check_lockfile redirects the
process-wide sys.stdout and sys.stderr around cli.main. A call given a _meta.progressToken gets
notifications/progress as its index lookups finish; a cancelled call sends no more of them, and no result.

Tools (all read-only; they look packages up on PyPI, or the index in WHEELGAP_INDEX_URL):
- check_package: one package (latest release or a given version) on each target.
- check_lockfile: what `wheelgap PATH --json` reports, so project config, notes and skipped packages match the CLI.
- check_dockerfile: what `wheelgap PATH --dockerfile DOCKERFILE --json` reports, plus the image it read.
- list_targets: the platform names the other tools accept.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO

from packaging.version import InvalidVersion, Version

from . import __version__, pypi
from .api import Result, check_package, make_targets, running_python
from .check import Status
from .dockerfile import DockerfileError, read_dockerfile
from .pyodide import PyodideLockError, load_pyodide_lock
from .sources import SourceError
from .targets import (
    ALIASES,
    DEFAULT_GLIBC,
    DEFAULT_MACOS,
    DEFAULT_MUSL,
    DEFAULT_PLATFORMS,
    EVERY_NATIVE_PLATFORM,
    MOBILE_PLATFORMS,
    NATIVE_PLATFORMS,
    PLATFORMS,
    TargetError,
)

# The handshake-era revisions, oldest first. A client asking for one of these gets it back; any other version gets the
# newest, and the client decides whether it can use that (as the MCP lifecycle spec says).
PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
_STRUCTURED_FROM = "2025-06-18"  # the first revision with structuredContent in tool results
_PROGRESS_MESSAGE_FROM = "2025-03-26"  # the first revision with a message in notifications/progress

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

INSTRUCTIONS = (
    "wheelgap tells you whether pip or uv can install a package from a prebuilt wheel on a platform, or will have to "
    "build it from source (or can't install it at all). Use check_lockfile on a project's uv.lock, poetry.lock, "
    "pdm.lock, pylock.toml, Pipfile.lock or requirements.txt before changing the platforms it deploys to, "
    "check_dockerfile before changing a Dockerfile's FROM line (it reads the image's Python, platform and libc for "
    "you), and check_package before adding a dependency. Pass the project's Python version: results differ between "
    "versions. Linux results assume a glibc (manylinux) or musl version: pass the one your image or distro has."
)

_STATUS_HELP = (
    "Statuses: 'wheel' (a compatible wheel exists), 'source' (no wheel, so installers build the sdist and need a "
    "compiler and headers), 'missing' (nothing installable: no wheel and no sdist, or, on pyodide, iOS and Android, "
    "which can't build sdists, no wheel), 'unknown' (the lookup failed: not a pass), 'n/a' (environment markers say "
    "it isn't installed there), 'bundled' (Pyodide ships its own build). On pyodide, packages Pyodide ships itself "
    "(numpy and many others) show 'source' or 'missing' unless a pyodide_lock is given (check_lockfile also reads "
    "[tool.wheelgap] pyodide-lock)."
)

_PYTHON_PROP = {
    "type": "string",
    "description": "Target Python version(s), comma-separated, e.g. '3.13' or '3.12,3.14t' (t = free-threaded).",
}
_TARGETS_PROP = {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Platforms, e.g. ['linux-aarch64', 'musl-x86_64', 'win-arm64', 'pyodide'], Docker platforms such as "
        f"'linux/arm64', or the groups 'all', 'everything' and 'mobile'. Default: {', '.join(DEFAULT_PLATFORMS)}. "
        "list_targets gives every name."
    ),
}
_VERSION_PROPS = {
    "glibc": {
        "type": "string",
        "description": f"glibc version for linux-* targets, e.g. '2.28' for a RHEL 8 image. Default: {DEFAULT_GLIBC}.",
    },
    "musl": {"type": "string", "description": f"musl version for musl-* (Alpine) targets. Default: {DEFAULT_MUSL}."},
    "macos": {"type": "string", "description": f"Oldest macOS version to support. Default: {DEFAULT_MACOS}."},
}
_PYODIDE_LOCK_PROP = {
    "type": "string",
    "description": (
        "Absolute path or https URL of a Pyodide release's pyodide-lock.json, so packages that release ships itself "
        "count as 'bundled' on pyodide targets for its Python. Its Python must be one a pyodide target uses. "
        "Relative paths are resolved against the server's working directory."
    ),
}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "check_package",
        "title": "Check a package's wheels",
        "description": (
            "Check whether one PyPI package has a prebuilt wheel for each target platform and Python. Returns each "
            "target's status, the targets with a gap, ok (true only if every target installs without building) and "
            "the libc and macOS versions assumed. " + _STATUS_HELP
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "The package name alone, e.g. 'numpy'."},
                "version": {
                    "type": "string",
                    "description": "An exact version, e.g. '2.3.4'. Default: the latest release, which may not "
                    "support an older Python (it then shows gaps an older release wouldn't have).",
                },
                "python": _PYTHON_PROP | {"description": _PYTHON_PROP["description"] + " Default: the server's."},
                "targets": _TARGETS_PROP,
                **_VERSION_PROPS,
                "pyodide_lock": _PYODIDE_LOCK_PROP,
            },
            "required": ["name"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "check_lockfile",
        "title": "Check a lockfile's wheels",
        "description": (
            "Check every package a lockfile installs (uv.lock, poetry.lock, pdm.lock, pylock.toml, Pipfile.lock, "
            "requirements.txt, or a project directory containing one), exactly as the wheelgap command line does, and "
            "report the packages without a wheel on some target. Environment markers, .python-version and the "
            "project's [tool.wheelgap] settings (targets, python, glibc, ignore, offline, index-url, ...) apply; "
            "arguments given here override them, except that offline = true in the project can't be turned off. "
            "Notes and errors name command-line flags (--offline is the offline argument here). "
            "Packages from git, paths, URLs or private indexes are listed under "
            "'skipped': they were NOT checked. 'notes' says when an answer may be incomplete. Requirements files are "
            "looked up on the index. " + _STATUS_HELP
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the lockfile or project directory (relative paths are resolved "
                    "against the server's working directory).",
                },
                "python": _PYTHON_PROP
                | {
                    "description": _PYTHON_PROP["description"] + " Default: [tool.wheelgap] python, then "
                    ".python-version next to the lockfile, else the server's."
                },
                "targets": _TARGETS_PROP,
                **_VERSION_PROPS,
                "pyodide_lock": _PYODIDE_LOCK_PROP
                | {"description": _PYODIDE_LOCK_PROP["description"] + " Default: [tool.wheelgap] pyodide-lock."},
                "offline": {
                    "type": "boolean",
                    "description": "Use only the files recorded in the lockfile; never contact the index.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "check_dockerfile",
        "title": "Check a lockfile against a Dockerfile's image",
        "description": (
            "Check a lockfile for the image a Dockerfile's final stage builds on, exactly as `wheelgap PATH "
            "--dockerfile DOCKERFILE --json` does: the image's Python, platform (from FROM --platform, default "
            "linux/amd64) and glibc or musl version (python:3.13-alpine means musl) set the target. Use it before "
            "changing a FROM line, e.g. from -slim to -alpine or to --platform=linux/arm64. Returns what "
            "check_lockfile returns, plus 'image': the python, platform, glibc and musl wheelgap read from the "
            "Dockerfile. A Dockerfile wheelgap can't read the way Docker does is a tool error saying what to pass "
            "instead. " + _STATUS_HELP
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dockerfile": {
                    "type": "string",
                    "description": "Absolute path to the Dockerfile (relative paths are resolved against the server's "
                    "working directory).",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path to the lockfile or project directory. Default: the Dockerfile's "
                    "folder.",
                },
                "targets": _TARGETS_PROP
                | {
                    "description": "Platforms to check instead of the image's, e.g. ['linux/arm64'] (with an Alpine "
                    "image, Docker platforms mean musl). Default: the image's platform."
                },
            },
            "required": ["dockerfile"],
            "additionalProperties": False,
        },
        "annotations": _READ_ONLY,
    },
    {
        "name": "list_targets",
        "title": "List target platforms",
        "description": "List the platform names and groups that check_package, check_lockfile and check_dockerfile "
        "accept.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": _READ_ONLY | {"openWorldHint": False},
    },
]


class ToolError(Exception):
    """A problem with the tool's arguments or input, reported to the model as a tool result with isError."""


_PYTHON_RE = re.compile(r"\d+\.\d+t?", re.ASCII)
_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?", re.ASCII)
_GAP = {Status.SOURCE.value, Status.MISSING.value, Status.UNKNOWN.value}


def _check_keys(tool: str, args: dict[str, Any]) -> None:
    """Models guess argument names ('platforms', 'python_version'): refuse them rather than ignore them."""
    known = next(t for t in TOOLS if t["name"] == tool)["inputSchema"]["properties"]
    if unknown := sorted(set(args) - set(known)):
        raise ToolError(f"unknown argument(s): {', '.join(unknown)} (accepted: {', '.join(known) or 'none'})")


def _string(args: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    value = args.get(key)
    if value is None:
        if required:
            raise ToolError(f"'{key}' is required")
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ToolError(f"'{key}' must be a non-empty string" if required else f"'{key}' must be a string")
    return value


def _platforms(args: dict[str, Any]) -> list[str]:
    platforms = args.get("targets")
    if platforms is None:
        return []
    if not isinstance(platforms, list) or not all(isinstance(p, str) for p in platforms):
        raise ToolError("'targets' must be an array of strings")
    return [p.strip() for p in platforms if p.strip()]


def _pythons(args: dict[str, Any]) -> list[str]:
    python = _string(args, "python") or ""
    parts = [p.strip() for p in python.split(",") if p.strip()]
    if bad := [p for p in parts if not _PYTHON_RE.fullmatch(p)]:
        raise ToolError(f"can't read Python version(s) {', '.join(map(repr, bad))} (expected e.g. '3.13' or '3.14t')")
    return parts


def _versions(args: dict[str, Any]) -> dict[str, str]:
    out = {}
    for key in _VERSION_PROPS:
        value = _string(args, key)
        if value is not None and value.strip():
            out[key] = value.strip()
    return out


def _lock_spec(args: dict[str, Any]) -> str:
    spec = (_string(args, "pyodide_lock") or "").strip()
    return spec if not spec or "://" in spec else str(Path(spec).expanduser())


def _check_package(args: dict[str, Any]) -> dict[str, Any]:
    _check_keys("check_package", args)
    name = (_string(args, "name", required=True) or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ToolError(f"{name!r} isn't a package name: pass the name alone, and an exact version as 'version'")
    version = (_string(args, "version") or "").strip() or None
    if version is not None:
        try:
            Version(version)
        except InvalidVersion:
            raise ToolError(f"'version' must be an exact version such as '2.3.4', not {version!r}") from None
    versions = _versions(args)
    try:
        targets = make_targets(_pythons(args) or None, _platforms(args) or None, **versions)
    except TargetError as e:
        raise ToolError(str(e)) from e
    lock_spec = _lock_spec(args)
    try:
        locks = [load_pyodide_lock(lock_spec)] if lock_spec else []
    except ValueError as e:  # PyodideLockError, or a file that isn't UTF-8
        raise ToolError(f"{lock_spec}: {e}" if isinstance(e, UnicodeDecodeError) else str(e)) from e
    except OSError as e:  # a lock path that can't be read
        raise ToolError(f"{lock_spec}: {e.strerror or e}") from e
    try:
        result: Result = check_package(name, version, targets, pyodide_locks=locks)
    except PyodideLockError as e:  # a lock for a Python no pyodide target uses
        raise ToolError(str(e)) from e
    out: dict[str, Any] = result.to_dict()
    out["ok"] = result.ok
    out["gaps"] = [t.label(True) for t in result.gaps]
    out["targets"] = [t.label(True) for t in targets]
    out["assumes"] = {
        "glibc": versions.get("glibc", DEFAULT_GLIBC),
        "musl": versions.get("musl", DEFAULT_MUSL),
        "macos": versions.get("macos", DEFAULT_MACOS),
    }
    return out


def _check_lockfile(args: dict[str, Any]) -> dict[str, Any]:
    """Run the command line itself with --json, so config, notes and skipped packages are exactly the CLI's."""
    _check_keys("check_lockfile", args)
    raw = _string(args, "path", required=True) or ""
    offline = args.get("offline")
    if offline is not None and not isinstance(offline, bool):
        raise ToolError("'offline' must be true or false")
    path = _existing(raw)
    # --name=value forms, and -- before the path, so no value can be read as another option.
    argv = ["--json"]
    argv += [f"--python={p}" for p in _pythons(args)]
    argv += [f"--target={p}" for p in _platforms(args)]
    argv += [f"--{key}={value}" for key, value in _versions(args).items()]
    if lock_spec := _lock_spec(args):
        argv.append(f"--pyodide-lock={lock_spec}")
    if offline:
        argv.append("--offline")
    return _run_cli(argv, path)


def _check_dockerfile(args: dict[str, Any]) -> dict[str, Any]:
    """check_lockfile with --dockerfile, plus the image the CLI reads from the Dockerfile."""
    _check_keys("check_dockerfile", args)
    dockerfile = _existing(_string(args, "dockerfile", required=True) or "")
    if dockerfile.is_dir():
        raise ToolError(f"{args['dockerfile']}: is a directory (pass the Dockerfile itself)")
    raw = (_string(args, "path") or "").strip()
    path = _existing(raw) if raw else dockerfile.parent
    try:
        image = read_dockerfile(dockerfile)
    except DockerfileError as e:  # wheelgap's message; its CLI advice, where it gives some, translated
        message = str(e).replace("pass -p/-t instead", "use check_lockfile with python and targets instead")
        raise ToolError(message) from e
    except OSError as e:  # a Dockerfile that can't be read
        raise ToolError(f"{args['dockerfile']}: {e.strerror or e}") from e
    argv = ["--json", f"--dockerfile={dockerfile}"]
    argv += [f"--target={p}" for p in _platforms(args)]
    try:
        out = _run_cli(argv, path)
    except ToolError as e:
        if raw or "no lockfile found" not in str(e):
            raise
        raise ToolError(f"{e} (pass 'path': the lockfile or project directory)") from e
    out["image"] = {"python": image.python, "platform": image.platform, "glibc": image.glibc, "musl": image.musl}
    return out


def _existing(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        raise ToolError(f"{raw}: no such file or directory (pass an absolute path)")
    return path


def _run_cli(argv: list[str], path: Path) -> dict[str, Any]:
    """`wheelgap ARGV -- PATH` with --json in ARGV: its result as the lockfile tools return it."""
    from . import cli

    argv = [*argv, "--", str(path)]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
    except SystemExit as e:  # argparse rejected a value
        code = e.code if isinstance(e.code, int) else 2
    messages = [line.removeprefix("wheelgap: ") for line in err.getvalue().splitlines() if line.strip()]
    notes = [m.removeprefix("note: ") for m in messages if m.startswith("note: ")]
    # SystemExit is a BaseException: without the handler above it would end the server, not just this call.
    if code not in (0, 1):
        errors = [m.removeprefix("error: ") for m in messages if not m.startswith("note: ")]
        raise ToolError("; ".join(errors) or f"wheelgap exited with status {code}")
    data = json.loads(out.getvalue())
    # The CLI leaves the Python out of labels when there's only one; always give it, as check_package does.
    label = {t["label"]: f"{t['python']} {t['platform']}" for t in data["targets"]}
    platform_of = {t["label"]: t["platform"] for t in data["targets"]}
    ignored_on = {i["name"]: set(i["platforms"]) for i in data["ignored_targets"]}
    gaps = []
    for package in data["packages"]:
        cells = [
            label[cell]
            for cell, status in package["results"].items()
            if status in _GAP and platform_of[cell] not in ignored_on.get(package["name"], ())
        ]
        if cells:
            results = {label[cell]: status for cell, status in package["results"].items()}
            gaps.append(package | {"results": results, "gaps": cells})
    # A bundled build at another version still passes, as in the CLI, but a pin micropip can't install must be said.
    differs = [p for p in data["packages"] if p.get("bundled_version_differs")]
    for p in differs:
        notes.append(
            f"{p['name']}: Pyodide ships {p['bundled']}, not the {p['version']} asked for; it counts as bundled, "
            "but micropip may not install that version"
        )
    if data["skipped"]:
        n = len(data["skipped"])
        notes.append(f"{n} package{'s' if n != 1 else ''} weren't checked (see skipped); ok covers the others only")
    return {
        "path": str(path.resolve()),
        "targets": list(label.values()),
        "packages": data["summary"]["packages"],
        "ok": not gaps,
        "unknown": sum(1 for g in gaps if Status.UNKNOWN.value in g["results"].values()),
        "bundled_version_differs": [
            {"name": p["name"], "version": p["version"], "bundled": p["bundled"]} for p in differs
        ],
        "gaps": gaps,
        "skipped": data["skipped"],
        "ignored": data["ignored"],
        "ignored_targets": data["ignored_targets"],
        "notes": notes,
    }


def _list_targets(args: dict[str, Any]) -> dict[str, Any]:
    _check_keys("list_targets", args)
    return {
        "platforms": list(PLATFORMS),
        "default": list(DEFAULT_PLATFORMS),
        "groups": {"all": NATIVE_PLATFORMS, "everything": EVERY_NATIVE_PLATFORM, "mobile": MOBILE_PLATFORMS},
        "aliases": dict(ALIASES),
        "defaults": {"glibc": DEFAULT_GLIBC, "musl": DEFAULT_MUSL, "macos": DEFAULT_MACOS},
        "server_python": running_python(),
    }


_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "check_package": _check_package,
    "check_lockfile": _check_lockfile,
    "check_dockerfile": _check_dockerfile,
    "list_targets": _list_targets,
}


class _Call:
    """A tools/call that is queued or running on the worker."""

    def __init__(self, token: str | int | None) -> None:
        self.token = token
        self.cancelled = False
        self.base = 0  # lookups in fill_all runs that have finished (check_lockfile may run more than one)
        self.total = 0  # lookups in the current fill_all run
        self.sent = -1  # the last progress sent: the spec says each one must be higher


class Server:
    """The protocol state for one stdio connection.

    With `send`, tools/call runs on a worker thread and its result goes out through `send` (handle returns None for
    it); without, handle returns the result itself.
    """

    def __init__(self, send: Callable[[object], None] | None = None) -> None:
        self.protocol_version = PROTOCOL_VERSIONS[-1]
        self._send = send
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wheelgap-mcp") if send else None
        self._calls: dict[str | int, _Call] = {}
        self._lock = threading.Lock()  # guards _calls and each _Call's cancelled flag

    def close(self, wait: bool = True) -> None:
        """Wait for queued and running calls to send their results (or, with wait=False, drop the queued ones)."""
        if not wait:  # Ctrl-C: the running call may still finish its lookups, but nothing more is written
            with self._lock:
                for call in self._calls.values():
                    call.cancelled = True
                self._send = None
        if self._worker is not None:
            self._worker.shutdown(wait=wait, cancel_futures=not wait)

    def handle(self, message: object) -> dict[str, Any] | list[dict[str, Any]] | None:
        """The response to one parsed message (or batch), or None for notifications and calls left to the worker."""
        if isinstance(message, list):  # JSON-RPC batches (only 2025-03-26 allowed them, but they cost nothing)
            if not message:
                return _error(None, INVALID_REQUEST, "empty batch")
            if self._worker is not None and any(
                isinstance(m, dict) and m.get("method") == "tools/call" for m in message
            ):
                # Its reply is one array, so the whole batch waits its turn on the worker.
                self._worker.submit(self._batch_on_worker, message)
                return None
            return self._batch(message)
        return self._one(message)

    def _batch(self, message: list[object]) -> list[dict[str, Any]] | None:
        replies = [r for r in (self._one(m, inline=True) for m in message) if r is not None]
        return replies or None

    def _batch_on_worker(self, message: list[object]) -> None:
        try:
            reply: object = self._batch(message)
        except Exception as e:  # a bug: the client still gets an answer rather than waiting forever
            reply = _error(None, INTERNAL_ERROR, f"internal error: {type(e).__name__}: {e}")
        with self._lock:
            if reply is not None and self._send is not None:
                self._send(reply)

    def _one(self, message: object, inline: bool = False) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(_id(message), INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        method = message.get("method")
        params = message.get("params")
        if "id" not in message or ("method" not in message and ("result" in message or "error" in message)):
            # A notification (initialized, cancelled, ...) or a response to us: nothing to send.
            if method == "notifications/cancelled" and isinstance(params, dict):
                self._cancel(params.get("requestId"))
            return None
        msg_id = message["id"]
        if not isinstance(msg_id, (str, int)) or isinstance(msg_id, bool):
            return _error(None, INVALID_REQUEST, "id must be a string or an integer")
        if not isinstance(method, str):
            return _error(msg_id, INVALID_REQUEST, "method must be a string")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(msg_id, INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return _reply(msg_id, self._initialize(params))
        if method == "ping":
            return _reply(msg_id, {})
        if method == "tools/list":
            return _reply(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or name not in _HANDLERS:
                return _error(msg_id, INVALID_PARAMS, f"unknown tool: {name!r}")
            arguments = params.get("arguments")
            if arguments is None:
                arguments = {}
            if not isinstance(arguments, dict):
                return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
            if inline or self._worker is None:
                return _reply(msg_id, self._call(name, arguments))
            meta = params.get("_meta")
            token = meta.get("progressToken") if isinstance(meta, dict) else None
            if not isinstance(token, (str, int)) or isinstance(token, bool):
                token = None
            call = _Call(token)
            with self._lock:
                self._calls[msg_id] = call
            self._worker.submit(self._run, msg_id, call, name, arguments)
            return None
        return _error(msg_id, METHOD_NOT_FOUND, f"method not found: {method}")

    def _cancel(self, msg_id: object) -> None:
        """notifications/cancelled: an unknown or finished request is ignored, as the spec says."""
        if isinstance(msg_id, (str, int)) and not isinstance(msg_id, bool):
            with self._lock:
                if call := self._calls.get(msg_id):
                    call.cancelled = True

    def _run(self, msg_id: str | int, call: _Call, name: str, arguments: dict[str, Any]) -> None:
        """On the worker: run one call and send its result, unless it was cancelled."""
        if call.cancelled:  # cancelled while it waited for the worker: don't start it
            with self._lock:
                if self._calls.get(msg_id) is call:
                    del self._calls[msg_id]
            return
        try:
            if call.token is None:
                result = self._call(name, arguments)
            else:
                with pypi.reporting_progress(lambda done, total: self._progress(call, done, total)):
                    result = self._call(name, arguments)
            reply = _reply(msg_id, result)
        except Exception as e:  # a bug: the client still gets an answer rather than waiting forever
            reply = _error(msg_id, INTERNAL_ERROR, f"internal error: {type(e).__name__}: {e}")
        try:
            with self._lock:  # the lock, so a cancel handled before this point always drops the result
                if not call.cancelled and self._send is not None:
                    self._send(reply)
        finally:
            with self._lock:
                if self._calls.get(msg_id) is call:
                    del self._calls[msg_id]

    def _progress(self, call: _Call, done: int, total: int) -> None:
        """Called by fill_all on the worker. Progress counts up over every fill_all run in the call."""
        if done == 0:  # a new fill_all run: the previous one (if any) finished all its lookups
            call.base += call.total
            call.total = total
        progress, overall = call.base + done, call.base + call.total
        if progress <= call.sent:
            return
        params: dict[str, Any] = {"progressToken": call.token, "progress": progress, "total": overall}
        if self.protocol_version >= _PROGRESS_MESSAGE_FROM:
            params["message"] = f"{progress} of {overall} index lookups done"
        with self._lock:
            if call.cancelled or self._send is None:
                return
            call.sent = progress
            self._send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        asked = params.get("protocolVersion")
        self.protocol_version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[-1]
        return {
            "protocolVersion": self.protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "wheelgap", "version": __version__},
            "instructions": INSTRUCTIONS,
        }

    def _call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            # wheelgap's own messages go to stderr already; this keeps any stray print off the protocol stream.
            with contextlib.redirect_stdout(sys.stderr):
                data = _HANDLERS[name](arguments)
            text = json.dumps(data, indent=1)
        except (ToolError, TargetError, SourceError) as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        except Exception as e:  # a bug: report it to the model rather than end the connection
            return {"content": [{"type": "text", "text": f"internal error: {type(e).__name__}: {e}"}], "isError": True}
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if self.protocol_version >= _STRUCTURED_FROM:
            result["structuredContent"] = data
        return result


def _id(message: object) -> Any:
    msg_id = message.get("id") if isinstance(message, dict) else None
    return msg_id if isinstance(msg_id, (str, int)) and not isinstance(msg_id, bool) else None


def _reply(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(stdin: BinaryIO, stdout: BinaryIO) -> None:
    """Answer messages from stdin until it closes, then wait for the calls still queued or running."""
    write_lock = threading.Lock()

    def send(reply: object) -> None:
        # ensure_ascii keeps every message on one line whatever the package metadata contains.
        data = json.dumps(reply, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        with write_lock:  # the worker writes too: a line is never split by another
            stdout.write(data)
            stdout.flush()

    server = Server(send)
    finished = False
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line.decode("utf-8-sig"))
            except (ValueError, RecursionError) as e:  # bad UTF-8 or JSON, an over-long integer, absurd nesting
                reply: object = _error(None, PARSE_ERROR, f"parse error: {type(e).__name__}")
            else:
                try:
                    reply = server.handle(message)
                except Exception as e:  # never let one bad message end the connection
                    reply = _error(_id(message), INTERNAL_ERROR, f"internal error: {type(e).__name__}: {e}")
            if reply is not None:
                send(reply)
        finished = True
    finally:
        server.close(wait=finished)  # on Ctrl-C, don't start the calls still queued


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args in (["--version"], ["-V"]):
        print(f"wheelgap-mcp {__version__}")
        return 0
    if args:
        print(
            "usage: wheelgap-mcp\n\nAn MCP server for wheelgap over stdio. Add it to your agent's MCP settings with "
            "the command `uvx --from wheelgap wheelgap-mcp`.",
            file=sys.stderr,
        )
        return 0 if args in (["-h"], ["--help"]) else 2
    try:
        serve(sys.stdin.buffer, sys.stdout.buffer)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
