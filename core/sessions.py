"""Normalise ``play_sessions.jsonl`` records for Stats and Achievements.

Both engines used to carry their own copy of the same cleaning rules (title
required, seconds inferred from start/end, missing start filled from the end).
A single implementation keeps the numbers identical between the two tabs.
"""
from __future__ import annotations


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_session(record):
    """Return a consistent session dict, or ``None`` when it is unusable."""
    if not isinstance(record, dict):
        return None
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    seconds = _int(record.get("seconds"))
    end = _int(record.get("end") or record.get("at"))
    start = _int(record.get("start"))
    if seconds <= 0 and end > start:
        seconds = end - start
    if seconds <= 0 or end <= 0:
        return None
    if start <= 0:
        start = max(0, end - seconds)
    return {
        "id": record.get("id") or "",
        "title": title,
        "start": start,
        "end": end,
        "seconds": seconds,
        "source": record.get("source") or "",
    }


def normalize_sessions(records):
    """Valid sessions only, in input order."""
    sessions = []
    for record in records or []:
        session = normalize_session(record)
        if session is not None:
            sessions.append(session)
    return sessions
