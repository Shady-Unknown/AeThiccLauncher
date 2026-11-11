import json
import os
import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal, pyqtSlot

from core import secrets
from core.paths import PathManager

TOKEN_CACHE_FILENAME = "igdb_token.json"

IGDB_BASE = "https://api.igdb.com/v4"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

PAGE_SIZE = 50
MAX_OFFSET = 10000  # guardia di sicurezza, non un limite pratico
REQUEST_DELAY_SECONDS = 0.35

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# "Macro" categories only. filter is appended to the shared where clause.
#   genres = [X]  -> IGDB genre id
#   themes = [X]  -> IGDB theme id
#   ""            -> no genre/theme restriction (trending / new releases)
IGDB_CATEGORIES = [
    {"key": "trending", "title": "\U0001f525 Trending", "filter": "", "sort": "hypes desc"},
    {"key": "new_releases", "title": "\U0001f195 New Releases", "filter": "", "sort": "first_release_date desc"},
    {"key": "horror", "title": "\U0001f9df Horror", "filter": "genres = [5]", "sort": "hypes desc"},
    {"key": "sandbox", "title": "\U0001f3dd Sandbox", "filter": "themes = [33]", "sort": "hypes desc"},
    {"key": "survival", "title": "\u26fa Survival", "filter": "themes = [21]", "sort": "hypes desc"},
    {"key": "platform", "title": "\U0001f3c4 Platform", "filter": "genres = [8]", "sort": "hypes desc"},
    {"key": "racing", "title": "\U0001f3c1 Racing", "filter": "genres = [10]", "sort": "hypes desc"},
    {"key": "shooter", "title": "\U0001f3af Shooter", "filter": "genres = [2]", "sort": "hypes desc"},
    {"key": "rpg", "title": "\U0001f409 RPG", "filter": "genres = [12]", "sort": "hypes desc"},
    {"key": "strategy", "title": "\u265f Strategy", "filter": "genres = [15]", "sort": "hypes desc"},
    {"key": "action", "title": "\u2694 Action", "filter": "genres = [4]", "sort": "hypes desc"},
    {"key": "puzzle", "title": "\U0001f9e9 Puzzle", "filter": "genres = [9]", "sort": "hypes desc"},
    {"key": "sport", "title": "\U0001f3c6 Sport", "filter": "genres = [14]", "sort": "hypes desc"},
    {"key": "simulator", "title": "\U0001f3ae Simulator", "filter": "genres = [13]", "sort": "hypes desc"},
    {"key": "fighting", "title": "\U0001f977 Fighting", "filter": "genres = [25]", "sort": "hypes desc"},
    {"key": "adventure", "title": "\U0001f3dc Adventure", "filter": "genres = [31]", "sort": "hypes desc"},
    {"key": "indie", "title": "\U0001f3b2 Indie", "filter": "genres = [32]", "sort": "hypes desc"},
]

COMMON_WHERE = ("game_type = 0 & platforms = [6] & cover != null "
                "& external_games != null & first_release_date != null")
FIELDS = ("name,slug,first_release_date,hypes,total_rating,cover.url,"
          "external_games.category,external_games.external_game_source,"
          "external_games.uid,game_type")

STEAM_EXTERNAL_CATEGORY = 1


def igdb_cover_big(cover_url):
    """Bump an IGDB cover URL from t_thumb to t_cover_big (264x352)."""
    if not cover_url:
        return ""
    url = cover_url.replace("t_thumb", "t_cover_big")
    if url.startswith("//"):
        url = "https:" + url
    return url


def normalize_game(raw):
    """Flatten one IGDB game object into the fields the discovery layer needs."""
    steam_appid = ""
    for ext in raw.get("external_games") or []:
        src = int(ext.get("external_game_source") or ext.get("category") or -1)
        if src == STEAM_EXTERNAL_CATEGORY:
            uid = str(ext.get("uid") or "").strip()
            if uid.isdigit():
                steam_appid = uid
                break

    return {
        "igdb_id": raw.get("id"),
        "name": raw.get("name") or "",
        "slug": raw.get("slug") or "",
        "steam_appid": steam_appid,
        "cover_url": igdb_cover_big((raw.get("cover") or {}).get("url") or ""),
        "first_release_date": raw.get("first_release_date"),
        "hypes": raw.get("hypes") or 0,
        "total_rating": raw.get("total_rating"),
    }


class IGDBWorker(QThread):
    """Fetches released PC games from IGDB for the discovery categories.

    Signals fire on the GUI thread. The thread keeps an event loop after
    run() so lazy pages can be requested later via request_page().
    """

    categories_ready = pyqtSignal(dict)
    page_ready = pyqtSignal(str, list, bool)  # key, games, has_more
    error_occurred = pyqtSignal(str)

    def __init__(self, category_keys=None, initial_keys=None, parent=None):
        super().__init__(parent)
        self._categories = [c for c in IGDB_CATEGORIES
                            if category_keys is None or c["key"] in category_keys]
        self._initial_keys = set(initial_keys) if initial_keys is not None else None
        self._client_id = None
        self._secret = None
        self._token = None
        self._token_expires = 0.0
        self._offsets = {}
        self._loading_pages = set()
        self._stop_flag = False

    # ── credentials / token ──────────────────────────────────────────

    @staticmethod
    def _token_cache_path():
        d = PathManager.get_app_data_dir()
        os.makedirs(str(d), exist_ok=True)
        return os.path.join(str(d), TOKEN_CACHE_FILENAME)

    def _load_creds(self):
        client_id, secret = secrets.get_igdb_credentials()
        if not client_id or not secret:
            return False
        self._client_id = client_id
        self._secret = secret
        return True

    def _load_cached_token(self):
        try:
            with open(self._token_cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self._token = data.get("access_token")
            self._token_expires = float(data.get("expires_at") or 0)
            return bool(self._token)
        except Exception:
            return False

    def _save_token(self, token, expires_in):
        self._token = token
        self._token_expires = time.time() + max(0, int(expires_in) - 300)
        try:
            with open(self._token_cache_path(), "w", encoding="utf-8") as f:
                json.dump({"access_token": token, "expires_at": self._token_expires}, f)
        except Exception as e:
            print(f"[IGDB] token cache save error: {e}")

    def _refresh_token(self):
        try:
            r = requests.post(
                TWITCH_TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._secret,
                    "grant_type": "client_credentials",
                },
                timeout=10,
            )
            if r.status_code != 200:
                print(f"[IGDB] token status: {r.status_code} {r.text[:200]}")
                return False
            data = r.json()
            self._save_token(data.get("access_token"), int(data.get("expires_in") or 0))
            return True
        except Exception as e:
            print(f"[IGDB] token refresh error: {e}")
            return False

    def _ensure_token(self):
        if self._token and time.time() < self._token_expires:
            return True
        if self._load_cached_token() and time.time() < self._token_expires:
            return True
        return self._refresh_token()

    # ── queries ──────────────────────────────────────────────────────

    def _headers(self):
        return {
            "Client-ID": self._client_id,
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "Content-Type": "text/plain",
            "User-Agent": UA,
        }

    def _fetch_page(self, category, offset):
        where = COMMON_WHERE
        if category.get("filter"):
            where += f" & {category['filter']}"
        body = (
            f"fields {FIELDS}; "
            f"where {where}; "
            f"sort {category['sort']}; "
            f"limit {PAGE_SIZE}; "
            f"offset {offset};"
        )
        try:
            r = requests.post(
                f"{IGDB_BASE}/games",
                headers=self._headers(),
                data=body,
                timeout=15,
            )
            if r.status_code == 429:
                time.sleep(2.0)
                r = requests.post(
                    f"{IGDB_BASE}/games",
                    headers=self._headers(),
                    data=body,
                    timeout=15,
                )
            if r.status_code != 200:
                print(f"[IGDB] query status {r.status_code} for '{category['key']}': {r.text[:200]}")
                return []
            games = [normalize_game(g) for g in (r.json() or []) if g.get("name")]
            return games
        except Exception as e:
            print(f"[IGDB] query error for '{category['key']}': {e}")
            return []

    # ── lifecycle ────────────────────────────────────────────────────

    def stop(self):
        self._stop_flag = True
        self.quit()

    def run(self):
        if not self._load_creds():
            print("[IGDB] credentials not configured")
            self.error_occurred.emit("IGDB credentials not configured.")
            return
        if not self._ensure_token():
            print("[IGDB] could not obtain access token")
            self.error_occurred.emit("IGDB authentication failed.")
            return

        result = {}
        for category in self._categories:
            if self._stop_flag:
                break
            if self._initial_keys is not None and category["key"] not in self._initial_keys:
                continue
            games = self._fetch_page(category, 0)
            result[category["key"]] = games
            self._offsets[category["key"]] = len(games)
            time.sleep(REQUEST_DELAY_SECONDS)

        print(f"[IGDB] fetched {len(result)} categories")
        self.categories_ready.emit(result)
        if self._stop_flag:
            return
        self.exec()

    @pyqtSlot(str)
    def request_page(self, category_key):
        """Lazy-load the next IGDB page for a category (queued, worker thread)."""
        try:
            category = next((c for c in self._categories
                             if c["key"] == category_key), None)
            if category is None:
                return
            offset = self._offsets.get(category_key, 0)
            if offset >= MAX_OFFSET:
                return
            games = self._fetch_page(category, offset)
            if games:
                self._offsets[category_key] = offset + len(games)
                self.page_ready.emit(category_key, games, len(games) >= PAGE_SIZE)
            else:
                self.page_ready.emit(category_key, [], False)
        finally:
            self._loading_pages.discard(category_key)
