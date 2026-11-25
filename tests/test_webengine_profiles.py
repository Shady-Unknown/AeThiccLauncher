"""WebEngine profile configuration (P2-6) and scraper teardown guards."""
import unittest
from unittest import mock

from PyQt6.QtCore import QObject
from PyQt6.QtWebEngineCore import QWebEngineProfile

from tests.qt_app import ensure_app
from ui.webengine_profiles import configure_media_profile, describe_profile

ensure_app()


class _Policy:
    name = "NoPersistentCookies"


class _FakeProfile:
    def __init__(self, off_the_record=True):
        self.cache_type = None
        self.cookie_policy = None
        self._off = off_the_record

    def setHttpCacheType(self, value):
        self.cache_type = value

    def setPersistentCookiesPolicy(self, value):
        self.cookie_policy = value

    def isOffTheRecord(self):
        return self._off

    def storageName(self):
        return ""

    def persistentStoragePath(self):
        return "/tmp/QtWebEngine/OffTheRecord"

    def persistentCookiesPolicy(self):
        return _Policy()


class MediaProfileTests(unittest.TestCase):
    def test_memory_cache_and_no_persistent_cookies(self):
        profile = _FakeProfile()

        result = configure_media_profile(profile)

        self.assertIs(result, profile)
        self.assertEqual(
            profile.cache_type, QWebEngineProfile.HttpCacheType.MemoryHttpCache
        )
        self.assertEqual(
            profile.cookie_policy,
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies,
        )

    def test_describe_profile_reports_runtime_facts(self):
        info = describe_profile(_FakeProfile())

        self.assertTrue(info["off_the_record"])
        self.assertEqual(info["storage_name"], "")
        self.assertEqual(info["cookies"], "NoPersistentCookies")
        self.assertEqual(describe_profile(None), {})


class ScraperShutdownTests(unittest.TestCase):
    def _scraper(self):
        from network.scraper import SteamRIPScraper

        scraper = SteamRIPScraper.__new__(SteamRIPScraper)
        QObject.__init__(scraper)
        scraper.timer = mock.Mock()
        scraper.queue = mock.Mock()
        scraper.profile = mock.Mock()
        scraper.browser = mock.Mock()
        scraper.page = mock.Mock()
        scraper.current_job = object()
        scraper.current_mode = "extract"
        scraper._interactive = True
        scraper._shutting_down = False
        scraper._shutdown_done = False
        return scraper

    def test_shutdown_is_idempotent_and_clears_the_interceptor(self):
        scraper = self._scraper()

        scraper.shutdown()
        scraper.shutdown()

        scraper.profile.setUrlRequestInterceptor.assert_called_once_with(None)
        scraper.queue.clear.assert_called_once()
        self.assertIsNone(scraper.browser)

    def test_new_jobs_are_rejected_after_shutdown(self):
        scraper = self._scraper()
        scraper._shutting_down = True

        self.assertIsNone(scraper._enqueue_job("extract", url="x"))
        scraper.queue.enqueue.assert_not_called()


if __name__ == "__main__":
    unittest.main()
