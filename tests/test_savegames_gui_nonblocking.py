"""C6: savegames operations never wait on the coordinator from the GUI thread."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QEventLoop, QObject
from PyQt6.QtWidgets import QApplication

from managers.backup_service import BackupService
from managers.save_manager import SavegamesManager
from tests.qt_app import ensure_app
from ui.controllers.library import LibraryController
from ui.controllers.settings import SettingsController


class _State:
    root = Path("/tmp/launcher-state")

    def snapshot(self):
        return Path("/tmp/launcher-state/20260101_120000")


def _bare_manager(meta, prompt):
    manager = object.__new__(SavegamesManager)
    manager.app_config = {'savegames': {}}
    manager.installed_games = {meta['title']: meta}
    manager.parent = None
    manager._prompt_save_path = prompt
    manager._notify_callback = lambda *args: None
    manager.manifest_store = mock.Mock()
    manager.manifest_store.find_entry.return_value = None
    manager.save_meta = mock.Mock(return_value=True)
    manager._get_compatdata_base = lambda grid_id: ""
    return manager


class SavePathPromptTests(unittest.TestCase):
    def _manager(self, prompt):
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "Game"
            install.mkdir()
            meta = {"title": "My Game", "install_dir": str(install)}
            manager = _bare_manager(meta, prompt)
            paths = manager._get_paths()
            paths._shortcut_inventory = lambda force=False: ([], [])
            paths.refresh_steam_identity = lambda meta, force=False: {"changed": False}
            paths.emulator_save_path = lambda meta: {}
            paths._find_manifest_entry = lambda title: None
            return manager, meta

    def test_prompt_runs_outside_the_coordinator_lock(self):
        observed = []

        def prompt(title):
            observed.append((title, None, manager_holder[0]._coordinator().is_active()))
            return "/manual/saves"

        manager_holder = [None]
        manager, meta = self._manager(prompt)
        manager_holder[0] = manager

        result = manager.ensure_save_path_for_game(meta)

        self.assertEqual([(title, active) for title, _, active in observed],
                         [("My Game", False)])
        self.assertEqual(result['savegame_path_linux'], "/manual/saves")
        manager.save_meta.assert_called_once()

    def test_meta_change_between_prompt_and_commit_is_revalidated(self):
        def prompt(title):
            meta['savegame_path_win'] = "changed while the dialog was open"
            return "/manual/saves"

        manager, meta = self._manager(prompt)

        result = manager.ensure_save_path_for_game(meta)

        self.assertEqual(result['savegame_path_win'],
                         "changed while the dialog was open")
        self.assertNotIn('savegame_path_linux', result)
        manager.save_meta.assert_not_called()

    def test_prepare_never_prompts(self):
        calls = []

        def prompt(title):
            calls.append(title)
            return "/manual/saves"

        manager, meta = self._manager(prompt)

        prepared = manager.prepare_save_path_for_game(meta)

        self.assertTrue(prepared['needs_prompt'])
        self.assertEqual(calls, [])

    def test_tracked_game_removed_before_commit_is_not_written(self):
        manager, meta = self._manager(lambda title: "/manual/saves")
        prepared = manager.prepare_save_path_for_game(meta)
        self.assertTrue(prepared['needs_prompt'])
        self.assertTrue(prepared['tracked'])

        manager.installed_games.pop(meta['title'])
        result = manager.commit_manual_save_path(
            meta, "/manual/saves", prepared['state'], prepared['title'],
            tracked=prepared['tracked'])

        self.assertNotIn('savegame_path_linux', result)
        manager.save_meta.assert_not_called()

    def test_headless_untracked_commit_still_applies(self):
        manager, meta = self._manager(lambda title: "/manual/saves")
        manager.installed_games.pop(meta['title'])
        prepared = manager.prepare_save_path_for_game(meta)
        self.assertTrue(prepared['needs_prompt'])
        self.assertFalse(prepared['tracked'])

        result = manager.commit_manual_save_path(
            meta, "/manual/saves", prepared['state'], prepared['title'],
            tracked=prepared['tracked'])

        self.assertEqual(result['savegame_path_linux'], "/manual/saves")
        manager.save_meta.assert_called_once()


class BackupStateNonBlockingTests(unittest.TestCase):
    def test_backup_state_reports_busy_without_waiting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = object.__new__(SavegamesManager)
            manager.installed_games = {}
            manager.app_config = {'savegames': {'sync_folder': str(root / "Mirror")}}
            manager.get_sync_folder = lambda: str(root / "Staging")
            manager._operations = None
            service = BackupService(manager, _State())

            entered = threading.Event()
            release = threading.Event()

            def hold():
                manager._coordinator().run(
                    lambda: (entered.set(), release.wait(5))[0])

            thread = threading.Thread(target=hold)
            thread.start()
            self.assertTrue(entered.wait(2))
            try:
                started = time.monotonic()
                result = service.backup_state(push=True)
                elapsed = time.monotonic() - started
            finally:
                release.set()
                thread.join(2)

            self.assertFalse(thread.is_alive())
            self.assertLess(elapsed, 0.5)
            self.assertTrue(result['ok'])
            self.assertEqual(result['mirror']['status'], 'busy')


class _HelperShim(QObject):
    _run_savegames_task = LibraryController._run_savegames_task

    def __init__(self):
        super().__init__()
        self.results = []


class GuiHelperTests(unittest.TestCase):
    def setUp(self):
        ensure_app()

    def test_ui_helper_runs_the_task_off_the_gui_thread(self):
        shim = _HelperShim()
        threads = []
        done = threading.Event()

        shim._run_savegames_task(
            lambda: (threads.append(threading.current_thread()), "done")[1],
            lambda result: (shim.results.append(result), done.set()),
        )

        deadline = time.monotonic() + 5
        while not done.is_set() and time.monotonic() < deadline:
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents, 20)

        self.assertTrue(done.is_set(), "the worker never completed")
        self.assertTrue(threads)
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertEqual(shim.results, ["done"])


class SettingsMirrorTextTests(unittest.TestCase):
    def test_busy_mirror_is_surfaced_in_the_settings_message(self):
        text = SettingsController._state_backup_mirror_text(
            {'status': 'busy', 'report': {}})
        self.assertIn('skipped', text.lower())
        self.assertIn('busy', text)

    def test_clean_mirror_still_reports_the_copy_count(self):
        text = SettingsController._state_backup_mirror_text(
            {'status': 'completed', 'configured': True, 'copied': 2})
        self.assertIn('2 file(s) copied', text)

    def test_no_mirror_adds_no_text(self):
        self.assertEqual(
            SettingsController._state_backup_mirror_text(
                {'status': 'completed', 'report': {}}),
            "")


if __name__ == "__main__":
    unittest.main()
