"""Stats engine: one on-demand snapshot for the Stats tab.

The manager only reads local data (state files, installed metadata, catalog
enrichment and the Steam stats cache). It never touches the network and never
writes files. That keeps it cheap enough to rebuild the snapshot after a game
session or a price refresh.

``plan.md`` §8 defines the metrics; this module implements the S1 subset
(hero, per-game, developers/publishers, genres, timeline and finance).
"""
from __future__ import annotations

import statistics
import time
from datetime import date, datetime, timedelta

from core import events as ev
from core.price_utils import detect_currency, parse_price_to_cents

RANGE_DAYS = {
    "30d": 30,
    "90d": 90,
    "365d": 365,
    "all": None,
}

DURATION_BUCKETS = (
    ("<30m", 0, 30 * 60),
    ("30-60m", 30 * 60, 60 * 60),
    ("1-2h", 60 * 60, 2 * 60 * 60),
    ("2-4h", 2 * 60 * 60, 4 * 60 * 60),
    ("4h+", 4 * 60 * 60, None),
)

INVALIDATING_EVENTS = (
    ev.EVENT_SESSION_END,
    ev.EVENT_GAME_INSTALLED,
    ev.EVENT_GAME_REMOVED,
    ev.EVENT_PRICE_REFRESHED,
    ev.EVENT_IGDB_DETAILS_READY,
    ev.EVENT_REGION_CHANGED,
    ev.EVENT_PREFS_CHANGED,
    ev.EVENT_BACKUP_CREATED,
)


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


def _signed_appid(value):
    try:
        appid = int(value)
    except (TypeError, ValueError):
        return None
    return appid - 0x100000000 if appid >= 0x80000000 else appid


class StatsManager:
    def __init__(self, state_store=None, installed_games=None, catalog=None,
                 details=None, gold_tier=None, steam_stats=None, config=None,
                 backup_stats=None, clock=None):
        self._state = state_store
        self._installed_games = installed_games
        self._catalog = catalog
        self._details = details
        self._gold_tier = gold_tier
        self._steam_stats = steam_stats
        self._config = config
        self._backup_stats = backup_stats
        self._clock = clock or time.time

        self._cache = None
        self._cache_key = None
        self._game_timeline_cache = {}
        self._global_timeline_cache = {}

    # ------------------------------------------------------------------ events

    def bind(self, events) -> None:
        for name in INVALIDATING_EVENTS:
            events.subscribe(name, self._on_event)

    def _on_event(self, _payload) -> None:
        self.invalidate()

    def invalidate(self) -> None:
        self._cache = None
        self._cache_key = None
        self._game_timeline_cache.clear()
        self._global_timeline_cache.clear()

    # ------------------------------------------------------------------ public

    def snapshot(self, range_key=None, region=None) -> dict:
        config = self._config_value()
        prefs = self._prefs()
        stats_cfg = config.get("stats", {}) if isinstance(config, dict) else {}
        prefs = prefs if isinstance(prefs, dict) else {}

        range_key = self._normalize_range(
            range_key or stats_cfg.get("range") or prefs.get("range") or "90d"
        )
        region = str(
            region or stats_cfg.get("region") or prefs.get("region") or "US"
        ).upper()

        key = (range_key, region)
        if self._cache is not None and self._cache_key == key:
            return self._cache

        snapshot = self._build(range_key, region)
        self._cache = snapshot
        self._cache_key = key
        return snapshot

    def game_timeline(self, title, range_key=None) -> dict:
        """Timeline for one title, using the same range rules as the tab."""
        config = self._config_value()
        prefs = self._prefs()
        stats_cfg = config.get("stats", {}) if isinstance(config, dict) else {}
        prefs = prefs if isinstance(prefs, dict) else {}
        range_key = self._normalize_range(
            range_key or stats_cfg.get("range") or prefs.get("range") or "90d"
        )
        title = str(title or "")
        key = (title, range_key)
        cached = self._game_timeline_cache.get(key)
        if cached is not None:
            return cached

        sessions = [
            session for session in self._session_list(self._records("sessions"))
            if session["title"] == title
        ]
        timeline = self._timeline_from_sessions(sessions, range_key, self._now())
        timeline["title"] = title
        self._game_timeline_cache[key] = timeline
        return timeline

    def global_timeline(self, range_key=None) -> dict:
        """Global timeline for a specific range (used by the S3 heatmap)."""
        config = self._config_value()
        prefs = self._prefs()
        stats_cfg = config.get("stats", {}) if isinstance(config, dict) else {}
        prefs = prefs if isinstance(prefs, dict) else {}
        range_key = self._normalize_range(
            range_key or stats_cfg.get("range") or prefs.get("range") or "90d"
        )
        cached = self._global_timeline_cache.get(range_key)
        if cached is not None:
            return cached
        sessions = self._session_list(self._records("sessions"))
        timeline = self._timeline_from_sessions(sessions, range_key, self._now())
        self._global_timeline_cache[range_key] = timeline
        return timeline

    # ------------------------------------------------------------------ inputs

    def _config_value(self):
        return self._resolve(self._config) or {}

    def _installed(self) -> dict:
        value = self._resolve(self._installed_games)
        return dict(value) if isinstance(value, dict) else {}

    def _prefs(self) -> dict:
        if self._state is None or not hasattr(self._state, "load_prefs"):
            return {}
        try:
            return self._state.load_prefs() or {}
        except Exception:
            return {}

    @staticmethod
    def _resolve(value):
        return value() if callable(value) else value

    def _records(self, store_name: str) -> list[dict]:
        if self._state is None:
            return []
        store = getattr(self._state, store_name, None)
        if store is None:
            return []
        try:
            return store.load()
        except Exception:
            return []

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _normalize_range(value) -> str:
        key = str(value or "90d").strip().lower()
        if key in ("30", "30d", "month"):
            return "30d"
        if key in ("90", "90d", "quarter"):
            return "90d"
        if key in ("365", "365d", "year", "1y"):
            return "365d"
        if key in ("all", "alltime", "all-time", "tot"):
            return "all"
        return "90d"

    # ------------------------------------------------------------------ prices

    @staticmethod
    def _price_from_data(data, store, region):
        if not isinstance(data, dict):
            return None
        cents = data.get("final_cents")
        if cents is None:
            cents = parse_price_to_cents(data.get("final_price"))
        if cents is None:
            return None
        currency = data.get("currency") or detect_currency(data.get("final_price", ""))
        return {
            "cents": int(cents),
            "currency": currency or "USD",
            "store": store,
            "region": region,
            "on_sale": bool(data.get("on_sale")),
            "discount_percent": _int(data.get("discount_percent")),
        }

    def _price_index(self, region: str) -> dict:
        index = {}
        for record in self._records("prices"):
            if not isinstance(record, dict):
                continue
            if str(record.get("region") or "US").upper() != region:
                continue
            key = (str(record.get("title") or ""), str(record.get("store") or ""))
            current = index.get(key)
            if current is None or _int(record.get("at")) >= _int(current.get("at")):
                index[key] = record
        return index

    def _price_for(self, title: str, region: str, index: dict):
        prices = {}
        field = self._catalog_field(title, "prices", {})
        if isinstance(field, dict):
            prices = field
        stored_region = str(prices.get("region") or "").upper()
        if not stored_region or stored_region == region:
            for store in ("steam", "gog"):
                price = self._price_from_data(prices.get(store), store, region)
                if price is not None:
                    return price

        for store in ("steam", "gog"):
            record = index.get((title, store))
            if not record:
                continue
            price = self._price_from_data(record, store, region)
            if price is not None:
                return price
        return None

    # ------------------------------------------------------------------ catalog

    def _catalog_field(self, title, field, default=None):
        if self._catalog is None:
            return default
        try:
            return self._catalog.get_game_field(title, field, default)
        except Exception:
            return default

    def _steam_appid(self, title, meta):
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

    def _details_for(self, title):
        details = {}
        if self._details is not None:
            try:
                value = self._details.get(title)
                if isinstance(value, dict):
                    details = value
            except Exception:
                details = {}

        igdb_meta = self._catalog_field(title, "igdb_meta", {})
        if not isinstance(igdb_meta, dict):
            igdb_meta = {}

        genres = details.get("genres") or igdb_meta.get("genres") or []
        developers = details.get("developers") or igdb_meta.get("developers") or []
        publishers = details.get("publishers") or igdb_meta.get("publishers") or []
        return {
            "genres": [str(g) for g in genres if g],
            "developers": [str(d) for d in developers if d],
            "publishers": [str(p) for p in publishers if p],
            "rating": _float(details.get("rating") or igdb_meta.get("rating")),
            "release_date": details.get("release_date") or "",
            "cover_url": details.get("cover_url") or igdb_meta.get("cover_url") or "",
            "has_igdb": bool(genres or developers or publishers or details.get("summary")),
        }

    def _steam_entry(self, title, meta, cache):
        appid = self._steam_appid(title, meta)
        if not appid:
            return {}
        signed = _signed_appid(appid)
        if signed is None:
            return {}
        apps = cache.get("apps") if isinstance(cache, dict) else {}
        if not isinstance(apps, dict):
            return {}
        entry = apps.get(str(signed))
        return entry if isinstance(entry, dict) else {}

    def _is_gold(self, title):
        if callable(self._gold_tier):
            try:
                return bool(self._gold_tier(title))
            except Exception:
                return False
        if self._catalog is not None:
            try:
                return bool(self._catalog.is_gold_tier(title))
            except Exception:
                return False
        return False

    def _backup_for(self, title):
        if self._backup_stats is None:
            return {}
        try:
            value = self._backup_stats(title)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    # ------------------------------------------------------------------ sessions

    @staticmethod
    def _session_list(records):
        sessions = []
        for record in records:
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
            if start <= 0:
                start = max(0, end - seconds)
            sessions.append({
                "id": record.get("id") or "",
                "title": title,
                "start": start,
                "end": end,
                "seconds": seconds,
                "source": record.get("source") or "",
            })
        return sessions

    @staticmethod
    def _local_date(timestamp: int) -> date:
        return datetime.fromtimestamp(timestamp).date()

    @staticmethod
    def _streaks(sessions, now: int):
        days = sorted({StatsManager._local_date(s["end"]) for s in sessions})
        best = 0
        run = 0
        previous = None
        for day in days:
            if previous is not None and day == previous + timedelta(days=1):
                run += 1
            else:
                run = 1
            best = max(best, run)
            previous = day

        today = datetime.fromtimestamp(now).date()
        day_set = set(days)
        cursor = today if today in day_set else today - timedelta(days=1)
        current = 0
        while cursor in day_set:
            current += 1
            cursor -= timedelta(days=1)
        return current, best

    @staticmethod
    def _timeline_from_sessions(sessions, range_key: str, now: int) -> dict:
        days = RANGE_DAYS.get(range_key)
        cutoff = (now - days * 86400) if days is not None else None
        range_sessions = [
            session for session in sessions
            if cutoff is None or session["end"] >= cutoff
        ]

        by_hour = [0.0] * 24
        by_weekday = [0.0] * 7
        day_hours = {}
        buckets = {
            label: {"label": label, "count": 0, "hours": 0.0}
            for label, _, _ in DURATION_BUCKETS
        }
        for session in range_sessions:
            local_end = datetime.fromtimestamp(session["end"])
            hours = session["seconds"] / 3600.0
            by_hour[local_end.hour] += hours
            by_weekday[local_end.weekday()] += hours
            day_key = local_end.date().isoformat()
            day_hours[day_key] = day_hours.get(day_key, 0.0) + hours
            for label, lower, upper in DURATION_BUCKETS:
                if session["seconds"] >= lower and (upper is None or session["seconds"] < upper):
                    buckets[label]["count"] += 1
                    buckets[label]["hours"] += hours
                    break

        if days is None:
            timeline_days = [
                {"date": key, "hours": round(value, 3)}
                for key, value in sorted(day_hours.items())
            ]
        else:
            timeline_days = []
            start_day = datetime.fromtimestamp(now).date() - timedelta(days=days - 1)
            for offset in range(days):
                day = start_day + timedelta(days=offset)
                key = day.isoformat()
                timeline_days.append({"date": key, "hours": round(day_hours.get(key, 0.0), 3)})

        return {
            "days": timeline_days,
            "by_hour": [round(value, 3) for value in by_hour],
            "by_weekday": [round(value, 3) for value in by_weekday],
            "duration_buckets": [buckets[label] for label, _, _ in DURATION_BUCKETS],
            "total_hours": round(sum(day_hours.values()), 3),
            "sessions": len(range_sessions),
        }

    # ------------------------------------------------------------------ build

    def _build(self, range_key: str, region: str) -> dict:
        now = self._now()
        installed = self._installed()
        sessions = self._session_list(self._records("sessions"))
        installs = self._records("installs")
        price_index = self._price_index(region)
        steam_cache = self._resolve(self._steam_stats) or {}

        tracked_by_title = {}
        session_count_by_title = {}
        last_by_title = {}
        for session in sessions:
            title = session["title"]
            tracked_by_title[title] = tracked_by_title.get(title, 0) + session["seconds"]
            session_count_by_title[title] = session_count_by_title.get(title, 0) + 1
            last_by_title[title] = max(last_by_title.get(title, 0), session["end"])

        history_titles = {
            str(record.get("title") or "").strip()
            for record in installs
            if isinstance(record, dict) and record.get("title")
        }
        titles = set(installed) | set(tracked_by_title) | history_titles

        games = []
        for title in titles:
            meta = installed.get(title) or {}
            tracked = _int(tracked_by_title.get(title, 0))
            total = _int(meta.get("playtime_seconds")) if meta else 0
            total = max(total, tracked)
            imported = max(0, total - tracked)
            session_count = _int(session_count_by_title.get(title, 0))
            detail = self._details_for(title)
            price = self._price_for(title, region, price_index)
            steam = self._steam_entry(title, meta, steam_cache)
            last_played = max(
                _int(last_by_title.get(title, 0)),
                _int(steam.get("last_played")),
            )
            backup = self._backup_for(title)
            hours_total = total / 3600.0
            games.append({
                "title": title,
                "installed": bool(meta),
                "install_dir": str(meta.get("install_dir") or ""),
                "hours_tracked": tracked / 3600.0,
                "hours_imported": imported / 3600.0,
                "hours_total": hours_total,
                "sessions": session_count,
                "avg_session_minutes": (tracked / 60.0 / session_count) if session_count else None,
                "last_played": last_played,
                "steam_last_played": _int(steam.get("last_played")),
                "steam_2wks_hours": _int(steam.get("playtime_2wks")) / 60.0,
                "price_cents": price["cents"] if price else None,
                "price_currency": price["currency"] if price else "",
                "price_store": price["store"] if price else "",
                "price_on_sale": bool(price.get("on_sale")) if price else False,
                "cost_per_hour": (
                    (price["cents"] / 100.0) / hours_total
                    if price is not None and hours_total > 0 else None
                ),
                "genres": detail["genres"],
                "developers": detail["developers"],
                "publishers": detail["publishers"],
                "rating": detail["rating"],
                "cover_url": detail["cover_url"],
                "has_igdb": detail["has_igdb"],
                "gold_tier": self._is_gold(title),
                "install_size_bytes": _int(meta.get("install_size_bytes")) if meta.get("install_size_bytes") else None,
                "backup_count": _int(backup.get("count")) if backup else 0,
                "backup_size_bytes": _int(backup.get("size_bytes")) if backup else 0,
            })

        games.sort(key=lambda g: (-g["hours_total"], g["title"].lower()))

        installed_titles = [t for t in installed]
        tracked_total = sum(g["hours_tracked"] for g in games)
        imported_total = sum(g["hours_imported"] for g in games)
        hours_total = sum(g["hours_total"] for g in games)

        session_seconds = [s["seconds"] for s in sessions]
        session_count = len(sessions)
        avg_minutes = (sum(session_seconds) / session_count / 60.0) if session_count else 0.0
        median_minutes = (statistics.median(session_seconds) / 60.0) if session_count else 0.0
        longest = max(sessions, key=lambda s: s["seconds"]) if sessions else None
        night = sum(1 for s in sessions if datetime.fromtimestamp(s["end"]).hour < 6)
        weekend = sum(1 for s in sessions if datetime.fromtimestamp(s["end"]).weekday() >= 5)
        current_streak, best_streak = self._streaks(sessions, now)

        known_price_games = [g for g in games if g["price_cents"] is not None]
        played_known = [g for g in known_price_games if g["hours_total"] > 0]
        known_hours = sum(g["hours_total"] for g in played_known)
        known_value = sum(g["price_cents"] for g in played_known)
        avg_cost_per_hour = (known_value / 100.0 / known_hours) if known_hours > 0 else None

        installed_games = [g for g in games if g["installed"]]
        library_value = sum(g["price_cents"] for g in installed_games if g["price_cents"] is not None)
        unplayed_value = sum(
            g["price_cents"] for g in installed_games
            if g["price_cents"] is not None and g["hours_total"] <= 0
        )
        igdb_known = sum(1 for g in installed_games if g["has_igdb"])
        price_known = sum(1 for g in installed_games if g["price_cents"] is not None)

        timeline = self._timeline_from_sessions(sessions, range_key, now)

        developers = {}
        publishers = {}
        genres = {}
        for game in games:
            hours = game["hours_total"]
            if hours <= 0:
                continue
            for name in game["developers"]:
                entry = developers.setdefault(name, {"name": name, "titles": 0, "hours": 0.0, "gold_tier": 0})
                entry["titles"] += 1
                entry["hours"] += hours
                entry["gold_tier"] += 1 if game["gold_tier"] else 0
            for name in game["publishers"]:
                entry = publishers.setdefault(name, {"name": name, "titles": 0, "hours": 0.0, "gold_tier": 0})
                entry["titles"] += 1
                entry["hours"] += hours
                entry["gold_tier"] += 1 if game["gold_tier"] else 0
            if game["genres"]:
                share = hours / len(game["genres"])
                for name in game["genres"]:
                    entry = genres.setdefault(name, {"name": name, "titles": 0, "hours": 0.0})
                    entry["titles"] += 1
                    entry["hours"] += share

        def _sorted_breakdown(values):
            return sorted(
                values.values(),
                key=lambda item: (-item["hours"], item["name"].lower()),
            )

        coverage = {
            "igdb_percent": (igdb_known / len(installed_games) * 100.0) if installed_games else 0.0,
            "price_percent": (price_known / len(installed_games) * 100.0) if installed_games else 0.0,
            "tracked_percent": (tracked_total / hours_total * 100.0) if hours_total > 0 else 0.0,
        }

        return {
            "generated_at": now,
            "range": range_key,
            "region": region,
            "hero": {
                "hours_tracked": tracked_total,
                "hours_imported": imported_total,
                "hours_total": hours_total,
                "games_installed": len(installed_games),
                "games_played": sum(1 for g in games if g["hours_total"] > 0),
                "games_never_played": sum(1 for g in installed_games if g["hours_total"] <= 0),
                "sessions": session_count,
                "avg_session_minutes": avg_minutes,
                "median_session_minutes": median_minutes,
                "longest_session": (
                    {
                        "title": longest["title"],
                        "start": longest["start"],
                        "end": longest["end"],
                        "minutes": longest["seconds"] / 60.0,
                    } if longest else None
                ),
                "avg_cost_per_hour": avg_cost_per_hour,
                "library_value_cents": library_value,
                "current_streak": current_streak,
                "best_streak": best_streak,
                "night_owl_percent": (night / session_count * 100.0) if session_count else 0.0,
                "weekend_percent": (weekend / session_count * 100.0) if session_count else 0.0,
                "coverage": coverage,
            },
            "games": games,
            "top_games": [g for g in games if g["hours_total"] > 0][:10],
            "developers": _sorted_breakdown(developers),
            "publishers": _sorted_breakdown(publishers),
            "genres": _sorted_breakdown(genres),
            "timeline": timeline,
            "finance": {
                "library_value_cents": library_value,
                "known_prices": price_known,
                "unknown_prices": len(installed_games) - price_known,
                "unplayed_value_cents": unplayed_value,
                "price_coverage_percent": coverage["price_percent"],
            },
        }
