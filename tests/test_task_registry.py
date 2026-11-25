import threading
import time
import unittest


from core.task_registry import TaskRegistry

from tests.qt_app import ensure_app

_app = ensure_app()


class _FakeWorker:
    def __init__(self, running=True, cancel_stops=True, method="cancel"):
        self.running = running
        self.cancelled = False
        self.waited = 0
        self._method = method
        self._cancel_stops = cancel_stops

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
        if self._cancel_stops:
            self.running = False

    def stop(self):
        self.cancelled = True
        if self._cancel_stops:
            self.running = False

    def wait(self, ms):
        self.waited = ms


class TaskRegistryTests(unittest.TestCase):
    def test_track_and_forget(self):
        registry = TaskRegistry()
        worker = _FakeWorker()
        registry.track(worker)
        self.assertEqual(len(registry), 1)
        registry.forget(worker)
        self.assertEqual(len(registry), 0)

    def test_stop_all_cancels_running_workers(self):
        registry = TaskRegistry()
        worker = _FakeWorker()
        registry.track(worker)
        registry.stop_all(wait_ms=10)
        self.assertTrue(worker.cancelled)
        self.assertEqual(len(registry), 0)

    def test_running_filters_stopped_workers(self):
        registry = TaskRegistry()
        running = _FakeWorker(running=True)
        stopped = _FakeWorker(running=False)
        registry.track(running)
        registry.track(stopped)
        self.assertEqual(registry.running(), [running])

    def test_stop_fallback_when_cancel_takes_arguments(self):
        class _CancelWithArgs:
            def __init__(self):
                self.running = True
                self.stopped = False

            def isRunning(self):
                return self.running

            def cancel(self, titles):
                raise AssertionError("cancel(titles) must not be called here")

            def stop(self):
                self.stopped = True
                self.running = False

            def wait(self, ms):
                pass

        registry = TaskRegistry()
        worker = _CancelWithArgs()
        registry.track(worker)
        leftovers = registry.stop_all(wait_ms=50)
        self.assertTrue(worker.stopped)
        self.assertEqual(leftovers, [])

    def test_stop_all_reports_leftovers(self):
        registry = TaskRegistry()
        worker = _FakeWorker(cancel_stops=False)
        registry.track(worker)
        leftovers = registry.stop_all(wait_ms=0)
        self.assertEqual(leftovers, [worker])
        # A still-running QThread must stay referenced: dropping the only
        # Python reference aborts the interpreter.
        self.assertEqual(len(registry), 1)
        self.assertIn(worker, registry.running_workers())

    def test_wait_uses_shared_budget(self):
        registry = TaskRegistry()
        first = _FakeWorker(cancel_stops=False)
        second = _FakeWorker(cancel_stops=False)
        registry.track(first)
        registry.track(second)
        started = time.monotonic()
        registry.stop_all(wait_ms=200)
        elapsed_ms = (time.monotonic() - started) * 1000
        self.assertLess(elapsed_ms, 600)
        self.assertLessEqual(max(first.waited, second.waited), 200)

    def test_track_and_join_threads(self):
        registry = TaskRegistry()
        done = threading.Event()
        thread = threading.Thread(target=done.wait)
        registry.track_thread(thread)
        thread.start()
        done.set()
        leftovers = registry.join_threads(timeout_ms=500)
        self.assertEqual(leftovers, [])
        self.assertEqual(len(registry), 0)

    def test_join_threads_reports_leftovers(self):
        registry = TaskRegistry()
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        registry.track_thread(thread)
        thread.start()
        try:
            leftovers = registry.join_threads(timeout_ms=10)
            self.assertEqual(leftovers, [thread])
        finally:
            release.set()
            thread.join(1)


if __name__ == "__main__":
    unittest.main()
