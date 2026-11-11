import os
import json
import re
import threading
import time
import unicodedata
import difflib
import requests

from core.paths import PathManager

# Temporary: the gold tier list still lives in the current closed-source public
# repo. When the open-source repo ships lists/gold_tier_games.json and the
# closed-source repo is retired, update this URL.
GOLD_TIER_URL = "https://raw.githubusercontent.com/Shady-Unknown/ae-thicc-launcher/main/lists/gold_tier_games.json"
LOCAL_FILENAME = "gold_tier_games.json"
META_FILENAME = "gold_tier_meta.json"
_UA = "AeThiccLauncher/1.0 (gold-tier-resolver)"


class GoldTierManager:
    _instance = None
    _gold_data: dict = {}
    _initialized: bool = False
    _lookup: dict[str, str] = {}
    _normalized_keys: list[str] = []
    _steam_id_lookup: dict[str, str] = {}
    _debug_printed: set = set()

    # Remote refresh runs on a daemon thread: never block the GUI thread with
    # HTTP (a blocked raw.githubusercontent.com used to freeze startup for the
    # full requests timeout, ~10s, before the window was even shown).
    _last_fetch_time: float = 0.0
    _FETCH_COOLDOWN: float = 24 * 3600.0  # at most one HEAD check per day
    _refresh_lock = threading.Lock()
    _refresh_inflight: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def ensure_fetched(cls):
        """Load the local list immediately and refresh remotely in background."""
        if not cls._initialized:
            cls._load_local_only()
            # Mark initialized even when the local file is missing: the worker
            # will populate the data shortly and we must never retry synchronously.
            cls._initialized = True
        cls._schedule_refresh()

    @classmethod
    def _load_local_only(cls):
        dbs_dir = PathManager.get_dbs_dir()
        dbs_dir.mkdir(parents=True, exist_ok=True)
        local_path = dbs_dir / LOCAL_FILENAME
        data = {}
        if local_path.exists():
            data = cls._load_local(local_path)
        else:
            # First run: seed from the list shipped with the app (lists/ or data/).
            try:
                from core.config import get_base_dir
                base = get_base_dir()
                for candidate in (
                    os.path.join(base, "lists", LOCAL_FILENAME),
                    os.path.join(base, "data", LOCAL_FILENAME),
                ):
                    if os.path.isfile(candidate):
                        data = cls._load_local(candidate)
                        if data:
                            with open(local_path, "w", encoding="utf-8") as f:
                                json.dump(data, f, indent=2, ensure_ascii=False)
                            break
            except Exception as exc:
                print(f"[GoldTier] seed failed: {exc}")
        cls._apply_data(data)

    @classmethod
    def _apply_data(cls, data):
        cls._gold_data = cls._normalize_data(data if isinstance(data, dict) else {})
        cls._build_lookup()
        if cls._gold_data:
            print(f"[GoldTier] Loaded {len(cls._gold_data)} entries")
        else:
            print("[GoldTier] Data is empty — will retry in background")

    @classmethod
    def _schedule_refresh(cls):
        now = time.time()
        if now - cls._last_fetch_time < cls._FETCH_COOLDOWN:
            return
        with cls._refresh_lock:
            if cls._refresh_inflight:
                return
            cls._refresh_inflight = True
            cls._last_fetch_time = now
        thread = threading.Thread(
            target=cls._refresh_worker,
            name="gold-tier-refresh",
            daemon=True,
        )
        thread.start()

    @classmethod
    def _refresh_worker(cls):
        """HEAD/GET with ETag negotiation; never raises and never touches Qt."""
        try:
            dbs_dir = PathManager.get_dbs_dir()
            dbs_dir.mkdir(parents=True, exist_ok=True)
            local_path = dbs_dir / LOCAL_FILENAME
            meta_path = dbs_dir / META_FILENAME

            local_meta = {}
            if meta_path.exists():
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        local_meta = json.load(f)
                except Exception:
                    local_meta = {}

            needs_download = True
            if local_path.exists():
                needs_download = False
                try:
                    resp = requests.head(GOLD_TIER_URL, timeout=10, headers={"User-Agent": _UA})
                    if resp.status_code == 200:
                        remote_etag = resp.headers.get("ETag") or ""
                        remote_last_modified = resp.headers.get("Last-Modified") or ""
                        local_etag = local_meta.get("etag", "")
                        local_last_modified = local_meta.get("last_modified", "")
                        if remote_etag and remote_etag == local_etag:
                            needs_download = False
                        elif remote_last_modified and remote_last_modified == local_last_modified:
                            needs_download = False
                        else:
                            needs_download = True
                    else:
                        print(f"[GoldTier] HEAD returned {resp.status_code}, using cached")
                        needs_download = False
                except Exception as exc:
                    print(f"[GoldTier] HEAD failed ({exc}), using cached")
                    needs_download = False

            if not needs_download:
                return

            try:
                resp = requests.get(GOLD_TIER_URL, timeout=15, headers={"User-Agent": _UA})
                if resp.status_code != 200:
                    print(f"[GoldTier] GET returned {resp.status_code}, falling back to cache")
                    return
                data = resp.json()
                with open(local_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                new_meta = {}
                etag = resp.headers.get("ETag")
                last_modified = resp.headers.get("Last-Modified")
                if etag:
                    new_meta["etag"] = etag
                if last_modified:
                    new_meta["last_modified"] = last_modified
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(new_meta, f)
                cls._apply_data(data if isinstance(data, dict) else {})
            except Exception as exc:
                print(f"[GoldTier] GET failed ({exc}), using cached")
        finally:
            with cls._refresh_lock:
                cls._refresh_inflight = False

    @staticmethod
    def _normalize_data(data: dict) -> dict:
        if "gold_tier" in data and isinstance(data["gold_tier"], list):
            rebuilt = {}
            for entry in data["gold_tier"]:
                if isinstance(entry, dict) and entry.get("title"):
                    rebuilt[entry["title"]] = entry
            print(f"[GoldTier] Rebuilt list into dict: {len(rebuilt)} entries")
            return rebuilt
        if data:
            sample = next(iter(data.values()))
            if isinstance(sample, dict) and "title" in sample and "ethical_reason" in sample:
                rebuilt = {}
                for key, entry in data.items():
                    if isinstance(entry, dict) and entry.get("title"):
                        rebuilt[entry["title"]] = entry
                    else:
                        rebuilt[key] = entry
                print(f"[GoldTier] Rebuilt from indexed dict: {len(rebuilt)} entries")
                return rebuilt
        return data

    @classmethod
    def _build_lookup(cls):
        cls._lookup = {}
        cls._steam_id_lookup = {}
        for key in cls._gold_data:
            norm = cls._normalize(key)
            cls._lookup[norm] = key
            entry = cls._gold_data[key]
            if isinstance(entry, dict):
                sid = str(entry.get("steam_id", "") or "")
                if sid:
                    cls._steam_id_lookup[sid] = key
        cls._normalized_keys = sorted(cls._lookup.keys(), key=len, reverse=True)

    @staticmethod
    def _normalize(s: str) -> str:
        s = re.sub(r"[™®©]", "", s)
        s = unicodedata.normalize("NFKD", s)
        s = s.encode("ascii", "ignore").decode("ascii")
        s = re.sub(r"[^\w\s-]", "", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s.lower()

    @staticmethod
    def _load_local(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @classmethod
    def _resolve_title(cls, game_title: str) -> str | None:
        if not game_title:
            return None

        norm = cls._normalize(game_title)

        if norm in cls._lookup:
            return cls._lookup[norm]

        matches = difflib.get_close_matches(norm, cls._normalized_keys, n=1, cutoff=0.80)
        if matches:
            return cls._lookup[matches[0]]

        if game_title not in cls._debug_printed:
            cls._debug_printed.add(game_title)
            sample_keys = list(cls._gold_data.keys())[:5]
            print(f"[GoldTier] No title match for '{game_title}' (normalized: '{norm}')")
            print(f"[GoldTier] First 5 gold keys: {sample_keys}")

        return None

    @classmethod
    def _resolve_steam_id(cls, steam_id: str) -> str | None:
        if not steam_id:
            return None
        sid = str(steam_id).strip()
        return cls._steam_id_lookup.get(sid)

    @classmethod
    def is_gold_tier(cls, game_title: str) -> bool:
        cls.ensure_fetched()
        return cls._resolve_title(game_title) is not None

    @classmethod
    def is_gold_by_steam_id(cls, steam_id: str) -> bool:
        cls.ensure_fetched()
        return cls._resolve_steam_id(steam_id) is not None

    @classmethod
    def get_gold_reason(cls, game_title: str) -> str | None:
        cls.ensure_fetched()
        resolved = cls._resolve_title(game_title)
        if resolved is None:
            return None
        entry = cls._gold_data[resolved]
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            return entry.get("ethical_reason") or entry.get("reason") or str(entry)
        return str(entry)

    @classmethod
    def get_reason_by_steam_id(cls, steam_id: str) -> str | None:
        cls.ensure_fetched()
        resolved = cls._resolve_steam_id(steam_id)
        if resolved is None:
            return None
        entry = cls._gold_data[resolved]
        if isinstance(entry, str):
            return entry
        if isinstance(entry, dict):
            return entry.get("ethical_reason") or entry.get("reason") or str(entry)
        return str(entry)
