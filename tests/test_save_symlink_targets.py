"""Restore destinations through symlinks and junctions (G3 safety fix)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers.save_manager import RESTORE_JOURNAL_SUFFIX, SavegamesManager


def make_manager(staging, allowed):
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {'savegames': {'sync_folder': str(staging), 'keep_snapshots': 5}}
    manager.installed_games = {}
    manager.get_sync_folder = lambda: str(staging)
    manager._restore_allowed_targets = lambda meta: (
        list(allowed), [], {os.path.expanduser('~')}
    )
    return manager


class SymlinkTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Staging"
        self.install_dir = self.root / "Game"
        self.install_dir.mkdir()
        self.allowed = self.root / "Allowed"
        self.allowed.mkdir()
        self.outside = self.root / "Outside"
        self.outside.mkdir()
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "slot.sav").write_text("snapshot", encoding="utf-8")
        self.manager = make_manager(self.staging, [self.allowed])
        self.meta = {
            "title": "My Game",
            "install_dir": str(self.install_dir),
            "savegame_path_source": "user",
        }

    def snapshot(self):
        result = self.manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(self.source),
        )
        self.assertTrue(result["created"], result)
        return Path(result["snapshot"]).name

    def test_symlinked_parent_outside_allowed_is_rejected(self):
        link = self.allowed / "link"
        link.symlink_to(self.outside, target_is_directory=True)
        destination = link / "Saves"
        destination.mkdir()
        (destination / "slot.sav").write_text("original", encoding="utf-8")
        sentinel = self.outside / "keep.txt"
        sentinel.write_text("do not touch", encoding="utf-8")
        self.manager.get_source_save_path = mock.Mock(return_value=str(destination))
        snap = self.snapshot()

        result = self.manager.restore_snapshot(self.meta, snap)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsafe_target")
        self.assertEqual((destination / "slot.sav").read_text(encoding="utf-8"),
                         "original")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch")
        self.assertFalse(Path(str(destination) + RESTORE_JOURNAL_SUFFIX).exists())

    def test_symlinked_parent_inside_allowed_is_accepted(self):
        real = self.allowed / "real"
        real.mkdir()
        link = self.allowed / "link"
        link.symlink_to(real, target_is_directory=True)
        destination = link / "Saves"
        destination.mkdir()
        (destination / "slot.sav").write_text("original", encoding="utf-8")
        self.manager.get_source_save_path = mock.Mock(return_value=str(destination))
        snap = self.snapshot()

        result = self.manager.restore_snapshot(self.meta, snap)

        self.assertTrue(result["ok"], result)
        self.assertEqual((real / "Saves" / "slot.sav").read_text(encoding="utf-8"),
                         "snapshot")
        self.assertEqual(Path(result["destination"]).resolve(),
                         (real / "Saves").resolve())

    def test_final_directory_symlink_outside_is_rejected(self):
        destination = self.allowed / "GameSaves"
        destination.symlink_to(self.outside, target_is_directory=True)
        self.manager.get_source_save_path = mock.Mock(return_value=str(destination))
        snap = self.snapshot()

        result = self.manager.restore_snapshot(self.meta, snap)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsafe_target")

    def test_allowed_root_that_is_itself_a_symlink_is_accepted(self):
        real_root = self.root / "RealAllowed"
        real_root.mkdir()
        link_root = self.root / "AllowedLink"
        link_root.symlink_to(real_root, target_is_directory=True)
        destination = link_root / "Saves"
        destination.mkdir()
        (destination / "slot.sav").write_text("original", encoding="utf-8")
        manager = make_manager(self.staging, [link_root])
        manager._restore_allowed_targets = lambda meta: (
            [str(link_root)], [], {os.path.expanduser('~')}
        )
        manager.get_source_save_path = mock.Mock(return_value=str(destination))
        result = manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta, str(self.source))
        snap = Path(result["snapshot"]).name

        restored = manager.restore_snapshot(self.meta, snap)

        self.assertTrue(restored["ok"], restored)
        self.assertEqual((real_root / "Saves" / "slot.sav").read_text(encoding="utf-8"),
                         "snapshot")

    def test_explicit_custom_symlink_is_accepted(self):
        custom = self.root / "Custom"
        custom.symlink_to(self.outside, target_is_directory=True)
        destination = custom / "Saves"
        destination.mkdir()
        (destination / "slot.sav").write_text("original", encoding="utf-8")
        manager = make_manager(self.staging, [])
        manager._restore_allowed_targets = lambda meta: (
            [str(custom)], [str(custom)], {os.path.expanduser('~')}
        )
        manager.get_source_save_path = mock.Mock(return_value=str(destination))
        result = manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta, str(self.source))
        snap = Path(result["snapshot"]).name

        restored = manager.restore_snapshot(self.meta, snap)

        self.assertTrue(restored["ok"], restored)
        self.assertEqual((destination / "slot.sav").read_text(encoding="utf-8"),
                         "snapshot")

    def test_final_file_symlink_outside_is_rejected(self):
        source_file = self.root / "slot.sav"
        source_file.write_text("snapshot", encoding="utf-8")
        result = self.manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(source_file),
        )
        self.assertTrue(result["created"], result)
        snap = Path(result["snapshot"]).name
        target = self.outside / "slot.sav"
        target.write_text("original", encoding="utf-8")
        destination = self.allowed / "slot.sav"
        destination.symlink_to(target)
        self.manager.get_source_save_path = mock.Mock(return_value=str(destination))

        restored = self.manager.restore_snapshot(self.meta, snap)

        self.assertFalse(restored["ok"])
        self.assertEqual(restored["reason"], "unsafe_target")
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_target_is_revalidated_before_the_destructive_swap(self):
        destination = self.allowed / "Saves"
        destination.mkdir()
        (destination / "slot.sav").write_text("original", encoding="utf-8")
        self.manager.get_source_save_path = mock.Mock(return_value=str(destination))
        snap = self.snapshot()
        real_validate = self.manager._validate_restore_destination
        calls = {"n": 0}

        def flaky(path, meta):
            calls["n"] += 1
            if calls["n"] >= 2:
                return False, "changed meanwhile", path
            return real_validate(path, meta)

        with mock.patch.object(
            self.manager, "_validate_restore_destination", side_effect=flaky
        ):
            result = self.manager.restore_snapshot(self.meta, snap)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsafe_target")
        self.assertEqual((destination / "slot.sav").read_text(encoding="utf-8"),
                         "original")
        self.assertGreaterEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()
