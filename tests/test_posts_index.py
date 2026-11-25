import tempfile
import unittest
from pathlib import Path
from unittest import mock


from network import posts_index

from tests.qt_app import ensure_app

_app = ensure_app()


def _post(index):
    return {
        "link": f"https://example.test/game-{index}",
        "title": {"rendered": f"Game {index}"},
        "modified": "2026-01-01T00:00:00",
        "date": "2026-01-01T00:00:00",
        "categories": [],
    }


class PostsIndexCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "posts.json"
        self.worker = posts_index.PostsIndexWorker()

    def _run(self, request):
        with mock.patch.object(posts_index, "dest_path", return_value=str(self.dest)), \
             mock.patch.object(self.worker, "_request", side_effect=request), \
             mock.patch.object(self.worker, "_write_dev_snapshot"):
            return self.worker._full_download()

    def test_complete_download_stamps_full_sync(self):
        pages = {1: [_post(i) for i in range(posts_index.PAGE_SIZE)],
                 2: [_post(posts_index.PAGE_SIZE)]}

        data = self._run(lambda params: pages.get(params["page"], []))

        self.assertIsNotNone(data)
        self.assertTrue(data["full_sync_at"])
        self.assertNotIn("partial_page", data)
        self.assertEqual(len(data["posts"]), posts_index.PAGE_SIZE + 1)

    def test_interrupted_download_keeps_partial_page(self):
        calls = {"count": 0}

        def request(params):
            calls["count"] += 1
            if params["page"] == 2:
                self.worker._stop.set()
            return [_post(i) for i in range(posts_index.PAGE_SIZE)]

        data = self._run(request)

        self.assertIsNone(data)
        saved = posts_index._read_index(str(self.dest))
        self.assertIsNotNone(saved)
        self.assertEqual(saved.get("partial_page"), 2)
        self.assertFalse(saved.get("full_sync_at"))

    def test_failed_page_saves_partial(self):
        def request(params):
            self.worker._stop.set()
            return None

        data = self._run(request)

        self.assertIsNone(data)
        saved = posts_index._read_index(str(self.dest))
        self.assertEqual(saved.get("partial_page"), 1)
        self.assertFalse(saved.get("full_sync_at"))

    def test_sync_returns_none_when_full_download_fails(self):
        with mock.patch.object(self.worker, "_full_download", return_value=None):
            self.assertIsNone(self.worker._sync({"posts": {}}))


if __name__ == "__main__":
    unittest.main()
