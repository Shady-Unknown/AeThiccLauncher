"""Atomic file writes shared by the whole launcher.

Every write goes to a temporary file in the same directory, is flushed (and
optionally fsynced), and only then replaces the destination with
``os.replace``. A crash or a full disk can therefore leave the previous file
untouched, never a truncated one.

The helpers are stdlib-only so any layer (core, managers, network, ui, tools)
can import them without creating cycles.
"""
from __future__ import annotations

import logging

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)


class AtomicIOError(OSError):
    """A write could not be completed; the destination is untouched."""


def _fsync_dir(directory) -> None:
    """Persist the rename itself (POSIX only; Windows has no directory fd)."""
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _unlink_quiet(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _backup_existing(path: str) -> None:
    """Keep the last good version as ``<name>.bak`` (best effort)."""
    if not os.path.exists(path):
        return
    try:
        shutil.copy2(path, path + ".bak")
    except OSError as exc:
        log.exception(f"[AtomicIO] backup failed for {path}: {exc}")


def _atomic_write(path, writer, *, binary=False, encoding="utf-8",
                  fsync=True, backup=False) -> None:
    target = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(target)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=os.path.basename(target) + ".", suffix=".tmp", dir=directory)
    except OSError as exc:
        raise AtomicIOError(f"cannot create a temp file next to {target}: {exc}") from exc

    try:
        mode = "wb" if binary else "w"
        kwargs = {} if binary else {"encoding": encoding, "newline": "\n"}
        with os.fdopen(fd, mode, **kwargs) as fh:
            writer(fh)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        if backup:
            _backup_existing(target)
        os.replace(tmp, target)
        if fsync:
            _fsync_dir(directory)
    except BaseException as exc:
        _unlink_quiet(tmp)
        if isinstance(exc, AtomicIOError):
            raise
        if isinstance(exc, OSError):
            raise AtomicIOError(f"cannot write {target}: {exc}") from exc
        raise


def atomic_write_bytes(path, data, *, fsync=True, backup=False) -> None:
    """Atomically write ``bytes`` (VDF files, binary blobs)."""
    _atomic_write(path, lambda fh: fh.write(data), binary=True,
                  fsync=fsync, backup=backup)


def atomic_write_text(path, text, *, encoding="utf-8", fsync=True,
                      backup=False) -> None:
    """Atomically write text, always with ``\\n`` newlines."""
    _atomic_write(path, lambda fh: fh.write(text), encoding=encoding,
                  fsync=fsync, backup=backup)


def atomic_write_json(path, data, *, indent=None, separators=None,
                      ensure_ascii=False, fsync=True, backup=False) -> None:
    """Atomically write ``data`` as JSON, matching ``json.dump`` defaults."""
    def _dump(fh):
        json.dump(data, fh, indent=indent, separators=separators,
                  ensure_ascii=ensure_ascii)

    _atomic_write(path, _dump, fsync=fsync, backup=backup)


def preserve_corrupt_file(path):
    """Copy an unreadable file next to itself as ``<name>.corrupt-<UTC>``.

    The original stays in place so the caller can replace it on the next save;
    the copy is what the user can inspect or restore manually. Returns the copy
    path, or ``None`` when there was nothing to copy.
    """
    target = Path(path)
    if not target.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    destination = Path(str(target) + f".corrupt-{stamp}")
    try:
        shutil.copy2(target, destination)
    except OSError:
        return None
    return destination

