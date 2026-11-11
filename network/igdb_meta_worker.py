# igdb_meta_worker.py
"""
Arricchimento IGDB on-demand per la Discovery.

- Risolve l'appid Steam (dal catalogo, dalla riga IGDB o via Steam storesearch)
- Interroga IGDB in batch per external_games.uid
- Restituisce generi/temi/modalità (nomi), rating, anno e cover
- Cache della tassonomia in %APPDATA%/igdb_taxonomy.json

Pensato per girare su poche decine di titoli visibili alla volta.
"""
import json
import os
import re
import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from core.paths import PathManager
from network.igdb_worker import IGDB_BASE, UA, IGDBWorker

TAXONOMY_FILENAME = "igdb_taxonomy.json"
TAXONOMY_TTL = 30 * 24 * 3600
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
APPID_RESOLVE_LIMIT = 40
REQUEST_DELAY = 0.3


def _clean_title(text):
    text = re.sub(r'(?i)(game of the year|complete edition|ultimate edition|'
                  r"director\'s cut|deluxe edition|sunset edition|digital edition|"
                  r'gold edition|standard edition)', '', text or '')
    return re.sub(r'[^a-z0-9]', '', text.lower())


def _guess_appid(title):
    """Cerca l'appid Steam dal titolo (una richiesta leggera)."""
    try:
        clean = re.sub(r'\(.*?\)', '', title or '').strip()
        if not clean:
            return ""
        r = requests.get(
            STEAM_SEARCH_URL,
            params={"term": clean, "l": "english", "cc": "US"},
            headers={"User-Agent": UA},
            timeout=5,
        )
        if r.status_code != 200:
            return ""
        wanted = _clean_title(clean)
        best = ""
        best_score = 0
        for item in (r.json().get("items") or [])[:10]:
            name = item.get("name") or ""
            candidate = _clean_title(name)
            if not candidate:
                continue
            if candidate == wanted:
                return str(item.get("id") or "")
            if wanted and (wanted in candidate or candidate in wanted):
                score = min(len(wanted), len(candidate))
                if score > best_score:
                    best_score = score
                    best = str(item.get("id") or "")
        return best
    except Exception:
        return ""


def _taxonomy_path():
    return os.path.join(str(PathManager.get_app_data_dir()), TAXONOMY_FILENAME)


def load_taxonomy():
    try:
        with open(_taxonomy_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - float(data.get("fetched_at") or 0) > TAXONOMY_TTL:
            return None
        return data
    except Exception:
        return None


def _fetch_taxonomy(headers):
    def fetch(endpoint, field):
        r = requests.post(f"{IGDB_BASE}/{endpoint}",
                          headers=headers, data=f"fields id,name; limit 100;", timeout=15)
        if r.status_code != 200:
            return {}
        return {int(item["id"]): item["name"] for item in (r.json() or []) if item.get("id")}

    data = {
        "fetched_at": time.time(),
        "genres": fetch("genres", "genres"),
        "themes": fetch("themes", "themes"),
        "modes": fetch("game_modes", "modes"),
    }
    try:
        with open(_taxonomy_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[IGDBMeta] taxonomy save error: {e}")
    return data


def _cover_url(raw):
    url = (raw or {}).get("url") or ""
    if url.startswith("//"):
        url = "https:" + url
    return url.replace("t_thumb", "t_cover_big")


class IGDBMetaWorker(QThread):
    meta_ready = pyqtSignal(dict)          # title -> meta
    taxonomy_ready = pyqtSignal(dict)      # {"genres": {}, "themes": {}, "modes": {}}

    def __init__(self, jobs, parent=None):
        """jobs: list of (title, appid) — appid può essere vuoto."""
        super().__init__(parent)
        self._jobs = list(jobs)

    # ── appid ────────────────────────────────────────────────────────

    def _resolve_appids(self):
        resolved = {}
        lookups = 0
        for title, appid in self._jobs:
            if appid:
                resolved[title] = str(appid)
            elif lookups < APPID_RESOLVE_LIMIT:
                lookups += 1
                found = _guess_appid(title)
                if found:
                    resolved[title] = found
                time.sleep(REQUEST_DELAY)
        return resolved

    # ── main ─────────────────────────────────────────────────────────

    def run(self):
        base = IGDBWorker()
        if not base._load_creds() or not base._ensure_token():
            self.meta_ready.emit({})
            return
        headers = base._headers()

        taxonomy = load_taxonomy()
        if taxonomy is None:
            taxonomy = _fetch_taxonomy(headers)
        self.taxonomy_ready.emit(taxonomy)

        resolved = self._resolve_appids()
        if not resolved:
            self.meta_ready.emit({})
            return

        appid_to_title = {str(appid): title for title, appid in resolved.items()}
        appids = list(appid_to_title)
        results = {}
        for start in range(0, len(appids), 50):
            chunk = appids[start:start + 50]
            body = (
                "fields name,genres,themes,game_modes,total_rating,first_release_date,"
                "cover.url,external_games.uid; "
                "where external_games.uid = ("
                + ",".join(f'"{appid}"' for appid in chunk) + "); "
                f"limit {len(chunk) + 10};"
            )
            try:
                r = requests.post(f"{IGDB_BASE}/games", headers=headers, data=body, timeout=15)
                if r.status_code != 200:
                    print(f"[IGDBMeta] query status {r.status_code}")
                    continue
                for game in r.json() or []:
                    matched_appid = None
                    for ext in game.get("external_games") or []:
                        uid = str(ext.get("uid") or "")
                        if uid in appid_to_title:
                            matched_appid = uid
                            break
                    title = appid_to_title.get(matched_appid)
                    if not title:
                        continue
                    genres = [int(g) for g in (game.get("genres") or [])]
                    themes = [int(t) for t in (game.get("themes") or [])]
                    modes = [int(m) for m in (game.get("game_modes") or [])]
                    release = game.get("first_release_date")
                    results[title] = {
                        "igdb_id": game.get("id"),
                        "steam_appid": matched_appid or "",
                        "genres": [taxonomy["genres"].get(str(g), str(g)) for g in genres],
                        "genre_ids": genres,
                        "themes": [taxonomy["themes"].get(str(t), str(t)) for t in themes],
                        "theme_ids": themes,
                        "modes": [taxonomy["modes"].get(str(m), str(m)) for m in modes],
                        "mode_ids": modes,
                        "rating": round(game.get("total_rating") or 0, 1),
                        "release_year": time.gmtime(release).tm_year if release else 0,
                        "cover_url": _cover_url(game.get("cover")),
                    }
            except Exception as e:
                print(f"[IGDBMeta] batch error: {e}")
            time.sleep(REQUEST_DELAY)

        print(f"[IGDBMeta] enriched {len(results)}/{len(self._jobs)} titles")
        self.meta_ready.emit(results)
