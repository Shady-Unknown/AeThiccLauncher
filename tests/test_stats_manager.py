import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from core.state_store import StateStore
from managers.achievement_engine import compute_stats
from managers.stats_manager import StatsManager


def _ts(year, month, day, hour, minute=0):
    return int(time.mktime((year, month, day, hour, minute, 0, 0, 0, -1)))


class FakeCatalog:
    def __init__(self, prices=None, appids=None):
        self.prices = prices or {}
        self.appids = appids or {}

    def get_prices(self, title):
        return self.prices.get(title, {})

    def get_game_field(self, title, field, default=None):
        if field == "igdb_meta":
            return {}
        if field == "prices":
            return self.prices.get(title, default)
        if field == "steam_id":
            return self.appids.get(title, "")
        return default

    def get_steam_app_id(self, title):
        return self.appids.get(title, "")

    def is_gold_tier(self, title):
        return False


class FakeDetails:
    def __init__(self, data):
        self.data = data

    def get(self, title):
        return self.data.get(title)


class StatsManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(base_dir=Path(self.tmp.name))
        self.now = _ts(2026, 11, 11, 12)

        self.state.sessions.append_many([
            {
                "id": "s1", "title": "Game A",
                "start": _ts(2026, 11, 9, 22), "end": _ts(2026, 11, 9, 23),
                "seconds": 3600, "source": "launcher",
            },
            {
                "id": "s2", "title": "Game A",
                "start": _ts(2026, 11, 10, 8), "end": _ts(2026, 11, 10, 9),
                "seconds": 3600, "source": "launcher",
            },
            {
                "id": "s3", "title": "Game B",
                "start": _ts(2026, 11, 11, 0), "end": _ts(2026, 11, 11, 2),
                "seconds": 7200, "source": "launcher",
            },
        ])
        self.state.prices.append({
            "id": "price-c",
            "title": "Game C",
            "store": "steam",
            "region": "US",
            "currency": "USD",
            "initial_cents": 1000,
            "final_cents": 1000,
            "discount_percent": 0,
            "on_sale": False,
            "at": _ts(2026, 11, 1, 12),
        })

        self.installed = {
            "Game A": {
                "playtime_seconds": 3 * 3600,
                "steam_grid_id": "123",
                "install_dir": "D:/Games/A",
            },
            "Game B": {"playtime_seconds": 2 * 3600},
            "Game C": {"playtime_seconds": 0},
        }
        self.catalog = FakeCatalog(
            prices={
                "Game A": {
                    "region": "US",
                    "steam": {
                        "final_cents": 2000,
                        "initial_cents": 3000,
                        "currency": "USD",
                        "on_sale": False,
                        "discount_percent": 0,
                    },
                },
            },
            appids={"Game A": "123"},
        )
        self.details = FakeDetails({
            "Game A": {
                "genres": ["RPG"],
                "developers": ["Dev A"],
                "publishers": ["Pub A"],
                "summary": "A",
                "rating": 90,
                "cover_url": "https://img/a.jpg",
            },
            "Game B": {
                "genres": ["Horror"],
                "developers": ["Dev B"],
                "publishers": ["Pub B"],
                "summary": "B",
            },
            "Game C": {
                "genres": ["Puzzle"],
                "developers": ["Dev C"],
                "publishers": ["Pub C"],
                "summary": "C",
            },
        })
        self.steam = {
            "apps": {
                "123": {"last_played": _ts(2026, 11, 11, 10), "playtime_2wks": 120},
            }
        }

        self.manager = StatsManager(
            state_store=self.state,
            installed_games=self.installed,
            catalog=self.catalog,
            details=self.details,
            gold_tier=lambda title: title == "Game A",
            steam_stats=self.steam,
            config={"stats": {"region": "US", "range": "30d"}},
            clock=lambda: self.now,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_hero_metrics(self):
        snapshot = self.manager.snapshot()
        hero = snapshot["hero"]
        self.assertEqual(snapshot["range"], "30d")
        self.assertEqual(snapshot["region"], "US")
        self.assertAlmostEqual(hero["hours_tracked"], 4.0)
        self.assertAlmostEqual(hero["hours_imported"], 1.0)
        self.assertAlmostEqual(hero["hours_total"], 5.0)
        self.assertEqual(hero["games_installed"], 3)
        self.assertEqual(hero["games_played"], 2)
        self.assertEqual(hero["games_never_played"], 1)
        self.assertEqual(hero["sessions"], 3)
        self.assertAlmostEqual(hero["avg_session_minutes"], 80.0)
        self.assertAlmostEqual(hero["median_session_minutes"], 60.0)
        self.assertEqual(hero["longest_session"]["title"], "Game B")
        self.assertAlmostEqual(hero["longest_session"]["minutes"], 120.0)
        self.assertAlmostEqual(hero["avg_cost_per_hour"], 20.0 / 3.0)
        self.assertEqual(hero["library_value_cents"], 3000)
        self.assertEqual(hero["current_streak"], 3)
        self.assertEqual(hero["best_streak"], 3)
        self.assertAlmostEqual(hero["night_owl_percent"], 100.0 / 3.0)
        self.assertAlmostEqual(hero["weekend_percent"], 0.0)
        self.assertAlmostEqual(hero["coverage"]["tracked_percent"], 80.0)
        self.assertAlmostEqual(hero["coverage"]["igdb_percent"], 100.0)
        self.assertAlmostEqual(hero["coverage"]["price_percent"], 200.0 / 3.0)

    def test_per_game_and_breakdowns(self):
        snapshot = self.manager.snapshot()
        games = {g["title"]: g for g in snapshot["games"]}

        game_a = games["Game A"]
        self.assertAlmostEqual(game_a["hours_tracked"], 2.0)
        self.assertAlmostEqual(game_a["hours_imported"], 1.0)
        self.assertAlmostEqual(game_a["hours_total"], 3.0)
        self.assertEqual(game_a["sessions"], 2)
        self.assertAlmostEqual(game_a["avg_session_minutes"], 60.0)
        self.assertEqual(game_a["price_cents"], 2000)
        self.assertAlmostEqual(game_a["cost_per_hour"], 20.0 / 3.0)
        self.assertTrue(game_a["gold_tier"])
        self.assertEqual(game_a["genres"], ["RPG"])
        self.assertEqual(game_a["developers"], ["Dev A"])
        self.assertEqual(game_a["last_played"], _ts(2026, 11, 11, 10))
        self.assertAlmostEqual(game_a["steam_2wks_hours"], 2.0)

        game_b = games["Game B"]
        self.assertAlmostEqual(game_b["hours_tracked"], 2.0)
        self.assertAlmostEqual(game_b["hours_imported"], 0.0)
        self.assertEqual(game_b["sessions"], 1)

        game_c = games["Game C"]
        self.assertEqual(game_c["hours_total"], 0.0)
        self.assertEqual(game_c["price_cents"], 1000)
        self.assertEqual(game_c["price_store"], "steam")
        self.assertIsNone(game_c["cost_per_hour"])

        self.assertEqual([g["title"] for g in snapshot["top_games"]], ["Game A", "Game B"])
        self.assertEqual([d["name"] for d in snapshot["developers"]], ["Dev A", "Dev B"])
        self.assertEqual([p["name"] for p in snapshot["publishers"]], ["Pub A", "Pub B"])
        self.assertEqual([g["name"] for g in snapshot["genres"]], ["RPG", "Horror"])

        finance = snapshot["finance"]
        self.assertEqual(finance["library_value_cents"], 3000)
        self.assertEqual(finance["known_prices"], 2)
        self.assertEqual(finance["unknown_prices"], 1)
        self.assertEqual(finance["unplayed_value_cents"], 1000)

    def test_timeline_and_range(self):
        snapshot = self.manager.snapshot(range_key="30d")
        timeline = snapshot["timeline"]
        self.assertEqual(len(timeline["days"]), 30)
        self.assertAlmostEqual(sum(day["hours"] for day in timeline["days"]), 4.0)
        self.assertAlmostEqual(timeline["by_hour"][23], 1.0)
        self.assertAlmostEqual(timeline["by_hour"][9], 1.0)
        self.assertAlmostEqual(timeline["by_hour"][2], 2.0)
        self.assertAlmostEqual(sum(timeline["by_weekday"]), 4.0)
        self.assertEqual(
            timeline["by_weekday"][datetime.fromtimestamp(_ts(2026, 11, 9, 23)).weekday()],
            1.0,
        )
        buckets = {b["label"]: b for b in timeline["duration_buckets"]}
        self.assertEqual(buckets["1-2h"]["count"], 2)
        self.assertEqual(buckets["2-4h"]["count"], 1)

        # An old session stays in the all-time hero but not in the 30d chart.
        self.state.sessions.append({
            "id": "old", "title": "Game B",
            "start": _ts(2025, 1, 1, 12), "end": _ts(2025, 1, 1, 13),
            "seconds": 3600,
        })
        self.manager.invalidate()

        all_snapshot = self.manager.snapshot(range_key="all")
        self.assertEqual(all_snapshot["hero"]["sessions"], 4)
        self.assertAlmostEqual(sum(d["hours"] for d in all_snapshot["timeline"]["days"]), 5.0)

        recent = self.manager.snapshot(range_key="30d")
        self.assertAlmostEqual(sum(d["hours"] for d in recent["timeline"]["days"]), 4.0)

    def test_game_timeline_filters_title_and_range(self):
        timeline = self.manager.game_timeline("Game A", "30d")
        self.assertEqual(len(timeline["days"]), 30)
        self.assertAlmostEqual(timeline["total_hours"], 2.0)
        self.assertEqual(timeline["sessions"], 2)

        self.state.sessions.append({
            "id": "old-a", "title": "Game A",
            "start": _ts(2025, 1, 1, 12), "end": _ts(2025, 1, 1, 13),
            "seconds": 3600,
        })
        self.manager.invalidate()

        all_time = self.manager.game_timeline("Game A", "all")
        self.assertAlmostEqual(all_time["total_hours"], 3.0)
        recent = self.manager.game_timeline("Game A", "30d")
        self.assertAlmostEqual(recent["total_hours"], 2.0)

        missing = self.manager.game_timeline("Missing Game", "30d")
        self.assertEqual(missing["sessions"], 0)
        self.assertAlmostEqual(missing["total_hours"], 0.0)

    def test_global_timeline_for_charts(self):
        timeline = self.manager.global_timeline("30d")
        self.assertEqual(len(timeline["days"]), 30)
        self.assertEqual(timeline["sessions"], 3)
        self.assertAlmostEqual(timeline["total_hours"], 4.0)
        self.assertEqual(len(timeline["by_hour"]), 24)
        self.assertEqual(len(timeline["by_weekday"]), 7)

    def test_cache_and_invalidation(self):
        first = self.manager.snapshot()
        second = self.manager.snapshot()
        self.assertIs(first, second)

        self.manager.invalidate()
        third = self.manager.snapshot()
        self.assertIsNot(first, third)

    def test_region_change_uses_other_catalog_region(self):
        self.catalog.prices["Game A"] = {
            "region": "IT",
            "steam": {"final_cents": 1800, "currency": "EUR"},
        }
        self.manager.invalidate()
        snapshot = self.manager.snapshot(region="US")
        game_a = next(g for g in snapshot["games"] if g["title"] == "Game A")
        # The catalog entry is for IT, so the US fallback must not use it.
        self.assertIsNone(game_a["price_cents"])


class StatsManagerCacheRefreshTests(unittest.TestCase):
    def test_library_filled_after_cache_requires_invalidate(self):
        installed = {}
        details = FakeDetails({
            "Game A": {"developers": ["Team Cherry"]},
            "Game B": {"developers": ["Team Cherry"]},
        })
        manager = StatsManager(
            installed_games=installed,
            details=details,
            config=lambda: {"stats": {}},
        )
        self.assertEqual(manager.snapshot()["games"], [])

        installed["Game A"] = {"install_dir": "C:/Game A"}
        installed["Game B"] = {"install_dir": "C:/Game B"}
        # The empty snapshot is cached until the manager is invalidated.
        self.assertEqual(manager.snapshot()["games"], [])

        manager.invalidate()
        snapshot = manager.snapshot()
        self.assertEqual(len(snapshot["games"]), 2)
        stats = compute_stats(
            installed_games=installed,
            stats_snapshot=lambda: snapshot,
            config=lambda: {"stats": {}},
        )
        self.assertEqual(stats["max_titles_by_developer"], 2)


if __name__ == "__main__":
    unittest.main()
