"""Characterization of Discovery cache persistence (G2B).

The on-disk format is part of the contract: the tests pin the slim payload,
the debounce timer, the schema/keys validation and the failure path of the
atomic write. They do not require the cache module yet; after G2B they still
exercise the same behaviour through the manager.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers import discovery_store
from managers.discovery_manager import (
    CACHE_RETRY_DELAYS_MS, CACHE_SCHEMA, DiscoveryManager,
)
from tests.discovery_fakes import empty_services


class FlushPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "discovery_cache.json"
        self.manager = DiscoveryManager(empty_services())
        self.manager._data = {
            "row": {
                "title": "Row",
                "source": "steamrip",
                "next_offset": 2,
                "next_page": 3,
                "has_more": True,
                "fetched_at": 10,
                "schema": CACHE_SCHEMA,
                "catalog_size": 5,
                "_empty_pages": 1,
                "_dirty": True,
                "cards": [{
                    "title": "Game",
                    "cover_path": "cover.jpg",
                    "meta": {"big": "payload"},
                    "igdb": {"rating": 80},
                    "steamrip_categories": [3],
                    "url": "https://steamrip.com/game",
                    "source": "steamrip",
                }],
            },
            "all_games": {"cards": [{"title": "Ignored"}]},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_payload_is_slim_and_excludes_all_games(self):
        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch(
            "managers.discovery_store.atomic_write_json"
        ) as write:
            self.manager._flush_cache()

        path, payload = write.call_args.args
        self.assertEqual(path, str(self.path))
        self.assertEqual(write.call_args.kwargs,
                         {"separators": (",", ":"), "fsync": False})
        self.assertNotIn("all_games", payload)
        entry = payload["row"]
        self.assertEqual(set(entry), {
            "title", "source", "next_offset", "next_page", "has_more",
            "fetched_at", "schema", "catalog_size", "_empty_pages",
            "_dirty", "cards",
        })
        self.assertFalse(entry["_dirty"])
        self.assertEqual(set(entry["cards"][0]), {
            "title", "igdb", "steamrip_categories", "url", "source",
        })
        self.assertEqual(entry["cards"][0]["title"], "Game")
        # The in-memory entry is not rewritten by the flush.
        self.assertTrue(self.manager._data["row"]["_dirty"])

    def test_write_failure_is_logged_and_leaves_the_row_unsaved(self):
        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch(
            "managers.discovery_store.atomic_write_json",
            side_effect=OSError("disk full"),
        ), mock.patch.object(
            self.manager._cache_retry_timer, "start"
        ) as retry:
            with self.assertLogs("managers.discovery_manager", level="ERROR"):
                self.manager._flush_cache()

        # The row stays marked unsaved and a controlled retry is scheduled;
        # _dirty keeps its separate "cards need rebuilding" meaning.
        self.assertTrue(self.manager._data["row"]["_dirty"])
        self.assertTrue(self.manager._cache_unsaved)
        retry.assert_called_once_with(CACHE_RETRY_DELAYS_MS[0])
        self.assertEqual(self.manager._cache_retry_attempt, 1)

    def test_successful_flush_clears_the_unsaved_state(self):
        self.manager._cache_unsaved = True
        self.manager._cache_retry_attempt = 2
        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch("managers.discovery_store.atomic_write_json"):
            self.manager._flush_cache()
        self.assertFalse(self.manager._cache_unsaved)
        self.assertEqual(self.manager._cache_retry_attempt, 0)
        self.assertFalse(self.manager._cache_retry_timer.isActive())

    def test_retries_are_capped_then_wait_for_shutdown(self):
        self.manager._cache_unsaved = True
        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch(
            "managers.discovery_store.atomic_write_json",
            side_effect=OSError("disk full"),
        ), mock.patch.object(self.manager._cache_retry_timer, "start") as start:
            for _ in range(len(CACHE_RETRY_DELAYS_MS) + 2):
                self.manager._flush_cache()
        self.assertEqual(self.manager._cache_retry_attempt,
                         len(CACHE_RETRY_DELAYS_MS))
        self.assertEqual(start.call_count, len(CACHE_RETRY_DELAYS_MS))


class DebounceTests(unittest.TestCase):
    def setUp(self):
        self.manager = DiscoveryManager(empty_services())

    def test_save_timer_is_single_shot_and_1500_ms(self):
        self.assertTrue(self.manager._save_timer.isSingleShot())
        self.assertEqual(self.manager._save_timer.interval(), 1500)

    def test_save_cache_only_starts_the_timer_and_marks_unsaved(self):
        self.assertFalse(self.manager._cache_unsaved)
        with mock.patch.object(self.manager._save_timer, "start") as start:
            self.manager._save_cache()
            self.manager._save_cache()
        self.assertEqual(start.call_count, 2)
        self.assertTrue(self.manager._cache_unsaved)


class LoadCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "discovery_cache.json"
        self.manager = DiscoveryManager(empty_services())
        self.manager._data = {}

    def tearDown(self):
        self.tmp.cleanup()

    def _load(self):
        with mock.patch(
            "managers.discovery_store.cache_path", return_value=str(self.path)
        ), mock.patch.object(
            DiscoveryManager, "categories",
            lambda self: [{"key": "row"}, {"key": "keep"}],
        ), mock.patch.object(
            DiscoveryManager, "_title", lambda self, key: key.title()
        ):
            self.manager._load_cache()

    def test_corrupt_json_clears_the_rows(self):
        self.manager._data = {"keep": {"cards": []}}
        self.path.write_text("{not json", encoding="utf-8")
        self._load()
        self.assertEqual(self.manager._data, {})

    def test_non_dict_payload_is_ignored(self):
        self.manager._data = {"keep": {"cards": []}}
        self.path.write_text("[]", encoding="utf-8")
        self._load()
        self.assertEqual(self.manager._data, {"keep": {"cards": []}})

    def test_incomplete_unknown_and_all_games_entries_are_skipped(self):
        self.path.write_text(json.dumps({
            "all_games": {"schema": CACHE_SCHEMA, "cards": []},
            "unknown": {"schema": CACHE_SCHEMA, "cards": []},
            "bad": {"schema": CACHE_SCHEMA, "cards": "not-a-list"},
            "row": {"schema": CACHE_SCHEMA, "cards": [{"title": "New"}]},
        }), encoding="utf-8")
        self._load()
        self.assertEqual(set(self.manager._data), {"row"})
        entry = self.manager._data["row"]
        self.assertEqual(entry["games"], [])
        self.assertEqual(entry["title"], "Row")

    def test_missing_file_clears_the_rows(self):
        self.manager._data = {"keep": {"cards": []}}
        self._load()
        self.assertEqual(self.manager._data, {})


class CategoryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "discovery_categories.json"
        self.manager = DiscoveryManager(empty_services())

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_path(self):
        return mock.patch(
            "managers.discovery_store.category_index_path",
            return_value=str(self.path),
        )

    def test_round_trip(self):
        with self._patch_path():
            self.manager._on_category_index({"https://x/game": [3, 5]})
        self.assertEqual(
            self.manager.steamrip_categories_for("https://x/game/"), [3, 5])

        other = DiscoveryManager(empty_services())
        with self._patch_path():
            other._load_category_index()
        self.assertEqual(other._category_index,
                         {"https://x/game": [3, 5]})
        self.assertGreater(other._category_index_fetched_at, 0)

    def test_corrupt_index_is_ignored(self):
        self.path.write_text("not json", encoding="utf-8")
        with self._patch_path():
            self.manager._load_category_index()
        self.assertIsNone(self.manager._category_index)

    def test_empty_index_is_ignored(self):
        self.path.write_text(json.dumps({"index": {}, "fetched_at": 10}),
                             encoding="utf-8")
        with self._patch_path():
            self.manager._load_category_index()
        self.assertIsNone(self.manager._category_index)


class StoreFunctionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_rows_raises_on_broken_json(self):
        path = self.dir / "cache.json"
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            discovery_store.load_rows(str(path), {"row"}, lambda key: key)

    def test_load_rows_returns_empty_for_a_non_dict_payload(self):
        path = self.dir / "cache.json"
        path.write_text("[]", encoding="utf-8")
        self.assertEqual(
            discovery_store.load_rows(str(path), {"row"}, lambda key: key),
            ({}, 0),
        )

    def test_save_rows_uses_the_atomic_writer(self):
        path = str(self.dir / "cache.json")
        with mock.patch("managers.discovery_store.atomic_write_json") as write:
            discovery_store.save_rows(path, {"row": {}})
        write.assert_called_once_with(
            path, {"row": {}}, separators=(",", ":"), fsync=False)

    def test_save_rows_propagates_write_failures(self):
        path = str(self.dir / "cache.json")
        with mock.patch("managers.discovery_store.atomic_write_json",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                discovery_store.save_rows(path, {})

    def test_category_index_empty_payload_is_ignored(self):
        path = self.dir / "categories.json"
        path.write_text('{"index": {}, "fetched_at": 10}', encoding="utf-8")
        self.assertEqual(
            discovery_store.load_category_index(str(path)), ({}, 0.0))

    def test_category_index_missing_file_raises(self):
        with self.assertRaises(OSError):
            discovery_store.load_category_index(str(self.dir / "missing.json"))

    def test_category_index_save_payload_is_exact(self):
        path = str(self.dir / "categories.json")
        with mock.patch("managers.discovery_store.atomic_write_json") as write:
            discovery_store.save_category_index(path, {"u": [1]}, 12.5)
        write.assert_called_once_with(
            path, {"fetched_at": 12.5, "index": {"u": [1]}}, fsync=False)


if __name__ == "__main__":
    unittest.main()
