"""Worker per scaricare/cachare la featured image usata come sfondo."""
import logging

from PyQt6.QtCore import QThread, pyqtSignal

from core.cover_cache import CoverCache

log = logging.getLogger(__name__)


class FeaturedImageWorker(QThread):
    """Prova una lista di URL panoramici e restituisce il primo disponibile."""

    ready = pyqtSignal(str, str)  # url, path locale ("" se tutti falliscono)

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        if isinstance(urls, str):
            urls = [urls]
        self._urls = [str(url or "").strip() for url in (urls or [])]
        self._urls = [url for url in self._urls if url]

    def run(self):
        for url in self._urls:
            path = ""
            try:
                path = CoverCache.local_path_for(url, allow_placeholder=False) or ""
            except Exception as exc:  # pragma: no cover - rete/filesystem
                log.exception(f"[Background] featured image failed for {url}: {exc}")
            if path:
                self.ready.emit(url, path)
                return
        self.ready.emit(self._urls[0] if self._urls else "", "")
