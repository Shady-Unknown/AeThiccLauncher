"""Pure sorting/filtering and bar-metric helpers for the Stats library.

The bar list changes its primary value with the selected ordering: playtime
order puts hours inside the bar, value order puts the official price/hour
inside it, last-played order puts recency inside it, and so on. Keeping this
logic here makes it testable without Qt.
"""
from __future__ import annotations

import time

from core.price_utils import format_cents

DEFAULT_SORT_KEY = "installed_first"

SORT_OPTIONS = (
    {
        "key": "installed_first",
        "label": "Installed first",
        "primary": "hours",
        "secondary": ("value", "sessions"),
    },
    {
        "key": "playtime_desc",
        "label": "Playtime \u2193",
        "primary": "hours",
        "secondary": ("value", "sessions"),
    },
    {
        "key": "playtime_asc",
        "label": "Playtime \u2191",
        "primary": "hours",
        "secondary": ("value", "sessions"),
    },
    {
        "key": "value_best",
        "label": "Best value / h",
        "primary": "value",
        "secondary": ("hours", "sessions"),
    },
    {
        "key": "value_worst",
        "label": "Worst value / h",
        "primary": "value",
        "secondary": ("hours", "sessions"),
    },
    {
        "key": "sessions_desc",
        "label": "Sessions \u2193",
        "primary": "sessions",
        "secondary": ("hours", "value"),
    },
    {
        "key": "last_played_desc",
        "label": "Last played \u2193",
        "primary": "last_played",
        "secondary": ("hours", "value"),
    },
    {
        "key": "title_asc",
        "label": "Title A\u2013Z",
        "primary": "hours",
        "secondary": ("value", "sessions"),
    },
)

_SORT_BY_KEY = {option["key"]: option for option in SORT_OPTIONS}


def normalize_sort_key(key) -> str:
    key = str(key or DEFAULT_SORT_KEY)
    return key if key in _SORT_BY_KEY else DEFAULT_SORT_KEY


def sort_spec(key) -> dict:
    return _SORT_BY_KEY[normalize_sort_key(key)]


def primary_metric(key) -> str:
    return sort_spec(key)["primary"]


def filter_games(games, installed_only: bool = False):
    games = [game for game in (games or []) if isinstance(game, dict)]
    if not installed_only:
        return games
    return [game for game in games if game.get("installed")]


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _title(game):
    return str(game.get("title") or "").lower()


def _hours(game):
    return _float(game.get("hours_total"))


def _sessions(game):
    return _int(game.get("sessions"))


def _last_played(game):
    return _int(game.get("last_played"))


def _cost(game):
    value = game.get("cost_per_hour")
    return None if value is None else _float(value)


def sort_games(games, sort_key=DEFAULT_SORT_KEY):
    games = [game for game in (games or []) if isinstance(game, dict)]
    key = normalize_sort_key(sort_key)

    if key == "installed_first":
        return sorted(
            games,
            key=lambda game: (
                not bool(game.get("installed")),
                -_hours(game),
                _title(game),
            ),
        )
    if key == "playtime_asc":
        return sorted(games, key=lambda game: (_hours(game), _title(game)))
    if key == "value_best":
        return sorted(
            games,
            key=lambda game: (
                _cost(game) is None,
                _cost(game) if _cost(game) is not None else 0.0,
                _title(game),
            ),
        )
    if key == "value_worst":
        return sorted(
            games,
            key=lambda game: (
                _cost(game) is None,
                -(_cost(game) if _cost(game) is not None else 0.0),
                _title(game),
            ),
        )
    if key == "sessions_desc":
        return sorted(games, key=lambda game: (-_sessions(game), -_hours(game), _title(game)))
    if key == "last_played_desc":
        return sorted(games, key=lambda game: (-_last_played(game), _title(game)))
    if key == "title_asc":
        return sorted(games, key=lambda game: (_title(game), -_hours(game)))
    # playtime_desc
    return sorted(games, key=lambda game: (-_hours(game), _title(game)))


def metric_value(game, metric):
    if metric == "hours":
        return _hours(game)
    if metric == "value":
        return _cost(game)
    if metric == "sessions":
        return _sessions(game)
    if metric == "last_played":
        return _last_played(game)
    return 0


def bar_context(games, metric, now=None):
    """Pre-compute the normalisation values for one visible game list."""
    now = int(now or time.time())
    games = [game for game in (games or []) if isinstance(game, dict)]
    values = [metric_value(game, metric) for game in games]

    if metric == "value":
        positive = [value for value in values if value is not None and value > 0]
        return {
            "metric": metric,
            "min_positive": min(positive) if positive else None,
            "now": now,
        }
    if metric == "last_played":
        ages = [now - value for value in values if value and value > 0]
        return {
            "metric": metric,
            "max_age": max(ages) if ages else 0,
            "now": now,
        }
    numeric = [value for value in values if value is not None]
    return {
        "metric": metric,
        "max_value": max(numeric) if numeric else 0,
        "now": now,
    }


def _clamp(value):
    return max(0.0, min(1.0, float(value)))


def bar_ratio(game, metric, context):
    value = metric_value(game, metric)
    if metric == "value":
        if value is None:
            return 0.0
        if value <= 0:
            return 1.0
        minimum = context.get("min_positive")
        return _clamp((minimum / value) if minimum else 0.0)
    if metric == "last_played":
        if not value or value <= 0:
            return 0.0
        max_age = context.get("max_age") or 0
        if max_age <= 0:
            return 1.0
        age = max(0, int(context.get("now") or value) - int(value))
        return _clamp(1.0 - age / max_age)
    maximum = context.get("max_value") or 0
    if maximum <= 0 or value is None:
        return 0.0
    return _clamp(float(value) / float(maximum))


def relative_age(timestamp, now=None):
    timestamp = _int(timestamp)
    if timestamp <= 0:
        return "never"
    now = int(now or time.time())
    days = max(0, now - timestamp) // 86400
    if days <= 0:
        return "today"
    if days == 1:
        return "1d ago"
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    if days < 730:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _currency(game):
    return str(game.get("price_currency") or "USD")


def format_primary_value(game, metric, now=None):
    if metric == "hours":
        return f"{_hours(game):.1f}h"
    if metric == "value":
        value = _cost(game)
        if value is None:
            return "\u2014"
        return format_cents(int(round(value * 100)), _currency(game)) + "/h"
    if metric == "sessions":
        return str(_sessions(game))
    if metric == "last_played":
        return relative_age(_last_played(game), now)
    return ""


def format_secondary_values(game, spec, now=None):
    values = []
    for metric in spec.get("secondary", ()):
        if metric == "hours":
            values.append(f"{_hours(game):.1f}h")
        elif metric == "value":
            value = _cost(game)
            if value is not None:
                values.append(
                    format_cents(int(round(value * 100)), _currency(game)) + "/h"
                )
        elif metric == "sessions":
            values.append(f"{_sessions(game)} sess")
        elif metric == "last_played":
            values.append(relative_age(_last_played(game), now))
    return values
