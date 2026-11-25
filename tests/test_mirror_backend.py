import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers.mirror_backend import (
    FolderMirrorBackend,
    STATE_CONFLICT,
    STATE_LOCAL_NEWER,
    STATE_LOCAL_ONLY,
    STATE_MIRROR_NEWER,
    STATE_MIRROR_ONLY,
    STATE_SYNCED,
)
from managers.save_manager import SavegamesManager


def write_game(root, safe_title, title, ids, latest="", version=None,
               synced="2026-01-01T00:00:00Z", complete=True, skip_info=()):
    game = Path(root) / safe_title
    (game / "saves").mkdir(parents=True, exist_ok=True)
    for snapshot_id in ids:
        folder = game / "saves" / snapshot_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "save.dat").write_text("state " + snapshot_id, encoding="utf-8")
        if snapshot_id not in skip_info:
            (folder / "snapshot.json").write_text(json.dumps({
                "schema": 1,
                "id": snapshot_id,
                "complete": complete,
                "version": version or {},
            }), encoding="utf-8")
    manifest = {
        "game_title": title,
        "latest_snapshot": ("saves/" + latest) if latest else "",
        "snapshots": [{"id": snapshot_id} for snapshot_id in ids],
        "last_synced": synced,
        "game_version": version or {},
    }
    (game / "savegames.json").write_text(json.dumps(manifest), encoding="utf-8")


class FolderMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = self.root / "Staging"
        self.mirror = self.root / "Mirror"
        self.local.mkdir()
        self.backend = FolderMirrorBackend(str(self.local), str(self.mirror))

    def tearDown(self):
        self.tmp.cleanup()

    def test_test_writable(self):
        ok, error = self.backend.test()
        self.assertTrue(ok, error)
        self.assertTrue(self.mirror.is_dir())
        self.assertFalse((self.mirror / ".aethicc_write_test").exists())

    def test_test_rejects_unusable_folder(self):
        blocker = self.root / "blocked"
        blocker.write_text("not a folder", encoding="utf-8")
        ok, error = FolderMirrorBackend(str(self.local), str(blocker)).test()
        self.assertFalse(ok)
        self.assertTrue(error)

    def test_mirror_inside_local_is_rejected(self):
        nested = self.local / "Mirror"
        backend = FolderMirrorBackend(str(self.local), str(nested))
        ok, error = backend.test()
        self.assertFalse(ok)
        self.assertIn("outside", error.lower())
        report = backend.push()
        self.assertTrue(report["errors"])

    def test_local_inside_mirror_is_rejected(self):
        backend = FolderMirrorBackend(str(self.mirror / "Staging"), str(self.mirror))
        ok, error = backend.test()
        self.assertFalse(ok)
        self.assertIn("outside", error.lower())

    def test_push_copies_then_skips_unchanged(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        (self.local / "Game_A" / "mini_cover.jpg").write_bytes(b"cover")

        first = self.backend.push()
        self.assertEqual(first["copied"], 4)  # manifest + cover + save + snapshot.json
        self.assertEqual(first["errors"], [])
        self.assertTrue((self.mirror / "Game_A" / "saves" / "20260101_120000" / "save.dat").is_file())

        second = self.backend.push()
        self.assertEqual(second["copied"], 0)
        self.assertEqual(second["skipped"], 4)

    def test_push_updates_changed_files(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        self.backend.push()
        (self.local / "Game_A" / "saves" / "20260101_120000" / "save.dat").write_text(
            "much longer state", encoding="utf-8"
        )

        report = self.backend.push()

        self.assertEqual(report["copied"], 1)
        mirrored = (self.mirror / "Game_A" / "saves" / "20260101_120000" / "save.dat")
        self.assertEqual(mirrored.read_text(encoding="utf-8"), "much longer state")

    def test_push_never_deletes_mirror_files(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        self.mirror.mkdir()
        extra = self.mirror / "kept.txt"
        extra.write_text("keep me", encoding="utf-8")

        self.backend.push()

        self.assertTrue(extra.is_file())

    def test_push_cancels_between_files_and_never_deletes(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        (self.local / "Game_A" / "mini_cover.jpg").write_bytes(b"cover")
        self.mirror.mkdir()
        extra = self.mirror / "kept.txt"
        extra.write_text("keep me", encoding="utf-8")
        calls = {"count": 0}

        def should_cancel():
            calls["count"] += 1
            return calls["count"] > 2

        report = self.backend.push(should_cancel=should_cancel)

        self.assertTrue(report["cancelled"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["copied"], 1)
        self.assertTrue(extra.is_file())

    def test_push_skips_root_hidden_and_excluded_files(self):
        (self.local / ".launcher_device").write_text("device", encoding="utf-8")
        (self.local / ".mirror_imports.json").write_text("{}", encoding="utf-8")
        write_game(self.local, "Game_A", "Game A", [], "")

        self.backend.push(exclude_names=(".launcher_device",))

        self.assertFalse((self.mirror / ".launcher_device").exists())
        self.assertFalse((self.mirror / ".mirror_imports.json").exists())
        self.assertTrue((self.mirror / "Game_A" / "savegames.json").is_file())

    def test_launcher_state_is_mirrored(self):
        current = self.local / ".launcher_state" / "current"
        current.mkdir(parents=True)
        (current / "achievements.json").write_text("{}", encoding="utf-8")

        report = self.backend.push()

        self.assertEqual(report["copied"], 1)
        self.assertTrue(
            (self.mirror / ".launcher_state" / "current" / "achievements.json").is_file()
        )
        self.assertNotIn(".launcher_state", [e["safe_title"] for e in self.backend.compare()])

    def test_pull_missing_only_copies_absent_files(self):
        write_game(self.mirror, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        write_game(self.mirror, "Game_A_Extra", "Game A Extra", ["20260101_120000"], "20260101_120000")
        self.local.mkdir(exist_ok=True)
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        local_manifest = self.local / "Game_A" / "savegames.json"
        local_manifest.write_text('{"game_title": "local wins"}', encoding="utf-8")

        report = self.backend.pull_missing()

        self.assertTrue(report["copied"] >= 2)
        self.assertEqual(
            json.loads(local_manifest.read_text(encoding="utf-8"))["game_title"],
            "local wins",
        )
        self.assertTrue((self.local / "Game_A_Extra" / "savegames.json").is_file())

    def test_compare_states(self):
        # local only
        write_game(self.local, "LocalOnly", "Local Only", ["20260101_120000"], "20260101_120000")
        # mirror only
        write_game(self.mirror, "MirrorOnly", "Mirror Only", ["20260101_120000"], "20260101_120000")
        # synced
        write_game(self.local, "Synced", "Synced", ["20260101_120000"], "20260101_120000")
        write_game(self.mirror, "Synced", "Synced", ["20260101_120000"], "20260101_120000")
        # local newer
        write_game(self.local, "LocalNewer", "Local Newer",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        write_game(self.mirror, "LocalNewer", "Local Newer",
                   ["20260101_120000"], "20260101_120000")
        # mirror newer
        write_game(self.local, "MirrorNewer", "Mirror Newer",
                   ["20260101_120000"], "20260101_120000")
        write_game(self.mirror, "MirrorNewer", "Mirror Newer",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        # conflict
        write_game(self.local, "Conflict", "Conflict",
                   ["20260101_120000", "20260103_120000"], "20260103_120000")
        write_game(self.mirror, "Conflict", "Conflict",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")

        states = {entry["safe_title"]: entry["state"] for entry in self.backend.compare()}

        self.assertEqual(states["LocalOnly"], STATE_LOCAL_ONLY)
        self.assertEqual(states["MirrorOnly"], STATE_MIRROR_ONLY)
        self.assertEqual(states["Synced"], STATE_SYNCED)
        self.assertEqual(states["LocalNewer"], STATE_LOCAL_NEWER)
        self.assertEqual(states["MirrorNewer"], STATE_MIRROR_NEWER)
        self.assertEqual(states["Conflict"], STATE_CONFLICT)

    def test_compare_uses_folder_fallback_without_manifest(self):
        game = self.local / "NoManifest" / "saves" / "20260101_120000"
        game.mkdir(parents=True)
        states = {entry["safe_title"]: entry["state"] for entry in self.backend.compare()}
        self.assertEqual(states["NoManifest"], STATE_LOCAL_ONLY)

    def test_write_manifest(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000",
                   version={"build": 42})
        self.backend.push()

        payload = self.backend.write_manifest(device_id="abc")

        self.assertEqual(payload["device_id"], "abc")
        self.assertIn("Game_A", payload["games"])
        self.assertEqual(payload["games"]["Game_A"]["latest_snapshot"], "20260101_120000")
        self.assertEqual(payload["games"]["Game_A"]["game_version"]["build"], 42)
        stored = json.loads((self.mirror / "backup_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["schema"], 1)


class ManagerMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = self.root / "Staging"
        self.mirror = self.root / "Mirror"
        self.local.mkdir()
        self.manager = object.__new__(SavegamesManager)
        self.manager.parent = None
        self.manager.installed_games = {}
        self.manager.app_config = {"savegames": {"sync_folder": str(self.mirror)}}
        self.manager.get_sync_folder = lambda: str(self.local)

    def tearDown(self):
        self.tmp.cleanup()

    def test_mirror_folder_read_from_config(self):
        self.assertEqual(self.manager.get_mirror_folder(), str(self.mirror))

    def test_import_is_one_time_and_non_destructive(self):
        write_game(self.mirror, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")

        first = self.manager.ensure_mirror_import()
        self.assertTrue(first["copied"] > 0)
        self.assertTrue((self.local / "Game_A" / "savegames.json").is_file())

        (self.local / "Game_A" / "savegames.json").write_text("local", encoding="utf-8")
        second = self.manager.ensure_mirror_import()
        self.assertEqual(second["copied"], 0)
        self.assertEqual((self.local / "Game_A" / "savegames.json").read_text(encoding="utf-8"), "local")

    def test_push_mirror_without_configuration(self):
        self.manager.app_config = {"savegames": {"sync_folder": ""}}
        report = self.manager.push_mirror()
        self.assertFalse(report["configured"])

    def test_compare_and_test_without_mirror(self):
        self.manager.app_config = {"savegames": {"sync_folder": ""}}
        self.assertEqual(self.manager.compare_mirror(), [])
        ok, error = self.manager.test_mirror()
        self.assertFalse(ok)
        self.assertIn("not configured", error.lower())

    def test_push_mirror_updates_manifest(self):
        write_game(self.local, "Game_A", "Game A", ["20260101_120000"], "20260101_120000")
        report = self.manager.push_mirror()
        self.assertTrue(report["configured"])
        self.assertEqual(report["errors"], [])
        self.assertTrue((self.mirror / "backup_manifest.json").is_file())


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.local = self.root / "Staging"
        self.mirror = self.root / "Mirror"
        self.local.mkdir()
        self.backend = FolderMirrorBackend(str(self.local), str(self.mirror))

    def tearDown(self):
        self.tmp.cleanup()

    def test_incomplete_local_snapshot_is_not_published(self):
        write_game(self.local, "Game_A", "Game A",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        (self.local / "Game_A" / "saves" / "20260102_120000"
         / "snapshot.json").write_text(
            json.dumps({"schema": 1, "complete": False}), encoding="utf-8")

        report = self.backend.push()

        self.assertEqual(report["errors"], [])
        self.assertTrue(
            (self.mirror / "Game_A" / "saves" / "20260101_120000").is_dir())
        self.assertFalse(
            (self.mirror / "Game_A" / "saves" / "20260102_120000").exists())

        states = {entry["safe_title"]: entry for entry in self.backend.compare()}
        self.assertEqual(states["Game_A"]["local_latest"], "20260102_120000")
        self.assertEqual(states["Game_A"]["mirror_latest"], "20260101_120000")

        payload = self.backend.write_manifest()
        self.assertEqual(payload["games"]["Game_A"]["latest_snapshot"],
                         "20260101_120000")

    def test_incomplete_and_partial_mirror_snapshots_are_not_imported(self):
        write_game(self.mirror, "Game_A", "Game A",
                   ["20260101_120000"], "20260101_120000")
        write_game(self.mirror, "Game_B", "Game B",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        (self.mirror / "Game_B" / "saves" / "20260102_120000"
         / "snapshot.json").write_text(
            json.dumps({"schema": 1, "complete": False}), encoding="utf-8")

        # Game_C: the local snapshot has an extra file, so the mirror copy is
        # only a partial publication and must not be imported.
        write_game(self.local, "Game_C", "Game C",
                   ["20260101_120000"], "20260101_120000")
        write_game(self.mirror, "Game_C", "Game C",
                   ["20260101_120000"], "20260101_120000")
        (self.local / "Game_C" / "saves" / "20260101_120000"
         / "extra.sav").write_text("only local", encoding="utf-8")

        self.backend.pull_missing()

        self.assertTrue(
            (self.local / "Game_A" / "saves" / "20260101_120000" / "save.dat").is_file())
        self.assertTrue(
            (self.local / "Game_B" / "saves" / "20260101_120000" / "save.dat").is_file())
        self.assertFalse(
            (self.local / "Game_B" / "saves" / "20260102_120000").exists())
        # The partial Game_C snapshot was not touched or completed locally.
        self.assertTrue(
            (self.local / "Game_C" / "saves" / "20260101_120000" / "extra.sav").is_file())

    def test_failed_push_keeps_the_manifest_and_retry_completes(self):
        manager = object.__new__(SavegamesManager)
        manager.parent = None
        manager.installed_games = {}
        manager.app_config = {"savegames": {"sync_folder": str(self.mirror)}}
        manager.get_sync_folder = lambda: str(self.local)
        manager._device_id = lambda root: "device"
        write_game(self.local, "Game_A", "Game A",
                   ["20260101_120000"], "20260101_120000")
        manager.push_mirror()
        manifest_path = self.mirror / "backup_manifest.json"
        self.assertTrue(manifest_path.is_file())
        before = manifest_path.read_bytes()

        write_game(self.local, "Game_A", "Game A",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        snapshot = self.local / "Game_A" / "saves" / "20260102_120000"
        for index in range(3):
            (snapshot / f"extra{index}.sav").write_text("x", encoding="utf-8")

        real_copy = FolderMirrorBackend._copy_atomic
        calls = {"count": 0}

        def failing_copy(source, target):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected failure")
            return real_copy(source, target)

        with mock.patch.object(FolderMirrorBackend, "_copy_atomic",
                               side_effect=failing_copy):
            report = manager.push_mirror()

        self.assertTrue(report["errors"])
        self.assertGreaterEqual(calls["count"], 2)
        self.assertEqual(manifest_path.read_bytes(), before)
        # The file whose copy failed is still missing on the mirror.
        self.assertFalse(
            (self.mirror / "Game_A" / "saves" / "20260102_120000"
             / "extra0.sav").exists())

        # Retry reuses push() and only then is the snapshot advertised.
        report = manager.push_mirror()
        self.assertEqual(report["errors"], [])
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["games"]["Game_A"]["latest_snapshot"],
                         "20260102_120000")

    def test_game_only_on_the_mirror_appears_in_the_manifest(self):
        write_game(self.mirror, "MirrorOnly", "Mirror Only",
                   ["20260101_120000"], "20260101_120000")

        payload = self.backend.write_manifest()

        self.assertIn("MirrorOnly", payload["games"])
        self.assertEqual(payload["games"]["MirrorOnly"]["latest_snapshot"],
                         "20260101_120000")

    def test_latest_snapshot_skips_the_unpublished_newest(self):
        write_game(self.mirror, "Game_A", "Game A",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        write_game(self.local, "Game_A", "Game A",
                   ["20260101_120000", "20260102_120000"], "20260102_120000")
        (self.local / "Game_A" / "saves" / "20260102_120000"
         / "extra.sav").write_text("local only", encoding="utf-8")

        payload = self.backend.write_manifest()

        self.assertEqual(payload["games"]["Game_A"]["latest_snapshot"],
                         "20260101_120000")

    @unittest.skipIf(os.name == "nt", "symlinks need privileges on Windows")
    def test_symlinked_snapshot_is_verified_by_target(self):
        write_game(self.local, "Game_A", "Game A",
                   ["20260101_120000"], "20260101_120000")
        os.symlink("save.dat",
                   self.local / "Game_A" / "saves" / "20260101_120000" / "link.sav")
        self.backend.push()
        self.assertIn("Game_A", self.backend.write_manifest()["games"])

        mirror_link = (self.mirror / "Game_A" / "saves" / "20260101_120000"
                       / "link.sav")
        self.assertTrue(mirror_link.is_symlink())
        os.remove(mirror_link)
        os.symlink("snapshot.json", mirror_link)

        self.assertNotIn("Game_A", self.backend.write_manifest()["games"])


if __name__ == "__main__":
    unittest.main()


class ManagerMirrorFailureTests(unittest.TestCase):
    def _manager(self):
        from managers.save_manager import SavegamesManager
        manager = object.__new__(SavegamesManager)
        manager.get_sync_folder = lambda: "/staging"
        manager.get_mirror_folder = lambda: "/mirror"
        manager._device_id = lambda root: "device"
        manager.ensure_mirror_import = lambda: {}
        return manager

    def test_partial_push_failure_keeps_the_previous_manifest(self):
        manager = self._manager()
        class Backend:
            def __init__(self, *args, **kwargs):
                pass
            def push(self, exclude_names=(), should_cancel=None):
                return {"configured": True, "copied": 1, "skipped": 0,
                        "cancelled": False, "errors": ["one file failed"]}
            def write_manifest(self, device_id=""):
                raise AssertionError("manifest must not be rewritten after a partial push")
        with mock.patch("managers.mirror_backend.FolderMirrorBackend", Backend):
            report = manager.push_mirror()
        self.assertEqual(report["errors"], ["one file failed"])

    def test_clean_push_writes_the_manifest(self):
        manager = self._manager()
        called = []
        class Backend:
            def __init__(self, *args, **kwargs):
                pass
            def push(self, exclude_names=(), should_cancel=None):
                return {"configured": True, "copied": 1, "skipped": 0,
                        "cancelled": False, "errors": []}
            def write_manifest(self, device_id=""):
                called.append(device_id)
        with mock.patch("managers.mirror_backend.FolderMirrorBackend", Backend):
            manager.push_mirror()
        self.assertEqual(called, ["device"])

    def test_cancelled_push_does_not_start_or_write_the_manifest(self):
        manager = self._manager()
        manager.request_cancel()
        class Backend:
            def __init__(self, *args, **kwargs):
                pass
            def push(self, exclude_names=(), should_cancel=None):
                raise AssertionError("a cancelled push must not start")
            def write_manifest(self, device_id=""):
                raise AssertionError("no manifest after cancellation")
        with mock.patch("managers.mirror_backend.FolderMirrorBackend", Backend):
            report = manager.push_mirror()
        self.assertTrue(report["cancelled"])
        self.assertEqual(report["copied"], 0)

    def test_partial_push_cancel_keeps_the_previous_manifest(self):
        manager = self._manager()
        class Backend:
            def __init__(self, *args, **kwargs):
                pass
            def push(self, exclude_names=(), should_cancel=None):
                return {"configured": True, "copied": 1, "skipped": 0,
                        "cancelled": True, "errors": []}
            def write_manifest(self, device_id=""):
                raise AssertionError("manifest must not be rewritten after cancel")
        with mock.patch("managers.mirror_backend.FolderMirrorBackend", Backend):
            report = manager.push_mirror()
        self.assertTrue(report["cancelled"])
        self.assertEqual(report["copied"], 1)
