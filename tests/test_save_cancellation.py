"""Cooperative cancellation of savegame operations at their safe points.

The tests gate ``shutil.copy2`` on real events and never sleep, so a cancelled
restore/snapshot is exercised deterministically instead of via timing luck.
"""
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from managers import save_manager as save_manager_module
from managers.save_coordinator import SavegamesOperationCoordinator
from managers.save_manager import SavegamesManager


def make_manager(staging, keep=5):
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {
        'savegames': {'sync_folder': str(staging), 'keep_snapshots': keep}
    }
    manager.installed_games = {}
    manager.get_sync_folder = lambda: str(staging)
    return manager


class CancellationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Staging"
        self.install = self.root / "Game"
        self.install.mkdir()
        self.destination = self.root / "Saves" / "My Game"
        self.destination.mkdir(parents=True)
        (self.destination / "slot1.sav").write_text("state A", encoding="utf-8")
        (self.destination / "slot2.sav").write_text("state A2", encoding="utf-8")

        self.manager = make_manager(self.staging)
        self.meta = {
            "title": "My Game",
            "install_dir": str(self.install),
            "savegame_path_source": "user",
        }
        self.manager.get_source_save_path = mock.Mock(
            return_value=str(self.destination))
        # Permissive roots: the safety rules are covered by RestoreTargetTests.
        self.manager._restore_allowed_targets = lambda meta: (
            [str(self.root)], [], {os.path.expanduser("~")})

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, manager=None):
        manager = manager or self.manager
        return manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(self.destination), reason="sync",
        )

    def snapshot_id(self, manager=None):
        result = self.snapshot(manager)
        self.assertTrue(result["created"], result)
        return Path(result["snapshot"]).name


class CoordinatorCancelTests(unittest.TestCase):
    def test_cancel_is_cooperative_and_not_a_refusal(self):
        coordinator = SavegamesOperationCoordinator()
        self.assertFalse(coordinator.cancel_requested())

        coordinator.request_cancel()

        self.assertTrue(coordinator.cancel_requested())
        # Cancellation is observed by the operation, not enforced by the lock.
        self.assertEqual(coordinator.run(lambda: "ok"), "ok")


class SnapshotCancellationTests(CancellationFixture):
    def test_cancel_during_copy_removes_tmp_and_skips_retention(self):
        old = self.snapshot_id()
        self.manager.app_config['savegames']['keep_snapshots'] = 1
        (self.destination / "slot1.sav").write_text("state B", encoding="utf-8")
        (self.destination / "slot3.sav").write_text("extra", encoding="utf-8")

        real_copy = shutil.copy2
        started = threading.Event()
        release = threading.Event()

        def gated_copy(src, dst, *args, **kwargs):
            if not started.is_set():
                started.set()
                release.wait(5)
            return real_copy(src, dst, *args, **kwargs)

        results = []

        def run_snapshot():
            results.append(self.snapshot())

        with mock.patch.object(save_manager_module.shutil, "copy2",
                               side_effect=gated_copy):
            thread = threading.Thread(target=run_snapshot)
            thread.start()
            self.assertTrue(started.wait(2), "the copy never started")
            self.manager.request_cancel()
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())

        result = results[0]
        self.assertTrue(result["cancelled"], result)
        self.assertFalse(result["created"])
        saves = self.staging / "My_Game" / "saves"
        folders = sorted(p.name for p in saves.iterdir() if p.is_dir())
        self.assertEqual(folders, [old])
        self.assertEqual(list(saves.glob("*.tmp")), [])
        # The folder list above is the real guard: ignoring cancellation would
        # publish the new snapshot and retention (keep=1) would delete `old`.

    def test_cancel_before_start_touches_nothing(self):
        self.manager.request_cancel()

        result = self.snapshot()

        self.assertTrue(result["cancelled"])
        self.assertFalse(result["created"])
        self.assertFalse((self.staging / "My_Game").exists())


class RestoreCancellationTests(CancellationFixture):
    def test_cancel_during_copy_leaves_destination_untouched(self):
        snapshot_id = self.snapshot_id()
        (self.destination / "slot1.sav").write_text("live", encoding="utf-8")
        (self.destination / "live_only.sav").write_text("only live", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.destination.iterdir()}
        journal = str(self.destination) + save_manager_module.RESTORE_JOURNAL_SUFFIX
        tmp = str(self.destination) + save_manager_module.RESTORE_TMP_SUFFIX

        real_copy = shutil.copy2
        started = threading.Event()
        release = threading.Event()

        def gated_copy(src, dst, *args, **kwargs):
            if (save_manager_module.RESTORE_TMP_SUFFIX in str(dst)
                    and not started.is_set()):
                started.set()
                release.wait(5)
            return real_copy(src, dst, *args, **kwargs)

        results = []

        def run_restore():
            results.append(self.manager.restore_snapshot(self.meta, snapshot_id))

        with mock.patch.object(save_manager_module.shutil, "copy2",
                               side_effect=gated_copy):
            thread = threading.Thread(target=run_restore)
            thread.start()
            self.assertTrue(started.wait(2), "the restore copy never started")
            self.manager.request_cancel()
            release.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())

        result = results[0]
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "cancelled")
        self.assertEqual({p.name: p.read_bytes() for p in self.destination.iterdir()},
                         before)
        self.assertFalse(os.path.exists(tmp))
        self.assertFalse(os.path.exists(journal))
        # The snapshot source was never lost.
        self.assertTrue(
            (self.staging / "My_Game" / "saves" / snapshot_id / "slot1.sav").is_file())

        # A manager without a cancellation request restores normally afterwards.
        fresh = make_manager(self.staging)
        fresh.get_source_save_path = mock.Mock(return_value=str(self.destination))
        fresh._restore_allowed_targets = lambda meta: (
            [str(self.root)], [], {os.path.expanduser("~")})
        second = fresh.restore_snapshot(self.meta, snapshot_id)
        self.assertTrue(second["ok"], second)
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "state A")
        self.assertFalse((self.destination / "live_only.sav").exists())

    def test_file_restore_cancel_after_safety_keeps_source(self):
        file_dest = self.root / "Saves" / "profile.dat"
        file_dest.write_text("state A", encoding="utf-8")
        self.manager.get_source_save_path = mock.Mock(return_value=str(file_dest))
        first = self.manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(file_dest), reason="sync")
        self.assertTrue(first["created"], first)
        snapshot_id = Path(first["snapshot"]).name
        file_dest.write_text("state B", encoding="utf-8")

        real_create = self.manager.create_snapshot

        def create_then_cancel(*args, **kwargs):
            result = real_create(*args, **kwargs)
            # Cancellation arrives right after the safety snapshot and before
            # the source file is copied: the last safe point for file restores.
            self.manager.request_cancel()
            return result

        self.manager.create_snapshot = create_then_cancel
        journal = str(file_dest) + save_manager_module.RESTORE_JOURNAL_SUFFIX

        result = self.manager.restore_snapshot(self.meta, snapshot_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "cancelled")
        self.assertEqual(file_dest.read_text(encoding="utf-8"), "state B")
        self.assertFalse(os.path.exists(journal))
        source = self.staging / "My_Game" / "saves" / snapshot_id
        self.assertTrue((source / "profile.dat").is_file())
        self.assertTrue(result["safety_snapshot"])
        self.assertTrue(
            (self.staging / "My_Game" / "saves" / result["safety_snapshot"]).is_dir())

    def test_cancel_in_the_unsafe_window_does_not_abort_the_swap(self):
        snapshot_id = self.snapshot_id()
        (self.destination / "slot1.sav").write_text("live", encoding="utf-8")
        journal = str(self.destination) + save_manager_module.RESTORE_JOURNAL_SUFFIX
        old_dir = str(self.destination) + save_manager_module.RESTORE_OLD_SUFFIX

        real_write = self.manager._write_restore_journal

        def journal_then_cancel(root, target, tmp, old, phase, snapshot_id=''):
            result = real_write(root, target, tmp, old, phase, snapshot_id)
            if phase == "moving_old":
                # Between moving_old and the rename there is deliberately no
                # cancellation check: aborting there would strand the journal.
                self.manager.request_cancel()
            return result

        self.manager._write_restore_journal = journal_then_cancel

        result = self.manager.restore_snapshot(self.meta, snapshot_id)

        self.assertTrue(result["ok"], result)
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "state A")
        self.assertFalse(os.path.exists(journal))
        self.assertFalse(os.path.exists(old_dir))

    def test_failure_in_the_unsafe_window_rolls_back_before_clearing_journal(self):
        snapshot_id = self.snapshot_id()
        (self.destination / "slot1.sav").write_text("live", encoding="utf-8")
        journal = str(self.destination) + save_manager_module.RESTORE_JOURNAL_SUFFIX
        old_dir = str(self.destination) + save_manager_module.RESTORE_OLD_SUFFIX
        tmp = str(self.destination) + save_manager_module.RESTORE_TMP_SUFFIX

        real_replace = os.replace

        def replace_with_cancel(src, dst, *args, **kwargs):
            if (str(src).endswith(save_manager_module.RESTORE_TMP_SUFFIX)
                    and str(dst) == str(self.destination)):
                # Cancel + failure exactly between the swapped journal write
                # and the real swap: only rollback of old_dir is safe here.
                self.manager.request_cancel()
                raise OSError("injected swap failure")
            return real_replace(src, dst, *args, **kwargs)

        with mock.patch.object(save_manager_module.os, "replace",
                               side_effect=replace_with_cancel):
            result = self.manager.restore_snapshot(self.meta, snapshot_id)

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["reason"], "swap_failed")
        # Rolled back: original content back, no stranded artifacts.
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "live")
        self.assertFalse(os.path.exists(journal))
        self.assertFalse(os.path.exists(old_dir))
        self.assertFalse(os.path.exists(tmp))
        source = self.staging / "My_Game" / "saves" / snapshot_id
        self.assertTrue((source / "slot1.sav").is_file())


class SyncCancellationTests(unittest.TestCase):
    def test_cancel_between_games_records_the_processed_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "Staging"
            manager = make_manager(staging)
            manager.get_mirror_folder = lambda: ""
            manager._device_id = lambda device_root: "device"

            sources = {}
            games = {}
            for name in ("Game A", "Game B"):
                source = root / name
                source.mkdir()
                (source / "slot.sav").write_text("state " + name, encoding="utf-8")
                sources[name] = source
                install = root / ("inst-" + name)
                install.mkdir()
                games[name] = {
                    "title": name,
                    "install_dir": str(install),
                    "savegame_path_source": "user",
                }
            manager.installed_games = games
            manager.get_source_save_path = lambda meta: str(sources[meta["title"]])

            real_freshen = manager._freshen_json

            def freshen_then_cancel(game_root, title, *args, **kwargs):
                real_freshen(game_root, title, *args, **kwargs)
                # Cancellation only lands between games, after this game's
                # manifest update completed.
                manager.request_cancel()

            manager._freshen_json = freshen_then_cancel

            summary = manager.sync_all_games()

            self.assertTrue(summary["cancelled"], summary)
            self.assertEqual(summary["synced_games"], 1)
            self.assertEqual(summary["errors"], [])
            self.assertEqual(summary["mirror"], {})
            manifest_path = staging / "Game_A" / "savegames.json"
            self.assertTrue(manifest_path.is_file())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["latest_snapshot"].startswith("saves/"))
            self.assertEqual(len(manifest["snapshots"]), 1)
            self.assertFalse((staging / "Game_B").exists())


if __name__ == "__main__":
    unittest.main()
