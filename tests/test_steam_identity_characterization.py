"""Characterization of the cross-OS Steam identity problem (G3B-Fixes)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import save_paths, steam_integration
from core.steam_integration import get_steam_ids
from tests.steam_identity_fixtures import make_prefix, write_shortcuts


class SteamIdPathTests(unittest.TestCase):
    def test_same_game_on_two_os_paths_has_different_ids(self):
        windows = get_steam_ids(r"C:/SteamRip/inZOI/inZOI/inZOI.exe", "inZOI")
        linux = get_steam_ids(
            "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI/inZOI.exe", "inZOI")
        self.assertEqual(windows[1], "4092316751")
        self.assertEqual(linux[1], "3094713296")
        self.assertNotEqual(windows[1], linux[1])
        self.assertNotEqual(windows[2], linux[2])

    def test_only_the_current_prefix_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            current = "3094713296"
            make_prefix(root, current)
            with mock.patch.object(save_paths, "steamapps_roots",
                                   return_value=[str(root / "steamapps")]):
                self.assertIsNone(save_paths.compatdata_base("4092316751"))
                self.assertTrue(save_paths.compatdata_base(current))


class ExistingShortcutLookupLimitsTests(unittest.TestCase):
    def test_title_lookup_does_not_expose_the_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = write_shortcuts(root, "111", [{
                "title": "inZOI",
                "exe": "/old/inZOI.exe",
                "start_dir": "/old",
            }])
            second = write_shortcuts(root, "222", [{
                "title": "inZOI",
                "exe": "/new/inZOI.exe",
                "start_dir": "/new",
            }])
            with mock.patch.object(steam_integration, "get_steam_account_paths",
                                   return_value=[str(first), str(second)]):
                found = steam_integration.find_shortcut_in_steam("inZOI")
                owners = steam_integration.shortcut_owner_accounts("inZOI")
        self.assertIsNotNone(found)
        self.assertEqual(set(owners), {"111", "222"})


if __name__ == "__main__":
    unittest.main()
