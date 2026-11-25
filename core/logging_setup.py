"""File logging, dev console and diagnostics plumbing.

The historical codebase logs everything through ``print``. Migrating every
call at once is risky, so this module provides both:

* real ``logging`` handlers for new code;
* a print bridge that turns any remaining ``print`` into a log record, so
  release builds (PyInstaller ``--noconsole``) stop discarding diagnostics.

The log lives in ``<data root>/logs/launcher.log`` (2 MB, 3 backups).
"""
from __future__ import annotations

import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.paths import PathManager

LEVEL_ENV = "AETHICC_LOG_LEVEL"
_FILENAME = "launcher.log"
_MAX_BYTES = 2 * 1024 * 1024
_BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Streams captured before the bridge replaces them: the console handler must
# keep writing to the real terminal, never into the bridge (infinite loop).
_ORIGINAL_STDOUT = sys.stdout
_ORIGINAL_STDERR = sys.stderr
_bridge_lock = threading.Lock()

_SECRET_PATTERN = re.compile(
    r"(?i)\b(access_token|refresh_token|client_secret|api[_-]?key|authorization"
    r"|password|passwd|secret|token)\b(\s*[:=]\s*)([\"']?)([^\s\"',;}]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def _redact(text: str) -> str:
    """Mask secret-looking values so logs never carry credentials."""
    if not text:
        return text
    text = _BEARER_PATTERN.sub("Bearer ***", text)
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***", text)
    return text


def redact_text(text: str) -> str:
    """Public helper for diagnostics export (clipboard, dialogs)."""
    return _redact(text)


class _RedactionFilter(logging.Filter):
    def filter(self, record):
        try:
            record.msg = _redact(record.getMessage())
            record.args = ()
        except Exception:
            # best-effort: logging must never raise into its own handlers
            pass
        return True


def _coerce_level(level=None) -> int:
    if level is None:
        level = os.environ.get(LEVEL_ENV, "INFO")
    if isinstance(level, str):
        return getattr(logging, level.strip().upper(), logging.INFO)
    try:
        return int(level)
    except (TypeError, ValueError):
        return logging.INFO


def _console_stream():
    return _ORIGINAL_STDERR if _ORIGINAL_STDERR is not None else sys.__stderr__


def setup_logging(level=None, console: bool = False, log_dir=None) -> str:
    """Attach the rotating file handler (and an optional console handler).

    ``log_dir`` is explicit for tests; production uses the data root, which can
    move after the first-run wizard, hence the handler cleanup on every call.
    """
    log_level = _coerce_level(level)
    directory = Path(log_dir) if log_dir is not None else PathManager.get_logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _FILENAME

    root = logging.getLogger()
    root.setLevel(log_level)

    formatter = logging.Formatter(_FORMAT)
    redaction = _RedactionFilter()

    file_handler = None
    for existing in list(root.handlers):
        if isinstance(existing, RotatingFileHandler):
            if getattr(existing, "baseFilename", "") == str(path):
                file_handler = existing
            else:
                root.removeHandler(existing)
                try:
                    existing.close()
                except Exception:
                    # best-effort: logging must never raise into its own handlers
                    pass
        elif getattr(existing, "_aethicc_console", False):
            root.removeHandler(existing)
            try:
                existing.close()
            except Exception:
                # best-effort: logging must never raise into its own handlers
                pass

    if file_handler is None:
        file_handler = RotatingFileHandler(
            path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.addFilter(redaction)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    else:
        file_handler.setLevel(log_level)

    if console:
        handler = logging.StreamHandler(_console_stream())
        handler._aethicc_console = True
        handler.setLevel(log_level)
        handler.setFormatter(formatter)
        handler.addFilter(redaction)
        root.addHandler(handler)

    return str(path)


class _LogStream:
    """File-like object that forwards whole lines to a logger."""

    def __init__(self, logger_name: str, level: int):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self._buffer = ""
        self._local = threading.local()
        self.encoding = "utf-8"
        self.errors = "replace"

    def write(self, text):
        if not text:
            return 0
        if getattr(self._local, "active", False):
            return len(text)
        self._local.active = True
        try:
            self._buffer += str(text)
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                if line.strip():
                    self._logger.log(self._level, line.rstrip())
        finally:
            self._local.active = False
        return len(text)

    def flush(self):
        if getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            if self._buffer.strip():
                self._logger.log(self._level, self._buffer.rstrip())
            self._buffer = ""
        finally:
            self._local.active = False

    def isatty(self):
        return False

    def writable(self):
        return True

    def fileno(self):
        raise OSError("log stream has no file descriptor")

    def close(self):
        self.flush()


def install_print_bridge(stdout_level=None, stderr_level=None):
    """Route ``print`` output into the log (idempotent)."""
    with _bridge_lock:
        if not isinstance(sys.stdout, _LogStream):
            sys.stdout = _LogStream(
                "app.stdout",
                _coerce_level(stdout_level) if stdout_level is not None else logging.INFO,
            )
        if not isinstance(sys.stderr, _LogStream):
            sys.stderr = _LogStream(
                "app.stderr",
                _coerce_level(stderr_level) if stderr_level is not None else logging.WARNING,
            )


def remove_print_bridge():
    """Restore the original streams (used by tests and the console mode)."""
    with _bridge_lock:
        if isinstance(sys.stdout, _LogStream):
            sys.stdout = _ORIGINAL_STDOUT
        if isinstance(sys.stderr, _LogStream):
            sys.stderr = _ORIGINAL_STDERR


def install_qt_message_handler():
    """Forward Qt warnings (including QThread ones) to the log."""
    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:
        return None

    logger = logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def _handler(mode, context, message):
        location = ""
        try:
            if context is not None and context.file:
                location = f" ({context.file}:{context.line})"
        except Exception:
            # best-effort: logging must never raise into its own handlers
            pass
        logger.log(levels.get(mode, logging.INFO), "%s%s", message, location)

    qInstallMessageHandler(_handler)
    return _handler
