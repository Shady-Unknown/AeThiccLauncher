"""D1: savegames notifications are marshalled to the GUI thread."""
import threading
import time
import unittest

from PyQt6.QtCore import QEventLoop, QObject, Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from managers.save_manager import SavegamesManager
from tests.qt_app import ensure_app
from ui.shell.app_window import AppWindow


class _NoticeHarness(QObject):
    # Same signal and marshaller as AppWindow, without building the window.
    savegames_notice = pyqtSignal(str, str, str)
    _post_savegames_notification = AppWindow._post_savegames_notification

    def __init__(self):
        super().__init__()
        self.shown = []
        self.threads = []
        self.savegames_notice.connect(
            self._record, Qt.ConnectionType.QueuedConnection)

    def _record(self, level, title, message):
        self.threads.append(threading.current_thread())
        self.shown.append((level, title, message))


class _NotifyWorker(QThread):
    def __init__(self, manager, harness):
        super().__init__()
        self.manager = manager
        self.harness = harness
        self.sync_shown = None
        self.entered = threading.Event()

    def run(self):
        self.manager._notify_now("warning", "Savegames Manager", "hello")
        # Nothing may be shown synchronously in the worker thread.
        self.sync_shown = len(self.harness.shown)
        self.entered.set()


class NotificationMarshallingTests(unittest.TestCase):
    def setUp(self):
        ensure_app()

    def test_worker_notification_is_shown_once_on_the_gui_thread(self):
        harness = _NoticeHarness()
        manager = object.__new__(SavegamesManager)
        manager._notify_callback = harness._post_savegames_notification
        worker = _NotifyWorker(manager, harness)
        self.addCleanup(worker.wait, 5000)

        worker.start()
        self.assertTrue(worker.entered.wait(2), "the worker never notified")
        self.assertEqual(worker.sync_shown, 0)
        self.assertEqual(harness.shown, [])

        deadline = time.monotonic() + 5
        while not harness.shown and time.monotonic() < deadline:
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents, 20)

        self.assertEqual(len(harness.shown), 1)
        self.assertEqual(harness.shown[0],
                         ("warning", "Savegames Manager", "hello"))
        self.assertIs(harness.threads[0], threading.main_thread())
        worker.wait(5000)
        self.assertFalse(worker.isRunning())


if __name__ == "__main__":
    unittest.main()
