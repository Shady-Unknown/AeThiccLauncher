"""Background cover resolution (downloads + cache) for the Explorer."""
from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, pyqtSignal

from core.cover_cache import CoverCache

log = logging.getLogger(__name__)


class CoverResolveWorker(QThread):
    ready = pyqtSignal(int, str, object)  # request_id, title, resolved urls
    failed = pyqtSignal(int, str)

    def __init__(self, request_id, title, urls, installed_games, parent=None):
        super().__init__(parent)
        self.request_id = int(request_id)
        self.title = str(title or "")
        self.urls = list(urls or [])
        self.installed_games = dict(installed_games or {})
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            resolved = CoverCache.resolve_urls(
                self.urls, title=self.title, installed_games=self.installed_games)
        except Exception as exc:
            log.exception(f"[Cover] resolve failed for '{self.title}': {exc}")
            if not self._cancelled:
                self.failed.emit(self.request_id, str(exc))
            return
        if not self._cancelled:
            self.ready.emit(self.request_id, self.title, resolved)
