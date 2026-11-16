"""Cover cache: the vertical placeholder must not be used as a background."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.cover_cache import CoverCache
from core.paths import PathManager


class CacheUrlPlaceholderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._patch = patch.object(
            PathManager, "get_cache_dir", return_value=Path(self.root)
        )
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _failing_get(self, url, **_kwargs):
        raise RuntimeError("offline")

    def test_failed_download_returns_placeholder_by_default(self):
        with patch("core.cover_cache.requests.get", side_effect=self._failing_get):
            value = CoverCache._cache_url("https://example.test/a.jpg")
        self.assertTrue(value.startswith("file://"))
        self.assertIn("cover_placeholder", os.path.basename(value))

    def test_failed_download_returns_empty_when_placeholder_is_disallowed(self):
        with patch("core.cover_cache.requests.get", side_effect=self._failing_get):
            value = CoverCache._cache_url(
                "https://example.test/a.jpg", allow_placeholder=False
            )
        self.assertEqual(value, "")

    def test_local_path_for_background_skips_the_placeholder(self):
        with patch("core.cover_cache.requests.get", side_effect=self._failing_get):
            self.assertIsNone(
                CoverCache.local_path_for(
                    "https://example.test/a.jpg", allow_placeholder=False
                )
            )


if __name__ == "__main__":
    unittest.main()
