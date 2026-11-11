import json
import tempfile
import unittest
from pathlib import Path

from core.jsonl_store import JsonlStore
from managers.state_backup_manager import StateBackupManager


class StateBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.state_dir = root / "stats"
        self.app_data_dir = root / "app"
        self.sync_dir = root / "sync"
        self.state_dir.mkdir()
        self.app_data_dir.mkdir()
        self.sync_dir.mkdir()
        self.manager = StateBackupManager(
            state_dir=self.state_dir,
            app_data_dir=self.app_data_dir,
            sync_folder=self.sync_dir,
            keep_snapshots=2,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _remote(self, name):
        return self.manager.current_dir / name

    def _write_remote_json(self, name, data):
        self.manager.current_dir.mkdir(parents=True, exist_ok=True)
        (self._remote(name)).write_text(json.dumps(data), encoding="utf-8")

    def test_write_current_mirrors_state(self):
        JsonlStore(self.state_dir / "play_sessions.jsonl").append(
            {"id": "s1", "title": "Hades", "seconds": 60}
        )
        (self.state_dir / "stats_prefs.json").write_text('{"region":"IT"}', encoding="utf-8")

        copied = self.manager.write_current()
        self.assertEqual(copied, 2)
        self.assertTrue((self.manager.current_dir / "play_sessions.jsonl").exists())
        self.assertTrue((self.manager.current_dir / "stats_prefs.json").exists())
        manifest = json.loads((self.manager.current_dir / "manifest.json").read_text())
        self.assertTrue(manifest["device_id"])
        self.assertEqual(manifest["schema"], 1)

    def test_restore_merges_sessions_and_keeps_longest_end(self):
        local = JsonlStore(self.state_dir / "play_sessions.jsonl")
        local.append_many([
            {"id": "a", "title": "A", "start": 10, "end": 100, "seconds": 90},
            {"id": "b", "title": "B", "start": 100, "end": 200, "seconds": 100},
        ])
        remote = JsonlStore(self._remote("play_sessions.jsonl"))
        remote.append_many([
            {"id": "a", "title": "A", "start": 10, "end": 150, "seconds": 140},
            {"id": "c", "title": "C", "start": 200, "end": 300, "seconds": 100},
        ])

        result = self.manager.restore_merge()
        self.assertEqual(result["jsonl_added"], 1)

        records = {r["id"]: r for r in local.load()}
        self.assertEqual(len(records), 3)
        self.assertEqual(records["a"]["end"], 150)
        self.assertEqual(records["b"]["end"], 200)
        self.assertEqual(records["c"]["end"], 300)

        again = self.manager.restore_merge()
        self.assertEqual(again["jsonl_added"], 0)
        self.assertEqual(len(local.load()), 3)

    def test_achievements_merge_uses_earliest_unlock_and_max_progress(self):
        local_path = self.app_data_dir / "achievements.json"
        local_path.write_text(json.dumps({
            "schema": 1,
            "reset_at": 10,
            "unlocked": {"x": {"at": 100}},
            "progress": {"p": {"value": 1}},
        }), encoding="utf-8")
        self._write_remote_json("achievements.json", {
            "schema": 2,
            "reset_at": 20,
            "unlocked": {"x": {"at": 50}, "y": {"at": 200}},
            "progress": {"p": {"value": 3}, "q": {"value": 2}},
        })

        result = self.manager.restore_merge()
        self.assertEqual(result["achievements_added"], 1)

        merged = json.loads(local_path.read_text(encoding="utf-8"))
        self.assertEqual(merged["schema"], 2)
        self.assertEqual(merged["reset_at"], 20)
        self.assertEqual(merged["unlocked"]["x"]["at"], 50)
        self.assertIn("y", merged["unlocked"])
        self.assertEqual(merged["progress"]["p"]["value"], 3)
        self.assertEqual(merged["progress"]["q"]["value"], 2)

    def test_prefs_local_wins_but_region_is_imported(self):
        local_path = self.state_dir / "stats_prefs.json"
        local_path.write_text(json.dumps({"range": "365d", "region": ""}), encoding="utf-8")
        self._write_remote_json("stats_prefs.json", {
            "region": "IT",
            "range": "30d",
            "cinema_rate": 8.0,
        })

        self.manager.restore_merge()
        merged = json.loads(local_path.read_text(encoding="utf-8"))
        self.assertEqual(merged["region"], "IT")
        self.assertEqual(merged["range"], "365d")
        self.assertEqual(merged["cinema_rate"], 8.0)

    def test_snapshot_rotation(self):
        JsonlStore(self.state_dir / "play_sessions.jsonl").append(
            {"id": "s1", "title": "Hades", "seconds": 60}
        )
        first = self.manager.snapshot(now=1730000000)
        second = self.manager.snapshot(now=1730003600)
        third = self.manager.snapshot(now=1730007200)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third.exists())
        snapshots = [p for p in self.manager.snapshots_dir.iterdir() if p.is_dir()]
        self.assertEqual(len(snapshots), 2)

    def test_enabled_for_config(self):
        self.assertTrue(StateBackupManager.enabled_for({
            "savegames": {"sync_folder": "D:/Sync"}
        }))
        self.assertFalse(StateBackupManager.enabled_for({
            "savegames": {"sync_folder": ""}
        }))
        self.assertFalse(StateBackupManager.enabled_for({
            "savegames": {"sync_folder": "D:/Sync", "include_launcher_state": False}
        }))


if __name__ == "__main__":
    unittest.main()
