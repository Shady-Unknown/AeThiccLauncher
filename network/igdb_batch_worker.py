"""Batch IGDB enrichment for the installed library.

Each job carries a title plus a Steam appid or an IGDB id. Jobs are grouped
into chunks of 50 and resolved with the same detail fields used by the popup,
including developers/publishers so Stats has data for the whole library.
"""
from __future__ import annotations

import time

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from network.igdb_details_worker import DETAIL_FIELDS, STEAM_EXTERNAL_CATEGORY, parse_details
from network.igdb_worker import IGDB_BASE, IGDBWorker

BATCH_SIZE = 50
REQUEST_DELAY = 0.35
MAX_RETRIES = 2


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


class IGDBBatchWorker(QThread):
    batch_ready = pyqtSignal(dict)      # title -> details
    progress = pyqtSignal(int, int)     # done, total
    error_occurred = pyqtSignal(str)

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = list(jobs or [])
        self._stop_flag = False
        self.checked_titles = set()

    def stop(self):
        self._stop_flag = True

    def _request(self, headers, body):
        """Return a list on success (possibly empty), ``None`` on failure."""
        for attempt in range(MAX_RETRIES + 1):
            if self._stop_flag:
                return None
            try:
                response = requests.post(
                    f"{IGDB_BASE}/games", headers=headers, data=body, timeout=20
                )
            except Exception as exc:
                print(f"[IGDBBatch] request error: {exc}")
                return None
            if response.status_code == 200:
                return [g for g in (response.json() or []) if isinstance(g, dict)]
            if response.status_code == 429 and attempt < MAX_RETRIES:
                time.sleep(2.0)
                continue
            print(f"[IGDBBatch] query status {response.status_code}: {response.text[:200]}")
            return None
        return None

    @staticmethod
    def _match_appid(game, appid_to_title):
        for external in game.get("external_games") or []:
            if not isinstance(external, dict):
                continue
            try:
                source = int(
                    external.get("external_game_source")
                    or external.get("category")
                    or -1
                )
            except (TypeError, ValueError):
                source = -1
            uid = str(external.get("uid") or "").strip()
            if source == STEAM_EXTERNAL_CATEGORY:
                title = appid_to_title.get(uid)
                if title:
                    return title
        return None

    def run(self):
        if not self._jobs:
            self.batch_ready.emit({})
            return

        base = IGDBWorker()
        if not base._load_creds() or not base._ensure_token():
            self.error_occurred.emit("IGDB credentials not available.")
            self.batch_ready.emit({})
            return
        headers = base._headers()

        total = len(self._jobs)
        done = 0
        results = {}

        appid_jobs = [job for job in self._jobs if job.get("appid")]
        id_jobs = [job for job in self._jobs if not job.get("appid") and job.get("igdb_id")]

        for chunk in _chunks(appid_jobs, BATCH_SIZE):
            if self._stop_flag:
                break
            appid_to_title = {
                str(job["appid"]): job["title"] for job in chunk
            }
            body = (
                f"{DETAIL_FIELDS} where external_games.uid = ("
                + ",".join(f'"{appid}"' for appid in appid_to_title)
                + f"); limit {len(appid_to_title) + 10};"
            )
            games = self._request(headers, body)
            if games is not None:
                for game in games:
                    title = self._match_appid(game, appid_to_title)
                    if title:
                        results[title] = parse_details(game)
                self.checked_titles.update(appid_to_title.values())
            done += len(chunk)
            self.progress.emit(done, total)
            time.sleep(REQUEST_DELAY)

        for chunk in _chunks(id_jobs, BATCH_SIZE):
            if self._stop_flag:
                break
            id_to_title = {
                str(job["igdb_id"]): job["title"] for job in chunk
            }
            body = (
                f"{DETAIL_FIELDS} where id = ("
                + ",".join(str(igdb_id) for igdb_id in id_to_title)
                + f"); limit {len(id_to_title)};"
            )
            games = self._request(headers, body)
            if games is not None:
                for game in games:
                    title = id_to_title.get(str(game.get("id")))
                    if title:
                        results[title] = parse_details(game)
                self.checked_titles.update(id_to_title.values())
            done += len(chunk)
            self.progress.emit(done, total)
            time.sleep(REQUEST_DELAY)

        self.batch_ready.emit(results)
