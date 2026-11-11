"""Worker per scaricare/cachare la featured image usata come sfondo."""

from PyQt6.QtCore import QThread, pyqtSignal

from core.cover_cache import CoverCache


class FeaturedImageWorker(QThread):
    """Scarica (o recupera dalla cache) la featured image SteamRIP."""

    ready = pyqtSignal(str, str)  # url, path locale ("" se fallita)

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self._url = str(url or "")

    def run(self):
        path = ""
        try:
            path = CoverCache.local_path_for(self._url) or ""
        except Exception as exc:  # pragma: no cover - rete/filesystem
            print(f"[Background] featured image failed: {exc}")
        self.ready.emit(self._url, path)
