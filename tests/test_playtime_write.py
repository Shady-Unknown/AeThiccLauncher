import contextlib
import os
import tempfile
import unittest
from unittest import mock

import vdf

from core.steam_integration import shortcut_owner_accounts
from managers.playtime_manager import PlaytimeManager


ACCOUNT_A = "11111111"
ACCOUNT_B = "22222222"
GRID_A = 0x8000007B          # 2147483771 unsigned
GRID_B = 0x8000002A          # 2147483690 unsigned
SIGNED_A = GRID_A - 0x100000000
SIGNED_B = GRID_B - 0x100000000
SHORTCUT_A = (GRID_A << 32) | 0x02000000


def _app_block(appid, minutes, two_weeks=None):
    lines = ['"%s"' % appid, "{", '"Playtime" "%d"' % minutes]
    if two_weeks is not None:
        lines.append('"Playtime2wks" "%d"' % two_weeks)
    lines.append("}")
    return "\n".join(lines) + "\n"


def _localconfig(*blocks):
    lines = [
        '"UserLocalConfigStore"', "{",
        '"Software"', "{",
        '"Valve"', "{",
        '"Steam"', "{",
        '"apps"', "{",
        "".join(blocks),
        "}", "}", "}", "}", "}",
    ]
    return "\n".join(lines) + "\n"


def _manager(games):
    return PlaytimeManager(lambda: games, lambda: {})


class PatchPlaytimeTextTests(unittest.TestCase):
    def test_reports_patched_appids(self):
        text = _localconfig(_app_block(SIGNED_A, 5))
        new_text, patched = PlaytimeManager._patch_playtime_text(text, {str(SIGNED_A): 90})
        self.assertEqual(patched, {str(SIGNED_A)})
        self.assertIn('"Playtime" "90"', new_text)
        self.assertIn('"Playtime2wks" "90"', new_text)

    def test_already_correct_value_is_reported_as_patched(self):
        text = _localconfig(_app_block(SIGNED_A, 90, 90))
        new_text, patched = PlaytimeManager._patch_playtime_text(text, {str(SIGNED_A): 90})
        self.assertEqual(new_text, text)
        self.assertEqual(patched, {str(SIGNED_A)})

    def test_unpatchable_appid_is_not_reported(self):
        text = '"Settings" { }'
        new_text, patched = PlaytimeManager._patch_playtime_text(text, {"999": 30})
        self.assertEqual(new_text, text)
        self.assertEqual(patched, set())


class WritePlaytimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.acc_a = os.path.join(self.root, ACCOUNT_A)
        self.acc_b = os.path.join(self.root, ACCOUNT_B)
        for acc in (self.acc_a, self.acc_b):
            os.makedirs(os.path.join(acc, "config"), exist_ok=True)
        self.lc_a = os.path.join(self.acc_a, "config", "localconfig.vdf")
        self.lc_b = os.path.join(self.acc_b, "config", "localconfig.vdf")

    def _patch_steam(self, owners=None, accounts=None):
        owners = owners or {}
        accounts = accounts if accounts is not None else [self.acc_a, self.acc_b]
        paths = {ACCOUNT_A: self.lc_a, ACCOUNT_B: self.lc_b}
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch(
            "core.steam_integration.is_steam_running", return_value=False))
        stack.enter_context(mock.patch(
            "core.steam_integration.get_steam_account_paths", return_value=accounts))
        stack.enter_context(mock.patch(
            "core.steam_integration.get_steam_localconfig_path",
            side_effect=lambda account_id: paths.get(str(account_id), "")))
        stack.enter_context(mock.patch(
            "core.steam_integration.shortcut_owner_accounts",
            side_effect=lambda title, shortcut_id="", grid_id="": list(
                owners.get(title, []))))

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_clears_pending_only_for_written_appids(self):
        self._write(self.lc_a, _localconfig(_app_block(SIGNED_A, 0)))
        self._write(self.lc_b, '"Settings" { }')
        games = {
            "Game A": {
                "install_dir": self.acc_a,
                "steam_grid_id": str(GRID_A),
                "playtime_seconds": 5400,
                "playtime_sync_pending": True,
            },
            "Game B": {
                "install_dir": self.acc_b,
                "steam_grid_id": str(GRID_B),
                "playtime_seconds": 3600,
                "playtime_sync_pending": True,
            },
        }
        manager = _manager(games)
        self._patch_steam({"Game A": [ACCOUNT_A], "Game B": [ACCOUNT_B]})

        with mock.patch.object(manager, "_save_meta") as save_meta:
            self.assertTrue(manager.write_playtime_to_steam())

        self.assertFalse(games["Game A"]["playtime_sync_pending"])
        self.assertTrue(games["Game B"]["playtime_sync_pending"])
        save_meta.assert_called_once_with("Game A", games["Game A"])
        self.assertTrue(os.path.exists(self.lc_a + ".bak"))
        with open(self.lc_a, encoding="utf-8") as fh:
            self.assertIn('"Playtime" "90"', fh.read())
        with open(self.lc_b, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '"Settings" { }')

    def test_already_correct_value_clears_pending_without_writing(self):
        self._write(self.lc_a, _localconfig(_app_block(SIGNED_A, 90, 90)))
        games = {
            "Game A": {
                "install_dir": self.acc_a,
                "steam_grid_id": str(GRID_A),
                "playtime_seconds": 5400,
                "playtime_sync_pending": True,
            },
        }
        manager = _manager(games)
        self._patch_steam({"Game A": [ACCOUNT_A]})

        with mock.patch.object(manager, "_save_meta") as save_meta:
            self.assertTrue(manager.write_playtime_to_steam())

        self.assertFalse(games["Game A"]["playtime_sync_pending"])
        save_meta.assert_called_once_with("Game A", games["Game A"])
        self.assertFalse(os.path.exists(self.lc_a + ".bak"))

    def test_no_owner_keeps_pending_and_writes_nothing(self):
        self._write(self.lc_a, _localconfig(_app_block(SIGNED_A, 0)))
        self._write(self.lc_b, _localconfig(_app_block(SIGNED_A, 0)))
        games = {
            "Game A": {
                "install_dir": self.acc_a,
                "steam_grid_id": str(GRID_A),
                "playtime_seconds": 5400,
                "playtime_sync_pending": True,
            },
        }
        manager = _manager(games)
        self._patch_steam({})

        with mock.patch.object(manager, "_save_meta") as save_meta:
            self.assertFalse(manager.write_playtime_to_steam())

        self.assertTrue(games["Game A"]["playtime_sync_pending"])
        save_meta.assert_not_called()
        for path in (self.lc_a, self.lc_b):
            with open(path, encoding="utf-8") as fh:
                self.assertIn('"Playtime" "0"', fh.read())

    def test_stale_stored_account_is_ignored(self):
        self._write(self.lc_a, _localconfig(_app_block(SIGNED_A, 0)))
        games = {
            "Game A": {
                "install_dir": self.acc_a,
                "steam_grid_id": str(GRID_A),
                "steam_account_id": "99999999",
                "playtime_seconds": 5400,
                "playtime_sync_pending": True,
            },
        }
        manager = _manager(games)
        self._patch_steam({})

        with mock.patch.object(manager, "_save_meta") as save_meta:
            self.assertFalse(manager.write_playtime_to_steam())

        self.assertTrue(games["Game A"]["playtime_sync_pending"])
        save_meta.assert_not_called()

    def test_single_local_account_is_the_fallback(self):
        self._write(self.lc_a, _localconfig(_app_block(SIGNED_A, 0)))
        games = {
            "Game A": {
                "install_dir": self.acc_a,
                "steam_grid_id": str(GRID_A),
                "playtime_seconds": 5400,
                "playtime_sync_pending": True,
            },
        }
        manager = _manager(games)
        self._patch_steam({}, accounts=[self.acc_a])

        with mock.patch.object(manager, "_save_meta") as save_meta:
            self.assertTrue(manager.write_playtime_to_steam())

        self.assertFalse(games["Game A"]["playtime_sync_pending"])
        save_meta.assert_called_once()


class RecordSessionTests(unittest.TestCase):
    def test_pending_only_with_valid_grid_id(self):
        games = {
            "No Grid": {"install_dir": "/tmp/no-grid", "playtime_seconds": 0},
            "With Grid": {
                "install_dir": "/tmp/with-grid",
                "playtime_seconds": 0,
                "steam_grid_id": str(GRID_A),
            },
        }
        manager = _manager(games)

        with mock.patch.object(manager, "_save_meta"):
            manager.record_session("No Grid", 60)
            manager.record_session("With Grid", 60)

        self.assertNotIn("playtime_sync_pending", games["No Grid"])
        self.assertTrue(games["With Grid"]["playtime_sync_pending"])
        self.assertEqual(games["With Grid"]["playtime_seconds"], 60)


class PendingFilterTests(unittest.TestCase):
    def test_non_syncable_pending_is_hidden(self):
        games = {
            "No Grid": {"playtime_sync_pending": True, "playtime_seconds": 10},
            "With Grid": {
                "playtime_sync_pending": True,
                "playtime_seconds": 20,
                "steam_grid_id": str(GRID_A),
            },
        }
        manager = _manager(games)
        self.assertTrue(manager.has_pending())
        self.assertEqual(manager.pending_games(), [("With Grid", 20)])

    def test_only_non_syncable_has_no_pending(self):
        games = {"No Grid": {"playtime_sync_pending": True, "playtime_seconds": 10}}
        manager = _manager(games)
        self.assertFalse(manager.has_pending())
        self.assertEqual(manager.pending_games(), [])


class ShortcutOwnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        self.acc_a = os.path.join(self.root, ACCOUNT_A)
        self.acc_b = os.path.join(self.root, ACCOUNT_B)
        for acc in (self.acc_a, self.acc_b):
            os.makedirs(os.path.join(acc, "config"), exist_ok=True)

    def _write_shortcuts(self, account_dir, entries):
        path = os.path.join(account_dir, "config", "shortcuts.vdf")
        data = {"shortcuts": {str(i): e for i, e in enumerate(entries)}}
        with open(path, "wb") as fh:
            fh.write(vdf.binary_dumps(data))

    def test_resolves_by_shortcut_id_grid_id_and_title(self):
        self._write_shortcuts(self.acc_a, [
            {"AppName": "Game A", "appid": SIGNED_A},
        ])
        self._write_shortcuts(self.acc_b, [
            {"AppName": "Game B", "appid": SIGNED_B},
        ])

        with mock.patch(
            "core.steam_integration.get_steam_account_paths",
            return_value=[self.acc_a, self.acc_b],
        ):
            self.assertEqual(
                shortcut_owner_accounts("Renamed", shortcut_id=str(SHORTCUT_A)),
                [ACCOUNT_A],
            )
            self.assertEqual(
                shortcut_owner_accounts("Renamed", grid_id=str(GRID_B)),
                [ACCOUNT_B],
            )
            self.assertEqual(
                shortcut_owner_accounts("Game A"),
                [ACCOUNT_A],
            )
            self.assertEqual(
                shortcut_owner_accounts("Unknown", grid_id="123456"),
                [],
            )


if __name__ == "__main__":
    unittest.main()
