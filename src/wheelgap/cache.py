"""A small on-disk cache of pinned index lookups (/<name>/<version>/json), so a repeat run needn't ask again.

A cache that can't be read or written is only ever a miss: it never fails a run.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MAX_AGE = 24 * 60 * 60  # seconds: new wheels for an existing release show up within a day


def _now() -> float:
    return time.time()


def cache_dir() -> Path:
    """The platform's cache folder, as pip and uv choose it (without platformdirs)."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        base = local if os.path.isabs(local) else str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        xdg = os.environ.get("XDG_CACHE_HOME", "")
        base = xdg if os.path.isabs(xdg) else str(Path.home() / ".cache")  # the XDG spec ignores relative paths
    return Path(base) / "wheelgap" / "pinned-v1"


def _path(index: str, name: str, version: str) -> Path:
    # Credentials in the index URL (WHEELGAP_INDEX_URL=https://user:token@host/pypi) are left out of the key,
    # and what's left is hashed, so no URL is written in clear text.
    parts = urlsplit(index)
    index = urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))
    digest = hashlib.sha256(f"{index}\n{name}\n{version}".encode()).hexdigest()
    return cache_dir() / f"{digest}.json"


def load_files(index: str, name: str, version: str) -> list[dict] | None:
    """The cached file list of a pinned release, or None: not cached, older than MAX_AGE, or unreadable."""
    try:
        entry = json.loads(_path(index, name, version).read_text(encoding="utf-8"))
        saved, files = entry["saved"], entry["files"]
        if isinstance(saved, bool) or not isinstance(saved, int | float) or not 0 <= _now() - saved < MAX_AGE:
            return None
        if isinstance(files, list) and all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in files):
            return files
    except Exception:  # missing, corrupt or unreadable: a miss
        pass
    return None


def save_files(index: str, name: str, version: str, files: list[dict]) -> None:
    """Store a pinned release's file list. Only `filename` and `yanked` are kept: they're all fill() reads,
    and a file's URL (which a private mirror may sign) never reaches the disk."""
    try:
        kept = [{k: f[k] for k in ("filename", "yanked") if k in f} for f in files]
        path = _path(index, name, version)
        path.parent.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # private to the user (not on Windows)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        # Written next to the target, then renamed over it, so a reader never sees half a file.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"saved": _now(), "files": kept}, f)
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except Exception:  # read-only home, full disk, a race on Windows...: the next run just looks it up again
        pass
