"""Filesystem scan for the library, kept off the GUI thread.

The worker only reads folders and metadata; building the sidebar rows stays in
``LibraryManager`` on the GUI thread.
"""
from __future__ import annotations

import json
import logging
import os

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)


def config_score(meta):
    """How many optional configuration fields a metadata dict has."""
    score = 0
    if meta.get('savegame_path_win') or meta.get('savegame_path_linux') or meta.get('savegame_path_custom'):
        score += 1
    if meta.get('steam_shortcut_id'):
        score += 1
    if meta.get('executable_path'):
        score += 1
    return score


def scan_library_data(watched_folders):
    """Return ordered ``[(title, meta), ...]`` for every game folder."""
    entries = {}
    for folder in watched_folders or []:
        if not os.path.exists(folder):
            continue
        try:
            names = os.listdir(folder)
        except OSError as exc:
            log.exception(f"[Scan] error scanning folder {folder}: {exc}")
            continue
        for sub in names:
            if sub.lower() == "patches":
                continue
            game_dir = os.path.join(folder, sub)
            if not os.path.isdir(game_dir):
                continue
            meta_path = os.path.join(game_dir, "steamrip_meta.json")
            if not os.path.exists(meta_path):
                entries.setdefault(sub, {
                    "is_orphan": True,
                    "install_dir": game_dir,
                    "folder_name": sub,
                })
                continue
            try:
                with open(meta_path, 'r', encoding='utf-8') as fh:
                    meta = json.load(fh)
            except Exception as exc:
                log.exception(f"[Scan] error reading meta in {game_dir}: {exc}")
                entries.setdefault(sub, {
                    "is_orphan": True,
                    "install_dir": game_dir,
                    "folder_name": sub,
                })
                continue
            meta['install_dir'] = game_dir
            title = meta.get('title', sub)
            existing = entries.get(title)
            if existing is not None and not existing.get('is_orphan'):
                if config_score(meta) > config_score(existing):
                    entries[title] = meta
                continue
            entries[title] = meta
    return list(entries.items())


class LibraryScanWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, watched_folders, parent=None):
        super().__init__(parent)
        self.watched_folders = list(watched_folders or [])

    def run(self):
        try:
            entries = scan_library_data(self.watched_folders)
        except Exception as exc:
            log.exception(f"[Scan] library scan failed: {exc}")
            self.failed.emit(str(exc))
            return
        self.finished_ok.emit(entries)
