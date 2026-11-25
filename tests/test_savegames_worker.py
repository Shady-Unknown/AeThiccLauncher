import unittest


from ui.savegames_worker import SavegamesTaskWorker

from tests.qt_app import ensure_app

_app = ensure_app()


class SavegamesTaskWorkerTests(unittest.TestCase):
    def test_emits_the_task_result(self):
        worker = SavegamesTaskWorker(lambda: {"synced_games": 2})
        results = []
        worker.finished_ok.connect(results.append)

        worker.run()

        self.assertEqual(results, [{"synced_games": 2}])

    def test_emits_failure_without_crashing(self):
        def task():
            raise OSError("disk full")

        worker = SavegamesTaskWorker(task)
        failures = []
        worker.failed.connect(failures.append)

        worker.run()

        self.assertEqual(failures, ["disk full"])


if __name__ == "__main__":
    unittest.main()
