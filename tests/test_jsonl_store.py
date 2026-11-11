import json
import tempfile
import unittest
from pathlib import Path

from core.jsonl_store import JsonlStore


class JsonlStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.jsonl"
        self.store = JsonlStore(self.path, schema_version=7)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_adds_metadata(self):
        record = self.store.append({"title": "DOOM"})
        self.assertEqual(record["v"], 7)
        self.assertTrue(record["id"])
        self.assertIsInstance(record["at"], int)
        self.assertEqual(self.store.count(), 1)

    def test_load_skips_truncated_last_line(self):
        self.store.append({"title": "A"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"title": "B", "id": "b",')
        records = self.store.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "A")

    def test_merge_is_idempotent(self):
        first = self.store.append({"id": "same", "title": "A"})
        added = self.store.merge([dict(first), {"id": "new", "title": "B"}])
        self.assertEqual(added, 1)
        self.assertEqual(self.store.count(), 2)

    def test_compact_dedupes_by_id(self):
        self.store.append_many([
            {"id": "same", "title": "A"},
            {"id": "same", "title": "A"},
            {"id": "other", "title": "B"},
        ])
        kept, original = self.store.compact()
        self.assertEqual((kept, original), (2, 3))
        self.assertEqual(self.store.count(), 2)

    def test_compact_keep_last(self):
        self.store.append_many([{"id": str(i)} for i in range(10)])
        kept, original = self.store.compact(keep=3)
        self.assertEqual((kept, original), (3, 10))
        self.assertEqual([r["id"] for r in self.store.load()], ["7", "8", "9"])

    def test_rewrite_is_atomic_and_valid(self):
        self.store.rewrite([{"id": "x", "title": "X"}])
        records = self.store.load()
        self.assertEqual(len(records), 1)
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        json.loads(self.path.read_text(encoding="utf-8").strip())


if __name__ == "__main__":
    unittest.main()
