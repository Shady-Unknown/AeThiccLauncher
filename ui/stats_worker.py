"""Background builder for the Stats tab snapshot."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


class StatsSnapshotWorker(QThread):
    ready = pyqtSignal(int, str, str, object)  # request_id, range, region, snapshot
    failed = pyqtSignal(int, str)

    def __init__(self, manager, request_id, range_key, region, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.request_id = int(request_id)
        self.range_key = range_key
        self.region = region
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            snapshot = self.manager.snapshot(
                range_key=self.range_key, region=self.region)
        except Exception as exc:
            log.exception(f"[Stats] snapshot worker failed: {exc}")
            if not self._cancelled:
                self.failed.emit(self.request_id, str(exc))
            return
        if not self._cancelled:
            self.ready.emit(self.request_id, self.range_key, self.region, snapshot)
