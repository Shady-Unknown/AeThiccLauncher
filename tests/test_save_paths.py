import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
