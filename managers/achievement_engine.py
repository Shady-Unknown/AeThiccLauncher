"""Data-driven rules and statistics for launcher achievements.

Definitions are JSON files: ``data/launcher_achievements.json`` for the generic
set and ``data/achievement_packs/*.json`` for themed packs. Adding a normal
achievement means adding an entry there; no engine change is required.

Supported triggers:
- ``{"stat": "hours_total", "op": ">=", "value": 10}``
- ``{"event": "session_end", "where": {...}}``
- ``{"all": [...]}`` / ``{"any": [...]}``
- ``title`` / ``titles`` / ``franchise`` / ``list`` qualifiers for title-based
  stats (``hours_by_title``, ``distinct_titles_by_franchise``, ...).
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime

from core.data_files import user_root, load_json

DEFINITIONS_FILE = "launcher_achievements.json"
PACKS_DIR = "achievement_packs"
TITLES_FILE = "achievement_titles.json"
DEFAULT_TITLES = {
    "half_life": [
        "Half-Life", "Half-Life 2", "Half-Life 2: Episode One",
        "Half-Life 2: Episode Two", "Half-Life: Alyx", "Black Mesa",
        "Half-Life: Opposing Force", "Half-Life: Blue Shift",
    ],
    "portal": ["Portal", "Portal 2"],
    "quake": [
        "Quake", "Quake II", "Quake III Arena", "Quake 4",
        "Quake Champions", "Quake Live",
    ],
    "doom": [
        "DOOM", "DOOM II", "DOOM 3", "DOOM 3: BFG Edition",
        "DOOM (2016)", "DOOM Eternal", "DOOM 64",
    ],
    "elder_scrolls": [
        "The Elder Scrolls III: Morrowind", "The Elder Scrolls IV: Oblivion",
        "The Elder Scrolls V: Skyrim", "The Elder Scrolls Online",
    ],
    "fallout": [
        "Fallout", "Fallout 2", "Fallout 3", "Fallout: New Vegas",
        "Fallout 4", "Fallout 76",
    ],
    "civilization": [
        "Civilization III", "Civilization IV", "Civilization V",
        "Civilization VI", "Civilization VII",
    ],
    "cyberpunk": ["Cyberpunk 2077"],
    "stardew": ["Stardew Valley"],
    "phasmophobia": ["Phasmophobia"],
}

_EDITION_RE = re.compile(
    r"(?i)(game of the year|complete edition|ultimate edition|"
    r"director's cut|deluxe edition|sunset edition|digital edition|"
    r"gold edition|standard edition|definitive edition|enhanced edition|"
    r"remastered|remake|legacy edition|edition)"
)


def normalize_title(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("&", "and").lower()
    text = _EDITION_RE.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text)


def load_title_lists(data=None):
    payload = data if isinstance(data, dict) else load_json(TITLES_FILE, {})
    raw = payload.get("lists") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raw = payload.get("titles") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        raw = DEFAULT_TITLES
    return {
        str(name): [normalize_title(title) for title in titles if title]
        for name, titles in raw.items()
        if isinstance(titles, list)
    }


def _load_pack(path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, dict) or data.get("enabled") is False:
        return []
    pack = data.get("pack") if isinstance(data.get("pack"), dict) else {}
    achievements = data.get("achievements")
    if not isinstance(achievements, list):
        return []
    out = []
    for item in achievements:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        copy = dict(item)
        copy["pack_id"] = pack.get("id") or path.stem
        copy["pack_title"] = pack.get("title") or path.stem
        out.append(copy)
    return out


def load_definitions():
    definitions = []
    generic = load_json(DEFINITIONS_FILE, {})
    if isinstance(generic, dict):
        pack = generic.get("pack") if isinstance(generic.get("pack"), dict) else {}
        for item in generic.get("achievements") or []:
            if isinstance(item, dict) and item.get("id"):
                copy = dict(item)
                copy["pack_id"] = pack.get("id") or "launcher"
                copy["pack_title"] = pack.get("title") or "Launcher"
                definitions.append(copy)

    packs_dir = user_root() / PACKS_DIR
    if packs_dir.is_dir():
        for path in sorted(packs_dir.glob("*.json")):
            definitions.extend(_load_pack(path))

    unique = []
    seen = set()
    for definition in definitions:
        achievement_id = str(definition.get("id") or "")
        if not achievement_id or achievement_id in seen:
            continue
        seen.add(achievement_id)
        unique.append(definition)
    return unique


def _resolve(value):
    return value() if callable(value) else value


def _session_records(records):
    sessions = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        seconds = _int(record.get("seconds"))
        end = _int(record.get("end") or record.get("at"))
        start = _int(record.get("start"))
        if seconds <= 0 and end > start:
            seconds = end - start
        if seconds <= 0 or end <= 0:
            continue
        sessions.append({
            "title": title,
            "seconds": seconds,
            "start": start or max(0, end - seconds),
            "end": end,
        })
    return sessions


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _usage(records, name):
    return [
        record for record in (records or [])
        if isinstance(record, dict) and record.get("event") == name
    ]


def _all_titles(installed, installs, sessions):
    titles = set(installed or {})
    for record in installs or []:
        if isinstance(record, dict) and record.get("action", "add") == "add":
            title = str(record.get("title") or "").strip()
            if title:
                titles.add(title)
    for session in sessions:
        titles.add(session["title"])
    return titles


def _match_list(title, aliases):
    normalized = normalize_title(title)
    return bool(normalized) and normalized in aliases


def compute_stats(state_store=None, installed_games=None, config=None,
                  stats_snapshot=None, title_lists=None, now=None):
    """Return the flat stat dictionary consumed by JSON triggers."""
    now = int(now or time.time())
    config = _resolve(config) or {}
    installed = dict(_resolve(installed_games) or {})
    snapshot = _resolve(stats_snapshot) or {}
    title_lists = title_lists if isinstance(title_lists, dict) else load_title_lists()

    sessions = _session_records(
        state_store.sessions.load() if state_store and hasattr(state_store, "sessions") else []
    )
    installs = (
        state_store.installs.load() if state_store and hasattr(state_store, "installs") else []
    )
    usage = (
        state_store.usage.load() if state_store and hasattr(state_store, "usage") else []
    )
    prices = (
        state_store.prices.load() if state_store and hasattr(state_store, "prices") else []
    )
    prefs = {}
    if state_store is not None and hasattr(state_store, "load_prefs"):
        try:
            prefs = state_store.load_prefs() or {}
        except Exception:
            prefs = {}

    games = snapshot.get("games") or []
    hero = snapshot.get("hero") or {}
    timeline = snapshot.get("timeline") or {}
    installed_game_rows = [game for game in games if game.get("installed")]
    all_titles = _all_titles(installed, installs, sessions)

    tracked_by_title = defaultdict(float)
    session_days = defaultdict(set)
    session_ends = defaultdict(list)
    for session in sessions:
        tracked_by_title[session["title"]] += session["seconds"] / 3600.0
        try:
            day = datetime.fromtimestamp(session["end"]).date().isoformat()
        except (ValueError, OSError):
            continue
        session_days[day].add(session["title"])
        session_ends[session["title"]].append(session["end"])

    hours_by_title = {}
    max_hours_by_title = 0.0
    for game in games:
        title = str(game.get("title") or "")
        hours = _float(game.get("hours_total"))
        if title:
            hours_by_title[normalize_title(title)] = hours
        max_hours_by_title = max(max_hours_by_title, hours)

    if not hours_by_title:
        for title, hours in tracked_by_title.items():
            hours_by_title[normalize_title(title)] = hours
            max_hours_by_title = max(max_hours_by_title, hours)

    developer_titles = defaultdict(set)
    publisher_titles = defaultdict(set)
    genres = set()
    gold_count = 0
    for game in installed_game_rows:
        title = str(game.get("title") or "")
        for name in game.get("developers") or []:
            developer_titles[str(name)].add(title)
        for name in game.get("publishers") or []:
            publisher_titles[str(name)].add(title)
        genres.update(str(name) for name in (game.get("genres") or []) if name)
        if game.get("gold_tier"):
            gold_count += 1

    weekend_hours = 0.0
    weekday = timeline.get("by_weekday") or []
    if len(weekday) >= 7:
        weekend_hours = float(weekday[5]) + float(weekday[6])
    total_timeline_hours = sum(_float(value) for value in weekday)

    comeback = False
    for ends in session_ends.values():
        ordered = sorted(ends)
        if any(b - a >= 90 * 86400 for a, b in zip(ordered, ordered[1:])):
            comeback = True
            break

    never_launched = sum(
        1 for game in installed_game_rows
        if not game.get("sessions") and not game.get("hours_tracked")
    )
    if not installed_game_rows:
        never_launched = sum(
            1 for title in installed
            if not any(session["title"] == title for session in sessions)
        )

    total_hours = _float(hero.get("hours_total"))
    if not total_hours:
        total_hours = sum(session["seconds"] for session in sessions) / 3600.0

    max_session = max((session["seconds"] for session in sessions), default=0)
    max_titles_day = max((len(titles) for titles in session_days.values()), default=0)

    reinstall_count = 0
    seen_removed = set()
    seen_added = set()
    for record in sorted(
        (item for item in installs if isinstance(item, dict)),
        key=lambda item: _int(item.get("at")),
    ):
        title = str(record.get("title") or "")
        action = record.get("action") or "add"
        if not title:
            continue
        if action == "add":
            if title in seen_removed:
                reinstall_count += 1
                seen_removed.discard(title)
            seen_added.add(title)
        elif action == "remove":
            seen_removed.add(title)

    discounted_titles = {
        str(record.get("title") or "")
        for record in prices
        if isinstance(record, dict)
        and (record.get("discount_percent") or 0) > 0
        or isinstance(record, dict) and record.get("on_sale")
    }
    discounted_titles.discard("")

    hosters = {
        str(record.get("hoster") or "")
        for record in _usage(usage, "hoster_used")
        if record.get("hoster")
    }
    trailer_titles = {
        str(record.get("title") or "")
        for record in _usage(usage, "trailer_watched")
        if record.get("title")
    }
    cinema_rate = _float((config.get("stats") or {}).get("cinema_rate"), 6.0)
    below_cinema = sum(
        1 for game in games
        if game.get("cost_per_hour") is not None
        and _float(game.get("cost_per_hour")) < cinema_rate
    )

    distinct_by_list = {}
    hours_by_list = {}
    for list_name, aliases in title_lists.items():
        matched = [title for title in all_titles if _match_list(title, aliases)]
        distinct_by_list[list_name] = len(set(matched))
        hours_by_list[list_name] = sum(
            hours_by_title.get(normalize_title(title), 0.0)
            for title in set(matched)
        )

    return {
        "launcher_starts": len(_usage(usage, "launcher_start")),
        "library_folder_configured": bool(config.get("watched_folders")),
        "temp_folder_customized": bool(config.get("default_temp_folder")),
        "theme_customized": str(config.get("current_theme") or "PurpleRip") != "PurpleRip",
        "region_non_default": str(
            prefs.get("region") or (config.get("stats") or {}).get("region") or "US"
        ).upper() != "US",
        "distinct_titles_ever": len(all_titles),
        "max_titles_by_developer": max((len(v) for v in developer_titles.values()), default=0),
        "max_titles_by_publisher": max((len(v) for v in publisher_titles.values()), default=0),
        "genres_count_current": len(genres),
        "gold_tier_count_current": gold_count,
        "games_removed_total": sum(
            1 for record in installs
            if isinstance(record, dict) and record.get("action") == "remove"
        ),
        "reinstalled_titles_count": reinstall_count,
        "weekend_hours_ratio": (weekend_hours / total_timeline_hours) if total_timeline_hours else 0.0,
        "streak_days": _int(hero.get("best_streak")),
        "launches_total": len(_usage(usage, "game_launched")),
        "max_distinct_titles_per_day": max_titles_day,
        "max_session_seconds": max_session,
        "sessions_total": len(sessions),
        "hours_total": total_hours,
        "max_hours_by_title": max_hours_by_title,
        "comeback_detected": comeback,
        "never_launched_count": never_launched,
        "hosters_used": len(hosters),
        "resumes_total": len(_usage(usage, "download_resumed")),
        "trailers_watched_titles": len(trailer_titles),
        "discounted_titles_seen": len(discounted_titles),
        "orphans_recovered": len(_usage(usage, "orphan_recovered")),
        "backups_total": len(_usage(usage, "backup_created")),
        "updates_applied": len(_usage(usage, "update_applied")),
        "games_below_cinema_rate": below_cinema,
        "hours_by_title": hours_by_title,
        "hours_by_franchise": hours_by_list,
        "distinct_titles_by_franchise": distinct_by_list,
        "distinct_titles_by_list": distinct_by_list,
        "hours_by_list": hours_by_list,
    }


def _compare(actual, operator, expected):
    operator = operator or ">="
    if isinstance(expected, bool):
        return bool(actual) == expected
    if operator == "in":
        try:
            return actual in expected
        except TypeError:
            return False
    try:
        actual_number = float(actual)
        expected_number = float(expected)
    except (TypeError, ValueError):
        actual_number = expected_number = None
    if operator == ">=":
        return actual_number is not None and actual_number >= expected_number
    if operator == ">":
        return actual_number is not None and actual_number > expected_number
    if operator == "<=":
        return actual_number is not None and actual_number <= expected_number
    if operator == "<":
        return actual_number is not None and actual_number < expected_number
    if operator == "!=":
        return actual != expected
    return actual == expected


def _stat_value(trigger, stats):
    name = trigger.get("stat") or ""
    if name == "hours_by_title" and trigger.get("title"):
        return stats.get("hours_by_title", {}).get(normalize_title(trigger["title"]), 0.0)
    if name in ("distinct_titles_by_franchise", "distinct_titles_by_list"):
        key = trigger.get("franchise") or trigger.get("list")
        return stats.get(name, {}).get(str(key), 0)
    if name in ("hours_by_franchise", "hours_by_list"):
        key = trigger.get("franchise") or trigger.get("list")
        return stats.get(name, {}).get(str(key), 0.0)
    if name == "hours_by_title" and trigger.get("titles"):
        return sum(
            stats.get("hours_by_title", {}).get(normalize_title(title), 0.0)
            for title in trigger["titles"]
        )
    return stats.get(name, 0)


def _where_matches(where, event_name, payload, now):
    if not isinstance(where, dict):
        return True
    payload = payload or {}
    event_at = _int(payload.get("end") or payload.get("at") or now)
    for key, expected in where.items():
        if key == "local_hour_min":
            hour = datetime.fromtimestamp(event_at).hour
            if hour < int(expected):
                return False
        elif key == "local_hour_max":
            hour = datetime.fromtimestamp(event_at).hour
            if hour >= int(expected):
                return False
        elif key == "os":
            actual = payload.get("os") or sys.platform
            if str(actual).lower() != str(expected).lower():
                return False
        elif key == "flatpak":
            if bool(payload.get("flatpak")) != bool(expected):
                return False
        elif key == "title":
            if normalize_title(payload.get("title")) != normalize_title(expected):
                return False
        elif key in payload:
            if payload.get(key) != expected:
                return False
    return True


def evaluate_trigger(trigger, stats, event_name=None, payload=None, now=None):
    if not isinstance(trigger, dict):
        return False
    if "all" in trigger:
        return all(
            evaluate_trigger(item, stats, event_name, payload, now)
            for item in trigger.get("all") or []
        )
    if "any" in trigger:
        return any(
            evaluate_trigger(item, stats, event_name, payload, now)
            for item in trigger.get("any") or []
        )
    if "event" in trigger:
        if event_name != trigger.get("event"):
            return False
        return _where_matches(trigger.get("where"), event_name, payload, now or int(time.time()))
    if "stat" in trigger:
        return _compare(
            _stat_value(trigger, stats),
            trigger.get("op") or ">=",
            trigger.get("value"),
        )
    return False


def trigger_has_event(trigger):
    if not isinstance(trigger, dict):
        return False
    if "event" in trigger:
        return True
    if "all" in trigger:
        return any(trigger_has_event(item) for item in trigger.get("all") or [])
    if "any" in trigger:
        return any(trigger_has_event(item) for item in trigger.get("any") or [])
    return False


def progress_for(definition, stats):
    trigger = definition.get("trigger") or {}
    candidates = [trigger]
    if "all" in trigger:
        candidates = [item for item in trigger.get("all") or [] if isinstance(item, dict)]
    for item in candidates:
        if "stat" not in item:
            continue
        target = item.get("value")
        if isinstance(target, bool) or not isinstance(target, (int, float)):
            continue
        current = _stat_value(item, stats)
        try:
            return float(current), float(target)
        except (TypeError, ValueError):
            return None
    return None


def load_event_history(state_store):
    """Historical events replayed once for retroactive event achievements."""
    events = []
    if state_store is None:
        return events
    for record in state_store.usage.load() if hasattr(state_store, "usage") else []:
        if not isinstance(record, dict) or not record.get("event"):
            continue
        payload = dict(record)
        payload.setdefault("at", record.get("at"))
        events.append((record["event"], payload))
    for record in state_store.installs.load() if hasattr(state_store, "installs") else []:
        if not isinstance(record, dict) or not record.get("title"):
            continue
        action = record.get("action") or "add"
        events.append((
            "game_installed" if action == "add" else "game_removed",
            dict(record),
        ))
    for record in state_store.sessions.load() if hasattr(state_store, "sessions") else []:
        if not isinstance(record, dict) or not record.get("title"):
            continue
        events.append(("session_end", dict(record)))
    events.sort(key=lambda item: _int((item[1] or {}).get("at")))
    return events
