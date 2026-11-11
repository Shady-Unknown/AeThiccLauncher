import unittest

from PyQt6.QtCore import QCoreApplication

from core.task_registry import TaskRegistry

_app = QCoreApplication.instance() or QCoreApplication([])


class _FakeWorker:
    def __init__(self, running=True):
        self.running = running
        self.cancelled = False
        self.waited = 0

    def isRunning(self):
        return self.running

    def cancel(self):
        self.cancelled = True
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


if __name__ == "__main__":
    unittest.main()
