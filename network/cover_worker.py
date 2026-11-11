import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote as _urlquote

import requests
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from core.mini_cover_cache import MiniCoverManager, MINI_W, MINI_H
from core.cover_cache import to_file_url
from core import secrets
from managers.save_manager import safe_game_folder_name
from network.steamrip_api import fetch_page_cover

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

SGDB_API_DELAY = 1.1
SGDB_CALL_BUDGET = 100
COVER_WORKERS = 4
COVER_RETRY_LIMIT = 3
PAGE_FETCH_INTERVAL = 0.4   # throttle per il fallback HTTP


class CoverWorker(QThread):
    """Background cover downloader (multi-worker, parallel sources).

    Resolution chain per game:
      1. local/installed cover (MiniCoverManager)
      2. SGDB (cached grid in the master catalog, else SGDB API by appid)
      3. IGDB cover URL (download only)
      4. SteamRip page scrape (lazy; only for explicitly flagged jobs)

    Up to COVER_WORKERS games are processed concurrently. Inside one job,
    SGDB and IGDB are fetched in parallel and the first valid image wins
    (SGDB is preferred when both succeed). SGDB API calls are throttled
    globally across workers via a shared lock.
    """

    cover_ready = pyqtSignal(str, str)     # title, local file:// path
    scrape_requested = pyqtSignal(str, str)  # title, steamrip page url

    def __init__(self, installed_games=None, sync_folder="", parent=None):
        super().__init__(parent)
        self._installed_games = installed_games or {}
        self._sync_folder = sync_folder or ""
        self._lock = threading.Lock()
        self._sgdb_lock = threading.Lock()
        self._high = []
        self._low = []
        self._queued = set()
        self._resolved = set()
        self._attempts = {}
        self._sgdb_calls = 0
        self._sgdb_last_call = 0.0
        self._sgdb_key = secrets.get_sgdb_key()
        self._page_lock = threading.Lock()
        self._page_last = 0.0
        self._stop_flag = False

    # â”€â”€ public API (GUI thread) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def update_context(self, installed_games=None, sync_folder=None):
        if installed_games is not None:
            self._installed_games = installed_games
        if sync_folder is not None:
            self._sync_folder = sync_folder

    def enqueue(self, jobs, high=False):
        """jobs: list of dicts {title, catalog_entry?, igdb_cover_url?, url?}."""
        if self._stop_flag:
            return
        with self._lock:
            for job in jobs:
                title = job.get("title")
                if not title:
                    continue
                if title in self._resolved:
                    continue
                if high:
                    self._low = [j for j in self._low if j.get("title") != title]
                    self._high.append(job)
                elif title not in self._queued:
                    self._queued.add(title)
                    self._low.append(job)

    def cancel(self, titles):
        """Drop queued jobs for titles that left the lazy window, allowing
        them to be re-enqueued later (also unblocks failed/resolved titles)."""
        if self._stop_flag or not titles:
            return
        wanted = set(titles)
        with self._lock:
            self._high = [j for j in self._high if j.get("title") not in wanted]
            self._low = [j for j in self._low if j.get("title") not in wanted]
            self._queued.difference_update(wanted)
            self._resolved.difference_update(wanted)
            for title in wanted:
                self._attempts.pop(title, None)

    def download_url(self, title, url, high=True):
        """Force-download a specific image URL (used for SteamRIP scraped covers)."""
        if self._stop_flag or not title or not url:
            return
        with self._lock:
            self._resolved.discard(title)
            self._low = [j for j in self._low if j.get("title") != title]
            self._high.insert(0, {"title": title, "igdb_cover_url": url})

    def stop(self):
        self._stop_flag = True

    # â”€â”€ worker loop (multi-worker pool) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def run(self):
        with ThreadPoolExecutor(max_workers=COVER_WORKERS) as pool:
            while not self._stop_flag:
                jobs = []
                for _ in range(COVER_WORKERS):
                    job = self._pop_next()
                    if job is None:
                        break
                    jobs.append(job)
                if not jobs:
                    self.msleep(250)
                    continue
                futures = [pool.submit(self._process, job) for job in jobs]
                for job, fut in zip(jobs, futures):
                    try:
                        fut.result()
                    except Exception as e:
                        print(f"[CoverWorker] error for '{job.get('title')}': {e}")
                        with self._lock:
                            self._resolved.add(job.get("title"))

    def _pop_next(self):
        with self._lock:
            if self._high:
                job = self._high.pop(0)
            elif self._low:
                job = self._low.pop(0)
            else:
                return None
            title = job.get("title")
            self._queued.discard(title)
            self._attempts[title] = self._attempts.get(title, 0) + 1
            return job

    # â”€â”€ job processing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _process(self, job):
        title = job.get("title", "")
        if not title:
            return

        existing = MiniCoverManager.resolve_cover_path(
            title, self._installed_games, self._sync_folder)
        # A horizontal SteamRIP featured image is a fallback, not a final
        # cover: keep looking for the vertical IGDB/SGDB box art.
        if existing and MiniCoverManager.is_vertical_cover(existing):
            return

        entry = job.get("catalog_entry")
        igdb = job.get("igdb") or {}
        path = None

        if isinstance(entry, dict):
            sg = entry.get("sgdb_assets")
            if not isinstance(sg, dict):
                sg = {}
            grid = sg.get("grid")
            if grid:
                path = self._download(grid, title)

        if path is None:
            sid = ""
            if isinstance(entry, dict):
                sg = entry.get("sgdb_assets")
                if not isinstance(sg, dict):
                    sg = {}
                sid = str(entry.get("steam_id") or sg.get("steam_app_id") or "").strip()
            if not sid and isinstance(igdb, dict):
                sid = str(igdb.get("steam_appid") or "").strip()
            igdb_url = job.get("igdb_cover_url")

            # IGDB-first: cover giÃ  disponibile, zero quota/rate-limit.
            if igdb_url:
                img = self._fetch(igdb_url)
                if img is not None:
                    path = self._persist(title, img)

            # Fallback SGDB (solo se IGDB non ha dato una cover valida).
            if path is None and sid and sid.isdigit():
                img = self._sgdb_image(sid, title)
                if img is not None:
                    path = self._persist(title, img)

            # Pagina SteamRip prima della ricerca per nome: nessuna quota
            # SGDB e immagine ufficiale del sito.
            if path is None and job.get("allow_page_fetch") and job.get("url"):
                page_cover = self._page_fetch(job["url"])
                if page_cover:
                    img = self._fetch(page_cover)
                    if img is not None:
                        path = self._persist(title, img)

            # Ultima risorsa: SGDB per nome (giochi senza appid e senza URL).
            if path is None:
                grid = self._sgdb_grid_by_name(title)
                if grid:
                    img = self._fetch(grid)
                    if img is not None:
                        path = self._persist(title, img)

        if path is None and job.get("allow_scrape") and job.get("url"):
            self.scrape_requested.emit(title, job["url"])
            return

        if path:
            with self._lock:
                self._resolved.add(title)
                self._attempts.pop(title, None)
            self.cover_ready.emit(title, path)
        else:
            # Nessuna cover trovata: consenti un nuovo tentativo piu' avanti
            # (es. rete non pronta, cache cover ancora vuota su macchina nuova).
            with self._lock:
                if self._attempts.get(title, 0) >= COVER_RETRY_LIMIT:
                    self._resolved.add(title)

    def _page_fetch(self, url):
        # Throttle globale: una GET per volta verso SteamRip.
        with self._page_lock:
            wait = PAGE_FETCH_INTERVAL - (time.monotonic() - self._page_last)
            if wait > 0:
                time.sleep(wait)
            self._page_last = time.monotonic()
        return fetch_page_cover(url)

    def _sgdb_request(self, url):
        if not self._sgdb_key:
            return None
        with self._sgdb_lock:
            if self._sgdb_calls >= SGDB_CALL_BUDGET:
                return None
            self._sgdb_calls += 1
            wait = max(0.0, self._sgdb_last_call + SGDB_API_DELAY - time.monotonic())
            if wait:
                time.sleep(wait)
            self._sgdb_last_call = time.monotonic()
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {self._sgdb_key}"},
                             timeout=8)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    @staticmethod
    def _name_key(text):
        return "".join(c for c in (text or "").lower() if c.isalnum())

    def _sgdb_grid_by_name(self, title):
        """Cerca la cover su SteamGridDB usando il titolo (giochi senza appid)."""
        key = self._name_key(title)
        if len(key) < 3:
            return None
        search = self._sgdb_request(
            "https://www.steamgriddb.com/api/v2/search/autocomplete/"
            + _urlquote(title))
        results = (search or {}).get("data") or []
        if isinstance(results, dict):
            results = [results]
        game_id = None
        for item in results[:4]:
            if not isinstance(item, dict):
                continue
            name_key = self._name_key(item.get("name"))
            if not name_key:
                continue
            if key == name_key or key in name_key or name_key in key:
                game_id = item.get("id")
                break
        if not game_id:
            return None
        grids = self._sgdb_request(
            f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}"
            "?dimensions=600x900")
        data = (grids or {}).get("data")
        if isinstance(data, dict):
            return data.get("url")
        if isinstance(data, list) and data:
            return data[0].get("url")
        return None

    def _sgdb_image(self, appid, title):
        url = self._sgdb_grid_url(appid)
        if not url:
            return None
        return self._fetch(url)

    # â”€â”€ SGDB (globally rate-limited) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _sgdb_grid_url(self, appid):
        if not self._sgdb_key:
            return None
        with self._sgdb_lock:
            if self._sgdb_calls >= SGDB_CALL_BUDGET:
                return None
            self._sgdb_calls += 1
            wait = max(0.0, self._sgdb_last_call + SGDB_API_DELAY - time.monotonic())
            if wait:
                time.sleep(wait)
            self._sgdb_last_call = time.monotonic()

        headers = {"Authorization": f"Bearer {self._sgdb_key}"}
        try:
            r = requests.get(
                f"https://www.steamgriddb.com/api/v2/games/steam/{appid}",
                headers=headers,
                timeout=8,
            )
            if r.status_code != 200:
                return None
            payload = r.json()
            data = payload.get("data")
            # L'endpoint puÃ² restituire un oggetto singolo o una lista.
            if isinstance(data, dict):
                game_id = data.get("id")
            elif isinstance(data, list) and data:
                game_id = data[0].get("id")
            else:
                game_id = None
            if not game_id:
                return None

            with self._sgdb_lock:
                if self._sgdb_calls >= SGDB_CALL_BUDGET:
                    return None
                self._sgdb_calls += 1
                wait = max(0.0, self._sgdb_last_call + SGDB_API_DELAY - time.monotonic())
                if wait:
                    time.sleep(wait)
                self._sgdb_last_call = time.monotonic()

            r = requests.get(
                f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}?dimensions=600x900",
                headers=headers,
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json().get("data")
                if isinstance(data, dict):
                    return data.get("url")
                if isinstance(data, list) and data:
                    return data[0].get("url")
        except Exception as e:
            print(f"[CoverWorker] SGDB error for '{appid}': {e}")
        return None

    # â”€â”€ download + persist into the MiniCover cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def _ensure_https(url):
        if url and str(url).startswith("//"):
            return "https:" + url
        return url

    def _fetch(self, url):
        url = self._ensure_https(url)
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            if r.status_code != 200:
                return None
            img = Image.open(io.BytesIO(r.content))
            return img.convert("RGB")
        except Exception as e:
            print(f"[CoverWorker] fetch failed for '{url}': {e}")
            return None

    def _persist(self, title, img):
        try:
            img.thumbnail((MINI_W, MINI_H), Image.Resampling.LANCZOS)
            safe = safe_game_folder_name(title)
            cache_dir = MiniCoverManager.get_local_cache_dir()
            path = os.path.join(cache_dir, safe + ".jpg")
            img.save(path, "JPEG", quality=85)
            return to_file_url(path)
        except Exception as e:
            print(f"[CoverWorker] persist failed for '{title}': {e}")
            return None

    def _download(self, url, title):
        img = self._fetch(url)
        if img is None:
            return None
        return self._persist(title, img)
