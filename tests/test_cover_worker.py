"""Cover worker: image downloads go through the shared HTTP helper."""
import io
import unittest
from unittest.mock import patch

from PIL import Image

from network.cover_worker import CoverWorker


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (3, 4), "blue").save(buffer, "PNG")
    return buffer.getvalue()


class FetchTests(unittest.TestCase):
    def setUp(self):
        # ``_fetch`` only needs the static helper; skip the QThread/keyring init.
        self.worker = CoverWorker.__new__(CoverWorker)

    def test_fetch_uses_the_shared_helper_and_returns_an_image(self):
        with patch(
            "network.cover_worker.fetch_image_bytes", return_value=_png_bytes()
        ) as fetch:
            image = self.worker._fetch("https://steamrip.com/a.jpg")
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "https://steamrip.com/a.jpg")
        self.assertIsNotNone(image)
        self.assertEqual(image.size, (3, 4))

    def test_fetch_normalizes_protocol_relative_urls(self):
        with patch(
            "network.cover_worker.fetch_image_bytes", return_value=_png_bytes()
        ) as fetch:
            self.worker._fetch("//steamrip.com/a.jpg")
        self.assertEqual(fetch.call_args.args[0], "https://steamrip.com/a.jpg")

    def test_fetch_returns_none_when_the_helper_fails(self):
        with patch("network.cover_worker.fetch_image_bytes", return_value=None):
            self.assertIsNone(self.worker._fetch("https://steamrip.com/a.jpg"))


if __name__ == "__main__":
    unittest.main()
