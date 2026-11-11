# steam_build_worker.py
from PyQt6.QtCore import QThread, pyqtSignal

from core.version_status import fetch_steam_latest_build


class SteamBuildWorker(QThread):
    """Recupera l'ultima build pubblica su Steam (solo lato Steam)."""

    # title, app_id, build, date, timestamp
    result_ready = pyqtSignal(str, str, int, str, int)
    failed = pyqtSignal(str, str)  # title, app_id

    def __init__(self, title, app_id, parent=None):
        super().__init__(parent)
        self.title = title or ""
        self.app_id = str(app_id or "").strip()

    def run(self):
        result = fetch_steam_latest_build(self.app_id)
        if not result:
            self.failed.emit(self.title, self.app_id)
            return
        self.result_ready.emit(
            self.title,
            self.app_id,
            int(result["build"]),
            result.get("date", ""),
            int(result.get("timestamp") or 0),
        )
