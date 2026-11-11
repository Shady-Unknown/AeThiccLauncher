"""Deterministic sample snapshot used by the phrase tests only.

Production Stats uses ``StatsManager.snapshot()`` and real launcher state. This
module is not shipped with the app.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta

RANGE_DAYS = {"30d": 30, "90d": 90, "365d": 365, "all": 180}


def _make_game(title, hours, price, genres, dev, *, installed=False,
               gold=False, store="steam", rating=80.0, has_igdb=True,
               sessions=None, steam_2wks=0.0, backup_count=0):
    tracked = round(hours * 0.6, 1)
    imported = round(max(0.0, hours - tracked), 1)
    if sessions is None:
        sessions = max(1, int(hours // 4)) if hours > 0 else 0
    return {
        "title": title,
        "hours_tracked": tracked,
        "hours_imported": imported,
        "hours_total": round(tracked + imported, 1),
        "sessions": sessions,
        "avg_session_minutes": round(hours * 60.0 / sessions, 1) if sessions else None,
        "price_cents": price,
        "price_currency": "USD",
        "price_store": store,
        "price_on_sale": False,
        "genres": genres,
        "developers": [dev],
        "publishers": [dev],
        "gold_tier": gold,
        "rating": rating,
        "has_igdb": has_igdb,
        "steam_2wks_hours": steam_2wks,
        "install_size_bytes": (24 * 1024 ** 3) if installed else None,
        "backup_count": backup_count,
        "backup_size_bytes": (120 * 1024 ** 2) if backup_count else 0,
        "installed": installed,
    }


_INSTALLED = [
    _make_game("Neon Drift", 82.5, 3999, ["Racing", "Indie"], "Vector Bloom",
               installed=True, gold=True, rating=88.0, sessions=18,
               steam_2wks=3.0, backup_count=3),
    _make_game("Ashen Circuit", 45.0, 2499, ["Action", "RPG"], "Iron Lantern",
               installed=True, gold=True, rating=84.0, sessions=9,
               steam_2wks=1.5, backup_count=5),
    _make_game("Void Garden", 27.0, 5999, ["Adventure", "Puzzle"], "Quiet Signal",
               installed=True, rating=91.0, sessions=5, store="gog",
               backup_count=1),
    _make_game("Static Bloom", 12.5, 1999, ["Horror", "Indie"], "Soft Static",
               installed=True, rating=79.0, sessions=2, backup_count=2),
    _make_game("Midnight Relay", 0.0, 1499, ["RPG"], "Night Bus",
               installed=True, rating=76.0),
    _make_game("Paper Satellites", 0.0, 999, ["Puzzle"], "Fold Studio",
               installed=True, gold=True, rating=82.0, has_igdb=False,
               store="gog"),
]

_PAST = [
    _make_game("Glass Harbor", 63.0, 2999, ["Adventure", "Indie"], "Tidebreak",
               gold=True, rating=87.0, sessions=12),
    _make_game("Copper Vale", 38.0, 1999, ["RPG", "Strategy"], "Copperline",
               rating=81.0, sessions=11),
    _make_game("Solar Wake", 22.0, 3999, ["Action"], "Sunforge",
               rating=83.0, sessions=6),
    _make_game("Hollow Signal", 15.0, 899, ["Horror"], "Dead Air",
               rating=77.0, sessions=4),
    _make_game("Rust & Bloom", 9.0, 1499, ["Simulation"], "Greenhouse",
               rating=80.0, sessions=5),
    _make_game("Quiet Orbit", 7.0, 2499, ["Adventure"], "Orbit Works",
               rating=85.0, sessions=3),
    _make_game("Fable Circuit", 6.0, 1999, ["RPG"], "Storyloop",
               rating=78.0, sessions=2),
    _make_game("Driftwood", 5.0, 999, ["Indie"], "Salt & Pine",
               rating=82.0, sessions=3),
    _make_game("Neon Ruins", 4.0, 2999, ["Action", "RPG"], "Afterglow",
               rating=79.0, sessions=2),
    _make_game("Pale Horizon", 3.0, 1999, ["Adventure"], "Far Field",
               rating=84.0, sessions=1),
    _make_game("Signal Lost", 2.0, 999, ["Horror"], "Dead Air",
               rating=74.0, sessions=1),
    _make_game("Sunday Static", 1.5, 799, ["Puzzle"], "Soft Static",
               rating=81.0, sessions=1),
    _make_game("Ember Line", 0.0, 1499, ["Racing"], "Vector Bloom",
               rating=78.0),
    _make_game("Low Tide", 0.0, 1199, ["Indie"], "Salt & Pine",
               rating=80.0),
]

GAMES = _INSTALLED + _PAST


def _timeline(days, now):
    start = datetime.fromtimestamp(now).date() - timedelta(days=days - 1)
    points = []
    for index in range(days):
        value = 0.8 + ((index * 7) % 11) * 0.22
        if index % 9 == 0:
            value += 2.4
        if index % 13 == 0:
            value += 1.1
        points.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "hours": round(value, 3),
        })
    return points


def _seed_for(title):
    return sum((index + 1) * ord(char) for index, char in enumerate(str(title or ""))) % 997


def build_stub_game_timeline(game, range_key="90d", now=None):
    """Deterministic per-game series, with the same shape as StatsManager."""
    game = game if isinstance(game, dict) else {}
    now = int(now or time.time())
    days = RANGE_DAYS.get(range_key, 90)
    hours = float(game.get("hours_total") or 0)
    sessions = int(game.get("sessions") or 0)
    seed = _seed_for(game.get("title"))
    start = datetime.fromtimestamp(now).date() - timedelta(days=days - 1)

    weights = [1.0 + 0.7 * math.sin((index + seed) * 0.37) for index in range(days)]
    total_weight = sum(weights) or 1.0
    day_hours = [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "hours": round(hours * weight / total_weight, 3),
        }
        for index, weight in enumerate(weights)
    ]

    hour_weights = [0.3] * 24
    for hour, weight in (
        (9, 1.5), (10, 1.0), (14, 1.2), (18, 2.0),
        (20, 3.0), (21, 4.0), (22, 2.5), (23, 1.2),
    ):
        hour_weights[hour] = weight
    total_hour_weight = sum(hour_weights) or 1.0
    by_hour = [round(hours * weight / total_hour_weight, 3) for weight in hour_weights]

    weekday_weights = [0.8, 0.7, 0.8, 0.9, 1.2, 1.6, 1.4]
    total_weekday_weight = sum(weekday_weights)
    by_weekday = [
        round(hours * weight / total_weekday_weight, 3)
        for weight in weekday_weights
    ]

    labels = ("<30m", "30-60m", "1-2h", "2-4h", "4h+")
    ratios = (0.15, 0.25, 0.30, 0.20, 0.10)
    counts = [int(round(sessions * ratio)) for ratio in ratios] if sessions else [0] * 5
    if sessions:
        counts[-1] = max(0, counts[-1] + sessions - sum(counts[:4]))
    bucket_hours = [round(hours * ratio, 3) for ratio in ratios] if hours else [0.0] * 5

    return {
        "title": str(game.get("title") or ""),
        "days": day_hours,
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "duration_buckets": [
            {"label": label, "count": count, "hours": bucket_hour}
            for label, count, bucket_hour in zip(labels, counts, bucket_hours)
        ],
        "total_hours": round(hours, 3),
        "sessions": sessions,
    }


def build_stub_snapshot(range_key="90d", region="US", now=None):
    now = int(now or time.time())
    days = RANGE_DAYS.get(range_key, 90)
    games = [dict(game) for game in GAMES]
    for index, game in enumerate(games):
        game["install_dir"] = ""
        game["last_played"] = now - 3600 * (index + 1)
        game["steam_last_played"] = 0
        game["cover_url"] = ""
        game["cost_per_hour"] = (
            game["price_cents"] / 100.0 / game["hours_total"]
            if game["hours_total"] > 0 else None
        )

    played = [g for g in games if g["hours_total"] > 0]
    installed = [g for g in games if g["installed"]]
    known = [g for g in installed if g["price_cents"] is not None]
    played_known = [g for g in played if g["price_cents"] is not None]

    tracked = sum(g["hours_tracked"] for g in games)
    imported = sum(g["hours_imported"] for g in games)
    total = sum(g["hours_total"] for g in games)
    library_value = sum(g["price_cents"] for g in known)
    unplayed_value = sum(
        g["price_cents"] for g in installed
        if g["price_cents"] is not None and g["hours_total"] <= 0
    )
    known_hours = sum(g["hours_total"] for g in played_known)
    avg_cost = (
        sum(g["price_cents"] for g in played_known) / 100.0 / known_hours
        if known_hours > 0 else None
    )

    developers = {}
    publishers = {}
    genres = {}
    for game in played:
        for name in game["developers"]:
            entry = developers.setdefault(name, {"name": name, "titles": 0, "hours": 0.0, "gold_tier": 0})
            entry["titles"] += 1
            entry["hours"] += game["hours_total"]
            entry["gold_tier"] += 1 if game["gold_tier"] else 0
        for name in game["publishers"]:
            entry = publishers.setdefault(name, {"name": name, "titles": 0, "hours": 0.0, "gold_tier": 0})
            entry["titles"] += 1
            entry["hours"] += game["hours_total"]
            entry["gold_tier"] += 1 if game["gold_tier"] else 0
        if game["genres"]:
            share = game["hours_total"] / len(game["genres"])
            for name in game["genres"]:
                entry = genres.setdefault(name, {"name": name, "titles": 0, "hours": 0.0})
                entry["titles"] += 1
                entry["hours"] += share

    def _sorted(values):
        return sorted(values.values(), key=lambda item: (-item["hours"], item["name"].lower()))

    played_sorted = sorted(played, key=lambda item: (-item["hours_total"], item["title"].lower()))
    timeline_days = _timeline(days, now)
    by_hour = [0.0] * 24
    for hour, value in ((9, 1.2), (10, 0.8), (14, 1.5), (18, 2.1), (20, 3.4), (21, 4.2), (22, 2.8), (23, 1.0)):
        by_hour[hour] = value
    by_weekday = [8.5, 6.2, 7.4, 9.1, 12.3, 18.7, 15.4]

    return {
        "generated_at": now,
        "range": range_key,
        "region": str(region or "US").upper(),
        "hero": {
            "hours_tracked": round(tracked, 2),
            "hours_imported": round(imported, 2),
            "hours_total": round(total, 2),
            "games_installed": len(installed),
            "games_played": len(played),
            "games_never_played": len(installed) - len([g for g in installed if g["hours_total"] > 0]),
            "sessions": sum(g["sessions"] for g in games),
            "avg_session_minutes": 72.0,
            "median_session_minutes": 51.0,
            "longest_session": {
                "title": "Static Bloom",
                "start": now - 4 * 3600,
                "end": now - 3600,
                "minutes": 195.0,
            },
            "avg_cost_per_hour": round(avg_cost, 2) if avg_cost is not None else None,
            "library_value_cents": library_value,
            "current_streak": 4,
            "best_streak": 9,
            "night_owl_percent": 24.0,
            "weekend_percent": 31.0,
            "coverage": {
                "igdb_percent": 83.3,
                "price_percent": 100.0,
                "tracked_percent": round(tracked / total * 100.0, 1) if total else 0.0,
            },
        },
        "games": games,
        "top_games": played_sorted[:10],
        "developers": _sorted(developers),
        "publishers": _sorted(publishers),
        "genres": _sorted(genres),
        "timeline": {
            "days": timeline_days,
            "by_hour": by_hour,
            "by_weekday": by_weekday,
            "duration_buckets": [
                {"label": "<30m", "count": 3, "hours": 1.2},
                {"label": "30-60m", "count": 8, "hours": 6.1},
                {"label": "1-2h", "count": 14, "hours": 19.8},
                {"label": "2-4h", "count": 7, "hours": 22.4},
                {"label": "4h+", "count": 2, "hours": 10.2},
            ],
        },
        "finance": {
            "library_value_cents": library_value,
            "known_prices": len(known),
            "unknown_prices": len(installed) - len(known),
            "unplayed_value_cents": unplayed_value,
            "price_coverage_percent": 100.0,
        },
    }
