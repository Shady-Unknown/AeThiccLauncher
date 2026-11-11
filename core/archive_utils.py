"""Archive helpers shared by the downloader and the recovery flow."""
from __future__ import annotations

import os
import subprocess
import sys

from core.config import get_base_dir

ARCHIVE_EXTENSIONS = (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz")
INCOMPLETE_SUFFIX = ".incomplete"

# 7-Zip messages that mean the archive is truncated or unreadable. Other
# failures (for example encrypted headers) are intentionally ignored so the
# installer can still ask for the password.
_TRUNCATION_MARKERS = (
    "unexpected end of archive",
    "unexpected end of data",
    "cannot open the file as archive",
    "cannot open as archive",
    "is not archive",
)


def is_archive_path(path):
    """True when ``path`` looks like a supported archive file."""
    return str(path or "").lower().endswith(ARCHIVE_EXTENSIONS)


def incomplete_marker(path):
    return f"{path}{INCOMPLETE_SUFFIX}"


def is_marked_incomplete(path):
    return bool(path) and os.path.isfile(incomplete_marker(path))


def mark_incomplete(path, reason=""):
    """Remember that ``path`` is truncated, so we do not re-check it."""
    if not path:
        return
    try:
        with open(incomplete_marker(path), "w", encoding="utf-8") as handle:
            handle.write(str(reason or "incomplete"))
    except OSError:
        pass


def clear_incomplete(path):
    """Remove the incomplete marker, e.g. when a new download starts."""
    if not path:
        return
    try:
        os.remove(incomplete_marker(path))
    except OSError:
        pass


def seven_zip_path():
    base_dir = get_base_dir()
    if sys.platform == "win32":
        return os.path.join(base_dir, "bin", "Windows", "x64", "7z.exe")
    return os.path.join(base_dir, "bin", "Linux", "7zz")


def is_archive_complete(path, seven_z=None, timeout=30):
    """Return False when 7-Zip reports a truncated/unreadable archive.

    Only the archive headers are read, so this stays cheap even for very large
    files. If 7-Zip is unavailable we cannot verify and return True, leaving
    the normal extraction flow in charge.
    """
    path = str(path or "")
    try:
        if not os.path.isfile(path) or os.path.getsize(path) <= 0:
            return False
    except OSError:
        return False
    if not is_archive_path(path):
        return True

    seven_z = seven_z or seven_zip_path()
    if not os.path.isfile(seven_z):
        return True
    try:
        result = subprocess.run(
            [seven_z, "l", "-slt", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    output = (result.stdout or "").lower()
    return not any(marker in output for marker in _TRUNCATION_MARKERS)
