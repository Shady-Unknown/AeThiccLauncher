import unittest


from ui.stats_worker import StatsSnapshotWorker

from tests.qt_app import ensure_app

_app = ensure_app()


class _FakeManager:
    def __init__(self, result=None, error=None):
        self.result = result or {"hero": {"hours_total": 1.0}}
        self.error = error
        self.calls = []

    def snapshot(self, range_key=None, region=None):
        self.calls.append((range_key, region))
        if self.error:
            raise self.error
        return self.result


class StatsSnapshotWorkerTests(unittest.TestCase):
    def test_emits_snapshot_with_request_identity(self):
        manager = _FakeManager()
        worker = StatsSnapshotWorker(manager, 7, "90d", "IT")
        received = []
        worker.ready.connect(lambda *args: received.append(args))

        worker.run()

        self.assertEqual(manager.calls, [("90d", "IT")])
        self.assertEqual(received[0][:3], (7, "90d", "IT"))
        self.assertEqual(received[0][3], manager.result)

    def test_cancelled_worker_does_not_emit(self):
        manager = _FakeManager()
        worker = StatsSnapshotWorker(manager, 1, "90d", "US")
        received = []
        worker.ready.connect(lambda *args: received.append(args))
        worker.cancel()

        worker.run()

        self.assertEqual(received, [])

    def test_failure_emits_error(self):
        manager = _FakeManager(error=RuntimeError("boom"))
        worker = StatsSnapshotWorker(manager, 2, "90d", "US")
        failures = []
        worker.failed.connect(lambda *args: failures.append(args))

        worker.run()

        self.assertEqual(failures[0][0], 2)
        self.assertIn("boom", failures[0][1])


if __name__ == "__main__":
    unittest.main()
