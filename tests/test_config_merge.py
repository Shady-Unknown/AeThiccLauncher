import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import config as config_mod
from core.config import _deep_merge
from core.paths import PathManager


class DeepMergeTests(unittest.TestCase):
    def test_nested_defaults_are_preserved(self):
        base = {"savegames": {"sync_folder": "", "sync_interval": 30, "new_key": True}}
        user = {"savegames": {"sync_folder": "D:/Saves"}}
        merged = _deep_merge(base, user)
        self.assertEqual(merged["savegames"]["sync_folder"], "D:/Saves")
        self.assertEqual(merged["savegames"]["sync_interval"], 30)
        self.assertTrue(merged["savegames"]["new_key"])

    def test_lists_are_replaced(self):
        base = {"watched_folders": ["default"]}
        user = {"watched_folders": ["D:/Games", "E:/Games"]}
        merged = _deep_merge(base, user)
        self.assertEqual(merged["watched_folders"], ["D:/Games", "E:/Games"])

    def test_unknown_keys_are_kept(self):
        merged = _deep_merge({"a": 1}, {"b": 2})
        self.assertEqual(merged, {"a": 1, "b": 2})

    def test_inputs_are_not_mutated(self):
        base = {"a": {"b": 1}}
        user = {"a": {"c": 2}}
        base_copy = copy.deepcopy(base)
        user_copy = copy.deepcopy(user)
        _deep_merge(base, user)
        self.assertEqual(base, base_copy)
        self.assertEqual(user, user_copy)


class ConfigLoadTests(unittest.TestCase):
    def test_bom_encoded_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "config"
            config_dir.mkdir(parents=True)
            payload = {
                "watched_folders": ["D:/Games"],
                "onboarding": {"completed": True, "version": 1},
                "savegames": {"sync_folder": "E:/Saves"},
            }
            raw = b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8")
            (config_dir / "config.json").write_bytes(raw)

            with mock.patch.object(
                PathManager, "get_config_dir", return_value=config_dir
            ), mock.patch.object(
                PathManager, "get_legacy_root", return_value=Path(tmp) / "legacy"
            ):
                loaded = config_mod.load_config()

            self.assertEqual(loaded["watched_folders"], ["D:/Games"])
            self.assertEqual(loaded["savegames"]["sync_folder"], "E:/Saves")
            self.assertTrue(loaded["onboarding"]["completed"])


if __name__ == "__main__":
    unittest.main()
