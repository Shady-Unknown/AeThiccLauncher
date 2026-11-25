import unittest
from unittest import mock

from PyQt6.QtCore import QObject

from network.scrape_queue import ScrapeQueue
from network.scraper import SteamRIPScraper
from tests.qt_app import ensure_app

_app = ensure_app()


class _TestScraper(SteamRIPScraper):
    def __init__(self):
        QObject.__init__(self)
        self.timer = mock.Mock()
        self.browser = mock.Mock()
        self.queue = ScrapeQueue()
        self.current_job = None
        self.current_mode = None
        self._interactive = False
        self._shutting_down = False
        self.timeout_counter = 0


class ScraperQueueTests(unittest.TestCase):
    def test_jobs_run_one_at_a_time(self):
        scraper = _TestScraper()
        scraper._enqueue_job("extract", url="a")
        scraper._enqueue_job("extract", url="b")

        self.assertEqual(scraper.current_job.url, "a")
        self.assertEqual(scraper.browser.load.call_count, 1)

        scraper._complete_job()

        self.assertEqual(scraper.current_job.url, "b")
        self.assertEqual(scraper.browser.load.call_count, 2)

    def test_duplicate_of_current_job_is_ignored(self):
        scraper = _TestScraper()
        scraper._enqueue_job("cover", url="a", title="Game")

        self.assertIsNone(scraper._enqueue_job("cover", url="a", title="Game"))
        self.assertEqual(len(scraper.queue), 0)

    def test_timeout_emits_and_starts_next(self):
        scraper = _TestScraper()
        scraper._enqueue_job("cover", url="a", title="Game")
        scraper._enqueue_job("extract", url="b")
        emitted = []
        scraper.cover_fetched.connect(lambda title, url: emitted.append((title, url)))

        scraper._finish_job(emit_timeout=True)

        self.assertEqual(emitted, [("Game", "")])
        self.assertEqual(scraper.current_job.url, "b")

    def test_priority_runs_first(self):
        scraper = _TestScraper()
        scraper._enqueue_job("extract", url="a")
        scraper._enqueue_job("extract", url="b")
        scraper.queue.clear()
        scraper.current_job = None
        scraper.current_mode = None

        scraper._enqueue_job("extract", url="a")
        scraper._enqueue_job("cf_bypass", url="cf", priority=10)
        scraper._complete_job()

        self.assertEqual(scraper.current_job.mode, "cf_bypass")

    def test_search_pauses_and_resumes_the_queue(self):
        scraper = _TestScraper()
        scraper._enqueue_job("extract", url="a")
        scraper._enqueue_job("extract", url="b")

        scraper._interactive_preempt("search")
        self.assertIsNone(scraper.current_job)
        self.assertEqual(len(scraper.queue), 1)  # "b" stays queued

        scraper._enqueue_job("extract", url="c")
        self.assertIsNone(scraper.current_job)
        self.assertEqual(len(scraper.queue), 2)

        scraper.resume_background()
        self.assertEqual(scraper.current_job.url, "b")

    def test_download_browser_drops_pending_jobs(self):
        scraper = _TestScraper()
        scraper._enqueue_job("extract", url="a")
        scraper._enqueue_job("extract", url="b")

        scraper._interactive_preempt("download")

        self.assertIsNone(scraper.current_job)
        self.assertEqual(len(scraper.queue), 0)


if __name__ == "__main__":
    unittest.main()
