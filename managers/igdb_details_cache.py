import json
import os
import time

from core.paths import PathManager

FILENAME = "igdb_details.json"
CACHE_TTL = 30 * 24 * 3600
CACHE_SCHEMA = 2   # alza per invalidare le voci senza age_ratings


class IgdbDetailsCache:
    """Cache su disco dei dettagli IGDB mostrati nel popup scheda.

    Tenuta separata da master_catalog.json: i summary sono testi lunghi e
    non devono gonfiare il catalogo (che viene riscritto spesso).
    """

    _data = None

    @staticmethod
    def _path():
        d = PathManager.get_app_data_dir()
        os.makedirs(str(d), exist_ok=True)
        return os.path.join(str(d), FILENAME)

    @classmethod
    def _load(cls):
        if cls._data is not None:
            return cls._data
        try:
            with open(cls._path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            cls._data = data if isinstance(data, dict) else {}
        except Exception:
            cls._data = {}
        return cls._data

    @classmethod
    def get(cls, title, max_age=CACHE_TTL):
        entry = cls._load().get(title or "")
        if not isinstance(entry, dict):
            return None
        if entry.get("schema") != CACHE_SCHEMA:
            return None
        try:
            age = time.time() - float(entry.get("fetched_at") or 0)
        except (TypeError, ValueError):
            age = CACHE_TTL + 1
        if max_age and age > max_age:
            return None
        details = entry.get("details")
        return details if isinstance(details, dict) else None

    @classmethod
    def set(cls, title, details):
        cls.set_many({title: details})

    @classmethod
    def set_many(cls, items):
        """Update several titles and write the cache file once."""
        if not isinstance(items, dict):
            return
        data = cls._load()
        now = time.time()
        changed = False
        for title, details in items.items():
            if not title or not isinstance(details, dict):
                continue
            data[title] = {"fetched_at": now, "schema": CACHE_SCHEMA,
                           "details": details}
            changed = True
        if not changed:
            return
        try:
            with open(cls._path(), "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        except Exception as e:
            print(f"[IgdbDetails] save error: {e}")
