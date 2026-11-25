"""BackupService failure paths: the UI must receive a result, not an exception."""
import unittest
from pathlib import Path
from unittest import mock

from managers.backup_service import BackupService


class _State:
    root = Path("/tmp/launcher-state")

    def __init__(self, snapshot=None, error=None):
        self._snapshot = snapshot
        self._error = error
        self.restore_calls = 0

    def snapshot(self):
        if self._error is not None:
            raise self._error
        return self._snapshot

    def restore_merge(self):
        self.restore_calls += 1
        return {"files": 2, "jsonl_added": 1, "achievements_added": 1}

    def status(self):
        return {"root": str(self.root), "files": 2, "updated_at": 10}


class _Savegames:
    installed_games = {"A": {"title": "A"}, "B": {"title": "B"}}
    mirror_calls = 0

    def __init__(self, fail_titles=()):
        self.fail_titles = set(fail_titles)
        self.requests = []

    def backup_on_demand(self, meta):
        title = meta["title"]
        self.requests.append(title)
        if title in self.fail_titles:
            raise OSError(f"disk full for {title}")
        return title == "A"

    def push_mirror(self):
        self.mirror_calls += 1
        return {"copied": 1}

    def try_push_mirror(self, timeout_ms=0):
        self.mirror_calls += 1
        return {"status": "completed", "report": {"copied": 1}}

    def list_snapshots(self, title):
        return [{"id": "20260101_120000"}]

    def get_source_save_path(self, meta):
        return "/tmp/saves"

    def restore_snapshot(self, meta, snapshot_id, create_missing=False):
        return {"ok": True, "snapshot": snapshot_id, "create_missing": create_missing}


class BackupStateTests(unittest.TestCase):
    def test_state_not_configured(self):
        service = BackupService(_Savegames())

        self.assertEqual(service.backup_state()["reason"], "not_configured")
        self.assertEqual(service.restore_state()["reason"], "not_configured")
        self.assertEqual(service.state_status()["root"], "")

    def test_snapshot_error_is_returned(self):
        service = BackupService(_Savegames(), _State(error=OSError("disk full")))

        result = service.backup_state()

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "error")
        self.assertIn("disk full", result["detail"])

    def test_snapshot_missing_is_not_ok(self):
        service = BackupService(_Savegames(), _State(snapshot=None))

        result = service.backup_state(push=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["snapshot"], "")

    def test_successful_snapshot_and_mirror(self):
        savegames = _Savegames()
        service = BackupService(savegames, _State(snapshot=Path("/tmp/state/1")))

        result = service.backup_state(push=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["snapshot"], "1")
        self.assertEqual(result["mirror"],
                         {"copied": 1, "status": "completed"})
        self.assertEqual(savegames.mirror_calls, 1)

    def test_restore_delegates(self):
        state = _State()
        service = BackupService(_Savegames(), state)

        result = service.restore_state()

        self.assertEqual(state.restore_calls, 1)
        self.assertEqual(result["jsonl_added"], 1)


class BackupSavegamesTests(unittest.TestCase):
    def test_titles_are_filtered_and_counted(self):
        savegames = _Savegames()
        service = BackupService(savegames)

        report = service.backup_savegames(["A", "B", "missing"])

        self.assertEqual(report["synced_games"], 1)
        self.assertEqual(report["errors"], [])
        self.assertEqual(savegames.requests, ["A", "B"])

    def test_one_failure_does_not_stop_the_rest(self):
        savegames = _Savegames(fail_titles={"B"})
        service = BackupService(savegames)

        report = service.backup_savegames(["A", "B"])

        self.assertEqual(report["synced_games"], 1)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("B", report["errors"][0])
        self.assertEqual(savegames.requests, ["A", "B"])

    def test_restore_game_delegates(self):
        service = BackupService(_Savegames())

        result = service.restore_game(
            {"title": "A"}, "20260101_120000", create_missing=True
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["create_missing"])


if __name__ == "__main__":
    unittest.main()
