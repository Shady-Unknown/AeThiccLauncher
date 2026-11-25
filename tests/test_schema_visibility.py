"""Schema bumps must be visible in the log, not a silent empty cache."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from managers.achievements_manager import (
    STATE_SCHEMA, _read_state,
)
from managers.discovery_manager import CACHE_SCHEMA, DiscoveryManager
from tests.discovery_fakes import empty_services
from managers.igdb_details_cache import (
    CACHE_SCHEMA as IGDB_SCHEMA, IgdbDetailsCache,
)


class DiscoveryCacheSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "discovery_cache.json"
        self.manager = DiscoveryManager(empty_services())
        self.manager._data = {}
        self.manager._card_index = None

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_schema_rows_are_dropped_with_a_log(self):
        self.path.write_text(json.dumps({
            "row": {"schema": CACHE_SCHEMA - 1, "cards": [{"title": "Old"}]},
            "keep": {"schema": CACHE_SCHEMA, "cards": [{"title": "New"}]},
        }), encoding="utf-8")

        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch.object(
            DiscoveryManager, "categories", lambda self: [{"key": "row"}, {"key": "keep"}]
        ), mock.patch.object(
            DiscoveryManager, "_title", lambda self, key: key.title()
        ):
            with self.assertLogs("managers.discovery_manager", level="INFO") as logs:
                self.manager._load_cache()

        self.assertNotIn("row", self.manager._data)
        self.assertEqual(self.manager._data["keep"]["title"], "Keep")
        self.assertTrue(any("1 row(s)" in line for line in logs.output))

    def test_current_schema_does_not_log(self):
        self.path.write_text(json.dumps({
            "row": {"schema": CACHE_SCHEMA, "cards": []},
        }), encoding="utf-8")

        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch.object(
            DiscoveryManager, "categories", lambda self: [{"key": "row"}]
        ), mock.patch.object(
            DiscoveryManager, "_title", lambda self, key: key
        ):
            logger = "managers.discovery_manager"
            with mock.patch(f"{logger}.log.info") as info:
                self.manager._load_cache()

        self.assertIn("row", self.manager._data)
        info.assert_not_called()


class IgdbDetailsSchemaTests(unittest.TestCase):
    def test_old_schema_entries_are_dropped_with_a_log(self):
        now = time.time()
        data = {
            "Old Game": {"schema": IGDB_SCHEMA - 1, "fetched_at": now},
            "New Game": {"schema": IGDB_SCHEMA, "fetched_at": now},
        }

        with self.assertLogs("managers.igdb_details_cache", level="INFO") as logs:
            IgdbDetailsCache._trim(data)

        self.assertNotIn("Old Game", data)
        self.assertIn("New Game", data)
        self.assertTrue(any("1 entr" in line for line in logs.output))


class AchievementStateSchemaTests(unittest.TestCase):
    def test_newer_schema_is_preserved_and_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "achievements.json"
            path.write_text(
                json.dumps({"schema": STATE_SCHEMA + 1, "unlocked": {}}),
                encoding="utf-8",
            )
            with self.assertLogs("managers.achievements_manager", level="WARNING"):
                state = _read_state(path)

        self.assertEqual(state["schema"], STATE_SCHEMA + 1)

    def test_missing_schema_defaults_to_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "achievements.json"
            path.write_text(json.dumps({"unlocked": {}}), encoding="utf-8")
            state = _read_state(path)

        self.assertEqual(state["schema"], STATE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
