"""Emulator save folders (CODEX/RUNE/Goldberg) and their resolution."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import save_paths, steam_emu
from managers import save_locations as save_locations_module
from managers import save_manager as save_manager_module
from managers.save_manager import SavegamesManager


def make_manager():
    return object.__new__(SavegamesManager)


def _touch(path, text=""):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class DetectEmulatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Game"
        (self.root / "Control").mkdir(parents=True)
        self.exe = str(self.root / "Control" / "Game.exe")

    def test_codex(self):
        _touch(self.root / "Control" / "steam_api64.cdx")
        _touch(
            self.root / "Control" / "steam_emu.ini",
            "[Settings]\nAppId=870780\nUserName=STEAMRIP\n",
        )

        info = steam_emu.detect_emulator(self.exe, str(self.root))

        self.assertEqual(info["emulator"], "codex")
        self.assertEqual(info["appid"], "870780")
        self.assertEqual(
            info["windows_path"],
            r"%public%\Documents\Steam\CODEX\870780",
        )

    def test_rune(self):
        _touch(self.root / "Control" / "steam_api64.rne")
        _touch(self.root / "Control" / "steam_emu.ini", "AppId=1234\n")

        info = steam_emu.detect_emulator(self.exe, str(self.root))

        self.assertEqual(info["emulator"], "rune")
        self.assertEqual(
            info["windows_path"],
            r"%public%\Documents\Steam\RUNE\1234",
        )

    def test_goldberg_appid_file(self):
        _touch(self.root / "Control" / "steam_settings" / "steam_appid.txt", "555\n")

        info = steam_emu.detect_emulator(self.exe, str(self.root))

        self.assertEqual(info["emulator"], "goldberg")
        self.assertEqual(info["windows_path"], r"%appdata%\GSE Saves\555")

    def test_goldberg_offers_both_folders_and_appids(self):
        _touch(self.root / "Control" / "steam_settings" / "steam_appid.txt", "3357650\n")

        info = steam_emu.detect_emulator(
            self.exe, str(self.root), appid_hint="2421117525"
        )

        self.assertEqual(
            info["candidates"],
            [
                r"%appdata%\GSE Saves\2421117525",
                r"%appdata%\GSE Saves\3357650",
                r"%appdata%\Goldberg SteamEmu Saves\2421117525",
                r"%appdata%\Goldberg SteamEmu Saves\3357650",
            ],
        )
        self.assertEqual(info["windows_path"], info["candidates"][0])

    def test_no_emulator(self):
        self.assertEqual(steam_emu.detect_emulator(self.exe, str(self.root)), {})

    def test_appid_is_required(self):
        _touch(self.root / "Control" / "steam_api64.cdx")

        self.assertEqual(steam_emu.detect_emulator(self.exe, str(self.root)), {})


class EmulatorSavePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "Game"
        (self.root / "Control").mkdir(parents=True)
        _touch(self.root / "Control" / "steam_api64.cdx")
        _touch(self.root / "Control" / "steam_emu.ini", "AppId=870780\n")
        self.meta = {
            "title": "Control Ultimate Edition",
            "install_dir": str(self.root),
            "executable_path": str(self.root / "Control" / "Game.exe"),
        }

    def test_rescan_prefers_the_emulator_over_the_manifest(self):
        manager = make_manager()
        manager.installed_games = {}
        manager.find_manifest_entry = lambda title: {
            "files": {
                r"<winDocuments>\My Games\Control\Saves": {
                    "tags": ["save"],
                    "when": [{"os": "windows", "store": "gog"}],
                },
            }
        }
        manager.save_meta = lambda meta: None
        manager._notify_callback = None

        result = manager.rescan_save_folder(dict(self.meta))

        self.assertEqual(
            result["savegame_path_win"],
            r"%public%\Documents\Steam\CODEX\870780",
        )
        self.assertEqual(result["savegame_path_source"], "emulator")

    def test_rescan_keeps_a_user_path(self):
        manager = make_manager()
        manager.installed_games = {}
        manager.find_manifest_entry = lambda title: {}
        manager.save_meta = lambda meta: None
        manager._notify_callback = None
        meta = dict(self.meta)
        meta["savegame_path_win"] = r"%userprofile%\Documents\My Saves"
        meta["savegame_path_source"] = "user"

        result = manager.rescan_save_folder(meta)

        self.assertEqual(
            result["savegame_path_win"], r"%userprofile%\Documents\My Saves"
        )
        self.assertEqual(result["savegame_path_source"], "user")


class EmulatorFolderChoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "PRAGMATA"
        (self.root / "PRAGMATA").mkdir(parents=True)
        _touch(self.root / "PRAGMATA" / "steam_api64.dll")
        _touch(
            self.root / "PRAGMATA" / "steam_settings" / "steam_appid.txt",
            "3357650\n",
        )
        self.meta = {
            "title": "PRAGMATA",
            "install_dir": str(self.root),
            "executable_path": str(self.root / "PRAGMATA" / "PRAGMATA.exe"),
            "steam_grid_id": "2421117525",
        }
        self.prefix = Path(self.tmp.name) / "compatdata" / "2421117525"
        self.saves = (
            self.prefix / "pfx" / "drive_c" / "users" / "steamuser"
            / "AppData" / "Roaming" / "GSE Saves" / "2421117525" / "remote"
        )
        self.saves.mkdir(parents=True)

    def test_existing_gse_folder_with_the_shortcut_id_wins(self):
        manager = make_manager()
        with mock.patch.object(
            SavegamesManager, "_get_compatdata_base",
            return_value=str(self.prefix),
        ), mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ):
            info = manager.emulator_save_path(self.meta)

        self.assertEqual(info["emulator"], "goldberg")
        self.assertEqual(
            info["windows_path"],
            r"%appdata%\GSE Saves\2421117525",
        )

    def test_rescan_resolves_the_existing_saves_folder(self):
        manager = make_manager()
        manager.installed_games = {}
        manager.find_manifest_entry = lambda title: {}
        manager.save_meta = lambda meta: None
        manager._notify_callback = None
        with mock.patch.object(
            SavegamesManager, "_get_compatdata_base",
            return_value=str(self.prefix),
        ), mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ):
            result = manager.rescan_save_folder(dict(self.meta))

        self.assertEqual(
            result["savegame_path_win"],
            r"%appdata%\GSE Saves\2421117525",
        )
        self.assertEqual(
            Path(result["savegame_path_linux"]),
            self.saves.parent,
        )
        self.assertTrue(Path(result["savegame_path_linux"]).is_dir())


class CustomPortablePathTests(unittest.TestCase):
    def test_custom_steam_userdata_is_translated_on_linux(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            (root / "userdata" / "222222" / "870780" / "remote").mkdir(parents=True)
            manager = make_manager()
            meta = {
                "savegame_path_custom": (
                    r"<Steam-folder>\userdata\<storeUserId>\870780\remote"
                ),
                "steam_grid_id": "",
            }
            with mock.patch.object(
                save_locations_module.platform_info, "is_linux", return_value=True
            ), mock.patch.object(
                save_paths, "steam_userdata_roots",
                return_value=[str(root / "userdata")],
            ):
                resolved = manager.get_source_save_path(meta)

        self.assertEqual(
            resolved, str(root / "userdata" / "222222" / "870780" / "remote")
        )


if __name__ == "__main__":
    unittest.main()
