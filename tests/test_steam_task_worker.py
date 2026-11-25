import unittest

from tests.qt_app import ensure_app
from ui.steam_task_worker import SteamTaskWorker

_app = ensure_app()


class SteamTaskWorkerTests(unittest.TestCase):
    def test_emits_result(self):
        worker = SteamTaskWorker(lambda: (1, 2, True, "proton", "42"))
        received = []
        worker.ready.connect(received.append)
        worker.run()
        self.assertEqual(received, [(1, 2, True, "proton", "42")])

    def test_emits_failure(self):
        def task():
            raise RuntimeError("vdf locked")
        worker = SteamTaskWorker(task)
        failures = []
        worker.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, ["vdf locked"])


if __name__ == "__main__":
    unittest.main()
