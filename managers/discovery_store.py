"""Persistence for the Discovery row and category caches.

The store owns the on-disk schema and the atomic write calls; the manager keeps
the in-memory rows and decides when to save. ``load_rows`` raises on I/O or
JSON errors so the caller can keep its current fallback (an empty row set);
serialization and writes do not catch, which leaves the failure visible to the
manager's logging path.
"""
from __future__ import annotations

import json
import os

from core.atomic_io import atomic_write_json
from core.paths import PathManager

CACHE_FILENAME = "discovery_cache.json"
CATEGORY_INDEX_FILENAME = "discovery_categories.json"
CACHE_SCHEMA = 7


def _app_data_path(filename):
    directory = PathManager.get_app_data_dir()
    os.makedirs(str(directory), exist_ok=True)
    return os.path.join(str(directory), filename)


def cache_path():
    return _app_data_path(CACHE_FILENAME)


def category_index_path():
    return _app_data_path(CATEGORY_INDEX_FILENAME)


def load_rows(path, valid_keys, title_for):
    """Load cache rows, returning ``(rows, dropped)``.

    Only entries with a list of cards and the current schema are kept; unknown
    keys, ``all_games`` and incomplete entries are silently skipped. A stale
    schema row is counted so the caller can log it.
    """
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    rows = {}
    dropped = 0
    if not isinstance(data, dict):
        return rows, dropped

    for key, entry in data.items():
        if key == "all_games" or key not in valid_keys:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("cards"), list):
            continue
        if entry.get("schema") != CACHE_SCHEMA:
            dropped += 1
            continue
        entry.setdefault("games", [])
        entry.setdefault("title", title_for(key))
        rows[key] = entry
    return rows, dropped


def rows_payload(data):
    """Serialize the rows to the slim on-disk payload (no derived fields)."""
    payload = {}
    for key, entry in data.items():
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
    return payload


def save_rows(path, payload):
    # Atomic swap: an interrupted write must not leave a truncated cache
    # behind (the next start would throw every row away).
    atomic_write_json(path, payload, separators=(",", ":"), fsync=False)


def load_category_index(path):
    """Load ``(index, fetched_at)``; an empty/invalid index returns ``({}, 0)``."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    index = data.get("index") if isinstance(data, dict) else None
    if isinstance(index, dict) and index:
        return index, float(data.get("fetched_at") or 0)
    return {}, 0.0


def save_category_index(path, index, fetched_at):
    atomic_write_json(path, {
        "fetched_at": fetched_at,
        "index": index,
    }, fsync=False)
