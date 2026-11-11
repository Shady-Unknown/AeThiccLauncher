# page_freshness_worker.py
from PyQt6.QtCore import QThread, pyqtSignal

from network.resolver import check_page_freshness, fetch_page_headers
from core.version_status import parse_version_status_html


class PageFreshnessWorker(QThread):
    """HEAD condizionale (ETag/Last-Modified) fuori dal thread UI."""

    checked = pyqtSignal(str, bool)  # url, is_fresh

    def __init__(self, url, etag="", last_modified="", parent=None):
        super().__init__(parent)
        self.url = url
        self.etag = etag or ""
        self.last_modified = last_modified or ""

    def run(self):
        try:
            fresh = check_page_freshness(self.url, self.etag, self.last_modified)
        except Exception as e:
            print(f"[Freshness] check failed for {self.url}: {e}")
            fresh = True  # graceful degradation, come prima
        self.checked.emit(self.url, fresh)


class PageHeadersWorker(QThread):
    """Recupera ETag/Last-Modified di una pagina appena scrapata."""

    headers_ready = pyqtSignal(str, str, str)  # url, etag, last_modified

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            etag, last_modified = fetch_page_headers(self.url)
        except Exception:
            etag, last_modified = "", ""
        self.headers_ready.emit(self.url, etag or "", last_modified or "")


class VersionStatusWorker(QThread):
    """
    Backfill del blocco Version Status per le schede in cache create prima
    che il widget esistesse: una GET leggera + parsing, senza browser nascosto.
    """

    status_ready = pyqtSignal(str, str, dict)  # title, url, status

    def __init__(self, title, url, parent=None):
        super().__init__(parent)
        self.title = title
        self.url = url

    def run(self):
        status = {}
        try:
            from curl_cffi import requests
            response = requests.get(self.url, impersonate="chrome120", timeout=10)
            if response.status_code == 200 and response.text:
                status = parse_version_status_html(response.text) or {}
        except Exception as e:
            print(f"[VersionStatus] backfill failed for {self.url}: {e}")
        self.status_ready.emit(self.title, self.url, status)
