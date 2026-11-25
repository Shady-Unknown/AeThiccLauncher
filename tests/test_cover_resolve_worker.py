import unittest
from unittest import mock


from ui.cover_worker import CoverResolveWorker

from tests.qt_app import ensure_app

_app = ensure_app()


class CoverResolveWorkerTests(unittest.TestCase):
    def test_emits_resolved_urls(self):
        with mock.patch("ui.cover_worker.CoverCache.resolve_urls",
                        return_value=["file:///tmp/a.jpg"]):
            worker = CoverResolveWorker(3, "Game", ["https://x/a.jpg"], {})
            received = []
            worker.ready.connect(lambda *args: received.append(args))
            worker.run()

        self.assertEqual(received[0][:2], (3, "Game"))
        self.assertEqual(received[0][2], ["file:///tmp/a.jpg"])

    def test_cancel_prevents_emission(self):
        with mock.patch("ui.cover_worker.CoverCache.resolve_urls",
                        return_value=["file:///tmp/a.jpg"]):
            worker = CoverResolveWorker(1, "Game", ["https://x/a.jpg"], {})
            received = []
            worker.ready.connect(lambda *args: received.append(args))
            worker.cancel()
            worker.run()

        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
