import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.atomic_io import (
    AtomicIOError,
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    preserve_corrupt_file,
)


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "data.json"

    def _tmp_files(self):
        return list(self.dir.glob("*.tmp"))

    def test_json_write_and_overwrite(self):
        atomic_write_json(self.path, {"a": 1}, fsync=False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1})
        atomic_write_json(self.path, {"a": 2}, fsync=False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 2})
        self.assertEqual(self._tmp_files(), [])

    def test_backup_keeps_previous_version(self):
        atomic_write_json(self.path, {"a": 1}, fsync=False)
        atomic_write_json(self.path, {"a": 2}, fsync=False, backup=True)
        backup = self.dir / "data.json.bak"
        self.assertTrue(backup.exists())
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 2})

    def test_backup_false_never_creates_bak(self):
        atomic_write_json(self.path, {"a": 1}, fsync=False)
        atomic_write_json(self.path, {"a": 2}, fsync=False, backup=False)
        self.assertFalse((self.dir / "data.json.bak").exists())

    def test_failed_dump_leaves_original_and_cleans_tmp(self):
        atomic_write_json(self.path, {"a": 1}, fsync=False)
        with mock.patch("core.atomic_io.json.dump", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                atomic_write_json(self.path, {"a": 2}, fsync=False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(self._tmp_files(), [])

    def test_failed_replace_leaves_original_and_cleans_tmp(self):
        atomic_write_json(self.path, {"a": 1}, fsync=False)
        with mock.patch("core.atomic_io.os.replace", side_effect=OSError("nope")):
            with self.assertRaises(AtomicIOError):
                atomic_write_json(self.path, {"a": 2}, fsync=False)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 1})
        self.assertEqual(self._tmp_files(), [])

    def test_bytes_and_text_writers(self):
        binary = self.dir / "shortcuts.vdf"
        atomic_write_bytes(binary, b"\x00\x01binary", fsync=False)
        self.assertEqual(binary.read_bytes(), b"\x00\x01binary")

        text = self.dir / "config.vdf"
        atomic_write_text(text, "a\nb\n", fsync=False)
        self.assertEqual(text.read_text(encoding="utf-8"), "a\nb\n")
        self.assertEqual(self._tmp_files(), [])

    def test_missing_parent_is_created(self):
        nested = self.dir / "a" / "b" / "data.json"
        atomic_write_json(nested, {"a": 1}, fsync=False)
        self.assertTrue(nested.exists())


class PreserveCorruptTests(unittest.TestCase):
    def test_copies_without_removing_original(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.json"
        path.write_text("{broken", encoding="utf-8")

        copy = preserve_corrupt_file(path)

        self.assertTrue(path.exists())
        self.assertIsNotNone(copy)
        self.assertIn(".corrupt-", copy.name)
        self.assertEqual(copy.read_text(encoding="utf-8"), "{broken")

    def test_missing_file_returns_none(self):
        self.assertIsNone(preserve_corrupt_file("/tmp/does-not-exist-atomictest.json"))


if __name__ == "__main__":
    unittest.main()
