import json
import tempfile
import unittest
from pathlib import Path

from core.master_catalog import CatalogCorruptionError, MasterCatalog


class MasterCatalogPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "master_catalog.json"
        self.bak = Path(str(self.path) + ".bak")

    def _write(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_missing_file_starts_empty(self):
        catalog = MasterCatalog(str(self.path))
        self.assertEqual(len(catalog), 0)

    def test_flush_is_atomic_and_keeps_one_backup(self):
        catalog = MasterCatalog(str(self.path))
        catalog.set_url("Game A", "https://example.test/a")
        catalog.save_immediate()
        first = self.path.read_text(encoding="utf-8")

        catalog.set_url("Game B", "https://example.test/b")
        catalog.save_immediate()

        self.assertEqual(self.bak.read_text(encoding="utf-8"), first)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("Game A", data)
        self.assertIn("Game B", data)
        self.assertEqual(list(Path(self.tmp.name).glob("*.tmp")), [])

    def test_legacy_string_entries_are_normalised(self):
        self._write(self.path, {"Game A": "https://example.test/a"})
        catalog = MasterCatalog(str(self.path))
        self.assertEqual(catalog.get_entry("Game A")["url"], "https://example.test/a")

    def test_corrupt_main_recovers_from_backup(self):
        self.path.write_text("{broken", encoding="utf-8")
        self._write(self.bak, {"Game A": {"url": "https://example.test/a"}})

        catalog = MasterCatalog(str(self.path))

        self.assertEqual(catalog.get_entry("Game A")["url"], "https://example.test/a")
        recovered = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("Game A", recovered)
        # The good backup stays untouched until the next regular save.
        self.assertEqual(
            json.loads(self.bak.read_text(encoding="utf-8"))["Game A"]["url"],
            "https://example.test/a",
        )

    def test_non_object_main_recovers_from_backup(self):
        self._write(self.path, ["not", "a", "dict"])
        self._write(self.bak, {"Game A": {"url": "https://example.test/a"}})

        catalog = MasterCatalog(str(self.path))

        self.assertEqual(catalog.get_entry("Game A")["url"], "https://example.test/a")

    def test_both_corrupt_refuses_to_start(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.bak.write_text("{broken", encoding="utf-8")
        with self.assertRaises(CatalogCorruptionError):
            MasterCatalog(str(self.path))
        # The corrupt file is never silently replaced.
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_corrupt_without_backup_refuses_to_start(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(CatalogCorruptionError):
            MasterCatalog(str(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
