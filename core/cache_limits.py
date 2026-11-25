"""Small, pure helpers for bounded in-memory caches.

Caches that grow with browsing or library size need a deterministic cap.
Keeping the trim logic here makes it testable without importing the UI.
"""
from __future__ import annotations


def trim_mapping(mapping: dict, limit: int, timestamp_key: str | None = None) -> int:
    """Drop entries until ``mapping`` holds at most ``limit`` items.

    With ``timestamp_key`` the oldest entries are removed first (the value must
    be a dict with that key); otherwise insertion order is used. Returns how
    many entries were removed.
    """
    if not isinstance(mapping, dict):
        return 0
    limit = max(0, int(limit))
    if len(mapping) <= limit:
        return 0

    if timestamp_key:
        def _stamp(item):
            value = item[1]
            raw = value.get(timestamp_key, 0) if isinstance(value, dict) else 0
            try:
                return float(raw or 0)
            except (TypeError, ValueError):
                return 0.0

        ordered = sorted(mapping.items(), key=_stamp)
    else:
        ordered = list(mapping.items())

    remove = len(mapping) - limit
    for key, _ in ordered[:remove]:
        mapping.pop(key, None)
    return remove


def trim_list(items: list, limit: int) -> int:
    """Keep the last ``limit`` items of a list, in place."""
    limit = max(0, int(limit))
    removed = max(0, len(items) - limit)
    if removed:
        del items[:removed]
    return removed
