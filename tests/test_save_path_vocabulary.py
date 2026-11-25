"""Steam save-path vocabulary and the Linux translation preview."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import save_paths
from managers import save_locations as save_locations_module
from managers.save_locations import SavePathResolver
from managers import save_manager as save_manager_module
from managers.save_manager import SavegamesManager
from ui.dialogs.manual_save_path_dialog import format_preview


def make_manager():
    return object.__new__(SavegamesManager)


class SteamUserdataTranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "Steam"
        (root / "steamapps").mkdir(parents=True)
        (root / "userdata" / "111111" / "999999").mkdir(parents=True)
        (root / "userdata" / "222222" / "870780" / "remote").mkdir(parents=True)
        # The account without the appid must not win just by being newer.
        os.utime(root / "userdata" / "111111", (2_000_000_000, 2_000_000_000))
        self.userdata = str(root / "userdata")
        self.base = str(Path(self.tmp.name) / "compatdata" / "870780")
        self.expected = str(root / "userdata" / "222222" / "870780" / "remote")

    def translate(self, path):
        return save_paths.translate_win_path_details(
            path, self.base, steam_roots=[self.userdata]
        )

    def test_steam_folder_token_with_documentation_userid(self):
        details = self.translate(
            r"<steam-folder>\userdata\<your-steam-userid>\870780\remote"
        )

        self.assertEqual(details["path"], self.expected)
        self.assertEqual(details["kind"], "steam")
        self.assertEqual(details["reason"], "")

    def test_store_user_id_token(self):
        details = self.translate(
            r"<steam-folder>\userdata\<storeUserId>\870780\remote"
        )

        self.assertEqual(details["path"], self.expected)

    def test_expanded_windows_steam_path(self):
        details = self.translate(
            r"C:\Program Files (x86)\Steam\userdata\222222\870780\remote"
        )

        self.assertEqual(details["path"], self.expected)
        self.assertEqual(details["kind"], "steam")

    def test_literal_account_is_used_as_is(self):
        details = self.translate(
            r"<steam-folder>\userdata\111111\999999"
        )

        self.assertTrue(details["path"].endswith(os.path.join("111111", "999999")))

    def test_missing_account_placeholder_has_a_reason(self):
        with tempfile.TemporaryDirectory() as empty:
            details = save_paths.translate_win_path_details(
                r"<steam-folder>\userdata\<storeUserId>\1\remote",
                self.base,
                steam_roots=[os.path.join(empty, "userdata")],
            )

        self.assertEqual(details["path"], "")
        self.assertEqual(details["reason"], "steam_userdata_not_found")

    def test_unknown_placeholder_is_reported(self):
        details = self.translate(r"<something-new>\Game\Saves")

        self.assertEqual(details["path"], "")
        self.assertEqual(details["reason"], "unknown_placeholder")
        self.assertEqual(details["token"], "<something-new>")

    def test_missing_compatdata_has_a_reason(self):
        details = save_paths.translate_win_path_details(
            r"%localappdata%\Game\Saves", ""
        )

        self.assertEqual(details["path"], "")
        self.assertEqual(details["reason"], "no_compatdata")

    def test_manager_translates_steam_userdata_without_an_appid(self):
        manager = make_manager()
        with mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ), mock.patch.object(
            save_paths, "steam_userdata_roots", return_value=[self.userdata]
        ):
            result = manager.translate_win_path_to_linux(
                r"<steam-folder>\userdata\<storeUserId>\870780\remote", ""
            )

        self.assertEqual(result, self.expected)

    def test_username_maps_to_the_proton_user(self):
        details = self.translate(r"%username%\Documents\Game")

        self.assertTrue(
            details["path"].replace("\\", "/").endswith(
                "/pfx/drive_c/users/steamuser/Documents/Game"
            )
        )

    def test_posix_path_is_native(self):
        details = self.translate("~/saves")
        self.assertEqual(details["kind"], "native")
        self.assertEqual(details["path"], os.path.expanduser("~/saves"))

    def test_xdg_data_expands_to_a_native_path(self):
        expanded = SavePathResolver.expand_manifest_template(
            r"<xdgData>\Game\Saves")
        details = save_paths.translate_win_path_details(expanded, "")

        self.assertEqual(details["kind"], "native")
        self.assertTrue(details["path"].endswith(os.path.join("Game", "Saves")))


class MissingSegmentTests(unittest.TestCase):
    def test_missing_segment_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "Documents").mkdir()
            result = save_paths.resolve_relative(
                str(base), r"Documents\My Games\Control\Saves"
            )

        self.assertEqual(
            result,
            os.path.join(str(base), "Documents", "My Games", "Control", "Saves"),
        )

    def test_documents_is_not_doubled_by_translation(self):
        with tempfile.TemporaryDirectory() as tmp:
            users = (
                Path(tmp) / "pfx" / "drive_c" / "users" / "steamuser"
            )
            (users / "Documents").mkdir(parents=True)

            translated = save_paths.translate_win_path(
                r"%userprofile%\Documents\My Games\Control\Saves", tmp
            )

        self.assertEqual(
            translated,
            os.path.join(
                str(users), "Documents", "My Games", "Control", "Saves"
            ),
        )


class ManifestVocabularyTests(unittest.TestCase):
    def test_low_integrity_appdata_placeholder(self):
        expanded = SavePathResolver.expand_manifest_template(
            r"<winLocalAppDataLow>\Company\Game")
        self.assertEqual(
            expanded, r"%userprofile%\AppData\LocalLow\Company\Game"
        )

    def test_steam_placeholders_are_kept_portable(self):
        template = r"<steam-folder>\userdata\<storeUserId>\870780\remote"
        self.assertEqual(
            SavePathResolver.expand_manifest_template(template), template
        )

    def test_store_user_id_candidates_are_not_dropped(self):
        entry = {
            "files": {
                r"<steam-folder>\userdata\<storeUserId>\870780\remote": {
                    "tags": ["save"],
                    "when": [{"os": "windows"}],
                },
            }
        }

        result = SavegamesManager.extract_win_save_path(make_manager(), entry)

        self.assertIn("<storeUserId>", result)
        self.assertIn("<steam-folder>", result)


class StoreUserIdTests(unittest.TestCase):
    def test_store_user_id_outside_userdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            details = save_paths.translate_win_path_details(
                r"%appdata%\Nightreign\<storeUserId>\NR0000.sl2",
                tmp,
                account_id="123456",
            )

        self.assertEqual(details["kind"], "prefix")
        self.assertTrue(
            details["path"].replace("\\", "/").endswith(
                "/AppData/Roaming/Nightreign/123456/NR0000.sl2"
            )
        )

    def test_missing_account_is_reported(self):
        details = save_paths.translate_win_path_details(
            r"%appdata%\Game\<storeUserId>\slot.sav",
            "/tmp/base",
            steam_roots=[],
        )

        self.assertEqual(details["path"], "")
        self.assertEqual(details["reason"], "account_not_found")


class RootSteamUserdataTests(unittest.TestCase):
    def test_root_userdata_becomes_the_steam_folder(self):
        manager = make_manager()
        entry = {
            "files": {
                "<root>/userdata/<storeUserId>/2246340/remote": {
                    "tags": ["save"],
                    "when": [{"store": "steam"}],
                },
            }
        }

        chosen = manager.extract_win_save_path(entry)

        self.assertEqual(
            chosen,
            r"<steam-folder>\userdata\<storeUserId>\2246340\remote",
        )

    def test_root_userdata_translates_to_the_steam_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            (root / "userdata" / "222222" / "2246340" / "remote").mkdir(parents=True)
            details = save_paths.translate_win_path_details(
                r"<steam-folder>\userdata\<storeUserId>\2246340\remote",
                "",
                steam_roots=[str(root / "userdata")],
            )

        self.assertEqual(details["kind"], "steam")
        self.assertTrue(
            details["path"].endswith(os.path.join("222222", "2246340", "remote"))
        )


class ManifestStoreRankingTests(unittest.TestCase):
    ENTRY = {
        "files": {
            r"<winLocalAppData>\Game\GOG\Saves": {
                "tags": ["save"],
                "when": [{"os": "windows", "store": "gog"}],
            },
            r"<winLocalAppData>\Game\Steam\Saves\<storeUserId>": {
                "tags": ["save"],
                "when": [{"os": "windows", "store": "steam"}],
            },
        }
    }

    def test_steam_variant_is_preferred(self):
        chosen = make_manager().extract_win_save_path(self.ENTRY)

        self.assertIn("Steam", chosen)

    def test_existing_variant_wins_over_the_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "compatdata" / "1"
            gog = (
                prefix / "pfx" / "drive_c" / "users" / "steamuser"
                / "AppData" / "Local" / "Game" / "GOG" / "Saves"
            )
            gog.mkdir(parents=True)
            manager = make_manager()
            manager.installed_games = {}
            with mock.patch.object(
                SavegamesManager, "_get_compatdata_base",
                return_value=str(prefix),
            ), mock.patch.object(
                save_locations_module.platform_info, "is_linux", return_value=True
            ):
                chosen = manager.extract_win_save_path(
                    self.ENTRY, {"steam_grid_id": "1"}
                )

        self.assertIn("GOG", chosen)


class PreviewDialogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / "Steam"
        (root / "userdata" / "222222" / "870780" / "remote").mkdir(parents=True)
        self.userdata = str(root / "userdata")

    def test_format_preview_states(self):
        text, color = format_preview({
            "path": "/tmp/game", "kind": "prefix", "reason": "", "exists": True,
        })
        self.assertIn("/tmp/game", text)
        self.assertEqual(color, "#00ff88")

        text, color = format_preview({
            "path": "/tmp/game", "kind": "prefix", "reason": "", "exists": False,
        })
        self.assertIn("not created yet", text)

        text, color = format_preview({
            "path": "~/.config/game", "kind": "native", "reason": "",
        })
        self.assertIn("Native Linux path", text)

        text, color = format_preview({
            "path": "", "kind": "", "reason": "unknown_placeholder",
            "token": "<foo>",
        })
        self.assertIn("&lt;foo&gt;", text)
        self.assertIn("steam-folder", text)

        text, color = format_preview({"reason": "no_compatdata"})
        self.assertIn("no Steam appid", text)

        self.assertEqual(format_preview({"reason": "empty"}), ("", ""))
        self.assertEqual(format_preview({"reason": "not_linux"}), ("", ""))

    def test_manager_preview_steam_userdata(self):
        manager = make_manager()
        manager.installed_games = {}
        with mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ), mock.patch.object(
            save_paths, "steam_userdata_roots", return_value=[self.userdata]
        ):
            details = manager.preview_linux_path(
                r"<steam-folder>\userdata\<storeUserId>\870780\remote"
            )

        self.assertTrue(details["ok"])
        self.assertEqual(details["kind"], "steam")
        self.assertTrue(details["exists"])

    def test_manager_preview_without_appid_or_compatdata(self):
        manager = make_manager()
        manager.installed_games = {"Game": {}}
        with mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ):
            details = manager.preview_linux_path(
                r"%localappdata%\Game\Saves", "Game"
            )

        self.assertFalse(details["ok"])
        self.assertEqual(details["reason"], "no_compatdata")

    def test_manager_preview_reports_native_paths(self):
        manager = make_manager()
        manager.installed_games = {}
        with mock.patch.object(
            save_locations_module.platform_info, "is_linux", return_value=True
        ):
            details = manager.preview_linux_path("/home/deck/saves")

        self.assertTrue(details["ok"])
        self.assertEqual(details["kind"], "native")
