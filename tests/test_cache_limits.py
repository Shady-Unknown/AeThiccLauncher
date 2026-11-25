import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.cache_limits import trim_list, trim_mapping
from managers.igdb_details_cache import IgdbDetailsCache


class TrimMappingTests(unittest.TestCase):
    def test_keeps_newest_entries_by_timestamp(self):
        data = {
            "old": {"cached_at": 10},
            "newest": {"cached_at": 30},
            "middle": {"cached_at": 20},
        }
        removed = trim_mapping(data, 2, "cached_at")
        self.assertEqual(removed, 1)
        self.assertEqual(set(data), {"middle", "newest"})

    def test_insertion_order_without_timestamp(self):
        data = {"a": {}, "b": {}, "c": {}}
        trim_mapping(data, 2)
        self.assertEqual(set(data), {"b", "c"})

    def test_no_trim_when_under_limit(self):
        data = {"a": {"cached_at": 1}}
        self.assertEqual(trim_mapping(data, 5, "cached_at"), 0)
        self.assertEqual(set(data), {"a"})

    def test_zero_limit_clears_mapping(self):
        data = {"a": {}, "b": {}}
        trim_mapping(data, 0)
        self.assertEqual(data, {})


class TrimListTests(unittest.TestCase):
    def test_keeps_the_last_items(self):
        items = [1, 2, 3, 4]
        self.assertEqual(trim_list(items, 2), 2)
        self.assertEqual(items, [3, 4])

    def test_zero_limit_clears(self):
        items = [1, 2]
        trim_list(items, 0)
        self.assertEqual(items, [])


class SessionCacheTrimTests(unittest.TestCase):
    def test_session_cache_uses_cached_at(self):
        from ui.controllers.catalog import CatalogController

        fake = SimpleNamespace(session_cache={
            "old": {"cached_at": 1},
            "newer": {"cached_at": 3},
            "newest": {"cached_at": 5},
        })
        with mock.patch("ui.controllers.catalog.SESSION_CACHE_LIMIT", 2):
            removed = CatalogController._trim_session_cache(fake)
        self.assertEqual(removed, 1)
        self.assertEqual(set(fake.session_cache), {"newer", "newest"})


class IgdbCacheLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "igdb_details.json"
        self._original = IgdbDetailsCache._data
        IgdbDetailsCache._data = None
        self.addCleanup(self._restore)

    def _restore(self):
        IgdbDetailsCache._data = self._original

    def test_expired_and_wrong_schema_entries_are_dropped(self):
        payload = {
            "good": {"schema": 2, "fetched_at": 10 ** 10, "details": {"a": 1}},
            "old": {"schema": 2, "fetched_at": 0, "details": {"a": 1}},
            "legacy": {"schema": 1, "fetched_at": 10 ** 10, "details": {}},
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with mock.patch.object(IgdbDetailsCache, "_path", return_value=str(self.path)):
            data = IgdbDetailsCache._load()

        self.assertEqual(set(data), {"good"})

    def test_cache_is_capped(self):
        now = 10 ** 10
        payload = {
            f"game{i}": {"schema": 2, "fetched_at": now + i, "details": {}}
            for i in range(6)
        }
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with mock.patch.object(IgdbDetailsCache, "_path", return_value=str(self.path)), \
             mock.patch("managers.igdb_details_cache.CACHE_MAX_ENTRIES", 3):
            data = IgdbDetailsCache._load()

        self.assertEqual(len(data), 3)
        self.assertEqual(set(data), {"game3", "game4", "game5"})


class JsonlCompactionTests(unittest.TestCase):
    def test_compact_history_deduplicates_without_dropping_records(self):
        from core.state_store import StateStore

        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(base_dir=Path(tmp))
            now = 10 ** 10
            store.sessions.append({"id": "a", "at": now})
            store.sessions.append({"id": "b", "at": now + 1})
            store.sessions.append({"id": "a", "at": now + 2})

            report = store.compact_history()
            remaining = store.sessions.count()

        self.assertEqual(report["sessions"], (3, 2))
        self.assertEqual(remaining, 2)


if __name__ == "__main__":
    unittest.main()
