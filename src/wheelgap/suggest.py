"""Find the nearest newer release with a wheel for a target, without fetching."""

from packaging.version import InvalidVersion, Version

from .check import Status, status_for
from .pypi import _files, _supports_python
from .targets import Target


def nearest_with_wheel(releases: dict, pinned: str, target: Target) -> str | None:
    """Return the lowest newer release with a non-yanked, compatible wheel.

    `releases` is the release-to-file-list mapping from the PyPI project JSON API.
    Pre-releases are considered only when `pinned` is itself a pre-release. Invalid
    release versions are skipped; an invalid pin raises `InvalidVersion`.
    File Requires-Python metadata follows the same rules as range lookups, and a release
    whose file list is malformed is skipped, as range lookups skip it.
    """
    current = Version(pinned)
    candidates = []
    for text in releases:
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if version > current and (current.is_prerelease or not version.is_prerelease):
            candidates.append((version, text))
    python = f"{target.major}.{target.minor}"
    for _, text in sorted(candidates):
        try:
            entries = _files(releases[text])
        except ValueError:
            continue
        files = [entry["filename"] for entry in entries if not entry.get("yanked") and _supports_python(entry, python)]
        if status_for(files, target) is Status.WHEEL:
            return text
    return None
