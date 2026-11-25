"""GUI consumers must not run savegame filesystem work on the GUI thread."""
import unittest
from unittest import mock

from PyQt6.QtCore import QObject, pyqtSignal

from tests.qt_app import ensure_app


class FakeWorker(QObject):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, task, parent=None):
        super().__init__(None)
        self.task = task
        self.started = False

    def start(self):
        self.started = True

    def isRunning(self):
        return False


class _FakeSavegames:
    def __init__(self, result=True):
        self.result = result
        self.calls = 0

    def backup_before_update(self, meta):
        self.calls += 1
        return self.result

    def backup_on_demand(self, meta):
        self.calls += 1
        return self.result


class InstallerPreUpdateTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        from ui.controllers.installer_flow import InstallerFlowCoordinator
        self.Flow = InstallerFlowCoordinator
        self.app = mock.Mock()
        self.app._shutdown_done = False
        self.app.savegames_manager = _FakeSavegames(True)
        self.flow = self.Flow.__new__(self.Flow)
        self.flow.app_window = self.app

    def test_extraction_waits_for_the_backup_worker(self):
        called = []
        with mock.patch("ui.controllers.installer_flow.SavegamesTaskWorker",
                        FakeWorker), mock.patch(
                "ui.controllers.installer_flow.TaskRegistry.instance",
                return_value=None):
            self.flow._start_pre_update_backup({"title": "Game"}, lambda: called.append(True))
            worker = self.flow._pre_update_backup_worker
            self.assertTrue(worker.started)
            self.assertEqual(called, [])
            worker.finished_ok.emit(True)
            self.assertEqual(called, [True])
            self.assertEqual(self.app.savegames_manager.calls, 0)  # task runs in worker

    def test_backup_failure_still_follows_the_existing_policy(self):
        called = []
        with mock.patch("ui.controllers.installer_flow.SavegamesTaskWorker",
                        FakeWorker), mock.patch(
                "ui.controllers.installer_flow.TaskRegistry.instance",
                return_value=None):
            self.flow._start_pre_update_backup({"title": "Game"}, lambda: called.append(True))
            self.flow._pre_update_backup_worker.failed.emit("disk full")
        self.assertEqual(called, [True])

    def test_shutdown_does_not_start_extraction(self):
        called = []
        self.app._shutdown_done = True
        with mock.patch("ui.controllers.installer_flow.SavegamesTaskWorker",
                        FakeWorker), mock.patch(
                "ui.controllers.installer_flow.TaskRegistry.instance",
                return_value=None):
            self.flow._start_pre_update_backup({"title": "Game"}, lambda: called.append(True))
            self.flow._pre_update_backup_worker.finished_ok.emit(True)
        self.assertEqual(called, [])


class SessionBackupTests(unittest.TestCase):
    def test_post_session_backup_runs_in_worker(self):
        ensure_app()
        from ui.controllers.session import SessionController
        controller = SessionController.__new__(SessionController)
        controller.savegames_manager = _FakeSavegames(True)
        controller._shutdown_done = False
        controller.events = mock.Mock()
        with mock.patch("ui.controllers.session.SavegamesTaskWorker",
                        FakeWorker), mock.patch(
                "ui.controllers.session.TaskRegistry.instance",
                return_value=None), mock.patch(
                "ui.controllers.session.QMessageBox.information"):
            controller._start_session_backup({"title": "Game"}, "Game")
            worker = controller._session_backup_worker
            self.assertTrue(worker.started)
            self.assertEqual(controller.savegames_manager.calls, 0)
            worker.finished_ok.emit(True)
        controller.events.emit_event.assert_called_once()


if __name__ == "__main__":
    unittest.main()
