import tempfile
import unittest
from pathlib import Path

from core.events import (
    EVENT_ACHIEVEMENT_UNLOCKED,
    EVENT_PREFS_CHANGED,
    EVENT_STATS_INVALIDATED,
    EventBus,
)
from core.state_store import StateStore
from managers.achievements_manager import AchievementsManager
from managers.stats_manager import StatsManager


class StateStorePrefsEventTests(unittest.TestCase):
    def test_save_prefs_emits_the_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            store = StateStore(base_dir=Path(tmp))
            store.bind(bus)
            seen = []
            bus.subscribe(EVENT_PREFS_CHANGED, seen.append)

            store.save_prefs({"region": "IT"})

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get("region"), "IT")


class StatsInvalidationEventTests(unittest.TestCase):
    def test_external_invalidation_emits(self):
        bus = EventBus()
        manager = StatsManager()
        manager.bind(bus)
        seen = []
        bus.subscribe(EVENT_STATS_INVALIDATED, seen.append)

        manager.invalidate()

        self.assertEqual(len(seen), 1)

    def test_event_driven_invalidation_does_not_loop(self):
        bus = EventBus()
        manager = StatsManager()
        manager.bind(bus)
        seen = []
        bus.subscribe(EVENT_STATS_INVALIDATED, seen.append)

        manager._on_event(None)

        self.assertEqual(seen, [])


class AchievementEventTests(unittest.TestCase):
    def test_unlock_emits_the_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = EventBus()
            manager = AchievementsManager(
                base_dir=Path(tmp),
                definitions=[{
                    "id": "test_unlock",
                    "title": "Test",
                    "trigger": {
                        "stat": "library_folder_configured",
                        "op": "==",
                        "value": True,
                    },
                }],
                config=lambda: {"watched_folders": ["/games"]},
                events=bus,
                clock=lambda: 100,
            )
            seen = []
            bus.subscribe(EVENT_ACHIEVEMENT_UNLOCKED, seen.append)

            manager.evaluate(silent=False)

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].get("count"), 1)
        self.assertIn("test_unlock", seen[0].get("ids") or [])


if __name__ == "__main__":
    unittest.main()
