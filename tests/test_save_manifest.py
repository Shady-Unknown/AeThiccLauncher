import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers.save_manifest import LudusaviManifest
from managers.save_manager import SavegamesManager, _utc_from_snapshot_name


def make_manager():
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {}
    manager.installed_games = {}
    return manager


class ManifestLookupTests(unittest.TestCase):
    def _manager(self, keys):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        manifest = root / "ludusavi_manifest.yaml"
        manifest.write_text(
            "".join(f'"{raw}":\n  files: {{}}\n' for raw in keys.values()),
            encoding="utf-8",
        )
        manager = make_manager()
        manager.manifest_store = LudusaviManifest(
            str(manifest), str(root / "ludusavi_etag.txt"))
        manager.manifest_store.ensure = mock.Mock(return_value=True)
        return manager

    def test_single_word_title_requires_an_exact_match(self):
        manager = self._manager({
            "asdivine menace": "Asdivine Menace",
            "menace": "Menace",
        })
        self.assertEqual(manager.find_manifest_entry("Menace"),
                         {"files": {}})

        manager = self._manager({"asdivine menace": "Asdivine Menace"})
        self.assertIsNone(manager.find_manifest_entry("Menace"))

    def test_multi_word_title_still_matches_an_edition_suffix(self):
        manager = self._manager({
            "doom eternal the ancient gods": "DOOM Eternal: The Ancient Gods",
        })
        self.assertEqual(manager.find_manifest_entry("DOOM Eternal"),
                         {"files": {}})


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


class LatestSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.staging = Path(self.tmp.name)
        self.game_root = self.staging / "My_Game"
        self.game_root.mkdir()
        self.manager = make_manager()

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, name):
        folder = self.game_root / "saves" / name
        folder.mkdir(parents=True)
        (folder / "slot.sav").write_text("data", encoding="utf-8")
        return folder

    def test_missing_old_latest_falls_back_to_the_newest_existing(self):
        self.snapshot("20260101_120000")
        newest = self.snapshot("20260102_120000")
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {},
            old={"latest_snapshot": "saves/20250101_000000"},
        )
        self.assertEqual(manifest["latest_snapshot"],
                         "saves/" + newest.name)
        self.assertEqual(manifest["snapshot_folder"], manifest["latest_snapshot"])

    def test_no_snapshots_clears_a_stale_latest(self):
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {},
            old={"latest_snapshot": "saves/20250101_000000"},
        )
        self.assertEqual(manifest["latest_snapshot"], "")
        self.assertEqual(manifest["snapshot_folder"], "")

    def test_explicit_snapshot_folder_is_used_when_it_exists(self):
        older = self.snapshot("20260101_120000")
        self.snapshot("20260102_120000")
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game", {},
            old={"latest_snapshot": "saves/20260102_120000"},
            snapshot_folder=str(older),
        )
        self.assertEqual(manifest["latest_snapshot"],
                         "saves/" + older.name)


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

    def test_without_prompt_callback_returns_empty(self):
        self.assertEqual(self.manager.prompt_manual_save_path("Game"), "")

    def test_injected_prompt_callback_is_used(self):
        self.manager._prompt_save_path = lambda title: "%userprofile%\\Docs\\" + title
        meta = {"title": "Game", "install_dir": "C:/Games/Game"}

        with mock.patch.object(self.manager, "find_manifest_entry", return_value=None):
            result = self.manager.ensure_save_path_for_game(meta)

        self.assertEqual(result["savegame_path_win"], "%userprofile%\\Docs\\Game")
        self.assertEqual(result["savegame_path_source"], "user")

    def test_injected_notify_callback_receives_manifest_notice(self):
        calls = []
        self.manager._notify_callback = lambda level, title, message: calls.append(
            (level, title, message))
        entry = {"files": {"%appdata%\\Game\\Saves": {"tags": ["save"]}}}
        meta = {"title": "Game", "install_dir": "C:/Games/Game"}

        with mock.patch.object(self.manager, "find_manifest_entry", return_value=entry):
            self.manager.ensure_save_path_for_game(meta)

        self.assertEqual(calls[0][0], "info")
        self.assertIn("Ludusavi", calls[0][2])


class SnapshotNameTests(unittest.TestCase):
    def test_utc_from_folder_name(self):
        self.assertEqual(_utc_from_snapshot_name("20260101_120000"), "2026-01-01T12:00:00Z")
        self.assertEqual(_utc_from_snapshot_name("20260101_120000_1"), "2026-01-01T12:00:00Z")

    def test_invalid_name_is_empty(self):
        self.assertEqual(_utc_from_snapshot_name("manual-copy"), "")


class ManifestCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = make_manager()
        self.manager.manifest_store = LudusaviManifest(
            str(self.root / "ludusavi_manifest.yaml"),
            str(self.root / "ludusavi_etag.txt"),
        )

    def _write_manifest(self, text):
        Path(self.manager.manifest_store.manifest_path).write_text(
            text, encoding="utf-8")

    def test_http_check_runs_once_per_session(self):
        self._write_manifest("Alpha Game:\n  files: {}\n")
        response = mock.Mock(status_code=304, headers={})
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response) as get:
            self.assertTrue(self.manager.manifest_store.ensure())
            self.assertTrue(self.manager.manifest_store.ensure())

        self.assertEqual(get.call_count, 1)

        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response) as get_forced:
            self.manager.manifest_store.ensure(force=True)
        self.assertEqual(get_forced.call_count, 1)

    def test_manifest_index_is_built_once(self):
        self._write_manifest(
            "Alpha Game:\n  files:\n    '%appdata%\\\\A':\n      tags: [save]\n"
            "Beta Game:\n  files: {}\n"
        )
        self.manager.manifest_store.ensure = mock.Mock(return_value=True)

        alpha = self.manager.find_manifest_entry("Alpha Game")
        beta = self.manager.find_manifest_entry("Beta Game")

        self.assertIsInstance(alpha, dict)
        self.assertIsInstance(beta, dict)
        self.assertEqual(self.manager.manifest_store.ensure.call_count, 1)

    def test_fallback_substring_still_matches(self):
        self._write_manifest(
            "The Alpha Game Deluxe Edition:\n  files:\n"
            "    '%appdata%\\\\A':\n      tags: [save]\n"
        )
        self.manager.manifest_store.ensure = mock.Mock(return_value=True)

        entry = self.manager.find_manifest_entry("Alpha Game")

        self.assertIsInstance(entry, dict)

    def test_new_manifest_invalidates_the_index(self):
        self._write_manifest("Old Game:\n  files: {}\n")
        not_modified = mock.Mock(status_code=304, headers={})
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=not_modified):
            self.manager.manifest_store.ensure()
        self.manager.find_manifest_entry("Old Game")
        self.assertIsNotNone(self.manager.manifest_store._index_cache)

        response = mock.Mock(status_code=200, headers={})
        response.iter_content.return_value = [b"New Game:\n  files: {}\n"]
        with mock.patch("managers.save_manifest.requests.get", return_value=response):
            self.manager.manifest_store.ensure(force=True)

        self.assertIsNone(self.manager.manifest_store._index_cache)
        self.assertEqual(self.manager.manifest_store._entry_cache, {})


if __name__ == "__main__":
    unittest.main()
