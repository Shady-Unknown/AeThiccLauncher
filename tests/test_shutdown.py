import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock


from PyQt6.QtCore import QEventLoop, QThread
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication

from core.task_registry import TaskRegistry
from network.scraper import SteamRIPScraper
from ui.savegames_worker import SavegamesTaskWorker
from ui.shell import app_window as app_window_module
from ui.shell.app_window import AppWindow

from tests.qt_app import ensure_app

_app = ensure_app()


class _Signal:
    def __init__(self):
        self.disconnected = None

    def disconnect(self, slot):
        self.disconnected = slot


class _Browser:
    def __init__(self):
        self.calls = []

    def stop(self):
        self.calls.append("stop")

    def setPage(self, page):
        self.calls.append(("setPage", page))

    def close(self):
        self.calls.append("close")

    def deleteLater(self):
        self.calls.append("deleteLater")


class _Page:
    def __init__(self):
        self.deleted = False

    def deleteLater(self):
        self.deleted = True


class ScraperShutdownTests(unittest.TestCase):
    def _fake(self):
        return SimpleNamespace(
            timer=mock.Mock(),
            profile=SimpleNamespace(downloadRequested=_Signal()),
            _handle_download_request=object(),
            browser=_Browser(),
            page=_Page(),
            force_close=False,
        )

    def test_stops_timer_and_closes_the_view(self):
        fake = self._fake()
        browser = fake.browser
        page = fake.page

        SteamRIPScraper.shutdown(fake)

        fake.timer.stop.assert_called_once()
        self.assertEqual(
            browser.calls, ["stop", ("setPage", None), "close", "deleteLater"])
        self.assertIsNone(fake.browser)
        self.assertIsNone(fake.page)
        self.assertTrue(page.deleted)
        self.assertIsNotNone(fake.profile.downloadRequested.disconnected)

    def test_disconnect_error_is_tolerated(self):
        fake = self._fake()

        def _boom(_slot):
            raise TypeError("not connected")

        fake.profile.downloadRequested.disconnect = _boom

        SteamRIPScraper.shutdown(fake)

        self.assertIsNone(fake.browser)


class _BlockingWorker(QThread):
    """Real QThread that blocks until the test releases it."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_calls = 0
        self.terminate_calls = 0

    def run(self):
        self.entered.set()
        self.release.wait(10)

    def cancel(self):
        self.cancel_calls += 1

    def terminate(self):
        # C4 forbids terminate(): record it so the test can prove it never ran.
        self.terminate_calls += 1
        super().terminate()


class _ShutdownWindow:
    """Binds the real AppWindow methods to a light harness (no QMainWindow)."""

    shutdown = AppWindow.shutdown
    on_about_to_quit = AppWindow.on_about_to_quit
    _shutdown_stage1 = AppWindow._shutdown_stage1
    _shutdown_stop_poll_timer = AppWindow._shutdown_stop_poll_timer
    _shutdown_workers_running = AppWindow._shutdown_workers_running
    _shutdown_savegames_running = staticmethod(
        AppWindow._shutdown_savegames_running)
    _shutdown_poll = AppWindow._shutdown_poll
    _shutdown_stage3 = AppWindow._shutdown_stage3
    _shutdown_stage3_impl = AppWindow._shutdown_stage3_impl
    _shutdown_finish = AppWindow._shutdown_finish
    closeEvent = AppWindow.closeEvent

    def __init__(self):
        self._shutdown_started = False
        self._shutdown_finished = False
        self.hidden = False
        self.quit_called = False
        self.quit_calls = 0

    def hide(self):
        self.hidden = True

    def _quit_application(self):
        self.quit_calls += 1
        self.quit_called = True


class _FakeWorker:
    def __init__(self):
        self.running = True
        self.cancelled = False

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        self.running = False

    def wait(self, ms):
        pass


class AppShutdownTests(unittest.TestCase):
    def setUp(self):
        ensure_app()

    def _window(self, **attributes):
        window = _ShutdownWindow()
        for key, value in attributes.items():
            setattr(window, key, value)
        return window

    @staticmethod
    def _pump_until(predicate, timeout_ms=5000):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if predicate():
                return True
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.AllEvents, 50)
        return predicate()

    def test_shutdown_returns_quickly_and_defers_the_final_stage(self):
        worker = _BlockingWorker()
        self.addCleanup(worker.wait, 5000)
        self.addCleanup(worker.release.set)
        registry = TaskRegistry()
        registry.track(worker)
        window = self._window(tasks=registry)
        worker.start()
        self.assertTrue(worker.entered.wait(5), "the worker never started")

        started = time.monotonic()
        window.shutdown()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.3)
        self.assertTrue(window._shutdown_started)
        self.assertFalse(window._shutdown_finished)
        self.assertFalse(window.quit_called)
        self.assertIn(worker, registry.running_workers())
        self.assertGreaterEqual(worker.cancel_calls, 1)
        self.assertEqual(worker.terminate_calls, 0)

        # Closing while teardown drains must not accept the close.
        event = QCloseEvent()
        window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(window.hidden)

        worker.release.set()
        self.assertTrue(self._pump_until(lambda: window._shutdown_finished),
                        "the final stage never ran")

        self.assertTrue(window.quit_called)
        self.assertEqual(registry.running_workers(), [])
        worker.wait(1000)
        self.assertFalse(worker.isRunning())
        self.assertEqual(worker.terminate_calls, 0)

        event = QCloseEvent()
        window.closeEvent(event)
        self.assertTrue(event.isAccepted())

        # A second shutdown is a no-op: the sequence already finished.
        window.shutdown()
        self.assertTrue(window._shutdown_finished)
        self.assertEqual(worker.terminate_calls, 0)

    def test_shutdown_is_idempotent_and_stops_timers(self):
        class _Scraper:
            def __init__(self):
                self.calls = 0

            def shutdown(self):
                self.calls += 1

        scraper = _Scraper()
        window = self._window(
            _state_backup_timer=mock.Mock(),
            savegames_timer=mock.Mock(),
            _steam_build_timer=mock.Mock(),
        )
        window.scraper = scraper

        window.shutdown()

        # No tracked worker: the final stage runs synchronously.
        self.assertTrue(window._shutdown_finished)
        self.assertTrue(window.quit_called)
        window.shutdown()

        self.assertEqual(scraper.calls, 1)
        for name in ("_state_backup_timer", "savegames_timer", "_steam_build_timer"):
            getattr(window, name).stop.assert_called_once()

    def test_deferred_playtime_is_flushed_before_close_operations(self):
        order = []
        savegames = SimpleNamespace(
            flush_pending_playtime=lambda: order.append("flush"),
            close_operations=lambda wait_ms=0: order.append(("close", wait_ms)),
        )
        window = self._window(savegames_manager=savegames)

        window.shutdown()

        self.assertEqual(order, ["flush", ("close", 0)])
        self.assertTrue(window._shutdown_finished)

    def test_about_to_quit_runs_the_bounded_fallback(self):
        worker = _FakeWorker()
        registry = TaskRegistry()
        registry.track(worker)
        window = self._window(tasks=registry)

        window.on_about_to_quit()

        self.assertTrue(worker.cancelled)
        self.assertTrue(window._shutdown_finished)
        self.assertTrue(window.quit_called)
        self.assertEqual(window.quit_calls, 1)

    def test_about_to_quit_with_a_stuck_worker_skips_the_final_flush(self):
        worker = _BlockingWorker()
        self.addCleanup(worker.wait, 5000)
        self.addCleanup(worker.release.set)
        registry = TaskRegistry()
        registry.track(worker)

        master_catalog = mock.Mock()
        dl_manager = SimpleNamespace(active_worker=None, save_state=mock.Mock())
        window = self._window(
            tasks=registry,
            master_catalog=master_catalog,
            dl_manager=dl_manager,
        )
        window.backup_service = mock.Mock()
        window._flush_state_backup = mock.Mock()

        worker.start()
        self.assertTrue(worker.entered.wait(5), "the worker never started")

        with mock.patch.object(app_window_module,
                               "SHUTDOWN_FALLBACK_STOP_MS", 10):
            window.on_about_to_quit()

        # The worker is still writing: stage 3 must not race with it.
        master_catalog.save_immediate.assert_not_called()
        window._flush_state_backup.assert_not_called()
        dl_manager.save_state.assert_not_called()
        self.assertTrue(window._shutdown_finished)
        self.assertTrue(window.quit_called)
        self.assertEqual(window.quit_calls, 1)
        self.assertEqual(worker.terminate_calls, 0)
        # The poll timer created by stage 1 must not survive into process exit.
        timer = getattr(window, "_shutdown_poll_timer", None)
        self.assertIsNotNone(timer)
        self.assertFalse(timer.isActive())

    def test_quit_application_is_idempotent(self):
        window = SimpleNamespace()
        fake_app = SimpleNamespace(quit=mock.Mock())
        fake_application = mock.Mock()
        fake_application.instance = mock.Mock(return_value=fake_app)

        with mock.patch.object(app_window_module, "QApplication",
                               fake_application):
            AppWindow._quit_application(window)
            AppWindow._quit_application(window)

        fake_app.quit.assert_called_once_with()

    def test_stage3_failure_still_finishes_and_quits_once(self):
        class _RaisingTab:
            def release_webengine(self):
                raise ValueError("unexpected WebEngine failure")

        window = self._window()
        window.tab_explorer = _RaisingTab()

        window._shutdown_stage3()

        self.assertTrue(window._shutdown_finished)
        self.assertTrue(window.quit_called)
        self.assertEqual(window.quit_calls, 1)
        # A second call must not run the sequence or quit again.
        window._shutdown_stage3()
        self.assertEqual(window.quit_calls, 1)

    def test_stop_all_is_not_called_while_a_savegames_worker_runs(self):
        started = threading.Event()
        release = threading.Event()
        worker = SavegamesTaskWorker(
            lambda: (started.set(), release.wait(5))[0])
        self.addCleanup(worker.wait, 5000)
        self.addCleanup(release.set)
        registry = TaskRegistry()
        registry.track(worker)
        registry.stop_all = mock.Mock(wraps=registry.stop_all)
        window = self._window(tasks=registry)

        worker.start()
        self.assertTrue(started.wait(5), "the savegames worker never started")

        window.shutdown()

        registry.stop_all.assert_not_called()
        self.assertFalse(window._shutdown_finished)

        release.set()
        self.assertTrue(self._pump_until(lambda: window._shutdown_finished),
                        "the final stage never ran")
        worker.wait(1000)
        registry.stop_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
