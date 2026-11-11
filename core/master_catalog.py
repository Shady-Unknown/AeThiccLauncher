import json
import os
import time
from PyQt6.QtCore import QObject, QTimer

from core.meta_utils import extract_steam_id_from_store_url

CATALOG_TTL = 12 * 60 * 60
PRICES_TTL = 24 * 60 * 60
DEBOUNCE_MS = 3000

ENRICHMENT_FIELDS = {
    "steam_id",
    "store_urls",
    "prices",
    "sgdb_assets",
    "version_status",
    "steamrip_categories",
    "igdb_meta",
    "gold_tier",
}


class MasterCatalog(QObject):
    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self._db_path = db_path
        self._data = {}
        self._dirty = False

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._flush)

        self.load()

    # --- Loading / Saving ---

    def load(self):
        if os.path.exists(self._db_path):
            try:
                with open(self._db_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    normalized = {}
                    for k, v in loaded.items():
                        if isinstance(v, str):
                            normalized[k] = {"url": v}
                        elif isinstance(v, dict):
                            normalized[k] = v
                        else:
                            normalized[k] = {"url": str(v)}
                    self._data = normalized
                print(f"[MasterCatalog] Loaded {len(self._data)} games")
            except Exception as e:
                print(f"[MasterCatalog] Load error: {e}")
                self._data = {}

    def _flush(self):
        t0 = time.perf_counter()
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with open(self._db_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, separators=(",", ":"), ensure_ascii=False)
            self._dirty = False
        except Exception as e:
            print(f"[MasterCatalog] Save error: {e}")
        dt = (time.perf_counter() - t0) * 1000.0
        if os.environ.get("SRP_DEBUG_PERF") == "1" and dt > 40:
            print(f"[PERF] master_catalog_flush: {dt:.0f}ms")

    def _debounce_save(self):
        self._dirty = True
        self._save_timer.stop()
        self._save_timer.start()

    def save_immediate(self):
        self._save_timer.stop()
        self._flush()

    # --- TTL ---

    def needs_refresh(self):
        if not os.path.exists(self._db_path):
            return True
        age = time.time() - os.path.getmtime(self._db_path)
        return age > CATALOG_TTL

    # --- Dict-like access (backward-compatible for title→url) ---

    def __len__(self):
        return len(self._data)

    def __bool__(self):
        return len(self._data) > 0

    def __contains__(self, key):
        return key in self._data

    def __getitem__(self, key):
        entry = self._data[key]
        if isinstance(entry, dict) and "url" in entry:
            return entry["url"]
        return entry

    def __iter__(self):
        return iter(self._data)

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        if isinstance(entry, dict) and "url" in entry:
            return entry["url"]
        return entry

    def as_dict(self):
        return self._data

    def get_entry(self, title):
        return self._data.get(title)

    def get_url(self, title):
        entry = self._data.get(title)
        if isinstance(entry, dict):
            return entry.get("url", "")
        return entry if isinstance(entry, str) else ""

    # --- Merge-on-refresh ---

    def refresh_from_remote(self, remote_dict):
        new_catalog = {}
        for title, value in remote_dict.items():
            if isinstance(value, str):
                new_catalog[title] = {"url": value}
            elif isinstance(value, dict):
                new_catalog[title] = dict(value)
            else:
                new_catalog[title] = {"url": str(value)}

        merged = {}
        for title, new_entry in new_catalog.items():
            if title in self._data:
                old_entry = self._data[title]
                merged_entry = dict(new_entry)
                for field in ENRICHMENT_FIELDS:
                    if field in old_entry:
                        merged_entry[field] = old_entry[field]
                merged[title] = merged_entry
            else:
                merged[title] = new_entry

        for title, old_entry in self._data.items():
            if title not in merged:
                merged[title] = dict(old_entry)

        self._data = merged
        self._debounce_save()
        return len(self._data)

    # --- Enrichment helpers (RAM instant, disk debounced) ---

    def set_game_field(self, title, field, value):
        if title not in self._data:
            self._data[title] = {"url": ""}
        self._data[title][field] = value
        self._debounce_save()

    def get_game_field(self, title, field, default=None):
        entry = self._data.get(title)
        if not isinstance(entry, dict):
            return default
        return entry.get(field, default)

    def set_url(self, title, url):
        if title in self._data and isinstance(self._data[title], dict):
            self._data[title]["url"] = url
        else:
            self._data[title] = {"url": url}
        self._debounce_save()

    def set_steam_app_id(self, title, steam_app_id):
        self.set_game_field(title, "steam_app_id", steam_app_id)

    def get_steam_app_id(self, title):
        return self.get_game_field(title, "steam_app_id", "")

    def set_store_urls(self, title, steam_url, gog_url):
        self.set_game_field(title, "store_urls", {
            "steam": steam_url,
            "gog": gog_url,
        })
        if steam_url:
            sid = self.get_game_field(title, "steam_id", "")
            if not sid:
                extracted = extract_steam_id_from_store_url(steam_url)
                if extracted:
                    self.set_game_field(title, "steam_id", extracted)

    def get_store_urls(self, title):
        result = self.get_game_field(title, "store_urls", {})
        if isinstance(result, dict):
            return result
        return {}

    def set_prices(self, title, steam_data, gog_data, region=None):
        prices = {
            "steam": steam_data,
            "gog": gog_data,
            "last_checked": time.time(),
        }
        if region:
            prices["region"] = str(region).upper()
        self.set_game_field(title, "prices", prices)

    def is_prices_stale(self, title, region=None):
        prices = self.get_game_field(title, "prices", {})
        if not isinstance(prices, dict):
            return True
        if region and str(prices.get("region") or "").upper() != str(region).upper():
            return True
        last_checked = prices.get("last_checked", 0)
        return (time.time() - last_checked) > PRICES_TTL

    def get_prices(self, title):
        result = self.get_game_field(title, "prices", {})
        if isinstance(result, dict):
            return result
        return {}

    def set_sgdb_assets(self, title, steam_app_id, grid_url="", hero_url="", logo_url="", wide_url="", mark_checked=False):
        previous = self.get_sgdb_assets(title)
        if not isinstance(previous, dict):
            previous = {}
        # Non azzerare asset già in cache se il chiamante non ne fornisce di nuovi.
        payload = {
            "steam_app_id": steam_app_id or previous.get("steam_app_id", ""),
            "grid": grid_url or previous.get("grid", ""),
            "hero": hero_url or previous.get("hero", ""),
            "logo": logo_url or previous.get("logo", ""),
            "wide": wide_url or previous.get("wide", ""),
        }
        # Conserva il timestamp dell'ultima verifica SGDB (serve a non ripetere
        # la ricerca API a ogni visualizzazione quando SGDB non ha copertine).
        checked_at = previous.get("checked_at")
        if mark_checked:
            checked_at = time.time()
        if checked_at:
            payload["checked_at"] = float(checked_at)
        self.set_game_field(title, "sgdb_assets", payload)

    def is_sgdb_checked_recently(self, title, max_age_seconds=7 * 24 * 3600):
        assets = self.get_sgdb_assets(title)
        if not isinstance(assets, dict):
            return False
        try:
            checked_at = float(assets.get("checked_at") or 0)
        except (TypeError, ValueError):
            return False
        return checked_at > 0 and (time.time() - checked_at) < max_age_seconds

    def get_sgdb_assets(self, title):
        result = self.get_game_field(title, "sgdb_assets", {})
        if isinstance(result, dict):
            return result
        return {}

    # --- Version Status (build SteamRIP vs Steam) ---

    def set_version_status(self, title, payload):
        """Salva/aggiorna i dati del widget Version Status (lato sito)."""
        if not isinstance(payload, dict) or not payload:
            return
        merged = dict(self.get_version_status(title))
        for key, value in payload.items():
            if value in (None, "", [], {}):
                merged.setdefault(key, value)
                continue
            merged[key] = value
        merged["site_checked_at"] = time.time()
        self.set_game_field(title, "version_status", merged)

    def update_version_status_steam(self, title, build, date_str="", timestamp=0):
        """Aggiorna solo il lato Steam (build pubblica su Steam)."""
        merged = dict(self.get_version_status(title))
        try:
            merged["steam_build"] = int(build)
        except (TypeError, ValueError):
            return
        if date_str:
            merged["steam_build_date"] = date_str
        merged["steam_build_ts"] = int(timestamp or 0)
        merged["steam_build_source"] = "steamcmd"
        merged["steam_checked_at"] = time.time()
        self.set_game_field(title, "version_status", merged)

    def get_version_status(self, title):
        result = self.get_game_field(title, "version_status", {})
        if isinstance(result, dict):
            return result
        return {}

    def is_steam_build_stale(self, title, max_age_seconds=24 * 3600):
        status = self.get_version_status(title)
        try:
            checked_at = float(status.get("steam_checked_at") or 0)
        except (TypeError, ValueError):
            checked_at = 0
        return checked_at <= 0 or (time.time() - checked_at) > max_age_seconds

    def set_gold_tier(self, title, is_gold, reason=""):
        self.set_game_field(title, "gold_tier", {
            "is_gold": is_gold,
            "reason": reason,
        })

    def is_gold_tier(self, title):
        entry = self.get_game_field(title, "gold_tier", {})
        if isinstance(entry, dict):
            return entry.get("is_gold", False)
        return False

    def get_gold_reason(self, title):
        entry = self.get_game_field(title, "gold_tier", {})
        if isinstance(entry, dict):
            return entry.get("reason", "")
        return ""
