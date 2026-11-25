import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import vdf

from core import steam_integration


def _write_shortcuts(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"shortcuts": {str(i): e for i, e in enumerate(entries)}}
    with open(path, "wb") as fh:
        fh.write(vdf.binary_dumps(data))


class ShortcutVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config" / "shortcuts.vdf"
        _write_shortcuts(self.path, [
            {"AppName": "Game A", "appid": 1},
            {"AppName": "Game B", "appid": 2},
        ])

    def test_reads_names(self):
        self.assertEqual(
            steam_integration._read_shortcut_names(self.path),
            {"Game A", "Game B"},
        )

    def test_verifies_presence_and_absence(self):
        self.assertTrue(steam_integration._verify_shortcut(self.path, "Game A", True))
        self.assertFalse(steam_integration._verify_shortcut(self.path, "Game C", True))
        self.assertTrue(steam_integration._verify_shortcut(self.path, "Game C", False))
        self.assertFalse(steam_integration._verify_shortcut(self.path, "Game A", False))

    def test_missing_file_is_empty(self):
        self.assertEqual(
            steam_integration._read_shortcut_names(Path(self.tmp.name) / "nope.vdf"),
            set(),
        )


class SteamCloseTests(unittest.TestCase):
    def test_wait_branch_closes_running_steam(self):
        with mock.patch.object(steam_integration, "is_steam_running", return_value=True), \
             mock.patch.object(steam_integration, "kill_steam_safely") as kill:
            steam_integration._close_steam_for_vdf(restart_steam=False)
        kill.assert_called_once()

    def test_wait_branch_does_not_touch_closed_steam(self):
        with mock.patch.object(steam_integration, "is_steam_running", return_value=False), \
             mock.patch.object(steam_integration, "kill_steam_safely") as kill:
            steam_integration._close_steam_for_vdf(restart_steam=False)
        kill.assert_not_called()

    def test_restart_branch_always_kills(self):
        with mock.patch.object(steam_integration, "is_steam_running", return_value=False), \
             mock.patch.object(steam_integration, "kill_steam_safely") as kill:
            steam_integration._close_steam_for_vdf(restart_steam=True)
        kill.assert_called_once()


class ProtonMappingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.vdf"
        self.path.write_text(
            '"InstallConfigStore"\n{\n\t"Software"\n\t{\n\t\t"Valve"\n\t\t{\n'
            '\t\t\t"Steam"\n\t\t\t{\n\t\t\t}\n\t\t}\n\t}\n}\n',
            encoding="utf-8",
        )

    def test_mapping_is_written_with_backup(self):
        ok = steam_integration._write_proton_mapping(
            str(self.path), "111", "222", "proton_experimental")

        self.assertTrue(ok)
        self.assertTrue(Path(str(self.path) + ".bak").exists())
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("CompatToolMapping", text)
        self.assertIn('"111"', text)
        self.assertIn('"222"', text)

    def test_missing_file_returns_false(self):
        self.assertFalse(steam_integration._write_proton_mapping(
            str(Path(self.tmp.name) / "missing.vdf"), "1", "2", "proton"))


class RemoveFromSteamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.account = Path(self.tmp.name) / "userdata" / "123"
        self.shortcuts = self.account / "config" / "shortcuts.vdf"
        _write_shortcuts(self.shortcuts, [
            {"AppName": "Game A", "appid": 1, "Exe": '"C:/Games/A/a.exe"'},
            {"AppName": "Game B", "appid": 2, "Exe": '"C:/Games/B/b.exe"'},
        ])

    def _remove(self):
        with mock.patch.object(steam_integration, "get_steam_account_paths",
                               return_value=[str(self.account)]), \
             mock.patch.object(steam_integration, "is_steam_running",
                               return_value=False), \
             mock.patch.object(steam_integration.time, "sleep"):
            return steam_integration.remove_from_steam("Game A", restart_steam=False)

    def test_removes_and_verifies(self):
        self.assertTrue(self._remove())
        self.assertEqual(
            steam_integration._read_shortcut_names(self.shortcuts), {"Game B"})
        self.assertTrue(Path(str(self.shortcuts) + ".bak").exists())

    def test_write_failure_is_not_reported_as_success(self):
        with mock.patch.object(steam_integration, "atomic_write_bytes",
                               side_effect=OSError("disk full")):
            self.assertFalse(self._remove())
        self.assertEqual(
            steam_integration._read_shortcut_names(self.shortcuts),
            {"Game A", "Game B"},
        )


if __name__ == "__main__":
    unittest.main()
