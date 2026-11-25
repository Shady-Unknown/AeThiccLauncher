"""Steam identity refresh and automatic save-path recalculation."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import steam_integration
from managers import save_locations
from managers.save_locations import SavePathResolver
from tests.steam_identity_fixtures import make_prefix, write_shortcuts

MANIFEST_ENTRY = {
    "files": {
        r"%userprofile%\Documents\inZOI\SaveGames": {"tags": ["save"]},
    }
}


class IdentityRefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old_prefix = self.root / "steamapps" / "compatdata" / "4092316751"
        self.current_prefix = make_prefix(
            self.root, "3094713296", ("Documents", "inZOI", "SaveGames"))
        self.old_account = write_shortcuts(self.root, "111", [{
            "title": "inZOI",
            "exe": "/old/inZOI.exe",
            "start_dir": "/old",
        }])
        self.new_account = write_shortcuts(self.root, "222", [{
            "title": "inZOI",
            "exe": "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI/inZOI.exe",
            "start_dir": "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI",
        }])
        self.saved = []
        self.resolver = SavePathResolver(
            installed_games=lambda: {},
            find_manifest_entry=lambda title: MANIFEST_ENTRY,
            prompt_save_path=lambda title: "",
            notify=lambda *args: None,
            has_parent=lambda: False,
            save_meta=lambda meta: self.saved.append(dict(meta)) or True,
            get_compatdata_base=self._compatdata_base,
        )
        self.meta = {
            "title": "inZOI",
            "install_dir": "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI",
            "executable_path": r"C:/SteamRip/inZOI/inZOI/inZOI.exe",
            "steam_grid_id": "4092316751",
            "steam_shortcut_id": "17576366610451529728",
            "steam_account_id": "111",
            "savegame_path_win": r"%userprofile%\Documents\inZOI\SaveGames",
            "savegame_path_linux": "/old/compatdata/4092316751/pfx/Documents/inZOI/SaveGames",
            "savegame_path_source": "manifest",
        }

    def _compatdata_base(self, grid_id):
        path = self.root / "steamapps" / "compatdata" / str(grid_id)
        return str(path) if path.is_dir() else ""

    def _rescan(self, meta=None):
        meta = self.meta if meta is None else meta
        with mock.patch.object(
                steam_integration, "get_steam_account_paths",
                return_value=[str(self.old_account), str(self.new_account)]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            return self.resolver.rescan_save_folder(meta)

    def test_current_installation_wins_over_the_obsolete_shortcut_id(self):
        result = self._rescan()
        self.assertEqual(result["steam_grid_id"], "3094713296")
        self.assertEqual(result["steam_shortcut_id"], "13291692396849922048")
        self.assertEqual(result["steam_account_id"], "222")
        self.assertIn("3094713296", result["savegame_path_linux"])
        self.assertNotIn("4092316751", result["savegame_path_linux"])

    def test_ensure_refreshes_existing_automatic_paths(self):
        with mock.patch.object(
                steam_integration, "get_steam_account_paths",
                return_value=[str(self.old_account), str(self.new_account)]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            result = self.resolver.ensure_save_path_for_game(dict(self.meta))
        self.assertEqual(result["steam_grid_id"], "3094713296")
        self.assertIn("3094713296", result["savegame_path_linux"])

    def test_user_path_is_preserved_when_identity_changes(self):
        meta = dict(self.meta)
        meta["savegame_path_source"] = "user"
        meta["savegame_path_linux"] = "/manual/saves"
        meta["savegame_path_custom"] = "/manual/saves"
        result = self._rescan(meta)
        self.assertEqual(result["steam_grid_id"], "3094713296")
        self.assertEqual(result["savegame_path_custom"], "/manual/saves")
        self.assertEqual(result["savegame_path_linux"], "/manual/saves")

    def test_obsolete_windows_executable_is_replaced_by_the_live_shortcut(self):
        live = Path("/mnt/nvme_games_apps/SteamRip/inZOI/inZOI/inZOI.exe")
        with mock.patch("managers.save_locations.os.path.isfile",
                        side_effect=lambda path: Path(path) == live):
            result = self._rescan()
        self.assertEqual(result["executable_path"], str(live))

    def test_existing_user_executable_is_not_replaced(self):
        live = Path("/mnt/nvme_games_apps/SteamRip/inZOI/inZOI/inZOI.exe")
        meta = dict(self.meta)
        meta["executable_path"] = "/custom/exe"
        with mock.patch("managers.save_locations.os.path.isfile",
                        side_effect=lambda path: str(path) in (str(live), "/custom/exe")):
            result = self._rescan(meta)
        self.assertEqual(result["executable_path"], "/custom/exe")

    def test_second_rescan_does_not_write(self):
        self._rescan()
        self.saved.clear()
        self._rescan()
        self.assertEqual(self.saved, [])

    def test_signed_old_grid_is_compared_unsigned(self):
        meta = dict(self.meta)
        meta["steam_grid_id"] = "-202650545"  # signed form of 4092316751
        result = self._rescan(meta)
        self.assertEqual(result["steam_grid_id"], "3094713296")

    def test_same_installation_in_two_accounts_does_not_switch_ownership(self):
        duplicate = write_shortcuts(self.root, "444", [{
            "title": "inZOI",
            "exe": "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI/inZOI.exe",
            "start_dir": "/mnt/nvme_games_apps/SteamRip/inZOI/inZOI",
        }])
        meta = dict(self.meta)
        meta["steam_account_id"] = "111"
        with mock.patch.object(
                steam_integration, "get_steam_account_paths",
                return_value=[str(duplicate), str(self.new_account)]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            result = self.resolver.rescan_save_folder(meta)
        self.assertEqual(result["steam_grid_id"], "3094713296")
        self.assertEqual(result["steam_account_id"], "111")

    def test_batch_reads_the_vdf_index_once(self):
        metas = {"inZOI": dict(self.meta)}
        calls = []
        original = steam_integration.shortcut_records
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        with mock.patch.object(steam_integration, "shortcut_records",
                               side_effect=counted), \
                mock.patch.object(
                    steam_integration, "get_steam_account_paths",
                    return_value=[str(self.old_account), str(self.new_account)]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            self.resolver.refresh_steam_identities(metas)
        self.assertEqual(len(calls), 1)

    def test_no_current_prefix_does_not_keep_the_old_path(self):
        current = self.root / "steamapps" / "compatdata" / "3094713296"
        for path in sorted(current.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
        # Remove empty dirs without following anything.
        import shutil
        shutil.rmtree(current)
        result = self._rescan()
        self.assertEqual(result["steam_grid_id"], "3094713296")
        self.assertNotIn("4092316751", result.get("savegame_path_linux", ""))
        self.assertEqual(result.get("savegame_path_linux", ""), "")

    def test_path_recalculation_failure_rolls_back_memory(self):
        original = dict(self.meta)
        with mock.patch.object(
                self.resolver, "translate_win_path_to_linux",
                side_effect=RuntimeError("translation failed")):
            result = self._rescan(dict(self.meta))
        self.assertEqual(result, original)

    def test_metadata_failure_rolls_back_memory(self):
        original = dict(self.meta)
        def fail(meta):
            return False
        self.resolver._save_meta = fail
        result = self._rescan(dict(self.meta))
        self.assertEqual(result, original)

    def test_unreadable_vdf_is_not_treated_as_absence(self):
        broken = self.root / "broken" / "userdata" / "333" / "config"
        broken.mkdir(parents=True)
        (broken / "shortcuts.vdf").write_bytes(b"not a vdf")
        with mock.patch.object(
                steam_integration, "get_steam_account_paths",
                return_value=[str(self.new_account), str(broken.parents[0])]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            result = self.resolver.rescan_save_folder(dict(self.meta))
        self.assertEqual(result["steam_grid_id"], "4092316751")

    def test_multiple_same_title_installations_are_ambiguous(self):
        other = write_shortcuts(self.root, "333", [{
            "title": "inZOI",
            "exe": "/another/inZOI.exe",
            "start_dir": "/another",
        }])
        # New account points at the current installation; other account points
        # at a different one. Title alone must not choose either.
        with mock.patch.object(
                steam_integration, "get_steam_account_paths",
                return_value=[str(other), str(self.old_account)]), \
                mock.patch.object(save_locations.platform_info, "is_linux",
                                  return_value=True):
            result = self.resolver.rescan_save_folder(dict(self.meta))
        self.assertEqual(result["steam_grid_id"], "4092316751")


if __name__ == "__main__":
    unittest.main()
