"""Background IGDB backfill for the installed library.

The manager picks titles that are not in the details cache yet, prioritises
the ones with more playtime and starts one batch worker. Results are written
to :class:`managers.igdb_details_cache.IgdbDetailsCache`, merged into the
Master Catalog and announced on the event bus.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject

from core.events import EVENT_IGDB_DETAILS_READY
from managers.igdb_details_cache import IgdbDetailsCache
from network.igdb_batch_worker import IGDBBatchWorker


class IGDBBatchManager(QObject):
    def __init__(self, installed_games, catalog=None, details=None,
                 events=None, max_titles: int = 50, parent=None):
        super().__init__(parent)
        self._installed_games = installed_games
        self._catalog = catalog
        self._details = details or IgdbDetailsCache
        self._events = events
        self._max_titles = max(1, int(max_titles))
        self._worker = None
        self._jobs = []
        self._running = False
        self._failed = False

    @property
    def running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------ inputs

    @staticmethod
    def _resolve(value):
        return value() if callable(value) else value

    def _installed(self) -> dict:
        value = self._resolve(self._installed_games)
        return dict(value) if isinstance(value, dict) else {}

    def _catalog_field(self, title, field, default=None):
        if self._catalog is None:
            return default
        try:
            return self._catalog.get_game_field(title, field, default)
        except Exception:
            return default

    def _appid_for(self, title, meta):
        igdb_meta = self._catalog_field(title, "igdb_meta", {})
        if not isinstance(igdb_meta, dict):
            igdb_meta = {}
        candidates = [
            (meta or {}).get("steam_grid_id"),
            self._catalog_field(title, "steam_app_id", ""),
            self._catalog_field(title, "steam_id", ""),
            igdb_meta.get("steam_appid"),
        ]
        for candidate in candidates:
            if candidate and str(candidate).isdigit():
                return str(candidate)
        try:
            if self._catalog is not None:
                sid = self._catalog.get_steam_app_id(title)
                if sid and str(sid).isdigit():
                    return str(sid)
        except Exception:
            pass
        return ""

    def _igdb_id_for(self, title):
        igdb_meta = self._catalog_field(title, "igdb_meta", {})
        if not isinstance(igdb_meta, dict):
            return ""
        igdb_id = igdb_meta.get("igdb_id")
        return str(igdb_id) if igdb_id and str(igdb_id).isdigit() else ""

    def _cached(self, title):
        try:
            return self._details.get(title)
        except Exception:
            return None

    # ------------------------------------------------------------------ jobs

    def collect_jobs(self):
        jobs = []
        for title, meta in self._installed().items():
            if not title:
                continue
            if self._cached(title) is not None:
                continue
            appid = self._appid_for(title, meta)
            igdb_id = self._igdb_id_for(title)
            if not appid and not igdb_id:
                continue
            jobs.append({
                "title": title,
                "appid": appid,
                "igdb_id": igdb_id,
                "seconds": int((meta or {}).get("playtime_seconds") or 0),
            })
        jobs.sort(key=lambda job: (-job["seconds"], job["title"].lower()))
        return jobs[:self._max_titles]

    # ------------------------------------------------------------------ run

    def start(self):
        if self._running:
            return
        from core import secrets
        if not secrets.has_igdb_credentials():
            return
        jobs = self.collect_jobs()
        if not jobs:
            return

        self._jobs = jobs
        self._failed = False
        self._running = True
        self._worker = IGDBBatchWorker(jobs, self)
        self._worker.batch_ready.connect(self._on_ready)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def stop(self):
        if self._worker is not None:
            try:
                self._worker.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------ slots

    def _on_ready(self, results):
        results = results if isinstance(results, dict) else {}
        checked = set(getattr(self._worker, "checked_titles", set()))

        found = {}
        updates = {}
        for job in self._jobs:
            title = job.get("title") or ""
            details = results.get(title)
            if isinstance(details, dict) and details:
                found[title] = details
                updates[title] = details
            elif not self._failed and title in checked:
                # Negative cache only for titles actually queried with success:
                # a network/rate-limit failure must not hide a title for 30 days.
                updates[title] = {}

        if updates:
            try:
                if hasattr(self._details, "set_many"):
                    self._details.set_many(updates)
                else:
                    for title, details in updates.items():
                        self._details.set(title, details)
            except Exception as exc:
                print(f"[IGDBBatch] cache update failed: {exc}")

        for title, details in found.items():
            self._update_catalog(title, details)
            if self._events is not None:
                self._events.emit_event(EVENT_IGDB_DETAILS_READY, title=title)

    def _on_error(self, _message):
        self._failed = True

    def _on_finished(self):
        self._running = False
        self._worker = None
        self._jobs = []

    def _update_catalog(self, title, details):
        if self._catalog is None:
            return
        existing = self._catalog_field(title, "igdb_meta", {})
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)
        for key in ("genres", "themes", "modes"):
            value = details.get(key)
            if value:
                merged[key] = value
        if details.get("rating"):
            merged["rating"] = details["rating"]
        if details.get("release_year"):
            merged["release_year"] = details["release_year"]
        if details.get("release_date"):
            merged["release_date"] = details["release_date"]
        if details.get("cover_url"):
            merged["cover_url"] = details["cover_url"]
        if details.get("igdb_id"):
            merged["igdb_id"] = details["igdb_id"]
        if details.get("steam_appid"):
            merged["steam_appid"] = details["steam_appid"]
        try:
            self._catalog.set_game_field(title, "igdb_meta", merged)
        except Exception as exc:
            print(f"[IGDBBatch] catalog update failed for '{title}': {exc}")
