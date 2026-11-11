import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.paths import PathManager
from core.state_store import StateStore
from managers.achievement_engine import (
    compute_stats, evaluate_trigger, load_definitions, load_title_lists,
    normalize_title, progress_for,
)
from managers.achievements_manager import AchievementsManager

NOW = int(time.time())


def _ts(days_ago=0, hour=12):
    return int(time.mktime(
        time.localtime(NOW - days_ago * 86400)[:3] + (hour, 0, 0, 0, 0, -1)
    ))


SNAPSHOT = {
    "hero": {
        "hours_total": 3.0,
        "best_streak": 5,
        "games_installed": 2,
        "games_played": 1,
    },
    "timeline": {
        "by_weekday": [0, 0, 0, 0, 0, 10, 10],
    },
    "games": [
        {
            "title": "Game A", "installed": True, "hours_total": 3.0,
            "hours_tracked": 2.0, "hours_imported": 1.0, "sessions": 2,
            "cost_per_hour": 1.0, "price_cents": 1000, "price_currency": "USD",
            "developers": ["Dev A"], "publishers": ["Pub A"],
            "genres": ["RPG", "Action"], "gold_tier": True,
        },
        {
            "title": "Game B", "installed": True, "hours_total": 0.0,
            "hours_tracked": 0.0, "hours_imported": 0.0, "sessions": 0,
            "cost_per_hour": None, "price_cents": None, "price_currency": "",
            "developers": ["Dev A"], "publishers": ["Pub A"],
            "genres": ["RPG"], "gold_tier": False,
        },
    ],
}


class AchievementEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(base_dir=Path(self.tmp.name))
        self.state.sessions.append_many([
            {
                "id": "s-old", "title": "Game A", "seconds": 3600,
                "start": _ts(100), "end": _ts(100),
            },
            {
                "id": "s-new", "title": "Game A", "seconds": 3600,
                "start": _ts(1), "end": _ts(1),
            },
        ])
        self.state.installs.append_many([
            {"id": "i1", "title": "Game A", "action": "add", "at": _ts(120)},
            {"id": "i2", "title": "Game A", "action": "remove", "at": _ts(110)},
            {"id": "i3", "title": "Game A", "action": "add", "at": _ts(100)},
        ])
        self.state.usage.append_many([
            {"id": "u1", "event": "launcher_start", "os": "linux", "flatpak": False,
             "at": _ts(0, 5)},
            {"id": "u2", "event": "game_launched", "title": "Game A", "at": _ts(1)},
            {"id": "u3", "event": "hoster_used", "hoster": "gofile", "title": "Game A",
             "at": _ts(1)},
            {"id": "u4", "event": "download_resumed", "title": "Game A", "at": _ts(1)},
            {"id": "u5", "event": "trailer_watched", "title": "Game A", "at": _ts(1)},
            {"id": "u6", "event": "orphan_recovered", "title": "Game A", "at": _ts(1)},
            {"id": "u7", "event": "backup_created", "title": "Game A", "at": _ts(1)},
            {"id": "u8", "event": "update_applied", "title": "Game A", "at": _ts(1)},
        ])
        self.state.prices.append({
            "id": "p1", "title": "Game A", "store": "steam", "region": "US",
            "currency": "USD", "final_cents": 1000, "discount_percent": 25,
            "on_sale": True, "at": _ts(1),
        })
        self.state.save_prefs({"region": "IT"})

        self.config = {
            "watched_folders": ["D:/Games"],
            "default_temp_folder": "D:/Temp",
            "current_theme": "OrangeCyber",
            "stats": {"region": "IT", "cinema_rate": 6.0},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_compute_stats(self):
        stats = compute_stats(
            state_store=self.state,
            installed_games={"Game A": {}, "Game B": {}},
            config=self.config,
            stats_snapshot=SNAPSHOT,
            title_lists=load_title_lists(),
            now=NOW,
        )
        self.assertEqual(stats["launcher_starts"], 1)
        self.assertEqual(stats["launches_total"], 1)
        self.assertEqual(stats["sessions_total"], 2)
        self.assertEqual(stats["max_session_seconds"], 3600)
        self.assertEqual(stats["max_distinct_titles_per_day"], 1)
        self.assertAlmostEqual(stats["hours_total"], 3.0)
        self.assertEqual(stats["distinct_titles_ever"], 2)
        self.assertEqual(stats["max_titles_by_developer"], 2)
        self.assertEqual(stats["genres_count_current"], 2)
        self.assertEqual(stats["gold_tier_count_current"], 1)
        self.assertEqual(stats["games_removed_total"], 1)
        self.assertEqual(stats["reinstalled_titles_count"], 1)
        self.assertTrue(stats["comeback_detected"])
        self.assertEqual(stats["never_launched_count"], 1)
        self.assertEqual(stats["hosters_used"], 1)
        self.assertEqual(stats["resumes_total"], 1)
        self.assertEqual(stats["trailers_watched_titles"], 1)
        self.assertEqual(stats["discounted_titles_seen"], 1)
        self.assertEqual(stats["orphans_recovered"], 1)
        self.assertEqual(stats["backups_total"], 1)
        self.assertEqual(stats["updates_applied"], 1)
        self.assertEqual(stats["games_below_cinema_rate"], 1)
        self.assertAlmostEqual(stats["weekend_hours_ratio"], 1.0)
        self.assertTrue(stats["library_folder_configured"])
        self.assertTrue(stats["temp_folder_customized"])
        self.assertTrue(stats["theme_customized"])
        self.assertTrue(stats["region_non_default"])

    def test_trigger_operators_and_events(self):
        stats = compute_stats(
            state_store=self.state, installed_games={}, config=self.config,
            stats_snapshot=SNAPSHOT, now=NOW,
        )
        self.assertTrue(evaluate_trigger(
            {"stat": "hours_total", "op": ">=", "value": 3}, stats
        ))
        self.assertTrue(evaluate_trigger(
            {"all": [
                {"stat": "hours_total", "op": ">=", "value": 3},
                {"stat": "sessions_total", "op": "==", "value": 2},
            ]}, stats
        ))
        self.assertTrue(evaluate_trigger(
            {"any": [
                {"stat": "hours_total", "op": ">", "value": 99},
                {"stat": "gold_tier_count_current", "op": ">=", "value": 1},
            ]}, stats
        ))
        event = {"event": "launcher_start", "where": {"os": "linux"}}
        self.assertTrue(evaluate_trigger(
            event, stats, event_name="launcher_start", payload={"os": "linux"}
        ))
        self.assertFalse(evaluate_trigger(
            event, stats, event_name="launcher_start", payload={"os": "win32"}
        ))
        early = {"event": "launcher_start", "where": {
            "local_hour_min": 5, "local_hour_max": 7
        }}
        at_five = {"at": _ts(0, 5)}
        self.assertTrue(evaluate_trigger(
            early, stats, event_name="launcher_start", payload=at_five
        ))

    def test_title_normalisation_and_franchise(self):
        self.assertEqual(normalize_title("Half-Life 2: Game of the Year Edition"), "halflife2")
        titles = load_title_lists()
        self.assertIn("halflife2", titles["half_life"])
        stats = compute_stats(
            state_store=self.state,
            installed_games={"Half-Life 2": {}, "Portal": {}},
            config=self.config,
            stats_snapshot={"games": [
                {"title": "Half-Life 2", "installed": True, "hours_total": 4.0,
                 "hours_tracked": 4.0, "sessions": 2},
                {"title": "Portal", "installed": True, "hours_total": 2.0,
                 "hours_tracked": 2.0, "sessions": 1},
            ]},
            title_lists=titles,
            now=NOW,
        )
        self.assertEqual(stats["distinct_titles_by_franchise"]["half_life"], 1)
        self.assertEqual(stats["hours_by_franchise"]["half_life"], 4.0)
        self.assertEqual(stats["distinct_titles_by_list"]["portal"], 1)
        self.assertTrue(evaluate_trigger(
            {"stat": "hours_by_title", "title": "Half-Life 2",
             "op": ">=", "value": 4}, stats
        ))
        self.assertTrue(evaluate_trigger(
            {"stat": "distinct_titles_by_franchise", "franchise": "half_life",
             "op": ">=", "value": 1}, stats
        ))

    def test_progress(self):
        stats = compute_stats(
            state_store=self.state, installed_games={}, config=self.config,
            stats_snapshot=SNAPSHOT, now=NOW,
        )
        definition = {"trigger": {"stat": "hours_total", "op": ">=", "value": 100}}
        self.assertEqual(progress_for(definition, stats), (3.0, 100.0))


class AchievementsManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(base_dir=Path(self.tmp.name))
        self.state.usage.append({
            "id": "u1", "event": "launcher_start", "os": "linux",
            "flatpak": False, "at": _ts(0),
        })
        self.state.sessions.append({
            "id": "s1", "title": "Game A", "seconds": 7200,
            "start": _ts(1), "end": _ts(1),
        })
        self.definitions = [
            {
                "id": "stat_hours", "title": "Hours", "trigger": {
                    "stat": "hours_total", "op": ">=", "value": 1,
                },
            },
            {
                "id": "event_linux", "title": "Linux", "trigger": {
                    "event": "launcher_start", "where": {"os": "linux"},
                },
            },
            {
                "id": "hidden_streak", "title": "Hidden", "hidden": True, "trigger": {
                    "stat": "streak_days", "op": ">=", "value": 99,
                },
            },
        ]
        self.manager = AchievementsManager(
            state_store=self.state,
            installed_games={"Game A": {}},
            stats_provider=lambda: SNAPSHOT,
            config={"stats": {"cinema_rate": 6.0}},
            definitions=self.definitions,
            base_dir=Path(self.tmp.name),
            clock=lambda: NOW,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_retroactive_unlock_and_idempotence(self):
        unlocked = self.manager.evaluate(retroactive=True)
        self.assertEqual({badge["id"] for badge in unlocked}, {"stat_hours", "event_linux"})
        self.assertEqual(self.manager.unlocked_count(), 2)
        self.assertEqual(self.manager.evaluate(retroactive=True), [])
        self.assertEqual(self.manager.unlocked_count(), 2)
        hidden = next(
            badge for badge in self.manager.badges()
            if badge["id"] == "hidden_streak"
        )
        self.assertTrue(hidden["hidden_locked"])
        self.assertFalse(hidden["unlocked"])

    def test_icon_path_and_progress_badge(self):
        stat_def = self.definitions[0]
        badge = self.manager.badge_for(stat_def, stats={"hours_total": 0.5})
        self.assertIn("achievements", badge["icon_path"])
        self.assertEqual(badge["progress"], {"current": 0.5, "target": 1.0})

    def test_bundled_definitions_load(self):
        repo = Path(__file__).resolve().parent.parent
        with mock.patch.object(PathManager, "get_app_data_dir", return_value=repo):
            definitions = load_definitions()
        self.assertEqual(len(definitions), 40)
        self.assertEqual(len({item["id"] for item in definitions}), 40)
        self.assertTrue(all(item.get("icon") for item in definitions))


if __name__ == "__main__":
    unittest.main()
