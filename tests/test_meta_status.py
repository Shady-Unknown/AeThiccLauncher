"""Shared setup flags for the library and Explorer status strips."""
import unittest

from core.meta_status import has_save_path, has_steam_shortcut, missing_setup


class SetupFlagsTests(unittest.TestCase):
    def test_save_path_sources(self):
        self.assertTrue(has_save_path({"savegame_path_win": "%appdata%\\Game"}))
        self.assertTrue(has_save_path({"savegame_path_linux": "/home/u/Game"}))
        self.assertTrue(has_save_path({"savegame_path_custom": "/saves"}))
        self.assertFalse(has_save_path({}))
        self.assertFalse(has_save_path(None))

    def test_steam_shortcut(self):
        self.assertTrue(has_steam_shortcut({"steam_shortcut_id": "123"}))
        self.assertFalse(has_steam_shortcut({"steam_shortcut_id": "  "}))
        self.assertFalse(has_steam_shortcut({}))
        self.assertFalse(has_steam_shortcut(None))

    def test_missing_setup(self):
        self.assertEqual(missing_setup({}), ["SAVES", "STEAM"])
        self.assertEqual(
            missing_setup({
                "savegame_path_win": "%appdata%\\Game",
                "steam_shortcut_id": "123",
            }),
            [],
        )
        self.assertEqual(
            missing_setup({"savegame_path_win": "%appdata%\\Game"}),
            ["STEAM"],
        )

    def test_orphan_only_reports_json(self):
        self.assertEqual(missing_setup({"is_orphan": True}), ["JSON"])
        self.assertEqual(missing_setup(None), ["JSON"])


if __name__ == "__main__":
    unittest.main()
