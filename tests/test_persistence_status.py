"""Persistence APIs must report write failures, not only log them."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.config import CONFIG_FILE, save_config
from core.meta_utils import save_steamrip_meta


class SaveConfigStatusTests(unittest.TestCase):
    def test_success_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config"
            with mock.patch(
                "core.paths.PathManager.get_config_dir", return_value=target
            ):
                ok = save_config({"current_theme": "PurpleRip"})

            self.assertTrue(ok)
            self.assertTrue((target / CONFIG_FILE).is_file())

    def test_unwritable_target_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "config"
            blocked.write_text("not a directory", encoding="utf-8")
            with mock.patch(
                "core.paths.PathManager.get_config_dir", return_value=blocked
            ):
                with self.assertLogs("core.config", level="ERROR"):
                    ok = save_config({"current_theme": "PurpleRip"})

            self.assertFalse(ok)


class SaveGameMetaStatusTests(unittest.TestCase):
    def test_success_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_dir = Path(tmp) / "Game"
            install_dir.mkdir()

            ok = save_steamrip_meta(str(install_dir), {"title": "Game"})

            self.assertTrue(ok)
            data = json.loads(
                (install_dir / "steamrip_meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["title"], "Game")

    def test_unwritable_target_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocked = Path(tmp) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")

            with self.assertLogs("core.meta_utils", level="ERROR"):
                ok = save_steamrip_meta(str(blocked), {"title": "Game"})

            self.assertFalse(ok)

    def test_corrupt_metadata_is_preserved_and_rewritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_dir = Path(tmp) / "Game"
            install_dir.mkdir()
            meta_path = install_dir / "steamrip_meta.json"
            meta_path.write_text("{ not json", encoding="utf-8")

            with self.assertLogs("core.meta_utils", level="ERROR"):
                ok = save_steamrip_meta(str(install_dir), {"title": "Game"})

            self.assertTrue(ok)
            self.assertTrue(list(install_dir.glob("steamrip_meta.json.corrupt-*")))
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(data["title"], "Game")


if __name__ == "__main__":
    unittest.main()
