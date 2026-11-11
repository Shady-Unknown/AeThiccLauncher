# steamrip_api.py
"""
Client leggero per la WordPress REST API di SteamRip.

Fornisce:
- l'elenco delle categorie "gioco" del sito (tassonomia reale del catalogo)
- i post paginati per categoria / recenti
- un worker per pagina e un worker batch per il primo caricamento

Nota: i titoli REST sono HTML-escaped e includono "Free Download (vX)":
vengono normalizzati con html.unescape + strip_title.
"""
import html
import re
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal
from curl_cffi import requests
from bs4 import BeautifulSoup

from core.meta_utils import strip_title

BASE = "https://steamrip.com"
WP_API = f"{BASE}/wp-json/wp/v2"
PER_PAGE = 30
MAX_PER_PAGE = 100
REQUEST_DELAY_SECONDS = 0.2

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Categorie "gioco" del menu SteamRip (esclusi Uncategorized e Nudity).
# Ordine come nel menu del sito. I count sono indicativi (si aggiornano dalla REST API).
STEAMRIP_CATEGORIES = [
    {"id": 26, "name": "Action", "slug": "action", "count": 2742},
    {"id": 27, "name": "Adventure", "slug": "adventure", "count": 2551},
    {"id": 29, "name": "Anime", "slug": "anime", "count": 428},
    {"id": 53, "name": "Building", "slug": "building", "count": 271},
    {"id": 52, "name": "First-person Shooter", "slug": "fps", "count": 145},
    {"id": 32, "name": "Horror", "slug": "horror", "count": 524},
    {"id": 33, "name": "Indie", "slug": "indie", "count": 2705},
    {"id": 1538, "name": "Multiplayer", "slug": "multiplayer", "count": 382},
    {"id": 45, "name": "Open World", "slug": "open-world", "count": 303},
    {"id": 35, "name": "Racing", "slug": "racing", "count": 237},
    {"id": 63, "name": "Role-playing Game", "slug": "rpg", "count": 1237},
    {"id": 70, "name": "Sci-fi", "slug": "sci-fi", "count": 235},
    {"id": 41, "name": "Shooters", "slug": "shooting", "count": 380},
    {"id": 37, "name": "Simulation", "slug": "simulation", "count": 1664},
    {"id": 38, "name": "Sports", "slug": "sports", "count": 250},
    {"id": 42, "name": "Strategy", "slug": "strategy", "count": 1179},
    {"id": 119, "name": "Survival", "slug": "survival", "count": 266},
    {"id": 39, "name": "Virtual Reality", "slug": "vr", "count": 122},
]

DEFAULT_FIELDS = "title,link,categories,modified"

# Throttle globale: SteamRip risponde 429 se martellato (soprattutto con l'indice).
_REST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
MIN_REQUEST_INTERVAL = 0.35
RETRY_DELAYS = (2.0, 4.0, 8.0)


def _rest_get(params, timeout=15):
    """GET serializzata e con retry/backoff sui 429."""
    global _LAST_REQUEST
    last_response = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        with _REST_LOCK:
            wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST)
            if wait > 0:
                time.sleep(wait)
            _LAST_REQUEST = time.monotonic()
            last_response = requests.get(
                f"{WP_API}/posts",
                params=params,
                headers={"User-Agent": UA, "Accept": "application/json"},
                impersonate="chrome120",
                timeout=timeout,
            )
        if last_response.status_code != 429:
            return last_response
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
        print(f"[SteamRipAPI] 429 rate-limit, retry in {delay:.0f}s")
        time.sleep(delay)
    return last_response


def normalize_post(post):
    """REST post -> dict normalizzato per la Discovery."""
    raw_title = html.unescape(re.sub(r"<[^>]+>", "", (post.get("title") or {}).get("rendered") or ""))
    raw_title = raw_title.strip()
    title, version, mp_tag = strip_title(raw_title)
    return {
        "title": title.strip(),
        "raw_title": raw_title,
        "url": (post.get("link") or "").rstrip("/"),
        "modified": post.get("modified") or "",
        "categories": post.get("categories") or [],
        "version": version,
        "mp_tag": mp_tag,
    }


def fetch_page_cover(page_url, timeout=10):
    """
    Ultima spiaggia per le cover: legge la featured image della pagina SteamRip.
    GET diretto (niente browser nascosto), usato dal CoverWorker.
    """
    if not page_url:
        return ""
    try:
        r = requests.get(
            page_url,
            headers={"User-Agent": UA},
            impersonate="chrome120",
            timeout=timeout,
        )
        if r.status_code != 200 or not r.text:
            return ""
        soup = BeautifulSoup(r.text, "lxml")
        img = soup.find("figure", class_="single-featured-image")
        img = img.find("img") if img else None
        if img is None:
            img = soup.find("img", class_="wp-post-image")
        if img is None:
            return ""
        url = (img.get("data-src") or img.get("data-lazy-src")
               or img.get("src") or "").strip()
        if url.startswith("data:"):
            # lazy placeholder: prova con la srcset / data-src
            url = (img.get("data-src") or img.get("data-lazy-src") or "").strip()
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        return url
    except Exception as e:
        print(f"[SteamRipAPI] page cover error for {page_url}: {e}")
        return ""


def fetch_posts(page=1, category_ids=None, orderby="date", order="desc",
                per_page=PER_PAGE, fields=DEFAULT_FIELDS, timeout=15):
    """
    Ritorna (posts, total_pages, total).
    Se la pagina richiesta è oltre la fine, WP risponde 400: in quel caso
    restituiamo lista vuota e i totali dagli header.
    """
    params = {
        "page": max(1, int(page)),
        "per_page": max(1, min(int(per_page), MAX_PER_PAGE)),
        "orderby": orderby,
        "order": order,
        "_fields": fields,
    }
    if category_ids:
        params["categories"] = ",".join(str(c) for c in category_ids)

    r = _rest_get(params, timeout=timeout)
    total_pages = int(r.headers.get("X-WP-TotalPages") or 0)
    total = int(r.headers.get("X-WP-Total") or 0)
    if r.status_code == 400:
        return [], total_pages, total
    if r.status_code != 200:
        raise RuntimeError(f"SteamRip REST status {r.status_code}")
    return r.json() or [], total_pages, total


class SteamRipPostsWorker(QThread):
    """Una pagina di post SteamRip (categoria o recenti)."""

    page_ready = pyqtSignal(str, list, int, int)  # key, posts, page, total_pages
    error = pyqtSignal(str, str)                  # key, message

    def __init__(self, key, page, category_id=None, orderby="date", parent=None):
        super().__init__(parent)
        self.key = key
        self.page = int(page)
        self.category_id = category_id
        self.orderby = orderby

    def run(self):
        try:
            posts, total_pages, _total = fetch_posts(
                page=self.page,
                category_ids=[self.category_id] if self.category_id else None,
                orderby=self.orderby,
            )
            normalized = [normalize_post(p) for p in posts]
            self.page_ready.emit(self.key, normalized, self.page, total_pages)
        except Exception as e:
            print(f"[SteamRipAPI] page {self.page} error for '{self.key}': {e}")
            self.error.emit(self.key, str(e))


class SteamRipBatchWorker(QThread):
    """
    Primo caricamento sequenziale di più righe native.
    jobs: list of (key, page, category_id, orderby)
    """

    page_ready = pyqtSignal(str, list, int, int)
    error = pyqtSignal(str, str)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        from PyQt6.QtCore import QThread as _QThread
        for key, page, category_id, orderby in self._jobs:
            if self._stop_flag:
                break
            try:
                posts, total_pages, _total = fetch_posts(
                    page=page,
                    category_ids=[category_id] if category_id else None,
                    orderby=orderby,
                )
                self.page_ready.emit(key, [normalize_post(p) for p in posts],
                                     page, total_pages)
            except Exception as e:
                print(f"[SteamRipAPI] batch error for '{key}': {e}")
                self.error.emit(key, str(e))
            _QThread.msleep(int(REQUEST_DELAY_SECONDS * 1000))


class SteamRipIndexWorker(QThread):
    """
    Costruisce l'indice url -> [category_ids] dell'intero catalogo.
    ~51 richieste da 100 post. Usato per i filtri.
    """

    index_ready = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        index = {}
        page = 1
        total_pages = 1
        try:
            while page <= total_pages and not self._stop_flag:
                posts, total_pages, total = fetch_posts(page=page, per_page=100)
                for post in posts:
                    url = (post.get("link") or "").rstrip("/")
                    if url:
                        index[url] = post.get("categories") or []
                print(f"[SteamRipAPI] index page {page}/{total_pages} ({len(index)}/{total})")
                page += 1
            self.index_ready.emit(index)
        except Exception as e:
            print(f"[SteamRipAPI] index error: {e}")
