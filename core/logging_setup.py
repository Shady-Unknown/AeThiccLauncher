"""File logging setup with rotation.

The historical codebase logs everything through ``print``. Replacing it all
in one go would be risky, so this module adds a rotating file handler to the
stdlib root logger: new code can use ``logging.getLogger(__name__)`` and the
old ``print`` calls keep working.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from core.paths import PathManager

_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3


def setup_logging(level: int = logging.INFO) -> str:
    """Attach a rotating file handler and return the log path."""
    log_dir = PathManager.get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "launcher.log"

    root = logging.getLogger()
    root.setLevel(level)

    # The data root can change after the first-run wizard; drop handlers that
    # still point to the old location so we do not log to two files.
    for existing in list(root.handlers):
        if isinstance(existing, RotatingFileHandler) and getattr(existing, "baseFilename", "") != str(path):
            root.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                pass
        elif isinstance(existing, RotatingFileHandler) and getattr(existing, "baseFilename", "") == str(path):
            return str(path)

    handler = RotatingFileHandler(
        path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    return str(path)
