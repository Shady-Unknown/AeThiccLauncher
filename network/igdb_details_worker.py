# igdb_details_worker.py
"""
Dettagli IGDB per il popup scheda della Discovery.

Una sola richiesta per gioco, con i campi "editoriali" che non stanno in
igdb_meta (summary, aziende, piattaforme, screenshot). Il risultato viene
salvato in managers.igdb_details_cache, non nel master catalog.
"""
import re
import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from network.igdb_worker import IGDB_BASE, IGDBWorker

DETAIL_FIELDS = (
    "fields name,summary,storyline,aggregated_rating,total_rating,rating_count,"
    "first_release_date,genres.name,themes.name,game_modes.name,"
    "player_perspectives.name,platforms.name,"
    "involved_companies.company.name,involved_companies.developer,"
    "involved_companies.publisher,cover.url,screenshots.url,"
    "videos.video_id,url,age_ratings.organization,age_ratings.rating_category,"
    "external_games.category,external_games.external_game_source,"
    "external_games.uid;"
)

STEAM_EXTERNAL_CATEGORY = 1

# IGDB espone organization 1 = ESRB, 2 = PEGI. rating_category e' un enum
# globale: ESRB 1..7, PEGI 8..12. Teniamo solo questi due enti.
AGE_RATING_LABELS = {
    1: {1: "RP", 2: "EC", 3: "E", 4: "E10+", 5: "T", 6: "M", 7: "AO"},
    2: {8: "3", 9: "7", 10: "12", 11: "16", 12: "18"},
}


def _https(url):
    url = str(url or "")
    if url.startswith("//"):
        return "https:" + url
    return url


def _big_cover(url):
    url = _https(url)
    if not url:
        return ""
    for size in ("t_thumb", "t_cover_small", "t_cover_big"):
        if size in url:
            return url.replace(size, "t_cover_big_2x")
    return url


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _base_name(text):
    text = re.sub(
        r"(?i)(game of the year|complete edition|ultimate edition|"
        r"director\'s cut|deluxe edition|sunset edition|digital edition|"
        r"gold edition|standard edition|remastered|remake)",
        "",
        text or "",
    )
    return _norm(text)


def _match_score(query, name):
    """2 = match affidabile, 1 = parziale, 0 = no."""
    nq, nn = _norm(query), _norm(name)
    if not nq or not nn:
        return 0
    if nq == nn:
        return 2
    bq, bn = _base_name(query), _base_name(name)
    if bq and bq == bn:
        return 2
    if len(bq) >= 4 and (bq in bn or bn in bq):
        return 1
    return 0


def _age_ratings(items):
    """Estrae {"esrb": "M", "pegi": "18"} da age_ratings IGDB."""
    out = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            org = int(item.get("organization"))
            category = int(item.get("rating_category"))
        except (TypeError, ValueError):
            continue
        label = AGE_RATING_LABELS.get(org, {}).get(category)
        if not label:
            continue
        if org == 1:
            out["esrb"] = label
        elif org == 2:
            out["pegi"] = label
    return out


def _names(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            name = item.get("name")
            if name:
                out.append(str(name))
        elif item:
            out.append(str(item))
    return out


def parse_details(game):
    """Flatten one IGDB game object into the details cache shape."""
    release = game.get("first_release_date")
    release_str = ""
    if release:
        try:
            release_str = time.strftime("%Y-%m-%d", time.gmtime(int(release)))
        except (ValueError, OSError):
            release_str = ""

    developers, publishers = [], []
    for item in game.get("involved_companies") or []:
        if not isinstance(item, dict):
            continue
        company = item.get("company") or {}
        name = str(company.get("name") or "").strip()
        if not name:
            continue
        if item.get("developer"):
            developers.append(name)
        if item.get("publisher"):
            publishers.append(name)

    screenshots = []
    for shot in game.get("screenshots") or []:
        url = _https((shot or {}).get("url"))
        if url:
            screenshots.append(url.replace("t_thumb", "t_screenshot_big"))

    videos = [str(v.get("video_id")) for v in (game.get("videos") or [])
              if isinstance(v, dict) and v.get("video_id")]

    steam_appid = ""
    for ext in game.get("external_games") or []:
        if not isinstance(ext, dict):
            continue
        try:
            source = int(ext.get("external_game_source") or ext.get("category") or -1)
        except (TypeError, ValueError):
            source = -1
        uid = str(ext.get("uid") or "").strip()
        if source == STEAM_EXTERNAL_CATEGORY and uid.isdigit():
            steam_appid = uid
            break

    return {
        "igdb_id": game.get("id"),
        "name": game.get("name") or "",
        "summary": (game.get("summary") or "").strip(),
        "storyline": (game.get("storyline") or "").strip(),
        "aggregated_rating": round(game.get("aggregated_rating") or 0, 1),
        "rating": round(game.get("total_rating") or 0, 1),
        "rating_count": game.get("rating_count") or 0,
        "release_date": release_str,
        "release_year": int(release_str[:4]) if release_str else 0,
        "genres": _names(game.get("genres")),
        "themes": _names(game.get("themes")),
        "modes": _names(game.get("game_modes")),
        "perspectives": _names(game.get("player_perspectives")),
        "platforms": _names(game.get("platforms")),
        "developers": developers,
        "publishers": publishers,
        "cover_url": _big_cover((game.get("cover") or {}).get("url")),
        "screenshots": screenshots,
        "videos": videos,
        "url": _https(game.get("url")),
        "age_ratings": _age_ratings(game.get("age_ratings")),
        "steam_appid": steam_appid,
    }


class IGDBDetailsWorker(QThread):

    details_ready = pyqtSignal(str, dict)   # title, details ({} se non trovato)

    def __init__(self, title, igdb_id="", appid="", parent=None):
        super().__init__(parent)
        self._title = title
        self._igdb_id = str(igdb_id or "").strip()
        self._appid = str(appid or "").strip()

    def _where(self):
        if self._igdb_id.isdigit():
            return f"where id = {int(self._igdb_id)};"
        if self._appid.isdigit():
            return f'where external_games.uid = "{int(self._appid)}";'
        return ""

    def _query(self, headers, body):
        try:
            r = requests.post(f"{IGDB_BASE}/games", headers=headers,
                              data=body, timeout=12)
            if r.status_code != 200:
                print(f"[IGDBDetails] query status {r.status_code} for '{self._title}'")
                return []
            games = r.json() or []
            return [g for g in games if isinstance(g, dict)]
        except Exception as e:
            print(f"[IGDBDetails] error for '{self._title}': {e}")
            return []

    def _search_best(self, headers):
        """Fallback per titoli senza igdb_id/appid (es. StarCraft Remastered).

        IGDB ce l'ha per nome: cerchiamo e accettiamo solo un match affidabile,
        cosi' non rischiamo di mostrare la scheda di un altro gioco.
        """
        term = re.sub(r'["\r\n]', " ", self._title or "").strip()
        if not term:
            return None
        body = f'search "{term}"; {DETAIL_FIELDS} limit 10;'
        games = self._query(headers, body)

        best = None
        best_score = 0
        for game in games:
            score = _match_score(self._title, game.get("name") or "")
            if score > best_score:
                best = game
                best_score = score
                if score >= 2:
                    break
        if best is None:
            return None
        # Solo match affidabili: meglio nessun dato che la scheda sbagliata.
        return best if best_score >= 2 else None

    def run(self):
        base = IGDBWorker()
        if not base._load_creds() or not base._ensure_token():
            self.details_ready.emit(self._title, {})
            return
        headers = base._headers()

        where = self._where()
        if where:
            games = self._query(headers, f"{DETAIL_FIELDS} {where} limit 1;")
            game = games[0] if games else None
        else:
            game = self._search_best(headers)

        if not game:
            self.details_ready.emit(self._title, {})
            return

        self.details_ready.emit(self._title, parse_details(game))

    @staticmethod
    def _parse(game):
        return parse_details(game)
