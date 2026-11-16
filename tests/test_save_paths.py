import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import save_paths
from managers import save_manager as save_manager_module
from managers.save_manager import SavegamesManager

COMPATDATA_BASE = "/home/deck/.local/share/Steam/steamapps/compatdata/4092316751"

# Value produced by expanding "~" on Windows; it used to be stored in
# savegames.json as the "linux" save path for games missing from the manifest.
WINDOWS_GROWN_LINUX_PATH = (
    "C:Users\\jmain\\.local\\share\\Steam\\steamapps\\compatdata\\4092316751"
    "\\pfx\\drive_c\\users\\steamuser\\My Documents\\inZOI\\SaveGames"
)


def make_manager():
    """Build a manager without touching PathManager/config (not needed here)."""
    return object.__new__(SavegamesManager)


class LinuxPathDetectionTests(unittest.TestCase):
    def test_rejects_windows_grown_expanduser_path(self):
        self.assertFalse(SavegamesManager.is_linux_path(WINDOWS_GROWN_LINUX_PATH))

    def test_accepts_absolute_and_home_relative_paths(self):
        self.assertTrue(SavegamesManager.is_linux_path("/home/deck/Documents/inZOI/SaveGames"))
        self.assertTrue(SavegamesManager.is_linux_path("~/.local/share/Steam/steamapps/compatdata/1"))

    def test_missing_path_is_not_a_linux_path(self):
        self.assertFalse(SavegamesManager.is_linux_path(""))
        self.assertFalse(SavegamesManager.is_linux_path(None))


class TranslateWinPathTests(unittest.TestCase):
    def test_no_translation_off_linux(self):
        manager = make_manager()
        with mock.patch.object(save_manager_module.platform_info, "is_linux", return_value=False), \
                mock.patch.object(SavegamesManager, "_get_compatdata_base", return_value=COMPATDATA_BASE):
            result = manager.translate_win_path_to_linux(
                "%userprofile%\\My Documents\\inZOI\\SaveGames", "4092316751"
            )
        self.assertEqual(result, "")

    def test_userprofile_my_documents_maps_to_documents(self):
        manager = make_manager()
        with mock.patch.object(save_manager_module.platform_info, "is_linux", return_value=True), \
                mock.patch.object(SavegamesManager, "_get_compatdata_base", return_value=COMPATDATA_BASE):
            result = manager.translate_win_path_to_linux(
                "%userprofile%\\My Documents\\inZOI\\SaveGames", "4092316751"
            )

        normalized = result.replace("\\", "/")
        self.assertTrue(
            normalized.endswith("/pfx/drive_c/users/steamuser/Documents/inZOI/SaveGames"),
            normalized,
        )
        self.assertNotIn("My Documents", normalized)
        self.assertFalse(normalized.startswith("C:"))

    def test_localappdata_maps_to_appdata_local(self):
        manager = make_manager()
        with mock.patch.object(save_manager_module.platform_info, "is_linux", return_value=True), \
                mock.patch.object(SavegamesManager, "_get_compatdata_base", return_value=COMPATDATA_BASE):
            result = manager.translate_win_path_to_linux(
                "%localappdata%\\inZOI\\SaveGames", "4092316751"
            )

        normalized = result.replace("\\", "/")
        self.assertTrue(
            normalized.endswith("/pfx/drive_c/users/steamuser/AppData/Local/inZOI/SaveGames"),
            normalized,
        )

    def test_requires_grid_id(self):
        manager = make_manager()
        with mock.patch.object(save_manager_module.platform_info, "is_linux", return_value=True):
            result = manager.translate_win_path_to_linux("%userprofile%\\Documents\\inZOI", "")
        self.assertEqual(result, "")


class PersistedLinuxPathTests(unittest.TestCase):
    def test_drops_windows_grown_value(self):
        manager = make_manager()
        meta = {"savegame_path_linux": WINDOWS_GROWN_LINUX_PATH}
        self.assertEqual(manager._persisted_linux_path(meta), "")

    def test_keeps_valid_linux_value(self):
        manager = make_manager()
        meta = {"savegame_path_linux": "/home/deck/Documents/inZOI/SaveGames"}
        self.assertEqual(meta["savegame_path_linux"], manager._persisted_linux_path(meta))

    def test_missing_value_is_empty(self):
        manager = make_manager()
        self.assertEqual(manager._persisted_linux_path({}), "")


class TranslateHelpersTests(unittest.TestCase):
    def test_legacy_shell_folder_mapping(self):
        self.assertEqual(
            save_paths.map_legacy_shell_folder("My Documents\\inZOI\\Saves"),
            "Documents\\inZOI\\Saves",
        )
        self.assertEqual(
            save_paths.map_legacy_shell_folder("My Games\\Other"),
            "My Games\\Other",
        )

    def test_canonicalize_shell_folders_on_windows_paths(self):
        self.assertEqual(
            save_paths.canonicalize_shell_folders("%userprofile%\\My Documents\\inZOI"),
            "%userprofile%\\Documents\\inZOI",
        )
        self.assertEqual(
            save_paths.canonicalize_shell_folders("C:\\Users\\bob\\My Pictures\\Game"),
            "C:\\Users\\bob\\Pictures\\Game",
        )
        self.assertEqual(
            save_paths.canonicalize_shell_folders("%appdata%\\Game\\My Documents"),
            "%appdata%\\Game\\My Documents",
        )

    def test_translate_unknown_token_returns_empty(self):
        self.assertEqual(save_paths.translate_win_path("%weird%\\Saves", COMPATDATA_BASE), "")

    def test_translate_localappdata(self):
        result = save_paths.translate_win_path("%localappdata%\\Game\\Saves", COMPATDATA_BASE)
        normalized = result.replace("\\", "/")
        self.assertTrue(
            normalized.endswith("/pfx/drive_c/users/steamuser/AppData/Local/Game/Saves"),
            normalized,
        )

    def test_translate_c_users_other_account(self):
        result = save_paths.translate_win_path("C:\\users\\someone\\Saved Games\\Game", COMPATDATA_BASE)
        normalized = result.replace("\\", "/")
        self.assertTrue(
            normalized.endswith("/pfx/drive_c/users/steamuser/Saved Games/Game"),
            normalized,
        )

    def test_translate_c_drive_root(self):
        result = save_paths.translate_win_path("C:\\Game\\Saves", COMPATDATA_BASE)
        normalized = result.replace("\\", "/")
        self.assertTrue(normalized.endswith("/pfx/drive_c/Game/Saves"), normalized)

    def test_resolve_save_root_strips_file_names_and_wildcards(self):
        self.assertEqual(
            save_paths.resolve_save_root(os.path.join("base", "saves", "slot1.sav")),
            os.path.join("base", "saves"),
        )
        self.assertEqual(
            save_paths.resolve_save_root(os.path.join("base", "saves", "<storeUserId>")),
            os.path.join("base", "saves"),
        )
        self.assertEqual(
            save_paths.resolve_save_root(os.path.join("base", "saves")),
            os.path.join("base", "saves"),
        )


class SteamLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_vdf(self, relative, windows_style=False):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if windows_style:
            body = (
                '"libraryfolders"\n{\n'
                '\t"0"\n\t{\n\t\t"path"\t\t"C:\\\\Steam"\n\t}\n'
                '\t"1"\n\t{\n\t\t"path"\t\t"D:\\\\SteamLibrary"\n\t}\n}\n'
            )
        else:
            body = (
                '"libraryfolders"\n{\n'
                '\t"0"\n\t{\n\t\t"path"\t\t"/home/deck/.local/share/Steam"\n\t}\n'
                '\t"1"\n\t{\n\t\t"path"\t\t"/run/media/deck/SSD/SteamLibrary"\n\t}\n}\n'
            )
        path.write_text(body, encoding="utf-8")
        return path

    def test_parse_current_and_legacy_formats(self):
        current = self._write_vdf("steam/steamapps/libraryfolders.vdf")
        self.assertEqual(
            save_paths.parse_library_folders(current),
            ["/home/deck/.local/share/Steam", "/run/media/deck/SSD/SteamLibrary"],
        )

    def test_parse_escaped_windows_paths(self):
        vdf = self._write_vdf("steam/steamapps/libraryfolders.vdf", windows_style=True)
        self.assertEqual(
            save_paths.parse_library_folders(vdf),
            ["C:\\Steam", "D:\\SteamLibrary"],
        )

    def test_missing_vdf_is_empty(self):
        self.assertEqual(save_paths.parse_library_folders(self.root / "nope.vdf"), [])

    def test_compatdata_found_on_secondary_library(self):
        first = self.root / "lib1" / "steamapps"
        second = self.root / "lib2" / "steamapps"
        first.mkdir(parents=True)
        expected = second / "compatdata" / "123"
        expected.mkdir(parents=True)

        found = save_paths.compatdata_base("123", roots=[str(first), str(second)])
        self.assertEqual(found, str(expected))

    def test_compatdata_missing_returns_none_and_default(self):
        first = self.root / "lib1" / "steamapps"
        first.mkdir(parents=True)
        self.assertIsNone(save_paths.compatdata_base("999", roots=[str(first)]))
        self.assertEqual(
            save_paths.default_compatdata_base("999", roots=[str(first)]),
            str(first / "compatdata" / "999"),
        )

    def test_steamapps_roots_include_registered_libraries(self):
        extra = self.root / "Steam" / "steamapps"
        library = self.root / "OtherLibrary"
        library.mkdir(parents=True)
        self._write_vdf("Steam/steamapps/libraryfolders.vdf")
        # Point the VDF at the temp library instead of the real deck paths.
        vdf = extra / "libraryfolders.vdf"
        vdf.write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
            % str(library).replace("\\", "\\\\"),
            encoding="utf-8",
        )

        roots = save_paths.steamapps_roots(extra_paths=[str(extra)])
        self.assertIn(str(extra), roots)
        self.assertIn(str(library / "steamapps"), roots)


if __name__ == "__main__":
    unittest.main()
