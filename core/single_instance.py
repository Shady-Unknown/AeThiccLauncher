"""Single-instance guard for the launcher.

Two processes sharing the same data root can race on ``config.json``, the
master catalog and Steam's VDF files, so the second instance exits instead of
starting. The lock lives in the data root and is handled by ``QLockFile``,
which also detects (and removes) a stale lock left by a crashed process.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QLockFile

LOCK_FILENAME = "launcher.lock"
_STALE_MS = 30_000


class SingleInstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = QLockFile(str(self.path))
        self._lock.setStaleLockTime(_STALE_MS)

    def acquire(self) -> bool:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        if self._lock.tryLock(100):
            return True
        # A previous process may have died without releasing the lock.
        try:
            if self._lock.removeStaleLockFile() and self._lock.tryLock(100):
                return True
        except Exception:
            # best-effort: the lock may already be released during teardown
            pass
        return False

    def release(self) -> None:
        try:
            self._lock.unlock()
        except Exception:
            # best-effort: the lock may already be released during teardown
            pass

    @staticmethod
    def path_for_data_root(data_root) -> Path:
        return Path(data_root) / LOCK_FILENAME
