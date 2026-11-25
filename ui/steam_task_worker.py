"""Background Steam integration (kill/restart, VDF, artwork)."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


class SteamTaskWorker(QThread):
    ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(parent)
        self.task = task

    def run(self):
        try:
            result = self.task()
        except Exception as exc:
            log.exception(f"[Steam] background integration failed: {exc}")
            self.failed.emit(str(exc))
            return
        self.ready.emit(result)
