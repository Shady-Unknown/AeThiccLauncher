import re
import requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urljoin

from PyQt6.QtCore import QThread, pyqtSignal

from managers.trailer_manager import TrailerManager

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class TrailerWorker(QThread):

    trailers_ready = pyqtSignal(dict)

    def __init__(self, items, parent=None):
        """items: list of {"title": str, "meta": dict}."""
        super().__init__(parent)
        self._items = list(items)
        self._headers = {"User-Agent": UA}

    # ── appid resolution ─────────────────────────────────────────────

    @staticmethod
    def _clean_text(text):
        text = re.sub(r'(?i)(game of the year|complete edition|ultimate edition|'
                      r"director\'s cut|deluxe edition|sunset edition|"
                      r'digital edition|gold edition|standard edition)', '', text)
        return re.sub(r'[^a-z0-9]', '', (text or "").lower())

    @staticmethod
    def _match_score(search_title, result_title):
        s = TrailerWorker._clean_text(search_title)
        r = TrailerWorker._clean_text(result_title)
        if not s or not r:
            return 0
        if s == r:
            return 2
        if len(s) >= 3:
            if s in r or r in s:
                return 1
        return 0

    @staticmethod
    def _appid_from_meta(meta):
        for key in ("steam_id", "steam_app_id", "appid"):
            val = str(meta.get(key) or "").strip()
            if val.isdigit():
                return val
        return None

    @staticmethod
    def _appid_from_urls(meta):
        pattern = re.compile(r'(?:store\.steampowered\.com/app/|steamappid[/=])(\d+)')
        for key in ("source_url", "page_url", "url", "store_url", "steam_url"):
            val = str(meta.get(key) or "")
            m = pattern.search(val)
            if m:
                return m.group(1)
        return None

    def _resolve_appid(self, meta):
        appid = self._appid_from_meta(meta) or self._appid_from_urls(meta)
        if appid:
            return appid
        return self._search_appid(str(meta.get("title") or ""))

    def _search_appid(self, title):
        try:
            clean = re.sub(r'\(.*?\)', '', title).strip()
            if not clean:
                return None
            url = ("https://store.steampowered.com/api/storesearch/"
                   f"?term={quote(clean)}&l=english&cc=US")
            r = requests.get(url, headers=self._headers, timeout=4)
            if r.status_code != 200:
                return None
            data = r.json()
            best_id = None
            best_score = 0
            for item in data.get("items", [])[:10]:
                score = self._match_score(clean, item.get("name", ""))
                if score == 2:
                    return str(item.get("id"))
                if score == 1 and best_score < 1:
                    best_id = str(item.get("id"))
                    best_score = 1
            return best_id
        except Exception as e:
            print(f"[TrailerWorker] Search error for '{title}': {e}")
            return None

    # ── appdetails fetch ─────────────────────────────────────────────

    @staticmethod
    def _parse_variants(playlist, master_url):
        """Parse an HLS master playlist into [{"label", "url"}] video variants."""
        variants = []
        info = None
        for raw in (playlist or "").splitlines():
            line = raw.strip()
            if line.startswith("#EXT-X-STREAM-INF:"):
                info = line
                continue
            if info is not None and line and not line.startswith("#"):
                m = re.search(r"RESOLUTION=(\d+)x(\d+)", info)
                if m:
                    w, h = int(m.group(1)), int(m.group(2))
                    if h in (144, 240, 360, 480, 720, 1080, 1440, 2160):
                        label = f"{h}p"
                    else:
                        label = f"{w}x{h}"
                else:
                    b = re.search(r"BANDWIDTH=(\d+)", info)
                    label = f"{int(b.group(1)) // 1000}k" if b else "Auto"
                variants.append({"label": label, "url": urljoin(master_url, line)})
                info = None
        seen = set()
        deduped = []
        for v in variants:
            if v["label"] not in seen:
                seen.add(v["label"])
                deduped.append(v)
        return deduped

    def _fetch_variants(self, master_url):
        try:
            r = requests.get(master_url, headers=self._headers, timeout=8)
            if r.status_code != 200:
                return None
            variants = TrailerWorker._parse_variants(r.text, master_url)
            return variants or None
        except Exception as e:
            print(f"[TrailerWorker] variant fetch error: {e}")
            return None

    @staticmethod
    def _extract_trailer(app_data):
        movies = (app_data or {}).get("movies") or []
        for movie in movies:
            hls = movie.get("hls_h264")
            mp4 = movie.get("mp4") or {}
            webm = movie.get("webm") or {}
            mp4_url = mp4.get("max") or mp4.get("480") or ""
            webm_url = webm.get("max") or webm.get("480") or ""
            if hls:
                # Teniamo anche l'MP4 progressivo: l'HLS/DASH di Steam non
                # permette il seek con il backend FFmpeg.
                return {"hls": hls, "mp4": mp4_url, "webm": webm_url}
            if mp4_url:
                return {"mp4": mp4_url, "webm": webm_url}
        return None

    def _fetch_appids(self, appids):
        results = {}

        def fetch_one(appid):
            try:
                url = ("https://store.steampowered.com/api/appdetails?"
                       f"appids={appid}&cc=US&l=english")
                r = requests.get(url, headers=self._headers, timeout=8)
                if r.status_code != 200:
                    return appid, None
                data = r.json()
                entry = data.get(appid) or data.get(str(appid))
                if entry and entry.get("success"):
                    trailer = TrailerWorker._extract_trailer(entry.get("data"))
                    if trailer and trailer.get("hls"):
                        variants = self._fetch_variants(trailer["hls"])
                        if variants:
                            trailer["variants"] = variants
                    return appid, trailer
            except Exception as e:
                print(f"[TrailerWorker] appdetails error ({appid}): {e}")
            return appid, None

        with ThreadPoolExecutor(max_workers=6) as pool:
            for appid, trailer in pool.map(fetch_one, appids):
                results[appid] = trailer
        return results

    def run(self):
        cache = TrailerManager.load_cache()
        resolved = {}

        pending_appids = []
        for item in self._items:
            title = str(item.get("title") or "")
            meta = item.get("meta") or {}
            if not title:
                continue
            appid = self._resolve_appid(meta)
            if not appid:
                resolved[title] = None
                continue
            if TrailerManager.is_fresh(appid, cache):
                trailer = TrailerManager.get_trailer(appid, cache)
                if trailer:
                    resolved[title] = trailer
                    continue
            pending_appids.append((title, appid))

        if pending_appids:
            unique = sorted({a for _, a in pending_appids})
            fetched = self._fetch_appids(unique)
            for title, appid in pending_appids:
                trailer = fetched.get(appid)
                if trailer:
                    TrailerManager.put_trailer(appid, trailer, cache)
                resolved[title] = trailer

        found = sum(1 for t in resolved.values() if t)
        print(f"[TrailerWorker] resolved {found}/{len(resolved)} trailers")
        self.trailers_ready.emit(resolved)
