import unittest
from types import SimpleNamespace

from ui.widgets.game_detail_popup import GameDetailPopup


class _Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class _FakeWorker:
    def __init__(self):
        self.stopped = False
        self.waited = False
        self.deleted = False
        self.finished = _Signal()

    def isRunning(self):
        return True

    def stop(self):
        self.stopped = True

    def wait(self, ms):
        self.waited = True

    def deleteLater(self):
        self.deleted = True


class PopupCloseTests(unittest.TestCase):
    def test_stop_download_never_waits_on_the_gui_thread(self):
        worker = _FakeWorker()
        fake = SimpleNamespace(
            _download_worker=worker,
            _retired_workers=[],
            _hide_trailer_progress=lambda: None,
        )
        fake._forget_worker = lambda w: None

        GameDetailPopup._stop_download(fake)

        self.assertTrue(worker.stopped)
        self.assertFalse(worker.waited)
        self.assertIsNone(fake._download_worker)
        self.assertEqual(fake._retired_workers, [worker])

    def test_close_hides_before_cleanup(self):
        order = []
        fake = SimpleNamespace(
            _gen=3,
            mw=SimpleNamespace(_discovery_click_guard=0),
        )
        fake.hide = lambda: order.append("hide")
        fake._stop_download = lambda: order.append("stop")
        fake._teardown_player = lambda: order.append("teardown")

        GameDetailPopup.close_popup(fake)

        self.assertEqual(order, ["hide", "stop", "teardown"])
        self.assertEqual(fake._gen, 4)

    def test_forget_worker_removes_and_deletes(self):
        worker = _FakeWorker()
        fake = SimpleNamespace(_retired_workers=[worker])

        GameDetailPopup._forget_worker(fake, worker)

        self.assertEqual(fake._retired_workers, [])
        self.assertTrue(worker.deleted)


if __name__ == "__main__":
    unittest.main()
