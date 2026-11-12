import os
import json
import tempfile
import unittest
from unittest.mock import patch

from core.meta_utils import EXTRACTING_MARKER, STEAMRIP_META_FILENAME
from core.download_queue_model import write_extraction_state
from core.archive_utils import mark_incomplete
from core.download_utils import normalize_dest_path
from ui.controllers.downloads import DownloadsController


class PendingExtractionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.install_dir = os.path.join(
            self.root, "Hollow Knight  Silksong download"
        )
        os.makedirs(self.install_dir)
        self.controller = object.__new__(DownloadsController)
        self.controller.installed_games = {
            "Hollow Knight: Silksong": {
                "install_dir": self.install_dir,
            },
            "Grounded 2": {
                "install_dir": os.path.join(self.root, "Grounded 2"),
            },
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _create_meta(self):
        with open(
            os.path.join(self.install_dir, STEAMRIP_META_FILENAME),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write('{"title": "Hollow Knight: Silksong"}')

    def _archive(self):
        return os.path.join(
            self.root, "Hollow_Knight__Silksong_download.zip"
        )

    def test_already_installed_archive_is_skipped(self):
        self._create_meta()
        self.assertTrue(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_interrupted_extraction_is_not_skipped(self):
        self._create_meta()
        with open(
            os.path.join(self.install_dir, EXTRACTING_MARKER),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("in progress")
        self.assertFalse(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_folder_without_metadata_is_not_skipped(self):
        self.assertFalse(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_unrelated_archive_is_not_skipped(self):
        self._create_meta()
        self.assertFalse(
            self.controller._archive_matches_installed(
                os.path.join(self.root, "inZOI-SteamRIP.com.rar"),
                "inZOI-SteamRIP.com",
            )
        )

    def test_explorer_search_text_strips_download_noise(self):
        self.assertEqual(
            DownloadsController._explorer_search_text(
                {"title": "inZOI-SteamRIP.com"}
            ),
            "inZOI",
        )
        self.assertEqual(
            DownloadsController._explorer_search_text(
                {"title": "Hollow Knight  Silksong download"}
            ),
            "Hollow Knight Silksong",
        )


class RecoveryQueueTests(unittest.TestCase):
    class FakeManager:
        def __init__(self, pending):
            self.pending = pending
            self.current_task = None
            self.queue = []
            self.dismissed_archives = []
            self.pending_result = None
            self.added = []

        def recover_pending_extractions(self):
            return list(self.pending)

        def remove_pending_extraction(self, archive_path):
            pass

        def set_pending_extractions(self, entries):
            self.pending_result = list(entries)

        def add_to_queue(self, task, auto_start=True):
            self.added.append((dict(task), auto_start))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.controller = object.__new__(DownloadsController)
        self.controller.installed_games = {}
        self.controller.is_task_running = False
        self.controller.temp_folder = self.root

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, name, data=b"x" * 32):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_recovery_never_prompts_and_classifies_rows(self):
        ready = self._file("Ready-Game.rar")
        broken = self._file("Broken-Game.rar")
        interrupted = self._file("Interrupted-Game.rar")
        mark_incomplete(
            broken,
            "truncated",
            meta={
                "title": "Broken Game",
                "provider": "GoFile",
                "total_bytes": 4096,
            },
        )
        write_extraction_state(interrupted, {
            "archive_path": interrupted,
            "install_dir": "/games/Interrupted Game",
            "title": "Interrupted Game",
            "pct": 61,
        })
        manager = self.FakeManager([
            {"archive_path": ready, "title": "Ready Game"},
            {"archive_path": broken, "title": "Broken Game"},
        ])
        self.controller.dl_manager = manager

        def complete(path, **_kwargs):
            return os.path.basename(path) == "Ready-Game.rar"

        with patch("ui.controllers.downloads.is_archive_complete", side_effect=complete), \
                patch("ui.controllers.downloads.QMessageBox.question") as question:
            self.controller._recover_pending_extractions()

        question.assert_not_called()
        pending_titles = [e.get("title") for e in manager.pending_result]
        self.assertIn("Ready Game", pending_titles)
        self.assertIn("Interrupted Game", pending_titles)
        interrupted_entry = next(
            e for e in manager.pending_result if e.get("title") == "Interrupted Game"
        )
        self.assertEqual(interrupted_entry["state"], "interrupted")
        self.assertEqual(interrupted_entry["pct"], 61)
        self.assertEqual(interrupted_entry["install_dir"], "/games/Interrupted Game")

        self.assertEqual(len(manager.added), 1)
        partial_task, auto_start = manager.added[0]
        self.assertFalse(auto_start)
        self.assertEqual(partial_task["title"], "Broken Game")
        self.assertEqual(partial_task["provider"], "GoFile")
        self.assertEqual(partial_task["total_bytes"], 4096)
        self.assertEqual(partial_task["phase"], "partial")

    def test_dismissed_archive_is_not_reattached(self):
        archive = self._file("Dismissed.rar")
        manager = self.FakeManager([{"archive_path": archive, "title": "Dismissed"}])
        manager.dismissed_archives = [normalize_dest_path(archive)]
        self.controller.dl_manager = manager
        with patch("ui.controllers.downloads.is_archive_complete", return_value=True):
            self.controller._recover_pending_extractions()
        self.assertNotIn(
            "Dismissed", [e.get("title") for e in manager.pending_result]
        )
        self.assertEqual(manager.added, [])


if __name__ == "__main__":
    unittest.main()
