import json
import os
import re
import time
import functools

from PyQt6.QtCore import QObject, Q_ARG, QMetaObject, Qt, QTimer, pyqtSignal

from core import secrets
from core.mini_cover_cache import MiniCoverManager
from core.paths import PathManager
from managers.save_manager import safe_game_folder_name
from network.igdb_worker import IGDB_CATEGORIES, MAX_OFFSET, PAGE_SIZE
from network.steamrip_api import (
    STEAMRIP_CATEGORIES,
    SteamRipBatchWorker,
    SteamRipIndexWorker,
    SteamRipPostsWorker,
)
from network.igdb_meta_worker import IGDBMetaWorker, load_taxonomy
from network.posts_index import PostsIndexWorker

CACHE_FILENAME = "discovery_cache.json"
CATEGORY_INDEX_FILENAME = "discovery_categories.json"
CACHE_TTL_SECONDS = 24 * 3600
RECENT_TTL_SECONDS = 3600
CATEGORY_INDEX_TTL_SECONDS = 24 * 3600
CACHE_SCHEMA = 5
CATALOG_CHUNK = 40

# Ordinamento scelto dall'utente. Vale per tutte le righe tranne quelle la
# cui identita' e' una data/ordine cronologico: New Releases, Recently
# Added/Updated e All Games mantengono l'ordine del sito. Trending, My
# Library e tutte le categorie SteamRip sono ordinabili.
SORT_MODES = ("", "title_asc", "title_desc", "added_desc", "added_asc",
              "release_desc", "release_asc", "rating_desc")
NON_SORTABLE_KEYS = frozenset({
    "new_releases", "recent_updates", "recent_added", "all_games",
})
EMPTY_PAGE_LIMIT = 5

PERF_LOG_MS = 40
PERF_DEBUG = os.environ.get('SRP_DEBUG_PERF') == '1'


def _perf(name):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                dt = (time.perf_counter() - t0) * 1000.0
                if PERF_DEBUG and dt > PERF_LOG_MS:
                    print(f"[PERF] {name}: {dt:.0f}ms")
        return wrapper
    return deco

# Le righe IGDB che restano (il resto passa alle categorie native SteamRip)
IGDB_ROW_KEYS = ("trending", "new_releases")


def clean_text(text):
    text = re.sub(r'(?i)(game of the year|complete edition|ultimate edition|'
                  r"director\'s cut|deluxe edition|sunset edition|"
                  r'digital edition|gold edition|standard edition)', '', text)
    return re.sub(r'[^a-z0-9]', '', (text or "").lower())


class DiscoveryManager(QObject):
    """Orchestrates IGDB category data, cross-matching vs the master catalog,
    and lazy cover downloads for the Discovery tab."""

    categories_changed = pyqtSignal()
    page_changed = pyqtSignal(str)
    cover_ready = pyqtSignal(str, str)
    scrape_requested = pyqtSignal(str, str)
    error_occurred = pyqtSignal(str)
    meta_ready = pyqtSignal(dict)
    taxonomy_ready = pyqtSignal(dict)

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self._data = {}
        self._worker = None
        self._igdb_retry_scheduled = False
        self._cover_worker = None
        # One upgrade attempt per title/session when the cached cover is a
        # horizontal fallback; avoids hammering SGDB on every row rebuild.
        self._cover_upgrade_attempted = set()
        self._native_workers = []
        self._native_loading = set()
        self._index_worker = None
        self._category_index = None
        self._index = None
        self._index_token = None
        self._meta_worker = None
        self._meta_inflight = set()
        self._meta_queue = []
        self._taxonomy = None
        self._match_cache = {}
        self._card_index = None
        self._posts_index = {}
        self._posts_worker = None
        self._catalog_order = None
        self._filters = {"search": "", "hide_installed": False,
                         "sr_category": None, "igdb_genre": None}

        # Salvataggio cache debounced: il file è grande (~4 MB) e scriverlo
        # in modo sincrono a ogni pagina congelava la UI durante il drag.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1500)
        self._save_timer.timeout.connect(self._flush_cache)

    # ── setup ────────────────────────────────────────────────────────

    def _start_igdb(self):
        """Start the IGDB worker only when credentials are configured."""
        if not secrets.has_igdb_credentials():
            print("[Discovery] IGDB credentials not configured; rows hidden")
            return

        from network.igdb_worker import IGDBWorker

        igdb_keys = [c["key"] for c in IGDB_CATEGORIES if c["key"] in IGDB_ROW_KEYS]
        keys_to_fetch = [k for k in igdb_keys if self._needs_fetch(k)]
        worker = IGDBWorker(category_keys=igdb_keys,
                            initial_keys=keys_to_fetch,
                            parent=self)
        worker.categories_ready.connect(self._on_categories)
        worker.page_ready.connect(self._on_page)
        worker.error_occurred.connect(self.error_occurred)
        self._worker = worker
        worker.start()
        if not keys_to_fetch:
            print(f"[Discovery] IGDB rows from cache ({len(igdb_keys)} keys)")

    def start(self):
        from network.cover_worker import CoverWorker

        self._cover_worker = CoverWorker(
            installed_games=getattr(self.mw, 'installed_games', {}) or {},
            sync_folder=self._sync_folder(),
            parent=self,
        )
        self._cover_worker.cover_ready.connect(self.cover_ready)
        self._cover_worker.scrape_requested.connect(self.scrape_requested)
        self._cover_worker.start()

        self._load_cache()
        self._load_category_index()
        self._taxonomy = load_taxonomy()
        if self._taxonomy:
            self.taxonomy_ready.emit(self._taxonomy)

        self._start_igdb()

        # SteamRip: righe native (Recently Updated + categorie) paginate via REST.
        native_jobs = self._native_jobs_to_fetch()
        if native_jobs:
            batch = SteamRipBatchWorker(native_jobs, parent=self)
            batch.page_ready.connect(self._on_native_page)
            batch.error.connect(self._on_native_error)
            batch.finished.connect(lambda w=batch: self._cleanup_native_worker(w, None))
            self._native_workers.append(batch)
            batch.start()
        else:
            print("[Discovery] SteamRip rows from cache")

        # L'indice completo (51 richieste) è opzionale: la REST API rate-limita.
        if os.environ.get("SRP_DISCOVERY_INDEX") == "1":
            QTimer.singleShot(3000, self._start_category_index)

        self.rebuild_all()
        QTimer.singleShot(8000, self._cleanup_stale_covers)
        self._start_posts_index()
        self._posts_timer = QTimer(self)
        self._posts_timer.setInterval(6 * 3600 * 1000)
        self._posts_timer.timeout.connect(self._start_posts_index)
        self._posts_timer.start()

    # ── posts index ──────────────────────────────────────────────────

    def _start_posts_index(self):
        worker = self._posts_worker
        if worker is not None and worker.isRunning():
            return
        worker = PostsIndexWorker(self)
        worker.ready.connect(self._on_posts_index_ready)
        worker.failed.connect(lambda msg: print(f"[PostsIndex] {msg}"))
        self._posts_worker = worker
        worker.start()

    def _on_posts_index_ready(self, data):
        self._posts_index = (data or {}).get("posts") or {}
        self._catalog_order = None
        entry = self._data.get("all_games")
        if entry is not None:
            entry["cards"] = []
            entry["next_offset"] = 0
            entry["has_more"] = True
            entry["_dirty"] = True
        print(f"[Discovery] posts index ready: {len(self._posts_index)} post")
        self.categories_changed.emit()

    def _catalog_entry_for(self, title):
        catalog = getattr(self.mw, "master_catalog", None)
        if catalog is None:
            return {}
        entry = catalog.get_entry(title)
        return entry if isinstance(entry, dict) else {}

    def _post_date_for(self, title):
        if not self._posts_index:
            return ""
        entry = self._catalog_entry_for(title)
        url = str(entry.get("url") or "").rstrip("/")
        if not url:
            return ""
        post = self._posts_index.get(url) or {}
        return str(post.get("date") or "")

    def _catalog_title_order(self):
        """Titoli del catalogo, i piu' recenti (data SteamRip) prima."""
        if self._catalog_order is not None:
            return self._catalog_order
        catalog = getattr(self.mw, "master_catalog", None)
        titles = list(catalog.keys()) if catalog else []
        dated = []
        undated = []
        for title in titles:
            date = self._post_date_for(title)
            if date:
                dated.append((date, title))
            else:
                undated.append(title)
        dated.sort(key=lambda item: item[1].lower())
        dated.sort(key=lambda item: item[0], reverse=True)
        undated.sort(key=str.lower)
        self._catalog_order = [t for _d, t in dated] + undated
        return self._catalog_order

    def _catalog_cards(self, titles):
        installed = getattr(self.mw, "installed_games", {}) or {}
        sync = self._sync_folder()
        cards = []
        for title in titles:
            entry = self._catalog_entry_for(title)
            igdb = entry.get("igdb_meta") or {}
            cards.append({
                "title": title,
                "cover_path": MiniCoverManager.resolve_cover_path(
                    title, installed, sync),
                "meta": dict(entry),
                "igdb": igdb,
                "igdb_meta": igdb,
                "steamrip_categories": list(
                    entry.get("steamrip_categories") or []),
                "url": str(entry.get("url") or "").rstrip("/"),
                "source": "catalog",
            })
        return cards

    def _ensure_catalog_row(self):
        entry = self._data.get("all_games")
        if entry is None:
            entry = {
                "title": "All Games",
                "source": "catalog",
                "games": [],
                "cards": [],
                "next_offset": 0,
                "has_more": True,
            }
            self._data["all_games"] = entry
        if not entry.get("cards"):
            self._extend_catalog_row(entry)
        return entry

    def _extend_catalog_row(self, entry=None, chunk=CATALOG_CHUNK):
        entry = entry or self._data.get("all_games")
        if not entry:
            return False
        order = self._catalog_title_order()
        start = int(entry.get("next_offset") or 0)
        end = min(len(order), start + chunk)
        if end <= start:
            entry["has_more"] = False
            return False
        entry.setdefault("cards", []).extend(
            self._catalog_cards(order[start:end]))
        entry["next_offset"] = end
        entry["has_more"] = end < len(order)
        entry["_dirty"] = False
        return True

    # ── rows ─────────────────────────────────────────────────────────

    @classmethod
    def _igdb_categories(cls):
        return [c for c in IGDB_CATEGORIES if c["key"] in IGDB_ROW_KEYS]

    def categories(self):
        rows = []
        if secrets.has_igdb_credentials():
            for cat in self._igdb_categories():
                rows.append({"key": cat["key"], "title": cat["title"], "source": "igdb"})
        rows.append({
            "key": "recent_added",
            "title": "\U0001f195 Recently Added",
            "source": "steamrip",
        })
        rows.append({
            "key": "recent_updates",
            "title": "\U0001f551 Recently Updated",
            "source": "steamrip",
        })
        for cat in STEAMRIP_CATEGORIES:
            rows.append({
                "key": f"sr_{cat['id']}",
                "title": cat["name"],
                "source": "steamrip",
                "category_id": cat["id"],
            })
        rows.append({
            "key": "all_games",
            "title": "\U0001f5c2\ufe0f All Games",
            "source": "catalog",
        })
        return rows

    def _row_for_key(self, key):
        for cat in self.categories():
            if cat["key"] == key:
                return cat
        return None

    def _native_jobs_to_fetch(self):
        jobs = []
        for cat in self.categories():
            if cat.get("source") != "steamrip":
                continue
            key = cat["key"]
            if not self._needs_fetch_native(key):
                continue
            orderby = "modified" if key == "recent_updates" else "date"
            jobs.append((key, 1, cat.get("category_id"), orderby))
        return jobs

    def _needs_fetch_native(self, key):
        entry = self._data.get(key)
        if not entry:
            return True
        if entry.get("source") != "steamrip" or entry.get("schema") != CACHE_SCHEMA:
            return True
        ttl = (RECENT_TTL_SECONDS
               if key in ("recent_updates", "recent_added")
               else CACHE_TTL_SECONDS)
        return time.time() - (entry.get("fetched_at") or 0) > ttl

    def _sync_folder(self):
        cfg = getattr(self.mw, 'app_config', {}) or {}
        return (cfg.get('savegames') or {}).get('sync_folder', '')

    # ── cache ────────────────────────────────────────────────────────

    @staticmethod
    def _cache_path():
        d = PathManager.get_app_data_dir()
        os.makedirs(str(d), exist_ok=True)
        return os.path.join(str(d), CACHE_FILENAME)

    def _load_cache(self):
        self._card_index = None
        try:
            with open(self._cache_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                valid_keys = {c["key"] for c in self.categories()}
                for key, entry in data.items():
                    if key == "all_games" or key not in valid_keys:
                        continue  # pulizia righe non più esistenti
                    if (isinstance(entry, dict)
                            and isinstance(entry.get("cards"), list)):
                        entry.setdefault("games", [])
                        entry.setdefault("title", self._title(key))
                        self._data[key] = entry
        except Exception:
            self._data = {}

    def _save_cache(self):
        self._save_timer.start()

    @_perf("flush_discovery_cache")
    def _flush_cache(self):
        # Scriviamo solo cio' che non e' ricostruibile: games e meta venivano
        # riscritti per intero a ogni pagina (5.4 MB, ~150ms di freeze).
        payload = {}
        for key, entry in self._data.items():
            if key == "all_games":
                continue
            cards = []
            for card in entry.get("cards") or []:
                cards.append({
                    "title": card.get("title", ""),
                    "igdb": card.get("igdb") or {},
                    "steamrip_categories": card.get("steamrip_categories") or [],
                    "url": card.get("url", ""),
                    "source": card.get("source", ""),
                })
            payload[key] = {
                "title": entry.get("title", ""),
                "source": entry.get("source", "igdb"),
                "next_offset": int(entry.get("next_offset") or 0),
                "next_page": entry.get("next_page"),
                "has_more": bool(entry.get("has_more")),
                "fetched_at": entry.get("fetched_at") or 0,
                "schema": entry.get("schema"),
                "catalog_size": entry.get("catalog_size"),
                "_empty_pages": entry.get("_empty_pages", 0),
                "_dirty": False,
                "cards": cards,
            }
        try:
            with open(self._cache_path(), "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)
        except Exception as e:
            print(f"[Discovery] cache save error: {e}")

    # ── category index (url -> SteamRip category ids) ────────────────

    @staticmethod
    def _category_index_path():
        d = PathManager.get_app_data_dir()
        os.makedirs(str(d), exist_ok=True)
        return os.path.join(str(d), CATEGORY_INDEX_FILENAME)

    def _load_category_index(self):
        try:
            with open(self._category_index_path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            index = data.get("index")
            if isinstance(index, dict) and index:
                self._category_index = index
                self._category_index_fetched_at = float(data.get("fetched_at") or 0)
        except Exception:
            pass

    def _category_index_stale(self):
        age = time.time() - getattr(self, "_category_index_fetched_at", 0)
        return age > CATEGORY_INDEX_TTL_SECONDS

    def _start_category_index(self):
        if self._index_worker is not None and self._index_worker.isRunning():
            return
        self._index_pending = False
        worker = SteamRipIndexWorker(parent=self)
        worker.index_ready.connect(self._on_category_index)
        worker.finished.connect(lambda w=worker: self._cleanup_index_worker(w))
        self._index_worker = worker
        worker.start()

    def _on_category_index(self, index):
        self._category_index = index or {}
        self._category_index_fetched_at = time.time()
        try:
            with open(self._category_index_path(), "w", encoding="utf-8") as f:
                json.dump({
                    "fetched_at": self._category_index_fetched_at,
                    "index": self._category_index,
                }, f, ensure_ascii=False)
        except Exception as e:
            print(f"[Discovery] category index save error: {e}")
        print(f"[Discovery] category index ready: {len(self._category_index)} titles")

    def _cleanup_index_worker(self, worker):
        if self._index_worker is worker:
            self._index_worker = None

    def steamrip_categories_for(self, url="", title=""):
        """Categorie SteamRip di un gioco (per i filtri)."""
        index = self._category_index or {}
        if url:
            return list(index.get(url.rstrip("/")) or [])
        if title:
            entry = self.mw.master_catalog.get_entry(title) if self.mw.master_catalog else None
            if isinstance(entry, dict) and entry.get("url"):
                return list(index.get(entry["url"].rstrip("/")) or [])
        return []

    def _needs_fetch(self, key):
        entry = self._data.get(key)
        if not entry:
            return True
        if entry.get("schema") != CACHE_SCHEMA:
            return True
        # An empty IGDB row is almost always a transient 429/timeout that we
        # cached before the retry succeeded. Do not treat it as fresh: retry on
        # the next startup instead of leaving Discovery blank for 24 hours.
        if (entry.get("source") == "igdb"
                and not entry.get("games") and not entry.get("cards")):
            return True
        return time.time() - (entry.get("fetched_at") or 0) > CACHE_TTL_SECONDS

    def _title(self, key):
        for c in self.categories():
            if c["key"] == key:
                return c["title"]
        return key.replace("_", " ").title()

    # ── worker signal handling ───────────────────────────────────────

    def _on_categories(self, cat_dict):
        now = time.time()
        for key, games in cat_dict.items():
            existing = self._data.get(key) or {}
            if not games and (existing.get("cards") or existing.get("games")):
                # A failed/empty IGDB response must not wipe a good cached row.
                existing["fetched_at"] = 0
                existing["schema"] = CACHE_SCHEMA
                self._data[key] = existing
                continue
            self._data[key] = {
                "title": self._title(key),
                "source": "igdb",
                "games": list(games),
                "next_offset": len(games),
                "has_more": len(games) >= PAGE_SIZE,
                "fetched_at": now if games else 0,
                "schema": CACHE_SCHEMA,
                "cards": [],
            }
        self._save_cache()
        self.rebuild_all()
        empty_keys = [
            key for key in IGDB_ROW_KEYS
            if self._data.get(key)
            and not self._data[key].get("cards")
            and not self._data[key].get("games")
        ]
        if empty_keys and not self._igdb_retry_scheduled:
            self._igdb_retry_scheduled = True
            QTimer.singleShot(15000, self._retry_empty_igdb)

    def _retry_empty_igdb(self):
        from network.igdb_worker import IGDBWorker

        if not secrets.has_igdb_credentials():
            return
        keys = [
            key for key in IGDB_ROW_KEYS
            if self._data.get(key)
            and not self._data[key].get("cards")
            and not self._data[key].get("games")
        ]
        if not keys:
            return
        worker = IGDBWorker(category_keys=IGDB_ROW_KEYS,
                            initial_keys=keys,
                            parent=self)
        worker.categories_ready.connect(self._on_categories)
        worker.page_ready.connect(self._on_page)
        worker.error_occurred.connect(self.error_occurred)
        self._worker = worker
        worker.start()

    def _on_page(self, key, games, has_more=False):
        entry = self._data.setdefault(key, {
            "title": self._title(key),
            "games": [],
            "next_offset": 0,
            "has_more": False,
            "fetched_at": time.time(),
            "schema": CACHE_SCHEMA,
            "cards": [],
        })
        if not games:
            entry["has_more"] = False
            entry["next_offset"] = MAX_OFFSET
        else:
            entry["games"].extend(games)
            entry["next_offset"] = (int(entry.get("next_offset") or 0)
                                    + len(games))
            entry["has_more"] = bool(has_more)
        entry["_dirty"] = False
        added = self._append_matched(key, games, source="igdb")
        self._track_empty_page(entry, added, bool(games))
        self._save_cache()
        self.page_changed.emit(key)

    def _track_empty_page(self, entry, added, had_games):
        """Se una pagina non aggiunge nessuna card la riga non cresce:
        senza questo limite lo scroll all'estremo rilanciava pagine in loop."""
        if not had_games:
            return
        if added:
            entry["_empty_pages"] = 0
            return
        empty = int(entry.get("_empty_pages") or 0) + 1
        entry["_empty_pages"] = empty
        if empty >= EMPTY_PAGE_LIMIT:
            entry["has_more"] = False
            entry["next_offset"] = MAX_OFFSET
            print(f"[Discovery] '{entry.get('title')}' fermata dopo "
                  f"{empty} pagine senza nuovi match")

    def _append_matched(self, key, games, source="igdb"):
        """Matcha SOLO i nuovi elementi: rimatchare l'intera riga a ogni
        pagina costava ~65ms su migliaia di giochi (freeze a cascata)."""
        entry = self._data.get(key)
        if not entry or not games:
            return 0
        cards = entry.setdefault("cards", [])
        known = {c.get("title") for c in cards if c.get("title")}
        added = 0
        for card in self._match_games(games, source=source):
            title = card.get("title")
            if title and title in known:
                continue
            if title:
                known.add(title)
            cards.append(card)
            added += 1
        entry["catalog_size"] = self._catalog_size()
        self._card_index = None
        return added

    @_perf("native_page")
    def _on_native_page(self, key, posts, page, total_pages):
        entry = self._data.setdefault(key, {
            "title": self._title(key),
            "source": "steamrip",
            "games": [],
            "next_page": 1,
            "has_more": True,
            "fetched_at": time.time(),
            "schema": CACHE_SCHEMA,
            "cards": [],
        })
        entry["source"] = "steamrip"
        entry["title"] = self._title(key)
        if posts:
            entry.setdefault("games", []).extend(posts)
        entry["next_page"] = int(page) + 1
        entry["has_more"] = int(page) < int(total_pages or 0)
        entry["fetched_at"] = time.time()
        entry["schema"] = CACHE_SCHEMA
        entry["_dirty"] = False
        added = self._append_matched(key, posts, source="steamrip")
        if entry["has_more"] and not added and posts:
            self._track_empty_page(entry, added, True)
        else:
            entry["_empty_pages"] = 0
        self._save_cache()
        self.page_changed.emit(key)

    def _on_native_error(self, key, message):
        self._native_loading.discard(key)
        print(f"[Discovery] SteamRip row '{key}' error: {message}")

    def _cleanup_native_worker(self, worker, key=None):
        if worker in self._native_workers:
            self._native_workers.remove(worker)
        if key:
            self._native_loading.discard(key)

    def request_more(self, key):
        """Ask for the next page of a category (IGDB or native SteamRip)."""
        if key == "all_games":
            entry = self._ensure_catalog_row()
            if not entry.get("has_more"):
                return False
            return self._extend_catalog_row(entry)
        entry = self._data.get(key)
        if not entry:
            return False
        if not entry.get("has_more", True):
            return False

        if entry.get("source") == "steamrip":
            if key in self._native_loading:
                return False
            row = self._row_for_key(key) or {}
            page = int(entry.get("next_page") or 2)
            worker = SteamRipPostsWorker(
                key, page,
                category_id=row.get("category_id"),
                orderby="modified" if key == "recent_updates" else "date",
                parent=self,
            )
            worker.page_ready.connect(self._on_native_page)
            worker.error.connect(self._on_native_error)
            worker.finished.connect(lambda w=worker: self._cleanup_native_worker(w, key))
            self._native_loading.add(key)
            self._native_workers.append(worker)
            worker.start()
            return True

        if self._worker is None:
            return False
        if key in self._worker._loading_pages:
            return False
        if (entry.get("next_offset") or 0) >= MAX_OFFSET:
            return False
        self._worker._loading_pages.add(key)
        QMetaObject.invokeMethod(
            self._worker, "request_page",
            Qt.ConnectionType.QueuedConnection,
            Q_ARG(str, key),
        )
        return True

    def _catalog_size(self):
        try:
            return len(self.mw.master_catalog)
        except Exception:
            return 0

    def _hydrate_entry(self, entry):
        """Aggiorna cover/meta dalle cache locali senza rifare il matching."""
        installed = getattr(self.mw, 'installed_games', {}) or {}
        sync = self._sync_folder()
        catalog = self.mw.master_catalog
        for card in entry.get("cards") or []:
            title = card.get("title")
            if not title:
                continue
            card["cover_path"] = MiniCoverManager.resolve_cover_path(title, installed, sync)
            meta = catalog.get_entry(title) if catalog else None
            if isinstance(meta, dict):
                card["meta"] = dict(meta)
                card["igdb_meta"] = meta.get("igdb_meta") or card.get("igdb_meta") or {}

    @_perf("rebuild_all")
    def rebuild_all(self):
        # Cache-first: rimarchiamo dirty solo se il set di titoli del catalogo
        # è cambiato (o mancano le card). L'arricchimento (meta/prezzi/version)
        # non invalida il matching.
        size = self._catalog_size()
        for key, entry in self._data.items():
            if key == "all_games":
                continue
            if entry.get("catalog_size") != size or not entry.get("cards"):
                entry["_dirty"] = True
        self._save_cache()
        self.categories_changed.emit()

    @_perf("ensure_cards")
    def _ensure_cards(self, key):
        entry = self._data.get(key)
        if not entry:
            return
        token = self._catalog_token()
        if not entry.get("_dirty") and "cards" in entry:
            if entry.get("_hydrate_token") != token:
                self._hydrate_entry(entry)
                entry["_hydrate_token"] = token
            return
        self._rebuild_matched(key)
        entry["_dirty"] = False
        entry["catalog_size"] = self._catalog_size()
        self._hydrate_entry(entry)
        entry["_hydrate_token"] = token

    # ── manutenzione cache ───────────────────────────────────────────

    def _cleanup_stale_covers(self):
        """Rimuove le mini-cover il cui titolo non esiste più nel catalogo
        né tra i giochi installati. Gira in background rispetto all'uso
        (timer), così l'avvio non paga nulla."""
        try:
            cache_dir = MiniCoverManager.get_local_cache_dir()
        except Exception:
            return

        catalog = getattr(self.mw, 'master_catalog', None)
        try:
            valid = {safe_game_folder_name(t) for t in catalog.keys()} if catalog else set()
        except Exception:
            valid = set()
        for title in (getattr(self.mw, 'installed_games', {}) or {}):
            valid.add(safe_game_folder_name(title))

        # Catalogo non ancora pronto: non rischiamo di cancellare il valido.
        if len(valid) < 50:
            return

        now = time.time()
        removed = 0
        try:
            names = os.listdir(cache_dir)
        except OSError:
            return
        for name in names:
            if not name.lower().endswith(".jpg"):
                continue
            if name[:-4] in valid:
                continue
            path = os.path.join(cache_dir, name)
            try:
                # I file recenti potrebbero essere appena stati scritti.
                if now - os.path.getmtime(path) < 3600:
                    continue
                os.remove(path)
                removed += 1
            except OSError:
                pass
        if removed:
            print(f"[Discovery] cover stale rimosse: {removed}")

    # ── matching ─────────────────────────────────────────────────────

    def _catalog_token(self):
        try:
            mtime = os.path.getmtime(self.mw.master_catalog._db_path)
        except Exception:
            mtime = 0
        return (len(self.mw.master_catalog), mtime)

    def _build_index(self):
        token = self._catalog_token()
        if self._index is not None and self._index_token == token:
            return self._index
        self._match_cache = {}
        appid_index = {}
        exact = {}
        cleaned = []
        urls = {}
        catalog = self.mw.master_catalog
        for title in catalog.keys():
            entry = catalog.get_entry(title)
            clean = clean_text(title)
            if clean and clean not in exact:
                exact[clean] = title
            if isinstance(entry, dict):
                sid = str(entry.get("steam_id") or "").strip()
                if not sid:
                    sg = entry.get("sgdb_assets")
                    if isinstance(sg, dict):
                        sid = str(sg.get("steam_app_id") or "").strip()
                if sid.isdigit() and sid not in appid_index:
                    appid_index[sid] = title
                raw = clean_text(str(entry.get("raw_title") or ""))
                if raw and raw not in exact:
                    exact[raw] = title
                url = (entry.get("url") or "").rstrip("/")
                if url and url not in urls:
                    urls[url] = title
            if clean:
                cleaned.append((clean, title))
        self._index = {"appid": appid_index, "exact": exact, "cleaned": cleaned, "urls": urls}
        self._index_token = token
        return self._index

    def _match_post(self, post, idx):
        """Match di un post SteamRip al titolo del catalogo (URL prima del titolo)."""
        url = (post.get("url") or "").rstrip("/")
        if url and url in idx["urls"]:
            return idx["urls"][url]
        clean = clean_text(post.get("title") or "")
        if clean and clean in idx["exact"]:
            return idx["exact"][clean]
        return self._match_one({"name": post.get("title") or "", "steam_appid": ""}, idx)

    def _match_one(self, game, idx):
        sid = str(game.get("steam_appid") or "").strip()
        if sid and sid in idx["appid"]:
            return idx["appid"][sid]

        name = str(game.get("name") or "")
        clean = clean_text(name)
        if not clean:
            return None
        if clean in idx["exact"]:
            return idx["exact"][clean]

        cached = self._match_cache.get(clean, "__miss__")
        if cached != "__miss__":
            return cached or None

        best = None
        best_score = 0
        for cc, title in idx["cleaned"]:
            if len(cc) < 3:
                continue
            if clean in cc:
                score = len(clean)
            elif cc in clean:
                score = len(cc)
            else:
                continue
            if score > best_score:
                best = title
                best_score = score
        self._match_cache[clean] = best or ""
        return best

    @_perf("rebuild_matched")
    def _rebuild_matched(self, key):
        entry = self._data.get(key)
        if not entry:
            return
        if not entry.get("games") and entry.get("cards"):
            # Cache slim dopo un riavvio: i match gia' fatti restano validi.
            return
        source = entry.get("source", "igdb")
        entry["cards"] = self._match_games(entry.get("games") or [], source=source)
        self._card_index = None

    def _match_games(self, games, source="igdb"):
        installed = getattr(self.mw, 'installed_games', {}) or {}
        sync = self._sync_folder()
        idx = self._build_index()
        matched = []
        seen = set()
        for game in games:
            if source == "steamrip":
                title = self._match_post(game, idx)
                igdb_meta = {}
                steamrip_categories = list(game.get("categories") or [])
                post_url = (game.get("url") or "").rstrip("/")
            else:
                title = self._match_one(game, idx)
                igdb_meta = game
                steamrip_categories = []
                post_url = ""
            if not title or title in seen:
                continue
            seen.add(title)
            entry = self.mw.master_catalog.get_entry(title)
            cover = MiniCoverManager.resolve_cover_path(title, installed, sync)
            if isinstance(entry, dict) and entry.get("steamrip_categories"):
                steamrip_categories = sorted(set(steamrip_categories) | set(entry["steamrip_categories"]))
            matched.append({
                "title": title,
                "cover_path": cover,
                "meta": dict(entry) if isinstance(entry, dict) else {},
                "igdb": igdb_meta,
                "steamrip_categories": steamrip_categories,
                "url": post_url,
                "source": source,
            })
        return matched

    # ── accessors ────────────────────────────────────────────────────

    @_perf("get_cards")
    def get_cards(self, key):
        if key == "all_games":
            entry = self._ensure_catalog_row()
            cards = entry.get("cards") or []
            if not self._filters_active():
                return cards
            return [card for card in cards if self._card_matches(card)]
        entry = self._data.get(key)
        if not entry:
            return []
        self._ensure_cards(key)
        cards = entry.get("cards") or []
        if self._filters_active():
            cards = [card for card in cards if self._card_matches(card)]
        if self.is_sortable_key(key):
            cards = self.sort_cards(cards)
        return cards

    def get_title(self, key):
        entry = self._data.get(key)
        if entry:
            return entry.get("title") or self._title(key)
        return self._title(key)

    def has_data(self, key):
        return bool(self._data.get(key))

    # ── IGDB enrichment (lazy) ───────────────────────────────────────

    def _invalidate_card_index(self):
        self._card_index = None

    def _find_card(self, title):
        if not title:
            return None
        idx = getattr(self, "_card_index", None)
        if idx is None:
            idx = {}
            for entry in self._data.values():
                for card in entry.get("cards") or []:
                    name = card.get("title")
                    if name and name not in idx:
                        idx[name] = card
            self._card_index = idx
        return idx.get(title)

    def enqueue_igdb_meta(self, titles):
        """Arricchisce in background i titoli visibili (generi/temi/cover/rating)."""
        if not titles or not secrets.has_igdb_credentials():
            return
        catalog = self.mw.master_catalog
        for title in titles:
            if not title or title in self._meta_inflight:
                continue
            entry = catalog.get_entry(title) if catalog else None
            if isinstance(entry, dict) and entry.get("igdb_meta"):
                continue
            card = self._find_card(title)
            appid = ""
            if card:
                appid = str((card.get("igdb") or {}).get("steam_appid") or "")
                if not appid:
                    appid = str((card.get("meta") or {}).get("steam_id") or "")
            if not appid and isinstance(entry, dict):
                appid = str(entry.get("steam_id") or "")
            self._meta_queue.append((title, appid))
        self._start_meta_worker()

    def _start_meta_worker(self):
        if self._meta_worker is not None and self._meta_worker.isRunning():
            return
        if not self._meta_queue:
            return
        jobs = self._meta_queue[:60]
        self._meta_queue = self._meta_queue[60:]
        self._meta_inflight.update(title for title, _ in jobs)
        worker = IGDBMetaWorker(jobs, parent=self)
        worker.meta_ready.connect(self._on_meta_ready)
        worker.taxonomy_ready.connect(self._on_taxonomy)
        worker.finished.connect(lambda w=worker: self._cleanup_meta_worker(w))
        self._meta_worker = worker
        worker.start()

    def _on_taxonomy(self, taxonomy):
        if taxonomy:
            self._taxonomy = taxonomy
            self.taxonomy_ready.emit(taxonomy)

    @_perf("on_meta_ready")
    def _on_meta_ready(self, results):
        for title, meta in (results or {}).items():
            if not isinstance(meta, dict):
                continue
            if self.mw.master_catalog:
                self.mw.master_catalog.set_game_field(title, "igdb_meta", meta)
                appid = str(meta.get("steam_appid") or "").strip()
                if appid.isdigit() and not self.mw.master_catalog.get_game_field(title, "steam_id"):
                    self.mw.master_catalog.set_game_field(title, "steam_id", appid)
            card = self._find_card(title)
            if card:
                card["igdb_meta"] = meta
                if isinstance(card.get("meta"), dict):
                    card["meta"]["igdb_meta"] = meta
        self.meta_ready.emit(results or {})

    def _cleanup_meta_worker(self, worker):
        for title, _appid in getattr(worker, "_jobs", []):
            self._meta_inflight.discard(title)
        if self._meta_worker is worker:
            self._meta_worker = None
        if self._meta_queue:
            QTimer.singleShot(0, self._start_meta_worker)

    def taxonomy_genres(self):
        if not self._taxonomy:
            return []
        genres = self._taxonomy.get("genres") or {}
        return sorted(genres.items(), key=lambda item: item[1])

    @_perf("search_cards")
    def search_cards(self):
        """Ricerca su TUTTO il catalogo, non solo sulle righe caricate."""
        query = str(self._filters.get("search") or "").strip().lower()
        if not query:
            return []
        catalog = getattr(self.mw, "master_catalog", None)
        if catalog is None:
            return []

        def score(title):
            low = title.lower()
            if low == query:
                return 0
            if low.startswith(query):
                return 1
            pos = low.find(query)
            if pos < 0:
                return 99
            if pos > 0 and not low[pos - 1].isalnum():
                return 2
            return 3

        matches = []
        for title in catalog.keys():
            rank = score(title)
            if rank < 99:
                matches.append((rank, title))
        matches.sort(key=lambda item: (item[0], item[1].lower()))
        titles = [title for _rank, title in matches[:120]]
        cards = self._catalog_cards(titles)
        if self._filters_active():
            cards = [card for card in cards if self._card_matches(card)]
        return cards

    # ── filters ──────────────────────────────────────────────────────

    def set_filters(self, filters):
        filters = filters or {}
        sort = str(filters.get("sort") or "")
        self._filters = {
            "search": str(filters.get("search") or "").strip().lower(),
            "hide_installed": bool(filters.get("hide_installed")),
            "sr_category": filters.get("sr_category"),
            "igdb_genre": filters.get("igdb_genre"),
            "sort": sort if sort in SORT_MODES else "",
        }

    def _filters_active(self):
        f = self._filters
        return bool(f["search"] or f["hide_installed"]
                    or f["sr_category"] or f["igdb_genre"])

    def _card_matches(self, card):
        title = card.get("title") or ""
        f = self._filters
        if f["search"] and f["search"] not in title.lower():
            return False
        if f["hide_installed"] and title in (getattr(self.mw, "installed_games", {}) or {}):
            return False
        if f["sr_category"]:
            cats = set(card.get("steamrip_categories") or [])
            meta = card.get("meta") or {}
            cats |= set(meta.get("steamrip_categories") or [])
            if int(f["sr_category"]) not in cats:
                return False
        if f["igdb_genre"]:
            meta = card.get("igdb_meta") or (card.get("meta") or {}).get("igdb_meta") or {}
            ids = [int(x) for x in (meta.get("genre_ids") or [])]
            if int(f["igdb_genre"]) not in ids:
                return False
        return True

    # ── ordinamento ──────────────────────────────────────────────────

    @staticmethod
    def is_sortable_key(key):
        """True se la riga puo' seguire il dropdown di ordinamento."""
        return key is not None and key not in NON_SORTABLE_KEYS

    def row_sort_active(self, key):
        return bool(self._filters.get("sort")) and self.is_sortable_key(key)

    def _igdb_for_card(self, card):
        igdb = card.get("igdb") or {}
        if isinstance(igdb, dict) and igdb:
            return igdb
        meta = card.get("meta") or {}
        igdb = meta.get("igdb_meta") or {}
        if isinstance(igdb, dict) and igdb:
            return igdb
        entry = self._catalog_entry_for(str(card.get("title") or ""))
        return entry.get("igdb_meta") or {}

    def _release_ts(self, card):
        try:
            return int(self._igdb_for_card(card).get("first_release_date") or 0)
        except (TypeError, ValueError):
            return 0

    def _rating(self, card):
        try:
            return float(self._igdb_for_card(card).get("total_rating") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def sort_cards(self, cards):
        """Ordina una lista di card secondo il dropdown corrente.

        Le card senza il dato richiesto finiscono in fondo, cosi' l'ordine
        delle altre resta leggibile.
        """
        mode = self._filters.get("sort") or ""
        cards = list(cards)
        if not mode or not cards:
            return cards

        if mode in ("title_asc", "title_desc"):
            cards.sort(key=lambda c: str(c.get("title") or "").lower(),
                       reverse=(mode == "title_desc"))
            return cards

        if mode in ("added_desc", "added_asc"):
            dated, undated = [], []
            for card in cards:
                date = self._post_date_for(str(card.get("title") or ""))
                if date:
                    dated.append((date, card))
                else:
                    undated.append(card)
            dated.sort(key=lambda item: item[0],
                       reverse=(mode == "added_desc"))
            return [card for _d, card in dated] + undated

        if mode in ("release_desc", "release_asc"):
            dated, undated = [], []
            for card in cards:
                ts = self._release_ts(card)
                if ts:
                    dated.append((ts, card))
                else:
                    undated.append(card)
            dated.sort(key=lambda item: item[0],
                       reverse=(mode == "release_desc"))
            return [card for _t, card in dated] + undated

        if mode == "rating_desc":
            rated, unrated = [], []
            for card in cards:
                rating = self._rating(card)
                if rating:
                    rated.append((rating, card))
                else:
                    unrated.append(card)
            rated.sort(key=lambda item: item[0], reverse=True)
            return [card for _r, card in rated] + unrated
        return cards

    # ── covers ───────────────────────────────────────────────────────

    def _job_for_card(self, card, allow_scrape=False, allow_page_fetch=False):
        entry = card.get("meta") or {}
        if not entry:
            # La cache slim non salva "meta": recuperiamo l'entry dal catalogo,
            # altrimenti su una macchina nuova (cache cover vuota) il job non
            # ha ne' grid SGDB ne' cover IGDB e la card resta senza immagine.
            catalog = getattr(self.mw, "master_catalog", None)
            title = card.get("title")
            if catalog is not None and title:
                entry = catalog.get_entry(title) or {}
        igdb = card.get("igdb") or {}
        igdb_meta = entry.get("igdb_meta") if isinstance(entry, dict) else {}
        cover_url = ((igdb or {}).get("cover_url")
                     or (igdb_meta or {}).get("cover_url"))
        if cover_url and str(cover_url).startswith("//"):
            cover_url = "https:" + cover_url
        return {
            "title": card["title"],
            "catalog_entry": entry,
            "igdb": igdb,
            "igdb_cover_url": cover_url,
            "url": entry.get("url") or "",
            "allow_scrape": allow_scrape,
            "allow_page_fetch": allow_page_fetch,
        }

    def enqueue_cards(self, cards, high=False, allow_scrape=False,
                      allow_page_fetch=False):
        """Enqueue covers for explicit card dicts.

        Search results are built on the fly by ``search_cards()`` and are not
        stored in ``self._data``: looking them up by title (the old
        ``enqueue_covers`` path) silently found nothing.
        """
        if self._cover_worker is None or not cards:
            return
        jobs = []
        for card in cards:
            if not isinstance(card, dict) or not card.get("title"):
                continue
            cover = card.get("cover_path")
            if cover and MiniCoverManager.is_vertical_cover(cover):
                continue
            if cover:
                # Horizontal fallback already cached: try one upgrade per
                # session instead of treating it as a final cover.
                if card["title"] in self._cover_upgrade_attempted:
                    continue
                self._cover_upgrade_attempted.add(card["title"])
            jobs.append(self._job_for_card(
                card, allow_scrape=allow_scrape,
                allow_page_fetch=allow_page_fetch))
        if jobs:
            self._cover_worker.enqueue(jobs, high=high)

    def enqueue_covers(self, titles, high=False, allow_scrape=False,
                       allow_page_fetch=False):
        """Enqueue covers for titles already present in loaded rows."""
        if self._cover_worker is None or not titles:
            return
        wanted = set(titles)
        cards = []
        for entry in self._data.values():
            for card in entry.get("cards") or []:
                if card["title"] in wanted and not card.get("cover_path"):
                    cards.append(card)
        self.enqueue_cards(cards, high=high, allow_scrape=allow_scrape,
                           allow_page_fetch=allow_page_fetch)

    def cancel_covers(self, titles):
        """Drop queued cover jobs for titles that left the lazy window."""
        if self._cover_worker is None or not titles:
            return
        self._cover_worker.cancel(titles)

    def set_cover_path(self, title, path):
        """Scrive la cover risolta nella card: le righe ricostruite la usano
        subito senza ri-scaricarla."""
        if not title or not path:
            return
        for entry in self._data.values():
            for card in entry.get("cards") or []:
                if card.get("title") == title:
                    card["cover_path"] = path

    def on_scrape_cover_fetched(self, title, img_url):
        if self._cover_worker is None:
            return
        if not img_url:
            return
        self._cover_worker.download_url(title, img_url, high=True)

    def shutdown(self):
        posts = self._posts_worker
        if posts is not None and posts.isRunning():
            try:
                posts.stop()
                posts.wait(3000)
            except RuntimeError:
                pass
        for w in (self._cover_worker, self._worker):
            if w is not None and w.isRunning():
                w.stop()
                w.wait(3000)
        for w in list(self._native_workers):
            if w.isRunning():
                if hasattr(w, "stop"):
                    w.stop()
                w.wait(2000)
        if self._index_worker is not None and self._index_worker.isRunning():
            self._index_worker.stop()
            self._index_worker.wait(2000)
        if self._meta_worker is not None and self._meta_worker.isRunning():
            self._meta_worker.wait(3000)
