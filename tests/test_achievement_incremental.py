"""Selective achievement evaluation: cached stats and due definitions."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.state_store import StateStore
from managers.achievement_engine import (
    trigger_event_names, trigger_stat_keys,
)
from managers.achievements_manager import AchievementsManager

NOW = int(time.time())

DEFINITIONS = [
    {
        "id": "hours", "title": "Hours",
        "trigger": {"stat": "hours_total", "op": ">=", "value": 1},
    },
    {
        "id": "sessions", "title": "Sessions",
        "trigger": {"stat": "sessions_total", "op": ">=", "value": 1},
    },
    {
        "id": "theme", "title": "Theme",
        "trigger": {"stat": "theme_customized", "op": "==", "value": True},
    },
    {
        "id": "store", "title": "Store",
        "trigger": {"event": "store_opened", "where": {"title": "DOOM"}},
    },
    {
        "id": "trailer", "title": "Trailer",
        "trigger": {"event": "trailer_watched", "where": {"title": "DOOM"}},
    },
]


class TriggerDependencyTests(unittest.TestCase):
    def test_simple_triggers(self):
        self.assertEqual(
            trigger_stat_keys({"stat": "hours_total", "op": ">=", "value": 1}),
            {"hours_total"},
        )
        self.assertEqual(
            trigger_event_names({"event": "launcher_start", "where": {}}),
            {"launcher_start"},
        )

    def test_nested_all_and_any(self):
        trigger = {
            "all": [
                {"stat": "hours_total", "op": ">=", "value": 1},
                {"any": [
                    {"stat": "sessions_total", "op": ">=", "value": 2},
                    {"event": "trailer_watched", "where": {"title": "DOOM"}},
                ]},
            ]
        }

        self.assertEqual(
            trigger_stat_keys(trigger), {"hours_total", "sessions_total"}
        )
        self.assertEqual(trigger_event_names(trigger), {"trailer_watched"})

    def test_empty_and_invalid_triggers(self):
        self.assertEqual(trigger_stat_keys({}), set())
        self.assertEqual(trigger_event_names(None), set())
        self.assertEqual(trigger_stat_keys({"all": []}), set())


class IncrementalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = StateStore(base_dir=Path(self.tmp.name))
        self.snapshot = {
            "hero": {"hours_total": 0.0, "best_streak": 0},
            "timeline": {"by_weekday": [0, 0, 0, 0, 0, 0, 0]},
            "games": [],
        }
        self.config = {"current_theme": "PurpleRip", "stats": {"cinema_rate": 6.0}}
        self.revision = [0]
        self.manager = None

    def tearDown(self):
        self.tmp.cleanup()

    def _manager(self, stats_revision=None):
        self.manager = AchievementsManager(
            state_store=self.state,
            installed_games={},
            stats_provider=lambda: self.snapshot,
            stats_revision=stats_revision,
            config=self.config,
            definitions=DEFINITIONS,
            base_dir=Path(self.tmp.name),
            clock=lambda: NOW,
        )
        return self.manager

    def _spy_compute(self):
        return mock.patch(
            "managers.achievements_manager.compute_stats",
            wraps=__import__(
                "managers.achievement_engine", fromlist=["compute_stats"]
            ).compute_stats,
        )

    def _session(self, seconds=3600, title="Game A"):
        self.state.on_session_end({
            "title": title,
            "seconds": seconds,
            "started_at": NOW - seconds,
            "ended_at": NOW,
        })

    def test_stats_are_cached_between_passes(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)
        with self._spy_compute() as compute:
            manager.evaluate()
            manager.evaluate()
        self.assertEqual(compute.call_count, 0)

    def test_new_session_rebuilds_once(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)
        self._session()

        with self._spy_compute() as compute:
            badges = manager.evaluate()
            manager.evaluate()

        self.assertEqual(compute.call_count, 1)
        self.assertEqual({badge["id"] for badge in badges}, {"hours", "sessions"})

    def test_install_event_rebuilds(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)
        with self._spy_compute() as compute:
            manager._on_event("game_installed", {"title": "Game A"})
            manager.evaluate()
        self.assertEqual(compute.call_count, 1)

    def test_config_change_rebuilds_and_only_theme_is_due(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)
        self.config["current_theme"] = "Neon"

        seen = []
        real = __import__(
            "managers.achievements_manager", fromlist=["evaluate_trigger"]
        ).evaluate_trigger

        def spy(trigger, stats, *args, **kwargs):
            seen.append(id(trigger))
            return real(trigger, stats, *args, **kwargs)

        with mock.patch("managers.achievements_manager.evaluate_trigger", spy):
            badges = manager.evaluate()

        self.assertEqual({badge["id"] for badge in badges}, {"theme"})
        self.assertEqual(len(seen), 1)

    def test_event_only_definition_unlocks_without_rebuild(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)

        with self._spy_compute() as compute:
            manager._on_event("store_opened", {"title": "DOOM", "at": NOW})
            badges = manager.evaluate()

        self.assertEqual(compute.call_count, 0)
        self.assertEqual({badge["id"] for badge in badges}, {"store"})

    def test_snapshot_revision_rebuilds_the_stats(self):
        manager = self._manager(stats_revision=lambda: self.revision[0])
        manager.evaluate(retroactive=True)
        self.snapshot["hero"]["hours_total"] = 2.0
        self.revision[0] += 1

        with self._spy_compute() as compute:
            badges = manager.evaluate()

        self.assertEqual(compute.call_count, 1)
        self.assertEqual({badge["id"] for badge in badges}, {"hours"})

    def test_retroactive_always_evaluates_every_definition(self):
        manager = self._manager()
        manager.evaluate()

        seen = []
        real = __import__(
            "managers.achievements_manager", fromlist=["evaluate_trigger"]
        ).evaluate_trigger

        def spy(trigger, stats, *args, **kwargs):
            seen.append(trigger)
            return real(trigger, stats, *args, **kwargs)

        with mock.patch("managers.achievements_manager.evaluate_trigger", spy):
            manager.evaluate(retroactive=True)

        top_level = {id(definition["trigger"]) for definition in DEFINITIONS}
        self.assertTrue(top_level.issubset({id(trigger) for trigger in seen}))

    def test_full_eval_env_forces_every_definition(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)

        seen = []
        real = __import__(
            "managers.achievements_manager", fromlist=["evaluate_trigger"]
        ).evaluate_trigger

        def spy(trigger, stats, *args, **kwargs):
            seen.append(trigger)
            return real(trigger, stats, *args, **kwargs)

        with mock.patch.dict(os.environ, {"AETHICC_ACHIEVEMENTS_FULL": "1"}), \
                mock.patch("managers.achievements_manager.evaluate_trigger", spy):
            manager.evaluate()

        top_level = {id(definition["trigger"]) for definition in DEFINITIONS}
        self.assertTrue(top_level.issubset({id(trigger) for trigger in seen}))

    def test_reload_forces_a_full_pass(self):
        manager = self._manager()
        manager.evaluate(retroactive=True)

        with self._spy_compute() as compute:
            manager.reload()

        self.assertEqual(compute.call_count, 1)


if __name__ == "__main__":
    unittest.main()
