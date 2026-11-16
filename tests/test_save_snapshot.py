import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import mini_cover_cache
from managers import save_manager as save_manager_module
from managers.save_manager import SavegamesManager


def make_manager(keep_snapshots=5):
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {'savegames': {'keep_snapshots': keep_snapshots}}
    manager.installed_games = {}
    return manager


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Staging"
        self.game_root = self.staging / "My_Game"
        self.source = self.root / "save_source"
        self.source.mkdir(parents=True)
        (self.source / "slot1.sav").write_text("hello", encoding="utf-8")
        (self.source / "nested").mkdir()
        (self.source / "nested" / "slot2.sav").write_text("world", encoding="utf-8")
        self.manager = make_manager()

    def tearDown(self):
        self.tmp.cleanup()

    def snapshots(self):
        saves = self.game_root / "saves"
        if not saves.is_dir():
            return []
        return sorted(p for p in saves.iterdir() if p.is_dir())

    def read_snapshot_info(self, path):
        return json.loads((path / "snapshot.json").read_text(encoding="utf-8"))

    def create(self, reason="sync"):
        return self.manager.create_snapshot(
            str(self.game_root), "My Game",
            {"version": "Build 25922370", "build": 25922370},
            str(self.source), reason=reason,
        )

    def test_creates_atomic_snapshot_with_metadata(self):
        result = self.create()
        self.assertTrue(result["created"])
        self.assertFalse(result["unchanged"])
        snapshot = Path(result["snapshot"])
        self.assertTrue(snapshot.is_dir())
        self.assertFalse(snapshot.name.endswith(".tmp"))
        self.assertTrue((snapshot / "slot1.sav").is_file())
        self.assertTrue((snapshot / "nested" / "slot2.sav").is_file())

        info = self.read_snapshot_info(snapshot)
        self.assertEqual(info["reason"], "sync")
        self.assertEqual(info["version"]["build"], 25922370)
        self.assertTrue(info["complete"])
        self.assertEqual(info["errors"], 0)
        self.assertEqual(info["kind"], "dir")
        self.assertTrue(info["content_hash"])
        self.assertTrue(info["created_at"].endswith("Z"))
        self.assertFalse(list(snapshot.parent.glob("*.tmp")))

    def test_identical_content_is_not_duplicated(self):
        first = self.create()
        second = self.create()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertTrue(second["unchanged"])
        self.assertEqual(first["snapshot"], second["snapshot"])
        self.assertEqual(len(self.snapshots()), 1)

    def test_changed_content_creates_new_snapshot(self):
        self.create()
        (self.source / "slot1.sav").write_text("hello again", encoding="utf-8")
        second = self.create()
        self.assertTrue(second["created"])
        self.assertEqual(len(self.snapshots()), 2)

    def test_completion_does_not_duplicate_across_different_versions(self):
        first = self.create()
        # Same content, different version: no new snapshot is created.
        second = self.manager.create_snapshot(
            str(self.game_root), "My Game",
            {"version": "Build 30000000", "build": 30000000},
            str(self.source), reason="sync",
        )
        self.assertFalse(second["created"])
        self.assertEqual(first["snapshot"], second["snapshot"])
        # The snapshot keeps the version it was taken with.
        info = self.read_snapshot_info(Path(first["snapshot"]))
        self.assertEqual(info["version"]["build"], 25922370)

    def test_retention_keeps_configured_count(self):
        manager = make_manager(keep_snapshots=2)
        for index in range(3):
            (self.source / "slot1.sav").write_text("state {}".format(index), encoding="utf-8")
            result = manager.create_snapshot(
                str(self.game_root), "My Game", {}, str(self.source), reason="sync"
            )
            self.assertTrue(result["created"])

        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(manager.keep_snapshots(), 2)

    def test_partial_copy_marks_snapshot_incomplete(self):
        real_copy2 = shutil.copy2

        def failing_copy(src, dst, *args, **kwargs):
            if str(src).endswith("nested{}slot2.sav".format(os.sep)):
                raise PermissionError("file is locked")
            return real_copy2(src, dst, *args, **kwargs)

        with mock.patch.object(save_manager_module.shutil, "copy2", side_effect=failing_copy):
            result = self.create()

        self.assertTrue(result["created"])
        self.assertEqual(len(result["errors"]), 1)
        snapshot = Path(result["snapshot"])
        info = self.read_snapshot_info(snapshot)
        self.assertFalse(info["complete"])
        self.assertEqual(info["errors"], 1)
        self.assertTrue((snapshot / "slot1.sav").is_file())
        self.assertFalse((snapshot / "nested" / "slot2.sav").exists())

    def test_failed_copy_creates_no_snapshot(self):
        with mock.patch.object(
            save_manager_module.shutil, "copy2", side_effect=PermissionError("locked")
        ):
            result = self.create()

        self.assertFalse(result["created"])
        self.assertTrue(result["errors"])
        self.assertEqual(self.snapshots(), [])
        saves = self.game_root / "saves"
        if saves.is_dir():
            self.assertEqual(list(saves.glob("*.tmp")), [])

    def test_single_file_source(self):
        single = self.root / "profile.dat"
        single.write_text("data", encoding="utf-8")
        result = self.manager.create_snapshot(
            str(self.game_root), "My Game", {}, str(single), reason="sync"
        )
        self.assertTrue(result["created"])
        snapshot = Path(result["snapshot"])
        self.assertTrue((snapshot / "profile.dat").is_file())
        info = self.read_snapshot_info(snapshot)
        self.assertEqual(info["kind"], "file")
        self.assertEqual(info["source_name"], "profile.dat")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlink_is_preserved(self):
        os.symlink("slot1.sav", self.source / "link.sav")
        result = self.create()
        snapshot = Path(result["snapshot"])
        link = snapshot / "link.sav"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.read_text(encoding="utf-8"), "hello")

    def test_tmp_and_hidden_dirs_are_ignored(self):
        save_root = self.game_root / "saves"
        (save_root / "20260101_120000.tmp").mkdir(parents=True)
        (save_root / ".hidden").mkdir()
        (save_root / "20260101_120000").mkdir()
        entries = self.manager._snapshot_entries(str(self.game_root))
        self.assertEqual([entry["id"] for entry in entries], ["20260101_120000"])


class KeepSnapshotsConfigTests(unittest.TestCase):
    def test_default(self):
        manager = object.__new__(SavegamesManager)
        manager.app_config = {}
        self.assertEqual(manager.keep_snapshots(), 5)

    def test_invalid_value_falls_back(self):
        manager = object.__new__(SavegamesManager)
        manager.app_config = {'savegames': {'keep_snapshots': 'many'}}
        self.assertEqual(manager.keep_snapshots(), 5)

    def test_zero_is_clamped(self):
        manager = object.__new__(SavegamesManager)
        manager.app_config = {'savegames': {'keep_snapshots': 0}}
        self.assertEqual(manager.keep_snapshots(), 1)


class EnsureGameAssetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self.tmp.name)
        self.game_root = self.staging / "My_Game"
        self.manager = make_manager()
        self.manager.get_sync_folder = lambda: str(self.staging)

    def tearDown(self):
        self.tmp.cleanup()

    def test_manifest_and_cover_are_created(self):
        with mock.patch.object(mini_cover_cache.MiniCoverManager, "persist_cover",
                               return_value=True) as persist:
            self.manager.ensure_game_assets(str(self.game_root), "My Game", {"playtime_seconds": 10})

        persist.assert_called_once()
        self.assertTrue((self.game_root / "savegames.json").is_file())

    def test_existing_cover_is_not_replaced(self):
        self.game_root.mkdir(parents=True)
        (self.game_root / "mini_cover.jpg").write_bytes(b"x" * 2048)
        with mock.patch.object(mini_cover_cache.MiniCoverManager, "persist_cover") as persist:
            self.manager.ensure_game_assets(str(self.game_root), "My Game", {})

        persist.assert_not_called()
        self.assertTrue((self.game_root / "savegames.json").is_file())


class PreUpdateBackupTests(unittest.TestCase):
    def test_pre_update_snapshot_is_labeled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "Staging"
            source = root / "saves"
            source.mkdir()
            (source / "save.dat").write_text("data", encoding="utf-8")

            manager = make_manager()
            manager.get_sync_folder = lambda: str(staging)
            manager.get_source_save_path = lambda meta: str(source)

            result = manager.backup_before_update({"title": "My Game", "version": "v1"})

            self.assertTrue(result)
            snapshots = list((staging / "My_Game" / "saves").iterdir())
            self.assertEqual(len(snapshots), 1)
            info = json.loads((snapshots[0] / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(info["reason"], "pre_update")


if __name__ == "__main__":
    unittest.main()
