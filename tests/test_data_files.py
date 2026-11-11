import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import data_files


class DataFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.bundle = root / "bundle"
        self.user = root / "user"
        self.state = root / "state"
        (self.bundle / "data").mkdir(parents=True)
        (self.user / "data").mkdir(parents=True)
        self.state.mkdir(parents=True)
        self.patches = [
            mock.patch("core.data_files.get_base_dir", return_value=str(self.bundle)),
            mock.patch.object(data_files.PathManager, "get_app_data_dir", return_value=self.user),
            mock.patch.object(data_files.PathManager, "get_state_dir", return_value=self.state),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_load_bundled_json(self):
        (self.bundle / "data" / "defs.json").write_text('{"schema": 1, "x": 2}', encoding="utf-8")
        self.assertEqual(data_files.load_bundled_json("defs.json"), {"schema": 1, "x": 2})

    def test_user_copy_wins(self):
        (self.bundle / "data" / "defs.json").write_text('{"schema": 1, "x": 1}', encoding="utf-8")
        (self.user / "data" / "defs.json").write_text('{"schema": 1, "x": 2}', encoding="utf-8")
        self.assertEqual(data_files.load_json("defs.json")["x"], 2)
        self.assertEqual(data_files.load_bundled_json("defs.json")["x"], 1)

    def test_schema_mismatch_returns_default(self):
        (self.bundle / "data" / "defs.json").write_text('{"schema": 2, "x": "bundle"}', encoding="utf-8")
        (self.user / "data" / "defs.json").write_text('{"schema": 1, "x": "user"}', encoding="utf-8")
        loaded = data_files.load_json("defs.json", {"fallback": True}, schema_version=2)
        self.assertEqual(loaded, {"fallback": True})

    def test_seed_user_copy(self):
        (self.bundle / "data" / "index.json").write_text('{"ok": true}', encoding="utf-8")
        seeded = data_files.seed_user_copy("index.json")
        self.assertIsNotNone(seeded)
        self.assertTrue(seeded.exists())
        self.assertEqual(json.loads(seeded.read_text(encoding="utf-8")), {"ok": True})

    def test_missing_file_returns_default(self):
        self.assertEqual(data_files.load_json("missing.json", {"fallback": 1}), {"fallback": 1})

    def test_ensure_user_data_copies_seed_and_excludes_posts_index(self):
        (self.bundle / "data" / "launcher_achievements.json").write_text(
            '{"schema": 1}', encoding="utf-8"
        )
        (self.bundle / "data" / "achievements").mkdir()
        (self.bundle / "data" / "achievements" / "icon.png").write_bytes(b"png")
        (self.bundle / "data" / "steamrip_posts_index.json").write_text(
            '{"posts": {}}', encoding="utf-8"
        )

        copied, _kept = data_files.ensure_user_data()

        self.assertEqual(copied, 2)
        self.assertTrue((self.user / "data" / "launcher_achievements.json").is_file())
        self.assertTrue((self.user / "data" / "achievements" / "icon.png").is_file())
        self.assertFalse((self.user / "data" / "steamrip_posts_index.json").exists())

    def test_ensure_user_data_refreshes_unmodified_files(self):
        source = self.bundle / "data" / "defs.json"
        target = self.user / "data" / "defs.json"
        source.write_text('{"v": 1}', encoding="utf-8")
        data_files.ensure_user_data()
        self.assertEqual(target.read_text(encoding="utf-8"), '{"v": 1}')

        source.write_text('{"v": 2}', encoding="utf-8")
        data_files.ensure_user_data()
        self.assertEqual(target.read_text(encoding="utf-8"), '{"v": 2}')

    def test_ensure_user_data_preserves_user_edits(self):
        source = self.bundle / "data" / "defs.json"
        target = self.user / "data" / "defs.json"
        source.write_text('{"v": 1}', encoding="utf-8")
        data_files.ensure_user_data()
        target.write_text('{"v": 99}', encoding="utf-8")

        source.write_text('{"v": 2}', encoding="utf-8")
        data_files.ensure_user_data()
        self.assertEqual(target.read_text(encoding="utf-8"), '{"v": 99}')


if __name__ == "__main__":
    unittest.main()
