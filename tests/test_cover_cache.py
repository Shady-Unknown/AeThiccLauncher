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

    def test_failed_download_returns_placeholder_by_default(self):
        with patch("core.cover_cache.fetch_image_bytes", return_value=None):
            value = CoverCache._cache_url("https://example.test/a.jpg")
        self.assertTrue(value.startswith("file://"))
        self.assertIn("cover_placeholder", os.path.basename(value))

    def test_failed_download_returns_empty_when_placeholder_is_disallowed(self):
        with patch("core.cover_cache.fetch_image_bytes", return_value=None):
            value = CoverCache._cache_url(
                "https://example.test/a.jpg", allow_placeholder=False
            )
        self.assertEqual(value, "")

    def test_local_path_for_background_skips_the_placeholder(self):
        with patch("core.cover_cache.fetch_image_bytes", return_value=None):
            self.assertIsNone(
                CoverCache.local_path_for(
                    "https://example.test/a.jpg", allow_placeholder=False
                )
            )

    def test_successful_download_is_cached_and_forwarded_to_the_helper(self):
        payload = b"\xff\xd8\xff\xe0fake-jpeg-bytes"
        with patch(
            "core.cover_cache.fetch_image_bytes", return_value=payload
        ) as fetch:
            value = CoverCache._cache_url("https://steamrip.com/a.jpg")
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "https://steamrip.com/a.jpg")
        self.assertTrue(value.startswith("file://"))
        path = value.replace("file://", "")
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), payload)


class ResolveCachedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._patch = patch.object(
            PathManager, "get_cache_dir", return_value=self.root)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def test_cached_file_is_returned_without_fetching(self):
        url = "https://example.test/a.jpg"
        path = Path(CoverCache._cache_path_for(url))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"image")

        with patch("core.cover_cache.fetch_image_bytes") as fetch:
            resolved = CoverCache.resolve_urls_cached([url])

        self.assertEqual(len(resolved), 1)
        self.assertTrue(resolved[0].startswith("file://"))
        fetch.assert_not_called()

    def test_uncached_url_becomes_placeholder_without_fetching(self):
        url = "https://example.test/missing.jpg"
        with patch("core.cover_cache.fetch_image_bytes") as fetch:
            resolved = CoverCache.resolve_urls_cached([url])

        self.assertIn("cover_placeholder", resolved[0])
        fetch.assert_not_called()

    def test_installed_steam_asset_wins(self):
        assets = self.root / "game" / "Steam_Assets"
        assets.mkdir(parents=True)
        cover = assets / "Cover.jpg"
        cover.write_bytes(b"image")

        resolved = CoverCache.resolve_urls_cached(
            ["https://example.test/remote.jpg"],
            title="Game",
            installed_games={"Game": {"install_dir": str(self.root / "game")}},
        )

        self.assertIn("Cover.jpg", resolved[0])


if __name__ == "__main__":
    unittest.main()
