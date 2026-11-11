import json
import os
import tempfile
import unittest
from pathlib import Path

from core.events import (
    EVENT_BACKUP_CREATED,
    EVENT_GAME_INSTALLED,
    EVENT_GAME_LAUNCHED,
    EVENT_GAME_REMOVED,
    EVENT_PRICE_REFRESHED,
    EVENT_SESSION_END,
    EventBus,
)
from core.state_store import (
    INSTALL_FILE,
    OPEN_SESSION_FILE,
    PRICE_FILE,
    SESSION_FILE,
    USAGE_FILE,
    StateStore,
)


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.bus = EventBus()
        self.store = StateStore(self.bus, base_dir=self.base)

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_end_is_written_once(self):
        self.bus.emit_event(
            EVENT_SESSION_END,
            payload={
                "id": "session-1",
                "title": "DOOM Eternal",
                "seconds": 3600,
                "started_at": 1730000000,
                "ended_at": 1730003600,
                "install_dir": "D:/Games/DOOM",
            },
        )
        self.bus.emit_event(
            EVENT_SESSION_END,
            payload={"id": "session-1", "title": "DOOM Eternal", "seconds": 3600},
        )

        records = self.store.sessions.load()
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["title"], "DOOM Eternal")
        self.assertEqual(record["seconds"], 3600)
        self.assertEqual(record["start"], 1730000000)
        self.assertEqual(record["end"], 1730003600)
        self.assertEqual(record["source"], "launcher")
        self.assertTrue((self.base / SESSION_FILE).exists())

    def test_game_launch_marker_and_close(self):
        self.bus.emit_event(
            EVENT_GAME_LAUNCHED,
            title="Skyrim",
            install_dir="D:/Games/Skyrim",
            pid=1234,
            started_at=1730000000,
        )
        marker = json.loads((self.base / OPEN_SESSION_FILE).read_text(encoding="utf-8"))
        self.assertIn("Skyrim", marker["open"])
        self.assertEqual(marker["open"]["Skyrim"]["pid"], 1234)

        self.bus.emit_event(
            EVENT_SESSION_END,
            title="Skyrim",
            seconds=600,
            started_at=1730000000,
            ended_at=1730000600,
        )
        marker = json.loads((self.base / OPEN_SESSION_FILE).read_text(encoding="utf-8"))
        self.assertEqual(marker["open"], {})

    def test_install_history_keeps_first_seen(self):
        self.bus.emit_event(
            EVENT_GAME_INSTALLED,
            payload={"id": "i1", "at": 1000, "title": "Elden Ring", "install_dir": "D:/Elden"},
        )
        self.bus.emit_event(
            EVENT_GAME_REMOVED,
            payload={"id": "i2", "at": 2000, "title": "Elden Ring"},
        )
        self.bus.emit_event(
            EVENT_GAME_INSTALLED,
            payload={"id": "i3", "at": 3000, "title": "Elden Ring", "install_dir": "D:/Elden2"},
        )

        records = self.store.installs.load()
        self.assertEqual([r["action"] for r in records], ["add", "remove", "add"])
        self.assertEqual(records[0]["first_seen_at"], 1000)
        self.assertEqual(records[2]["first_seen_at"], 1000)

    def test_usage_event_drops_install_dir(self):
        self.bus.emit_event(
            EVENT_BACKUP_CREATED,
            title="Hades",
            install_dir="D:/Games/Hades",
            bytes=1024,
        )
        records = self.store.usage.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "backup_created")
        self.assertEqual(records[0]["title"], "Hades")
        self.assertNotIn("install_dir", records[0])
        self.assertEqual(records[0]["bytes"], 1024)

    def test_price_history_is_capped_to_one_per_day(self):
        day_one = 1730000000
        self.bus.emit_event(
            EVENT_PRICE_REFRESHED,
            payload={
                "id": "price-1",
                "at": day_one,
                "title": "Hades",
                "region": "EU",
                "steam": {
                    "on_sale": True,
                    "discount_percent": 25,
                    "final_cents": 1500,
                    "initial_cents": 2000,
                    "currency": "EUR",
                },
            },
        )
        self.bus.emit_event(
            EVENT_PRICE_REFRESHED,
            payload={
                "id": "price-2",
                "at": day_one + 3600,
                "title": "Hades",
                "region": "EU",
                "steam": {
                    "on_sale": True,
                    "discount_percent": 50,
                    "final_cents": 1000,
                    "currency": "EUR",
                },
            },
        )
        self.bus.emit_event(
            EVENT_PRICE_REFRESHED,
            payload={
                "id": "price-3",
                "at": day_one + 86400,
                "title": "Hades",
                "region": "EU",
                "steam": {
                    "on_sale": False,
                    "discount_percent": 0,
                    "final_cents": 2000,
                    "currency": "EUR",
                },
            },
        )

        records = self.store.prices.load()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["final_cents"], 1500)
        self.assertEqual(records[1]["final_cents"], 2000)
        self.assertEqual(records[0]["region"], "EU")
        self.assertTrue((self.base / PRICE_FILE).exists())

    def test_price_history_parses_legacy_formatted_string(self):
        self.bus.emit_event(
            EVENT_PRICE_REFRESHED,
            payload={
                "id": "price-legacy",
                "at": 1730000000,
                "title": "Hades",
                "steam": {"on_sale": False, "final_price": "$19.99"},
            },
        )
        records = self.store.prices.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["final_cents"], 1999)
        self.assertEqual(records[0]["currency"], "USD")
        self.assertEqual(records[0]["region"], "US")

    def test_crash_recovery_closes_open_session(self):
        marker = {
            "v": 1,
            "open": {
                "Cyberpunk 2077": {
                    "id": "crashed-1",
                    "start": 1730000000,
                    "pid": 999,
                    "install_dir": "D:/Cyberpunk",
                }
            },
        }
        (self.base / OPEN_SESSION_FILE).write_text(json.dumps(marker), encoding="utf-8")

        store = StateStore(base_dir=self.base, clock=lambda: 1730003600)
        records = store.sessions.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["id"], "crashed-1")
        self.assertEqual(records[0]["seconds"], 3600)
        self.assertTrue(records[0]["recovered"])
        self.assertEqual(json.loads((self.base / OPEN_SESSION_FILE).read_text())["open"], {})

    def test_prefs_roundtrip(self):
        self.assertEqual(self.store.load_prefs()["region"], "US")
        saved = self.store.save_prefs({"region": "IT", "range": "365d"})
        self.assertEqual(saved["region"], "IT")
        self.assertEqual(saved["range"], "365d")
        self.assertAlmostEqual(saved["cinema_rate"], 6.0)
        self.assertEqual(self.store.load_prefs()["region"], "IT")


if __name__ == "__main__":
    unittest.main()
