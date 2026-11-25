"""C7: deferred playtime updates keep the absolute counter, never a stale one."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core.naming import safe_game_folder_name
from managers.playtime_manager import PlaytimeManager
from managers.save_manager import SavegamesManager


def _stack(tmp):
    root = Path(tmp)
    staging = root / "Staging"
    install = root / "Game"
    install.mkdir(parents=True)
    meta = {"title": "G", "install_dir": str(install), "playtime_seconds": 0}
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {'savegames': {'keep_snapshots': 5}}
    manager.installed_games = {"G": meta}
    manager.get_sync_folder = lambda: str(staging)
    playtime = PlaytimeManager(
        manager.installed_games, {},
        on_stub_updated=lambda title, m: manager.ensure_playtime_stub(title, m),
        on_stubs_refresh=lambda: manager.ensure_playtime_stubs(),
    )
    playtime.read_steam_playtime = lambda account_id=None: {}
    return manager, playtime, meta, staging


class _Hold:
    """Hold the coordinator lock in another thread for the duration of a test."""

    def __init__(self, manager):
        self.manager = manager
        self.entered = threading.Event()
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._run)

    def _run(self):
        self.manager._coordinator().run(
            lambda: (self.entered.set(), self.release.wait(5))[0])

    def __enter__(self):
        self.thread.start()
        assert self.entered.wait(2), "the coordinator was not held"
        return self

    def __exit__(self, *exc):
        self.release.set()
        self.thread.join(5)
        return False


class DeferredPlaytimeTests(unittest.TestCase):
    def test_record_session_defers_and_flushes_the_latest_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, playtime, meta, staging = _stack(tmp)
            with _Hold(manager):
                started = time.monotonic()
                playtime.record_session("G", 60)
                playtime.record_session("G", 60)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.5)
                # Two sessions coalesce into one pending title.
                self.assertEqual(manager._pending_playtime_titles, {"G"})
                self.assertEqual(meta["playtime_seconds"], 120)

            self.assertEqual(manager.flush_pending_playtime(), 0)
            manifest = json.loads(
                (staging / safe_game_folder_name("G") / "savegames.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(manifest["playtime_seconds"], 120)

    def test_restored_lower_value_wins_over_a_stale_pending_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, playtime, meta, staging = _stack(tmp)
            with _Hold(manager):
                playtime.record_session("G", 120)
                self.assertIn("G", manager._pending_playtime_titles)
                # A restore lowers the authoritative absolute value before the
                # deferred flush runs.
                playtime.restore_playtime("G", meta, 30)
                self.assertEqual(meta["playtime_seconds"], 30)

            manager.flush_pending_playtime()
            manifest = json.loads(
                (staging / safe_game_folder_name("G") / "savegames.json")
                .read_text(encoding="utf-8"))
            self.assertEqual(manifest["playtime_seconds"], 30)

    def test_pending_is_dropped_with_a_log_when_the_coordinator_is_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager, _playtime, meta, _staging = _stack(tmp)
            meta['playtime_seconds'] = 60
            manager._pending_playtime_titles = {"G"}
            manager._coordinator().close()

            with self.assertLogs("managers.save_manager", level="INFO") as logs:
                self.assertEqual(manager.flush_pending_playtime(), 0)

            self.assertNotIn("G", manager._pending_playtime_titles)
            self.assertTrue(any("already persisted" in line
                                for line in logs.output))

    def test_save_meta_false_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            _manager, playtime, meta, _staging = _stack(tmp)
            with mock.patch("managers.playtime_manager.save_steamrip_meta",
                            return_value=False):
                with self.assertLogs("managers.playtime_manager",
                                     level="WARNING"):
                    playtime._save_meta("G", meta)


if __name__ == "__main__":
    unittest.main()
