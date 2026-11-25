import json
import tempfile
import unittest
from pathlib import Path

from tests.qt_app import ensure_app
from ui.library_scan_worker import LibraryScanWorker, scan_library_data

_app = ensure_app()


def _write_meta(root, folder, title, **extra):
    game = Path(root) / folder
    game.mkdir(parents=True)
    payload = {"title": title, "version": "Build 1", **extra}
    (game / "steamrip_meta.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return game


class ScanLibraryDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_reads_games_and_orphans(self):
        _write_meta(self.root, "Game_A", "Game A")
        (self.root / "No_Meta").mkdir()

        entries = dict(scan_library_data([str(self.root)]))

        self.assertEqual(entries["Game A"]["version"], "Build 1")
        self.assertTrue(entries["No_Meta"]["is_orphan"])

    def test_duplicate_title_keeps_the_more_complete_meta(self):
        _write_meta(self.root, "A", "Same")
        _write_meta(self.root, "B", "Same", steam_shortcut_id="42")

        entries = dict(scan_library_data([str(self.root)]))

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries["Same"]["steam_shortcut_id"], "42")

    def test_missing_folder_is_ignored(self):
        self.assertEqual(scan_library_data([str(self.root / "nope")]), [])

    def test_worker_emits_entries(self):
        _write_meta(self.root, "Game_A", "Game A")
        worker = LibraryScanWorker([str(self.root)])
        received = []
        worker.finished_ok.connect(received.append)

        worker.run()

        self.assertEqual(len(received), 1)
        self.assertEqual(dict(received[0])["Game A"]["title"], "Game A")


if __name__ == "__main__":
    unittest.main()
