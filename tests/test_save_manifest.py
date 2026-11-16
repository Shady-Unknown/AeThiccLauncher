import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers.save_manager import SavegamesManager, _utc_from_snapshot_name


def make_manager():
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {}
    manager.installed_games = {}
    return manager


class GameManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self.tmp.name)
        self.game_root = self.staging / "My_Game"
        self.game_root.mkdir()
        self.manager = make_manager()

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, name="20260101_120000", info=None):
        folder = self.game_root / "saves" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "save.dat").write_text("data", encoding="utf-8")
        if info is not None:
            (folder / "snapshot.json").write_text(json.dumps(info), encoding="utf-8")
        return folder

    def read_manifest(self):
        return json.loads((self.game_root / "savegames.json").read_text(encoding="utf-8"))

    def test_manifest_schema_and_snapshot_list(self):
        snap = self.snapshot(info={
            "created_at": "2026-01-01T12:00:00Z",
            "device_id": "device-1",
            "version": {"build": 25922370, "label": "Build 25922370"},
            "reason": "sync",
        })
        meta = {
            "version": "Build 25922370",
            "build": 25922370,
            "build_date": "16 October 2026",
            "source_url": "https://example.test/game",
            "steam_grid_id": "42",
            "savegame_path_win": "%userprofile%\\Documents\\Game\\Saves",
            "savegame_path_source": "manifest",
            "playtime_seconds": 7200,
        }

        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", meta, snapshot_folder=str(snap)
        )

        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["latest_snapshot"], "saves/20260101_120000")
        self.assertEqual(manifest["snapshot_folder"], "saves/20260101_120000")
        self.assertEqual(manifest["snapshots"][0]["id"], "20260101_120000")
        self.assertEqual(manifest["snapshots"][0]["version"]["build"], 25922370)
        self.assertEqual(manifest["snapshots"][0]["reason"], "sync")
        self.assertFalse(manifest["playtime_only"])
        self.assertEqual(manifest["game_version"]["build"], 25922370)
        self.assertEqual(manifest["savegame_path_source"], "manifest")
        self.assertTrue((self.staging / ".launcher_device").is_file())

    def test_snapshot_without_metadata_uses_folder_time(self):
        self.snapshot(name="20260101_120000")
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {"playtime_seconds": 0}
        )
        self.assertEqual(manifest["snapshots"][0]["created_at"], "2026-01-01T12:00:00Z")

    def test_playtime_only_manifest(self):
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {"playtime_seconds": 3600}
        )
        self.assertTrue(manifest["playtime_only"])
        self.assertEqual(manifest["latest_snapshot"], "")
        self.assertEqual(manifest["snapshots"], [])

    def test_last_synced_only_moves_with_a_source(self):
        old = {"last_synced": "2020-01-01T00:00:00Z", "source_path": "D:/old"}
        preserved = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {}, old=old
        )
        self.assertEqual(preserved["last_synced"], "2020-01-01T00:00:00Z")

        refreshed = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {}, source_path="D:/new", old=old
        )
        self.assertNotEqual(refreshed["last_synced"], "2020-01-01T00:00:00Z")
        self.assertTrue(refreshed["last_synced"].endswith("Z"))

    def test_write_is_atomic(self):
        path = self.game_root / "savegames.json"
        self.manager._write_game_manifest_file(str(self.game_root), {"schema": 2})
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"schema": 2})
        self.assertFalse((self.game_root / "savegames.json.tmp").exists())

    def test_freshen_migrates_v1_manifest(self):
        self.snapshot(name="20260101_120000")
        v1 = {
            "game_title": "My Game",
            "source_path": "D:/saves",
            "last_synced": "2026-11-02 10:00:00",
            "snapshot_folder": "saves/20260101_120000",
            "playtime_seconds": 60,
            "playtime_only": False,
        }
        (self.game_root / "savegames.json").write_text(json.dumps(v1), encoding="utf-8")

        self.manager._freshen_json(str(self.game_root), "My Game", {"playtime_seconds": 60})

        manifest = self.read_manifest()
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(manifest["last_synced"], "2026-11-02 10:00:00")
        self.assertEqual(manifest["source_path"], "D:/saves")
        self.assertEqual(manifest["latest_snapshot"], "saves/20260101_120000")
        self.assertEqual(manifest["playtime_seconds"], 60)


class RescanPolicyTests(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.manager.save_meta = mock.Mock()

    def test_user_path_survives_missing_manifest(self):
        meta = {
            "title": "inZOI",
            "install_dir": "C:/Games/inZOI",
            "savegame_path_win": "%userprofile%\\My Documents\\inZOI\\SaveGames",
            "savegame_path_source": "user",
        }
        with mock.patch.object(self.manager, "find_manifest_entry", return_value=None):
            result = self.manager.rescan_save_folder(meta)

        self.assertEqual(result["savegame_path_win"], "%userprofile%\\My Documents\\inZOI\\SaveGames")
        self.assertEqual(result["savegame_path_source"], "user")
        self.manager.save_meta.assert_called()

    def test_user_path_is_not_overwritten_by_manifest(self):
        meta = {
            "title": "Game",
            "savegame_path_win": "%userprofile%\\Manual\\Saves",
            "savegame_path_source": "user",
        }
        entry = {"files": {"%appdata%\\Game\\Saves": {"tags": ["save"]}}}
        with mock.patch.object(self.manager, "find_manifest_entry", return_value=entry):
            result = self.manager.rescan_save_folder(meta)

        self.assertEqual(result["savegame_path_win"], "%userprofile%\\Manual\\Saves")
        self.assertEqual(result["savegame_path_source"], "user")

    def test_legacy_path_is_marked_as_user_instead_of_deleted(self):
        meta = {
            "title": "inZOI",
            "savegame_path_win": "%userprofile%\\My Documents\\inZOI\\SaveGames",
        }
        with mock.patch.object(self.manager, "find_manifest_entry", return_value=None):
            result = self.manager.rescan_save_folder(meta)

        # Kept (not deleted) and canonicalized; the flag tells the next rescan
        # that this path was chosen by the user.
        self.assertEqual(
            result["savegame_path_win"],
            "%userprofile%\\Documents\\inZOI\\SaveGames",
        )
        self.assertEqual(result["savegame_path_source"], "user")

    def test_manifest_path_is_refreshed(self):
        meta = {"title": "Game"}
        entry = {"files": {"%appdata%\\Game\\Saves": {"tags": ["save"]}}}
        with mock.patch.object(self.manager, "find_manifest_entry", return_value=entry):
            result = self.manager.rescan_save_folder(meta)

        self.assertEqual(result["savegame_path_win"], "%appdata%\\Game\\Saves")
        self.assertEqual(result["savegame_path_source"], "manifest")


class EnsureSavePathTests(unittest.TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.manager.save_meta = mock.Mock()

    def test_manifest_path_is_marked(self):
        entry = {"files": {"%appdata%\\Game\\Saves": {"tags": ["save"]}}}
        meta = {"title": "Game", "install_dir": "C:/Games/Game"}

        with mock.patch.object(self.manager, "find_manifest_entry", return_value=entry):
            result = self.manager.ensure_save_path_for_game(meta)

        self.assertEqual(result["savegame_path_win"], "%appdata%\\Game\\Saves")
        self.assertEqual(result["savegame_path_source"], "manifest")

    def test_manual_path_is_marked_user(self):
        meta = {"title": "inZOI", "install_dir": "C:/Games/inZOI"}
        with mock.patch.object(self.manager, "find_manifest_entry", return_value=None), \
                mock.patch.object(
                    self.manager, "prompt_manual_save_path",
                    return_value="%userprofile%\\My Documents\\inZOI\\SaveGames",
                ):
            result = self.manager.ensure_save_path_for_game(meta)

        self.assertEqual(result["savegame_path_win"], "%userprofile%\\My Documents\\inZOI\\SaveGames")
        self.assertEqual(result["savegame_path_source"], "user")

    def test_existing_path_is_left_untouched(self):
        meta = {"title": "Game", "savegame_path_win": "%appdata%\\Keep\\Saves"}
        with mock.patch.object(self.manager, "find_manifest_entry") as find:
            result = self.manager.ensure_save_path_for_game(meta)

        self.assertEqual(result["savegame_path_win"], "%appdata%\\Keep\\Saves")
        find.assert_not_called()


class SnapshotNameTests(unittest.TestCase):
    def test_utc_from_folder_name(self):
        self.assertEqual(_utc_from_snapshot_name("20260101_120000"), "2026-01-01T12:00:00Z")
        self.assertEqual(_utc_from_snapshot_name("20260101_120000_1"), "2026-01-01T12:00:00Z")

    def test_invalid_name_is_empty(self):
        self.assertEqual(_utc_from_snapshot_name("manual-copy"), "")


if __name__ == "__main__":
    unittest.main()
