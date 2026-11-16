import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import save_paths
from managers.backup_service import BackupService
from managers.save_manager import SavegamesManager


def make_manager(staging):
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {'savegames': {'sync_folder': str(staging), 'keep_snapshots': 5}}
    manager.installed_games = {}
    manager.get_sync_folder = lambda: str(staging)
    return manager


class RestoreTargetTests(unittest.TestCase):
    def test_drive_root_is_rejected(self):
        root = os.path.abspath(os.sep)
        ok, reason = save_paths.check_restore_target(root, protected=[root])
        self.assertFalse(ok)
        self.assertEqual(reason, "filesystem_root")

    def test_home_is_rejected(self):
        home = os.path.expanduser("~")
        ok, reason = save_paths.check_restore_target(home, allowed_roots=[home], protected=[home])
        self.assertFalse(ok)
        self.assertEqual(reason, "protected_path")

    def test_protected_ancestor_is_rejected(self):
        home = os.path.expanduser("~")
        ancestor = os.path.dirname(home)
        ok, reason = save_paths.check_restore_target(ancestor, protected=[home])
        self.assertFalse(ok)
        self.assertIn(reason, ("protected_path", "protected_ancestor", "outside_allowed_roots"))

    def test_inside_allowed_root_is_accepted(self):
        root = os.path.join(os.path.expanduser("~"), "Documents")
        target = os.path.join(root, "Game", "Saves")
        ok, reason = save_paths.check_restore_target(target, allowed_roots=[root], protected=[root])
        self.assertTrue(ok, reason)

    def test_allowed_root_itself_is_rejected(self):
        root = os.path.join(os.path.expanduser("~"), "Documents")
        ok, _ = save_paths.check_restore_target(root, allowed_roots=[root], protected=[root])
        self.assertFalse(ok)

    def test_exact_allowed_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, reason = save_paths.check_restore_target(
                tmp, allowed_roots=[], exact_allowed=[tmp], protected=[os.path.expanduser("~")]
            )
            self.assertTrue(ok, reason)

    def test_relative_path_is_rejected(self):
        ok, reason = save_paths.check_restore_target("saves/game")
        self.assertFalse(ok)
        self.assertEqual(reason, "not_absolute")


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.staging = self.root / "Staging"
        self.install_dir = self.root / "Game"
        self.install_dir.mkdir()
        self.destination = self.root / "Saves" / "MyGame"
        self.destination.mkdir(parents=True)
        (self.destination / "slot1.sav").write_text("state A", encoding="utf-8")

        self.manager = make_manager(self.staging)
        self.meta = {
            "title": "My Game",
            "install_dir": str(self.install_dir),
            "savegame_path_win": str(self.destination),
            "savegame_path_source": "user",
            "source_url": "https://example.test/game",
            "version": "Build 2",
            "build": 2,
        }
        # Deterministic destination and permissive roots: safety rules are
        # covered by RestoreTargetTests above.
        self.manager.get_source_save_path = mock.Mock(return_value=str(self.destination))
        self.manager._restore_allowed_targets = lambda meta: (
            [str(self.root)], [], {os.path.expanduser("~")}
        )

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, content, reason="sync"):
        (self.destination / "slot1.sav").write_text(content, encoding="utf-8")
        result = self.manager.create_snapshot(
            str(self.staging / "My_Game"), "My Game", self.meta,
            str(self.destination), reason=reason,
        )
        self.assertTrue(result["created"])
        return Path(result["snapshot"]).name

    def test_restore_replaces_content_and_keeps_safety_snapshot(self):
        first = self.snapshot("state A")
        self.snapshot("state B")
        (self.destination / "slot1.sav").write_text("state C", encoding="utf-8")
        (self.destination / "extra.sav").write_text("newer", encoding="utf-8")

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertTrue(result["ok"], result)
        self.assertEqual((self.destination / "slot1.sav").read_text(encoding="utf-8"), "state A")
        self.assertFalse((self.destination / "extra.sav").exists())
        self.assertTrue(result["safety_snapshot"])
        self.assertTrue(result["files"] >= 1)

        safety = self.staging / "My_Game" / "saves" / result["safety_snapshot"]
        safety_info = json.loads((safety / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(safety_info["reason"], "pre_restore")
        self.assertTrue((safety / "slot1.sav").is_file())
        self.assertEqual((safety / "slot1.sav").read_text(encoding="utf-8"), "state C")

    def test_snapshot_metadata_is_not_restored(self):
        first = self.snapshot("state A")
        (self.destination / "slot1.sav").write_text("state B", encoding="utf-8")

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertTrue(result["ok"], result)
        self.assertFalse((self.destination / "snapshot.json").exists())

    def test_manifest_records_restore(self):
        first = self.snapshot("state A")
        self.manager.restore_snapshot(self.meta, first)

        manifest = json.loads(
            (self.staging / "My_Game" / "savegames.json").read_text(encoding="utf-8")
        )
        self.assertTrue(manifest["last_restore_at"].endswith("Z"))

    def test_missing_destination_requires_confirmation(self):
        first = self.snapshot("state A")
        missing = self.root / "Saves" / "Missing"
        self.manager.get_source_save_path = mock.Mock(return_value=str(missing))

        refused = self.manager.restore_snapshot(self.meta, first)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], "destination_missing")
        self.assertFalse(missing.exists())

        created = self.manager.restore_snapshot(self.meta, first, create_missing=True)
        self.assertTrue(created["ok"], created)
        self.assertTrue((missing / "slot1.sav").is_file())

    def test_uninstalled_game_is_rejected(self):
        first = self.snapshot("state A")
        meta = dict(self.meta)
        meta["install_dir"] = str(self.root / "NotThere")

        result = self.manager.restore_snapshot(meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_installed")

    def test_unsafe_destination_is_rejected(self):
        first = self.snapshot("state A")
        self.manager.get_source_save_path = mock.Mock(return_value=os.path.expanduser("~"))
        self.manager._restore_allowed_targets = (
            SavegamesManager._restore_allowed_targets.__get__(self.manager)
        )

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsafe_target")

    def test_unknown_snapshot_is_rejected(self):
        self.snapshot("state A")
        result = self.manager.restore_snapshot(self.meta, "does-not-exist")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "snapshot_not_found")

    def test_staging_folder_is_rejected_as_destination(self):
        first = self.snapshot("state A")
        self.manager.get_source_save_path = mock.Mock(
            return_value=str(self.staging / "My_Game")
        )

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unsafe_target")
        self.assertEqual(result["detail"], "staging_folder")

    def test_file_destination_with_folder_snapshot_is_rejected(self):
        first = self.snapshot("state A")
        file_destination = self.root / "slot1.sav"
        file_destination.write_text("not a folder", encoding="utf-8")
        self.manager.get_source_save_path = mock.Mock(return_value=str(file_destination))

        result = self.manager.restore_snapshot(self.meta, first)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "destination_kind_mismatch")

    def test_list_snapshots_newest_first(self):
        first = self.snapshot("state A")
        second = self.snapshot("state B")

        snapshots = self.manager.list_snapshots("My Game")

        self.assertEqual([entry["id"] for entry in snapshots], [second, first])
        self.assertIn("path", snapshots[0])
        self.assertIn("version", snapshots[0])


class BackupServiceTests(unittest.TestCase):
    def test_restore_delegates(self):
        manager = mock.Mock()
        manager.restore_snapshot.return_value = {"ok": True}
        service = BackupService(manager)

        result = service.restore_game({"title": "Game"}, "snap", create_missing=True)

        self.assertTrue(result["ok"])
        manager.restore_snapshot.assert_called_once_with(
            {"title": "Game"}, "snap", create_missing=True
        )

    def test_backup_state_snapshots_and_pushes(self):
        savegames = mock.Mock()
        savegames.push_mirror.return_value = {
            "configured": True, "copied": 2, "skipped": 0, "errors": []
        }
        state = mock.Mock()
        state.snapshot.return_value = Path("/tmp/20260101_120000")
        state.root = Path("/tmp/.launcher_state")
        service = BackupService(savegames, state)

        result = service.backup_state()

        self.assertTrue(result["ok"])
        self.assertEqual(result["snapshot"], "20260101_120000")
        savegames.push_mirror.assert_called_once_with()

    def test_backup_state_without_manager(self):
        service = BackupService(mock.Mock())
        self.assertFalse(service.backup_state()["ok"])

    def test_restore_state_delegates(self):
        state = mock.Mock()
        state.restore_merge.return_value = {"files": 2, "jsonl_added": 1,
                                            "achievements_added": 1}
        service = BackupService(mock.Mock(), state)
        self.assertEqual(service.restore_state()["files"], 2)

    def test_state_status_without_manager(self):
        service = BackupService(mock.Mock())
        self.assertEqual(service.state_status()["files"], 0)

    def test_status_reports_destination(self):
        manager = mock.Mock()
        manager.get_source_save_path.return_value = "/tmp/saves"
        service = BackupService(manager)
        manager.list_snapshots.return_value = [{"id": "1"}, {"id": "2"}]

        status = service.status({"title": "Game"})

        self.assertEqual(status["snapshots"], 2)
        self.assertEqual(status["latest"], "1")
        self.assertEqual(status["destination"], "/tmp/saves")


if __name__ == "__main__":
    unittest.main()
