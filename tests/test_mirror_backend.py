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


def write_game(root, safe_title, title, ids, latest="", version=None, synced="2026-01-01T00:00:00Z"):
    game = Path(root) / safe_title
    (game / "saves").mkdir(parents=True, exist_ok=True)
    for snapshot_id in ids:
        folder = game / "saves" / snapshot_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "save.dat").write_text("state " + snapshot_id, encoding="utf-8")
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
        self.assertEqual(first["copied"], 3)  # manifest + cover + save
        self.assertEqual(first["errors"], [])
        self.assertTrue((self.mirror / "Game_A" / "saves" / "20260101_120000" / "save.dat").is_file())

        second = self.backend.push()
        self.assertEqual(second["copied"], 0)
        self.assertEqual(second["skipped"], 3)

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


if __name__ == "__main__":
    unittest.main()
