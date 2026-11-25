"""Integration tests for the global savegames operation lock."""
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from managers.save_coordinator import SavegamesOperationCoordinator
from managers.save_manager import SavegamesManager


class _Manager:
    """Stub body that records overlap and parks until the test releases it."""

    def __init__(self):
        self.manager = object.__new__(SavegamesManager)
        self.manager._operations = SavegamesOperationCoordinator()
        self.active = 0
        self.max_active = 0
        self.completed = 0
        self.guard = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def enter(self):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.entered.set()
        self.release.wait(5)
        with self.guard:
            self.active -= 1
            self.completed += 1

    def wait_parked(self, count, timeout=2.0):
        condition = self.manager._operations._condition
        with condition:
            return condition.wait_for(
                lambda: self.manager._operations._waiters >= count, timeout)


class CoordinationTests(unittest.TestCase):
    def _manager(self):
        helper = _Manager()
        manager = helper.manager
        manager._create_snapshot_impl = lambda *args, **kwargs: helper.enter()
        manager._restore_snapshot_impl = lambda *args, **kwargs: helper.enter()
        manager._backup_before_update_impl = lambda *args, **kwargs: helper.enter()
        manager._backup_on_demand_impl = lambda *args, **kwargs: helper.enter()
        manager._sync_all_games_impl = lambda: helper.enter()
        manager._list_snapshots_impl = lambda *args: helper.enter()
        return helper, manager

    def _run_with_parked_check(self, helper, targets, parked):
        threads = [threading.Thread(target=target) for target in targets]
        for thread in threads:
            thread.start()
        self.assertTrue(helper.entered.wait(2), "no operation started")
        self.assertTrue(helper.wait_parked(parked), "a caller never parked")
        helper.release.set()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive(), "a worker did not terminate")
        return threads

    def test_snapshot_and_restore_never_overlap(self):
        helper, manager = self._manager()
        self._run_with_parked_check(helper, [
            lambda: manager.create_snapshot("r", "t", {}, "s"),
            lambda: manager.restore_snapshot({}, "id"),
        ], parked=1)
        self.assertEqual(helper.max_active, 1)
        self.assertEqual(helper.completed, 2)

    def test_failed_operation_releases_the_lock(self):
        manager = object.__new__(SavegamesManager)
        manager._operations = None
        manager._create_snapshot_impl = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            manager.create_snapshot("r", "t", {}, "s")
        manager._create_snapshot_impl = lambda *args, **kwargs: "ok"
        self.assertEqual(manager.create_snapshot("r", "t", {}, "s"), "ok")

    def test_backup_and_sync_are_serialized(self):
        helper, manager = self._manager()
        self._run_with_parked_check(helper, [
            lambda: manager.backup_on_demand({}),
            lambda: manager.backup_before_update({}),
            lambda: manager.sync_all_games(),
        ], parked=2)
        self.assertEqual(helper.max_active, 1)
        self.assertEqual(helper.completed, 3)

    def test_playtime_stub_defers_instead_of_waiting(self):
        helper, manager = self._manager()
        restore = threading.Thread(
            target=lambda: manager.restore_snapshot({}, "id"))
        restore.start()
        self.assertTrue(helper.entered.wait(2))
        try:
            result = manager.ensure_playtime_stub("t", {})
            self.assertEqual(result["status"], "deferred")
            self.assertIn("t", manager._pending_playtime_titles)
            self.assertEqual(helper.max_active, 1)
        finally:
            helper.release.set()

        restore.join(5)
        self.assertFalse(restore.is_alive())
        self.assertEqual(helper.completed, 1)
        # 't' is not installed, so the deferred intent is dropped, not written.
        self.assertNotIn("t", manager._pending_playtime_titles)

    def test_global_lock_serializes_different_games_and_destinations(self):
        helper, manager = self._manager()
        self._run_with_parked_check(helper, [
            lambda: manager.create_snapshot("game-a", "A", {}, "dest-a"),
            lambda: manager.create_snapshot("game-b", "B", {}, "dest-b"),
        ], parked=1)
        self.assertEqual(helper.max_active, 1)
        self.assertEqual(helper.completed, 2)

    def test_real_restore_creates_its_safety_snapshot_under_the_lock(self):
        """A real restore -> safety snapshot transaction stays reentrant."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "Staging"
            install = root / "Game"
            install.mkdir()
            destination = root / "Saves" / "My Game"
            destination.mkdir(parents=True)
            (destination / "slot1.sav").write_text("state A", encoding="utf-8")

            manager = object.__new__(SavegamesManager)
            manager._operations = None
            manager.app_config = {"savegames": {"sync_folder": str(staging),
                                                "keep_snapshots": 5}}
            manager.installed_games = {}
            manager.get_sync_folder = lambda: str(staging)
            manager.get_source_save_path = mock.Mock(
                return_value=str(destination))
            manager._restore_allowed_targets = lambda meta: (
                [str(root)], [], {os.path.expanduser("~")})

            meta = {
                "title": "My Game",
                "install_dir": str(install),
                "savegame_path_source": "user",
            }
            game_root = str(staging / "My_Game")
            first = manager.create_snapshot(
                game_root, "My Game", meta, str(destination), reason="sync")
            self.assertTrue(first["created"], first)

            (destination / "slot1.sav").write_text("state B", encoding="utf-8")
            result = manager.restore_snapshot(
                meta, Path(first["snapshot"]).name)

            self.assertTrue(result["ok"], result)
            self.assertEqual(
                (destination / "slot1.sav").read_text(encoding="utf-8"),
                "state A")
            self.assertTrue(result["safety_snapshot"])
            safety = staging / "My_Game" / "saves" / result["safety_snapshot"]
            self.assertTrue((safety / "slot1.sav").is_file())
            self.assertEqual(
                (safety / "slot1.sav").read_text(encoding="utf-8"), "state B")


if __name__ == "__main__":
    unittest.main()
