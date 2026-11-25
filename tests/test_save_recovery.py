"""Recovery of interrupted savegame restores (G3 critical fix)."""
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from managers.save_manager import (
    RESTORE_JOURNAL_SUFFIX, RESTORE_OLD_SUFFIX, RESTORE_TMP_SUFFIX,
    SavegamesManager,
)


def make_manager(staging, root):
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {'savegames': {'sync_folder': str(staging), 'keep_snapshots': 5}}
    manager.installed_games = {}
    manager.get_sync_folder = lambda: str(staging)
    manager._restore_allowed_targets = lambda meta: (
        [str(root)], [], {os.path.expanduser('~')}
    )
    return manager


class RecoveryUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = make_manager(self.root / "Staging", self.root)

    def target(self):
        path = self.root / "Saves"
        path.mkdir(exist_ok=True)
        return path

    def write_journal(self, target, phase, root=None):
        root = root or target
        SavegamesManager._write_restore_journal(
            root, target, str(target) + RESTORE_TMP_SUFFIX,
            str(target) + RESTORE_OLD_SUFFIX, phase, "snap")

    def test_missing_destination_is_restored_from_old(self):
        target = self.target()
        target.rmdir()
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "slot.sav").write_text("original", encoding="utf-8")
        self.write_journal(target, "moved_old")

        reason, detail = self.manager._recover_interrupted_restore(target)

        self.assertEqual((reason, detail), ("", ""))
        self.assertEqual((target / "slot.sav").read_text(encoding="utf-8"),
                         "original")
        self.assertFalse(old.exists())
        self.assertFalse(Path(str(target) + RESTORE_JOURNAL_SUFFIX).exists())

    def test_swapped_journal_removes_the_old_copy(self):
        target = self.target()
        (target / "slot.sav").write_text("new", encoding="utf-8")
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "slot.sav").write_text("old", encoding="utf-8")
        self.write_journal(target, "swapped")

        reason, _ = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "")
        self.assertEqual((target / "slot.sav").read_text(encoding="utf-8"),
                         "new")
        self.assertFalse(old.exists())

    def test_both_copies_without_a_swapped_journal_are_ambiguous(self):
        target = self.target()
        (target / "slot.sav").write_text("new", encoding="utf-8")
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "slot.sav").write_text("old", encoding="utf-8")
        self.write_journal(target, "prepared")

        reason, detail = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "recovery_ambiguous")
        self.assertTrue(old.exists())
        self.assertEqual((target / "slot.sav").read_text(encoding="utf-8"),
                         "new")

    def test_tmp_is_never_deleted_when_target_is_missing(self):
        target = self.target()
        target.rmdir()
        tmp = Path(str(target) + RESTORE_TMP_SUFFIX)
        tmp.mkdir()
        (tmp / "slot.sav").write_text("new", encoding="utf-8")
        self.write_journal(target, "prepared")

        reason, detail = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "recovery_ambiguous")
        self.assertTrue(tmp.exists())

    def test_tmp_is_removed_when_the_target_is_still_present(self):
        target = self.target()
        (target / "slot.sav").write_text("original", encoding="utf-8")
        tmp = Path(str(target) + RESTORE_TMP_SUFFIX)
        tmp.mkdir()
        (tmp / "slot.sav").write_text("partial", encoding="utf-8")
        self.write_journal(target, "prepared")

        reason, _ = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "")
        self.assertFalse(tmp.exists())
        self.assertEqual((target / "slot.sav").read_text(encoding="utf-8"),
                         "original")

    def test_recovery_is_idempotent(self):
        target = self.target()
        target.rmdir()
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "slot.sav").write_text("original", encoding="utf-8")
        self.write_journal(target, "moved_old")
        self.assertEqual(self.manager._recover_interrupted_restore(target)[0], "")
        self.assertEqual(self.manager._recover_interrupted_restore(target)[0], "")

    def test_file_tmp_is_removed_but_original_is_kept(self):
        destination = self.root / "Saves"
        destination.mkdir()
        target = destination / "slot.sav"
        target.write_text("original", encoding="utf-8")
        tmp = Path(str(target) + RESTORE_TMP_SUFFIX)
        tmp.write_text("partial", encoding="utf-8")
        self.write_journal(target, "prepared", root=destination)

        reason, _ = self.manager._recover_interrupted_restore(
            target, journal_root=destination)

        self.assertEqual(reason, "")
        self.assertFalse(tmp.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_recovery_failure_is_reported_and_keeps_the_old_copy(self):
        target = self.target()
        target.rmdir()
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.mkdir()
        (old / "slot.sav").write_text("original", encoding="utf-8")
        self.write_journal(target, "moved_old")
        with mock.patch("managers.save_manager.os.replace",
                        side_effect=OSError("cannot rename")):
            reason, detail = self.manager._recover_interrupted_restore(target)
        self.assertEqual(reason, "recovery_failed")
        self.assertIn("cannot restore the original", detail)
        self.assertTrue(old.exists())

    def test_junction_artifact_is_removed_without_following_it(self):
        path = self.root / "artifact"
        path.mkdir()
        with mock.patch("managers.save_manager.os.path.isjunction",
                        return_value=True, create=True), \
                mock.patch("managers.save_manager.os.rmdir") as rmdir, \
                mock.patch("managers.save_manager.shutil.rmtree") as rmtree:
            self.assertTrue(SavegamesManager._remove_restore_path(path))
        rmdir.assert_called_once_with(path)
        rmtree.assert_not_called()

    def test_symlinked_old_copy_does_not_touch_its_target(self):
        target = self.target()
        (target / "slot.sav").write_text("new", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        old.symlink_to(outside, target_is_directory=True)
        self.write_journal(target, "swapped")

        reason, _ = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "")
        self.assertFalse(old.exists())
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"),
                         "keep")

    def test_symlinked_tmp_does_not_touch_its_target(self):
        target = self.target()
        (target / "slot.sav").write_text("original", encoding="utf-8")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        tmp = Path(str(target) + RESTORE_TMP_SUFFIX)
        tmp.symlink_to(outside, target_is_directory=True)
        self.write_journal(target, "prepared")

        reason, _ = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "")
        self.assertFalse(tmp.exists())
        self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"),
                         "keep")

    def test_journal_symlink_is_removed_without_touching_its_target(self):
        target = self.target()
        old = Path(str(target) + RESTORE_OLD_SUFFIX)
        target.rmdir()
        old.mkdir()
        (old / "slot.sav").write_text("original", encoding="utf-8")
        outside = self.root / "outside.json"
        outside.write_text("do not touch", encoding="utf-8")
        journal = Path(str(target) + RESTORE_JOURNAL_SUFFIX)
        journal.symlink_to(outside)
        self.assertEqual(self.manager._recover_interrupted_restore(target)[0], "")
        self.assertFalse(journal.exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not touch")

    def test_unrecognized_tmp_without_journal_is_kept(self):
        target = self.target()
        (target / "slot.sav").write_text("original", encoding="utf-8")
        tmp = Path(str(target) + RESTORE_TMP_SUFFIX)
        tmp.mkdir()
        (tmp / "slot.sav").write_text("unknown", encoding="utf-8")

        reason, _ = self.manager._recover_interrupted_restore(target)

        self.assertEqual(reason, "recovery_ambiguous")
        self.assertTrue(tmp.exists())

    def test_journal_for_another_target_is_ambiguous(self):
        destination = self.root / "Saves"
        destination.mkdir()
        target = destination / "slot.sav"
        target.write_text("original", encoding="utf-8")
        other = destination / "other.sav"
        self.write_journal(other, "prepared", root=destination)

        reason, detail = self.manager._recover_interrupted_restore(
            target, journal_root=destination)

        self.assertEqual(reason, "recovery_ambiguous")
        self.assertIn("another target", detail)


class RestoreRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Staging"
        self.install_dir = self.root / "Game"
        self.install_dir.mkdir()
        self.destination = self.root / "Saves" / "MyGame"
        self.destination.mkdir(parents=True)
        (self.destination / "slot.sav").write_text("state A", encoding="utf-8")
        self.manager = make_manager(self.staging, self.root)
        self.meta = {
            "title": "My Game",
            "install_dir": str(self.install_dir),
            "savegame_path_win": str(self.destination),
            "savegame_path_source": "user",
        }
        self.manager.get_source_save_path = mock.Mock(return_value=str(self.destination))

    def snapshot(self, content):
        (self.destination / "slot1.sav").write_text(content, encoding="utf-8")
        result = self.manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(self.destination),
        )
        self.assertTrue(result["created"], result)
        return Path(result["snapshot"]).name

    def test_restore_recovers_an_interrupted_old_copy_first(self):
        first = self.snapshot("state A")
        old = Path(str(self.destination) + RESTORE_OLD_SUFFIX)
        self.destination.rename(old)
        SavegamesManager._write_restore_journal(
            str(self.destination), str(self.destination),
            str(self.destination) + RESTORE_TMP_SUFFIX, str(old),
            "moved_old", first)

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertTrue(result["ok"], result)
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "state A")
        self.assertFalse(old.exists())

    def test_second_rename_failure_rolls_back_the_original(self):
        first = self.snapshot("state A")
        (self.destination / "slot1.sav").write_text("live", encoding="utf-8")
        tmp_dir = Path(str(self.destination) + RESTORE_TMP_SUFFIX)
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if Path(src) == tmp_dir and Path(dst) == self.destination:
                raise OSError("injected swap failure")
            return real_replace(src, dst)

        with mock.patch("managers.save_manager.os.replace", side_effect=flaky):
            result = self.manager.restore_snapshot(self.meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "swap_failed")
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "live")
        self.assertFalse(Path(str(self.destination) + RESTORE_JOURNAL_SUFFIX).exists())
        self.assertFalse(Path(str(self.destination) + RESTORE_OLD_SUFFIX).exists())

    def test_failed_rollback_keeps_old_and_recovery_restores_it(self):
        first = self.snapshot("state A")
        (self.destination / "slot1.sav").write_text("live", encoding="utf-8")
        tmp_dir = Path(str(self.destination) + RESTORE_TMP_SUFFIX)
        old_dir = Path(str(self.destination) + RESTORE_OLD_SUFFIX)
        real_replace = os.replace

        def flaky(src, dst):
            src, dst = Path(src), Path(dst)
            if src == tmp_dir and dst == self.destination:
                raise OSError("injected swap failure")
            if src == old_dir and dst == self.destination:
                raise OSError("injected rollback failure")
            return real_replace(src, dst)

        with mock.patch("managers.save_manager.os.replace", side_effect=flaky):
            result = self.manager.restore_snapshot(self.meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "swap_failed")
        self.assertTrue(old_dir.is_dir())
        self.assertFalse(self.destination.exists())

        # A later recovery must put the original back, without deleting it.
        reason, _ = self.manager._recover_interrupted_restore(self.destination)
        self.assertEqual(reason, "")
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"),
                         "live")


if __name__ == "__main__":
    unittest.main()
