"""Image HTTP helper: SteamRIP uses impersonation, other hosts are defensive."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from core import http_utils


def _png_bytes(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, "PNG")
    return buffer.getvalue()


PNG = _png_bytes()
HTML = b"<html><body>Cloudflare challenge</body></html>"


def _response(status=200, content=PNG):
    return SimpleNamespace(status_code=status, content=content)


class ImpersonatedHostTests(unittest.TestCase):
    def test_steamrip_hosts_are_impersonated(self):
        for url in (
            "https://steamrip.com/a.jpg",
            "https://www.steamrip.com/a.jpg",
            "https://cdn.steamrip.com/games/a.png",
        ):
            self.assertTrue(http_utils.is_impersonated_host(url), url)

    def test_other_hosts_are_not_impersonated(self):
        for url in (
            "https://notsteamrip.com/a.jpg",
            "https://example.test/a.jpg",
            "",
            "not-a-url",
        ):
            self.assertFalse(http_utils.is_impersonated_host(url), url)


class FetchImageBytesTests(unittest.TestCase):
    def test_steamrip_uses_curl_cffi_with_impersonation(self):
        with patch.object(
            http_utils.curl_requests, "get", return_value=_response()
        ) as impersonated, patch.object(http_utils.requests, "get") as plain:
            data = http_utils.fetch_image_bytes(
                "https://steamrip.com/a.jpg", user_agent="test-agent", timeout=5
            )
        self.assertEqual(data, PNG)
        impersonated.assert_called_once()
        self.assertEqual(impersonated.call_args.kwargs["impersonate"], http_utils.IMPERSONATE)
        self.assertEqual(
            impersonated.call_args.kwargs["headers"]["User-Agent"], "test-agent"
        )
        plain.assert_not_called()

    def test_other_host_uses_plain_requests(self):
        with patch.object(
            http_utils.requests, "get", return_value=_response()
        ) as plain, patch.object(http_utils.curl_requests, "get") as impersonated:
            data = http_utils.fetch_image_bytes("https://example.test/a.jpg")
        self.assertEqual(data, PNG)
        plain.assert_called_once()
        impersonated.assert_not_called()

    def test_403_on_other_host_retries_impersonated(self):
        with patch.object(
            http_utils.requests, "get", return_value=_response(status=403, content=b"")
        ), patch.object(
            http_utils.curl_requests, "get", return_value=_response()
        ) as impersonated:
            data = http_utils.fetch_image_bytes("https://cdn.example.test/a.jpg")
        self.assertEqual(data, PNG)
        impersonated.assert_called_once()
        self.assertEqual(impersonated.call_args.kwargs["impersonate"], http_utils.IMPERSONATE)

    def test_non_image_body_is_rejected(self):
        with patch.object(
            http_utils.requests, "get", return_value=_response(content=HTML)
        ):
            self.assertIsNone(
                http_utils.fetch_image_bytes("https://example.test/a.jpg")
            )

    def test_http_error_returns_none(self):
        with patch.object(
            http_utils.requests, "get", return_value=_response(status=500, content=b"")
        ):
            self.assertIsNone(
                http_utils.fetch_image_bytes("https://example.test/a.jpg")
            )

    def test_empty_url_returns_none_without_network(self):
        with patch.object(http_utils.requests, "get") as plain:
            self.assertIsNone(http_utils.fetch_image_bytes(""))
        plain.assert_not_called()

    def test_explicit_user_agent_header_wins(self):
        with patch.object(
            http_utils.requests, "get", return_value=_response()
        ) as plain:
            http_utils.fetch_image_bytes(
                "https://example.test/a.jpg",
                user_agent="default-agent",
                headers={"User-Agent": "explicit-agent"},
            )
        self.assertEqual(
            plain.call_args.kwargs["headers"]["User-Agent"], "explicit-agent"
        )


if __name__ == "__main__":
    unittest.main()
