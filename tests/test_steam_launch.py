import os
import sys
import tempfile
import unittest
from unittest import mock

import vdf

from core import steam_integration


class LaunchShortcutTests(unittest.TestCase):
    def test_empty_id_is_ignored(self):
        self.assertFalse(steam_integration.launch_shortcut(""))
        self.assertFalse(steam_integration.launch_shortcut(None))
        self.assertFalse(steam_integration.launch_shortcut("   "))

    def test_windows_uses_steam_exe(self):
        spawned = []

        def fake_popen(args, **kwargs):
            spawned.append(args)
            return mock.Mock()

        with mock.patch.object(steam_integration.sys, "platform", "win32"), \
             mock.patch.object(steam_integration, "_steam_executable",
                               return_value=r"C:\Steam\steam.exe"), \
             mock.patch.object(steam_integration.subprocess, "Popen", fake_popen):
            ok = steam_integration.launch_shortcut(" 1234567890123456789 ")

        self.assertTrue(ok)
        self.assertEqual(
            spawned,
            [[r"C:\Steam\steam.exe", "--", "steam://rungameid/1234567890123456789"]],
        )

    def test_windows_falls_back_to_steam_url_handler(self):
        opened = []

        def fake_popen(args, **kwargs):
            raise FileNotFoundError("no steam.exe")

        with mock.patch.object(steam_integration.sys, "platform", "win32"), \
             mock.patch.object(steam_integration, "_steam_executable",
                               return_value="steam.exe"), \
             mock.patch.object(steam_integration.subprocess, "Popen", fake_popen), \
             mock.patch.object(
                 steam_integration.os, "startfile",
                 lambda url: opened.append(url), create=True,
             ):
            ok = steam_integration.launch_shortcut("42")

        self.assertTrue(ok)
        self.assertEqual(opened, ["steam://rungameid/42"])

    def test_reports_failure_when_steam_cannot_start(self):
        with mock.patch.object(steam_integration.sys, "platform", "win32"), \
             mock.patch.object(steam_integration, "_steam_executable",
                               return_value="steam.exe"), \
             mock.patch.object(steam_integration.subprocess, "Popen",
                               mock.Mock(side_effect=FileNotFoundError("missing"))), \
             mock.patch.object(
                 steam_integration.os, "startfile",
                 mock.Mock(side_effect=OSError("no handler")), create=True,
             ):
            self.assertFalse(steam_integration.launch_shortcut("42"))

    def test_linux_uses_steam_binary(self):
        spawned = []

        def fake_popen(args, **kwargs):
            spawned.append(args)
            return mock.Mock()

        with mock.patch.object(steam_integration.sys, "platform", "linux"), \
             mock.patch.object(steam_integration.platform_info, "is_flatpak",
                               return_value=False), \
             mock.patch.object(steam_integration.subprocess, "Popen", fake_popen):
            ok = steam_integration.launch_shortcut("42")

        self.assertTrue(ok)
        self.assertEqual(spawned, [["steam", "steam://rungameid/42"]])


class FindShortcutTests(unittest.TestCase):
    def _write_shortcuts(self, root, entries):
        config_dir = os.path.join(root, "config")
        os.makedirs(config_dir, exist_ok=True)
        games = {"shortcuts": {}}
        for index, (name, appid) in enumerate(entries):
            games["shortcuts"][str(index)] = {
                "appid": appid,
                "AppName": name,
                "Exe": '"C\\Games\\game.exe"',
            }
        with open(os.path.join(config_dir, "shortcuts.vdf"), "wb") as handle:
            handle.write(vdf.binary_dumps(games))

    def test_recovers_64_bit_shortcut_id(self):
        grid_id = 0x81234567
        signed = grid_id - 0x100000000
        with tempfile.TemporaryDirectory() as tmp:
            self._write_shortcuts(tmp, [("Other", -1), ("My Game", signed)])
            with mock.patch.object(steam_integration, "get_steam_account_paths",
                                   return_value=[tmp]):
                found = steam_integration.find_shortcut_in_steam("My Game")

        self.assertEqual(found, (str((grid_id << 32) | 0x02000000), str(grid_id)))

    def test_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_shortcuts(tmp, [("Other", 1)])
            with mock.patch.object(steam_integration, "get_steam_account_paths",
                                   return_value=[tmp]):
                self.assertIsNone(steam_integration.find_shortcut_in_steam("My Game"))


class WindowsLaunchGameTests(unittest.TestCase):
    """Regressione: il lancio Windows deve passare da Steam quando c'è lo shortcut."""

    def _make_launcher(self, meta):
        from core.game_launcher import GameLauncher

        app_window = mock.MagicMock()
        app_window.installed_games = {"Hollow Knight": meta}
        launcher = GameLauncher(app_window)
        launcher.verify_steam_integrity = mock.Mock(return_value=False)
        launcher._start_process_monitor = mock.Mock()
        return launcher

    def test_launches_through_steam_when_shortcut_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "hollow_knight.exe")
            open(exe, "wb").close()
            meta = {
                "title": "Hollow Knight",
                "install_dir": tmp,
                "executable_path": exe,
                "steam_shortcut_id": "15861831900864708608",
            }
            launcher = self._make_launcher(meta)
            launched = []
            with mock.patch.object(steam_integration.sys, "platform", "win32"), \
                 mock.patch.object(steam_integration, "launch_shortcut",
                                   lambda sid: launched.append(sid) or True), \
                 mock.patch("core.game_launcher.subprocess.Popen",
                            mock.Mock(side_effect=AssertionError("direct launch"))):
                launcher.launch_game("Hollow Knight")

        self.assertEqual(launched, ["15861831900864708608"])
        launcher._start_process_monitor.assert_called_once()
        self.assertIsNone(launcher._start_process_monitor.call_args.args[0])

    def test_falls_back_to_direct_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "hollow_knight.exe")
            open(exe, "wb").close()
            meta = {
                "title": "Hollow Knight",
                "install_dir": tmp,
                "executable_path": exe,
                "steam_shortcut_id": "15861831900864708608",
            }
            launcher = self._make_launcher(meta)
            spawned = []
            with mock.patch.object(steam_integration.sys, "platform", "win32"), \
                 mock.patch.object(steam_integration, "launch_shortcut",
                                   mock.Mock(return_value=False)), \
                 mock.patch("core.game_launcher.subprocess.Popen",
                            lambda args, **kwargs: spawned.append(args) or mock.Mock(pid=4242)):
                launcher.launch_game("Hollow Knight")

        self.assertEqual(spawned, [[exe]])
        launcher._start_process_monitor.assert_called_once_with(4242, meta)


if __name__ == "__main__":
    unittest.main()
