"""Indice locale dei post SteamRip (WP REST).

Serve a Discovery per conoscere *tutti* i giochi presenti sul sito con data
di pubblicazione e categorie, senza dipendere dalle prime pagine delle righe.

Flusso:
- se esiste `data/steamrip_posts_index.json` accanto all'app (snapshot
  spedito con la release) e la copia locale manca, viene copiato;
- se la copia locale esiste, si fa solo un sync incrementale
  (`modified_after` dell'ultimo post conosciuto);
- se non esiste nessuno dei due, si scarica tutto in background;
- ogni FULL_RESYNC_DAYS si rifà un download completo per intercettare le
  cancellazioni (che il sync incrementale non vede).

In sviluppo (non congelato) il download completo scrive anche lo snapshot in
`data/`, cosi' basta avviare l'app una volta per aggiornarlo.
"""
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import QThread, pyqtSignal

from core.data_files import bundled_path as _bundled_path
from core.paths import PathManager
from network.steamrip_api import _rest_get, normalize_post

INDEX_FILENAME = "steamrip_posts_index.json"
SCHEMA_VERSION = 1
PAGE_SIZE = 100
SAVE_EVERY = 10
FULL_RESYNC_DAYS = 30
PAGE_PAUSE = 0.5
SAVE_EVERY = 5
MAX_PAGE_RETRIES = 6
DELTA_OVERLAP = timedelta(days=1)

_FIELDS = "title,link,date,modified,categories"


def bundled_path():
    return str(_bundled_path(INDEX_FILENAME))


def dest_path():
    d = PathManager.get_dbs_dir()
    os.makedirs(str(d), exist_ok=True)
    return os.path.join(str(d), INDEX_FILENAME)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        # le date WP REST sono senza fuso: le trattiamo come UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _empty_index():
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": "",
        "full_sync_at": "",
        "posts": {},
    }


def _read_index(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("posts"), dict):
        return None
    if data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def _write_index(path, data):
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[PostsIndex] save error: {e}")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def _post_to_entry(post):
    entry = normalize_post(post)
    entry["date"] = post.get("date") or ""
    entry["categories"] = [int(c) for c in (post.get("categories") or [])
                           if str(c).isdigit()]
    return entry


class PostsIndexWorker(QThread):
    """Prepara l'indice locale: seed dal bundle, poi sync incrementale."""

    ready = pyqtSignal(dict)
    progress = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    # ── entry point ─────────────────────────────────────────────────

    def run(self):
        try:
            data = self._load_or_seed()
            data = self._sync(data)
            if data is None:
                self.failed.emit("indice non disponibile")
                return
            _write_index(dest_path(), data)
            self.ready.emit(data)
        except Exception as e:
            print(f"[PostsIndex] error: {e}")
            self.failed.emit(str(e))

    # ── seed / load ─────────────────────────────────────────────────

    def _load_or_seed(self):
        dest = dest_path()
        bundle = bundled_path()

        local = _read_index(dest)
        seeded = _read_index(bundle)

        if local is None and seeded is not None:
            self.progress.emit("Preparo l'indice dei giochi…")
            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(bundle, dest)
            except Exception as e:
                print(f"[PostsIndex] copy seed failed: {e}")
            return seeded

        if local is not None and seeded is not None:
            local_at = _parse_iso(local.get("generated_at"))
            seed_at = _parse_iso(seeded.get("generated_at"))
            if seed_at and (local_at is None or seed_at > local_at):
                # snapshot piu' recente del locale: ripartiamo da li'
                try:
                    shutil.copy2(bundle, dest)
                except Exception as e:
                    print(f"[PostsIndex] copy newer seed failed: {e}")
                return seeded

        return local if local is not None else _empty_index()

    # ── sync ────────────────────────────────────────────────────────

    def _sync(self, data):
        posts = data.get("posts") or {}

        # Download completo interrotto: riprende dalla pagina salvata.
        if data.get("partial_page"):
            fresh = self._full_download()
            return fresh if fresh is not None else data

        full_at = _parse_iso(data.get("full_sync_at"))
        need_full = (
            not posts
            or full_at is None
            or (datetime.now(timezone.utc) - full_at) > timedelta(days=FULL_RESYNC_DAYS)
        )
        if need_full:
            fresh = self._full_download()
            if fresh is None:
                return data if posts else None
            return fresh

        watermark = self._watermark(posts)
        if watermark is None:
            fresh = self._full_download()
            return fresh if fresh is not None else data

        since = (watermark - DELTA_OVERLAP).strftime("%Y-%m-%dT%H:%M:%S")
        delta = self._delta_download(since)
        if delta:
            posts.update(delta)
            data["generated_at"] = _now_iso()
            print(f"[PostsIndex] {len(delta)} post aggiornati ({len(posts)} totali)")
        return data

    @staticmethod
    def _watermark(posts):
        best = None
        for entry in posts.values():
            modified = _parse_iso(entry.get("modified"))
            if modified is None:
                continue
            if best is None or modified > best:
                best = modified
        return best

    # ── downloads ───────────────────────────────────────────────────

    def _request(self, params):
        r = _rest_get(params)
        if r is None:
            return None
        if r.status_code != 200:
            print(f"[PostsIndex] REST status {r.status_code}")
            return None
        try:
            return r.json() or []
        except Exception:
            return None

    def _full_download(self):
        dest = dest_path()
        data = _read_index(dest) or _empty_index()
        posts = data.setdefault("posts", {})
        page = int(data.pop("partial_page", 1) or 1)
        total_pages = 0

        while True:
            self.progress.emit(f"Scarico l'indice dei giochi… pagina {page}")
            batch = None
            for attempt in range(MAX_PAGE_RETRIES):
                batch = self._request({
                    "page": page,
                    "per_page": PAGE_SIZE,
                    "orderby": "date",
                    "order": "desc",
                    "_fields": _FIELDS,
                })
                if batch is not None:
                    break
                wait = 30 * (attempt + 1)
                self.progress.emit(f"Rate limit, riprovo tra {wait}s…")
                if self._stop.wait(wait):
                    break

            if batch is None and not self._stop.is_set():
                # Salva il parziale: al prossimo avvio riprende da qui.
                data["partial_page"] = page
                _write_index(dest, data)
                print(f"[PostsIndex] download interrotto a pagina {page} "
                      f"({len(posts)} post salvati)")
                return None

            if not batch or self._stop.is_set():
                break
            for post in batch:
                entry = _post_to_entry(post)
                if entry["url"]:
                    posts[entry["url"]] = entry

            page += 1
            data["partial_page"] = page
            if page % SAVE_EVERY == 0:
                _write_index(dest, data)

            if len(batch) < PAGE_SIZE:
                break
            if self._stop.wait(PAGE_PAUSE):
                break

        data.pop("partial_page", None)
        now = _now_iso()
        data["schema"] = SCHEMA_VERSION
        data["generated_at"] = now
        data["full_sync_at"] = now
        data["posts"] = posts
        print(f"[PostsIndex] indice completo: {len(posts)} post")
        self._write_dev_snapshot(data)
        return data

    def _delta_download(self, since):
        posts = {}
        page = 1
        while True:
            batch = self._request({
                "page": page,
                "per_page": PAGE_SIZE,
                "modified_after": since,
                "orderby": "modified",
                "order": "asc",
                "_fields": _FIELDS,
            })
            if batch is None:
                return posts
            if not batch:
                break
            for post in batch:
                entry = _post_to_entry(post)
                if entry["url"]:
                    posts[entry["url"]] = entry
            if len(batch) < PAGE_SIZE:
                break
            page += 1
        return posts

    @staticmethod
    def _write_dev_snapshot(data):
        """In dev aggiorna anche il file da committare in data/."""
        if getattr(sys, "frozen", False):
            return
        path = bundled_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if _write_index(path, data):
            print(f"[PostsIndex] snapshot di sviluppo aggiornato: {path}")
