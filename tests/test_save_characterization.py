"""Additional Savegames contracts used while G3 splits the manager.

These tests cover the parts not already exercised by the save/mirror/restore
suites: the manifest fetch/ETag lifecycle and the exact schema-2 key set. They
describe behaviour that the extraction must preserve, not new behaviour.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from managers.save_manifest import LudusaviManifest
from managers.save_manager import SavegamesManager


def make_manager():
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {}
    manager.installed_games = {}
    return manager


class ManifestFetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = make_manager()
        self.manager.manifest_store = LudusaviManifest(
            str(self.root / "ludusavi_manifest.yaml"),
            str(self.root / "ludusavi_etag.txt"),
        )
        self.manifest_path = self.manager.manifest_store.manifest_path
        self.etag_path = self.manager.manifest_store.etag_path

    def test_download_writes_manifest_and_etag(self):
        response = mock.Mock(status_code=200, headers={"ETag": '"abc"'})
        response.iter_content.return_value = [b"New Game:\n  files: {}\n"]
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response):
            self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "New Game:\n  files: {}\n",
        )
        self.assertEqual(
            Path(self.etag_path).read_text(encoding="utf-8"), '"abc"'
        )

    def test_304_keeps_the_existing_manifest(self):
        Path(self.manifest_path).write_text("Old Game:\n", encoding="utf-8")
        Path(self.etag_path).write_text('"old"', encoding="utf-8")
        response = mock.Mock(status_code=304, headers={})
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response):
            self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "Old Game:\n",
        )
        self.assertEqual(
            Path(self.etag_path).read_text(encoding="utf-8"), '"old"'
        )

    def test_invalid_download_keeps_the_previous_manifest(self):
        Path(self.manifest_path).write_text("Old Game:\n  files: {}\n",
                                           encoding="utf-8")
        Path(self.etag_path).write_text('"old"', encoding="utf-8")
        response = mock.Mock(status_code=200, headers={"ETag": '"new"'})
        response.iter_content.return_value = [b"not: [valid: yaml\n"]
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response):
            with self.assertLogs("managers.save_manifest", level="WARNING"):
                self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "Old Game:\n  files: {}\n",
        )
        self.assertEqual(
            Path(self.etag_path).read_text(encoding="utf-8"), '"old"'
        )

    def test_empty_mapping_does_not_replace_the_previous_manifest(self):
        self._assert_invalid_structure(b"{}")

    def test_flat_mapping_does_not_replace_the_previous_manifest(self):
        self._assert_invalid_structure(b"foo: bar\n")

    def test_valid_yaml_list_does_not_replace_the_previous_manifest(self):
        self._assert_invalid_structure(b"- one\n- two\n")

    def _assert_invalid_structure(self, payload):
        Path(self.manifest_path).write_text("Old Game:\n  files: {}\n",
                                           encoding="utf-8")
        Path(self.etag_path).write_text('"old"', encoding="utf-8")
        response = mock.Mock(status_code=200, headers={"ETag": '"new"'})
        response.iter_content.return_value = [payload]
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response):
            with self.assertLogs("managers.save_manifest", level="WARNING"):
                self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "Old Game:\n  files: {}\n",
        )
        self.assertEqual(
            Path(self.etag_path).read_text(encoding="utf-8"), '"old"'
        )

    def test_invalid_download_without_a_previous_manifest_returns_false(self):
        response = mock.Mock(status_code=200, headers={})
        response.iter_content.return_value = [b"not: [valid: yaml\n"]
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response):
            with self.assertLogs("managers.save_manifest", level="WARNING"):
                self.assertFalse(self.manager.manifest_store.ensure(force=True))
        self.assertFalse(Path(self.manifest_path).exists())

    def test_network_error_keeps_the_existing_manifest(self):
        Path(self.manifest_path).write_text("Old Game:\n", encoding="utf-8")
        with mock.patch("managers.save_manifest.requests.get",
                        side_effect=OSError("offline")):
            with self.assertLogs("managers.save_manifest", level="ERROR"):
                self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "Old Game:\n",
        )

    def test_etag_write_failure_keeps_the_manifest_and_the_old_etag(self):
        Path(self.etag_path).write_text('"old"', encoding="utf-8")
        response = mock.Mock(status_code=200, headers={"ETag": '"new"'})
        response.iter_content.return_value = [b"New Game:\n  files: {}\n"]
        with mock.patch("managers.save_manifest.requests.get",
                        return_value=response), mock.patch(
                "managers.save_manifest.atomic_write_text",
                side_effect=OSError("read-only")):
            with self.assertLogs("managers.save_manifest", level="WARNING"):
                self.assertTrue(self.manager.manifest_store.ensure(force=True))
        self.assertEqual(
            Path(self.manifest_path).read_text(encoding="utf-8"),
            "New Game:\n  files: {}\n",
        )
        self.assertEqual(
            Path(self.etag_path).read_text(encoding="utf-8"), '"old"'
        )

    def test_network_error_without_a_manifest_returns_false(self):
        with mock.patch("managers.save_manifest.requests.get",
                        side_effect=OSError("offline")):
            with self.assertLogs("managers.save_manifest", level="ERROR"):
                self.assertFalse(self.manager.manifest_store.ensure(force=True))


class ManifestSchemaContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.staging = Path(self.tmp.name)
        self.game_root = self.staging / "My_Game"
        self.snapshot = self.game_root / "saves" / "20260101_120000"
        self.snapshot.mkdir(parents=True)
        (self.snapshot / "slot1.sav").write_text("state", encoding="utf-8")
        (self.snapshot / "snapshot.json").write_text(json.dumps({
            "created_at": "2026-01-01T12:00:00Z",
            "version": {"build": 2},
            "reason": "sync",
        }), encoding="utf-8")
        self.manager = make_manager()

    def test_schema_two_has_exactly_the_expected_keys(self):
        manifest = self.manager._build_game_manifest(
            str(self.game_root), "My Game",
            {"playtime_seconds": 60, "build": 2},
            snapshot_folder=str(self.snapshot),
        )
        self.assertEqual(manifest["schema"], 2)
        self.assertEqual(set(manifest), {
            "schema", "game_title", "safe_title", "device_id",
            "last_synced", "last_restore_at", "latest_snapshot", "snapshots",
            "source_path", "install_dir", "steam_grid_id", "game_version",
            "savegame_path_win", "savegame_path_linux", "savegame_path_custom",
            "savegame_path_source", "playtime_seconds", "playtime_hours",
            "playtime_only", "snapshot_folder",
        })
        self.assertEqual(manifest["latest_snapshot"],
                         "saves/20260101_120000")
        self.assertEqual(manifest["snapshot_folder"],
                         "saves/20260101_120000")


if __name__ == "__main__":
    unittest.main()
