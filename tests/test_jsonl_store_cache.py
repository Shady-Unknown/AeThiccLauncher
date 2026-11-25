"""JsonlStore read cache: one parse per file change, not per call."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.jsonl_store import JsonlStore


class CountingStore(JsonlStore):
    def __init__(self, path, schema_version=1):
        super().__init__(path, schema_version=schema_version)
        self.reads = 0

    def _read_file(self):
        self.reads += 1
        return super()._read_file()


def _record(index):
    return {"title": f"Game {index}", "seconds": 60, "at": 1000 + index}


class JsonlStoreCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "history.jsonl"
        self.store = CountingStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_is_cached_until_a_write(self):
        self.store.append(_record(1))
        first = self.store.load()
        second = self.store.load()

        self.assertEqual(first, second)
        self.assertEqual(self.store.reads, 1)

    def test_append_invalidates_the_cache(self):
        self.store.append(_record(1))
        self.store.load()

        self.store.append(_record(2))

        self.assertEqual(len(self.store.load()), 2)
        self.assertEqual(self.store.reads, 2)

    def test_append_many_invalidates_the_cache(self):
        self.store.append(_record(1))
        self.store.load()

        self.store.append_many([_record(2), _record(3)])

        self.assertEqual(len(self.store.load()), 3)

    def test_rewrite_invalidates_the_cache(self):
        self.store.append(_record(1))
        self.store.append(_record(2))
        self.store.load()

        self.store.rewrite([_record(9)])

        records = self.store.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Game 9")

    def test_external_append_is_detected(self):
        self.store.append(_record(1))
        self.store.load()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_record(2), separators=(",", ":")) + "\n")

        self.assertEqual(len(self.store.load()), 2)

    def test_external_rewrite_is_detected(self):
        self.store.append(_record(1))
        self.store.load()
        self.path.write_text(
            json.dumps(_record(7), separators=(",", ":")) + "\n",
            encoding="utf-8",
        )

        records = self.store.load()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "Game 7")

    def test_returned_list_does_not_corrupt_the_cache(self):
        self.store.append(_record(1))
        records = self.store.load()
        records.append(_record(99))

        self.assertEqual(len(self.store.load()), 1)

    def test_ids_and_count_use_the_cache(self):
        self.store.append(_record(1))
        self.store.append(_record(2))

        self.assertEqual(self.store.count(), 2)
        self.assertEqual(len(self.store.ids()), 2)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.reads, 1)

    def test_missing_file_stays_empty_until_created(self):
        self.assertEqual(self.store.load(), [])
        self.assertEqual(self.store.load(), [])
        with self.path.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(_record(3), separators=(",", ":")) + "\n")

        self.assertEqual(len(self.store.load()), 1)

    def test_fingerprint_changes_on_write(self):
        before = self.store.fingerprint()
        self.store.append(_record(1))
        self.assertNotEqual(before, self.store.fingerprint())

    def test_concurrent_append_keeps_the_file_consistent(self):
        def writer():
            for index in range(50):
                self.store.append(_record(index))

        thread = threading.Thread(target=writer)
        thread.start()
        for _ in range(20):
            self.store.load()
            time.sleep(0.001)
        thread.join()

        self.assertEqual(self.store.count(), 50)
        ids = [record["id"] for record in self.store.load()]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
