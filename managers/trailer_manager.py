import json
import os
import time

from core.paths import PathManager

TRAILER_CACHE_FILENAME = "trailer_cache.json"
CACHE_TTL_SECONDS = 7 * 24 * 3600
CACHE_VERSION = 3


class TrailerManager:
    """Persistent cache for resolved game trailer URLs (keyed by Steam appid)."""

    @staticmethod
    def _cache_path():
        d = PathManager.get_app_data_dir()
        os.makedirs(str(d), exist_ok=True)
        return os.path.join(str(d), TRAILER_CACHE_FILENAME)

    @staticmethod
    def load_cache():
        try:
            with open(TrailerManager._cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def save_cache(cache):
        try:
            with open(TrailerManager._cache_path(), "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except Exception as e:
            print(f"[TrailerCache] Failed to save cache: {e}")

    @staticmethod
    def get_trailer(appid, cache=None):
        if not appid:
            return None
        if cache is None:
            cache = TrailerManager.load_cache()
        entry = cache.get(str(appid))
        if not isinstance(entry, dict):
            return None
        if entry.get("ts", 0) < time.time() - CACHE_TTL_SECONDS:
            return None
        if entry.get("v") != CACHE_VERSION:
            return None
        hls = entry.get("hls")
        mp4 = entry.get("mp4")
        if not hls and not mp4:
            return None
        return {
            "hls": hls,
            "mp4": mp4,
            "webm": entry.get("webm"),
            "variants": entry.get("variants"),
        }

    @staticmethod
    def put_trailer(appid, trailer, cache=None):
        if not appid or not trailer:
            return
        if cache is None:
            cache = TrailerManager.load_cache()
        cache[str(appid)] = {
            "ts": time.time(),
            "v": CACHE_VERSION,
            "hls": trailer.get("hls"),
            "mp4": trailer.get("mp4"),
            "webm": trailer.get("webm"),
            "variants": trailer.get("variants"),
        }
        TrailerManager.save_cache(cache)

    @staticmethod
    def is_fresh(appid, cache=None):
        if not appid:
            return False
        if cache is None:
            cache = TrailerManager.load_cache()
        entry = cache.get(str(appid))
        if not isinstance(entry, dict):
            return False
        if entry.get("v") != CACHE_VERSION:
            return False
        return entry.get("ts", 0) >= time.time() - CACHE_TTL_SECONDS
