"""Background runner for savegames tasks (sync, mirror, restore)."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


class SavegamesTaskWorker(QThread):
    """Run a callable returning a report dict without blocking the GUI."""

    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task

    def run(self):
        try:
            result = self.task()
        except Exception as exc:
            log.exception(f"[Savegames] background task failed: {exc}")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(result)
