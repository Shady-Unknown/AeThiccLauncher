"""C5: savegames tab data collection runs off the GUI thread."""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication

from tests.qt_app import ensure_app
from managers.save_manager import SavegamesManager
from ui.tabs import savegames_tab as savegames_tab_module
from ui.tabs.savegames_tab import SavegamesTab


class _FakeManager:
    def __init__(self, sync_folder, mirror=""):
        self.sync_folder = str(sync_folder)
        self.mirror = str(mirror or "")
        self.compare_status = "completed"
        self.import_status = "completed"
        self.entries = []
        self.compare_threads = []
        self.import_threads = []
        self.stat_error = None

    def get_sync_folder(self):
        return self.sync_folder

    def get_mirror_folder(self):
        return self.mirror

    def try_ensure_mirror_import(self):
        self.import_threads.append(threading.current_thread())
        return {"status": self.import_status, "report": {}}

    def try_compare_mirror(self):
        self.compare_threads.append(threading.current_thread())
        if self.compare_status == "completed":
            return {"status": "completed", "entries": list(self.entries)}
        return {"status": self.compare_status, "entries": [],
                "detail": "another operation is running"}

    def get_latest_save_time(self, path):
        if self.stat_error is not None:
            raise self.stat_error
        for root, _dirs, files in os.walk(path):
            for name in files:
                return os.path.getmtime(os.path.join(root, name))
        return None


class TabRefreshTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.staging = root / "Staging"
        game = self.staging / "My_Game"
        (game / "saves" / "20260101_120000").mkdir(parents=True)
        (game / "saves" / "20260101_120000" / "slot1.sav").write_text(
            "state", encoding="utf-8")
        (game / "savegames.json").write_text(
            json.dumps({
                "game_title": "My Game",
                "last_synced": "2026-01-01T00:00:00Z",
                "playtime_seconds": 3600,
            }),
            encoding="utf-8",
        )
        self.manager = _FakeManager(self.staging, mirror=str(root / "Mirror"))
        self.tab = None

    def tearDown(self):
        self._wait_refresh()
        if self.tab is not None:
            self.tab.deleteLater()
        self.tmp.cleanup()

    def _make_tab(self):
        self.tab = SavegamesTab({"playtime": {}}, {}, self.manager)
        self.tab._request_cover = mock.Mock()
        return self.tab

    def _wait_refresh(self, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if (self.tab is not None
                    and self.tab._refresh_worker is None
                    and not self.tab._refresh_again):
                return True
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents, 20)
        return (self.tab is None
                or (self.tab._refresh_worker is None
                    and not self.tab._refresh_again))

    def test_collection_runs_off_the_gui_thread(self):
        tab = self._make_tab()
        self.assertTrue(self._wait_refresh(), "the refresh never finished")

        self.assertEqual(len(tab._cards), 1)
        self.assertTrue(self.manager.compare_threads)
        self.assertTrue(self.manager.import_threads)
        for thread in self.manager.compare_threads + self.manager.import_threads:
            self.assertIsNot(thread, threading.main_thread())

    def test_pixmap_is_never_built_off_the_gui_thread(self):
        real_pixmap = savegames_tab_module.QPixmap
        threads = []

        def spied(*args, **kwargs):
            threads.append(threading.current_thread())
            return real_pixmap(*args, **kwargs)

        with mock.patch.object(savegames_tab_module, "QPixmap",
                               side_effect=spied):
            tab = self._make_tab()
            self.assertTrue(self._wait_refresh(), "the refresh never finished")

        self.assertTrue(threads, "no QPixmap was constructed")
        for thread in threads:
            self.assertIs(thread, threading.main_thread())
        self.assertEqual(len(tab._cards), 1)

    def test_busy_compare_preserves_the_previous_cards(self):
        tab = self._make_tab()
        self.assertTrue(self._wait_refresh())
        cards = list(tab._cards)
        self.assertEqual(len(cards), 1)

        self.manager.compare_status = "busy"
        tab.refresh_view()
        self.assertTrue(self._wait_refresh())

        self.assertEqual(tab._cards, cards)
        self.assertNotIn("No backups", tab.message_label.text())

    def test_oserror_while_statting_keeps_the_previous_cards(self):
        tab = self._make_tab()
        self.assertTrue(self._wait_refresh())
        cards = list(tab._cards)

        self.manager.stat_error = OSError("snapshot pruned while reading")
        with self.assertLogs("ui.tabs.savegames_tab", level="WARNING"):
            tab.refresh_view()
            self.assertTrue(self._wait_refresh())

        self.assertEqual(tab._cards, cards)

    def test_late_callback_does_not_clear_a_newer_worker(self):
        tab = self._make_tab()
        self.assertTrue(self._wait_refresh())
        newer = object()
        tab._refresh_worker = newer
        tab._refresh_generation = 7
        tab._refresh_again = False

        tab._on_refresh_done(6, {"status": "completed",
                                 "compare_status": "busy"}, worker=object())
        self.assertIs(tab._refresh_worker, newer)

        tab._on_refresh_error(6, "late failure", worker=object())
        self.assertIs(tab._refresh_worker, newer)


class GetLatestSaveTimeTests(unittest.TestCase):
    def test_file_pruned_between_listing_and_stat_returns_best_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a.sav"
            first.write_text("x", encoding="utf-8")
            (root / "b.sav").write_text("y", encoding="utf-8")
            expected = os.stat(first).st_mtime
            manager = object.__new__(SavegamesManager)
            real_stat = os.stat

            def fake_stat(path, *args, **kwargs):
                if str(path).endswith("b.sav"):
                    raise FileNotFoundError(str(path))
                return real_stat(path, *args, **kwargs)

            with mock.patch("managers.save_manager.os.stat",
                            side_effect=fake_stat):
                value = manager.get_latest_save_time(str(root))

            self.assertIsNotNone(value)
            self.assertGreaterEqual(value, expected)


if __name__ == "__main__":
    unittest.main()
