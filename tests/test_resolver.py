import unittest
from unittest.mock import patch

from network.resolver import LinkResolver


class FakeCookies(dict):
    pass


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, cookies=None,
                 json_data=None, body=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.cookies = FakeCookies(cookies or {})
        self._json = json_data
        self._body = body if body is not None else text.encode("utf-8")
        self.closed = False

    def json(self):
        return self._json

    def iter_content(self, chunk_size=65536):
        yield self._body

    def close(self):
        self.closed = True


class GoFileResolverTests(unittest.TestCase):
    def _resolver(self, payload):
        resolver = LinkResolver()
        resolver._get_gofile_token = lambda: "guest-token"
        resolver._fetch_gofile_contents = (
            lambda content_id, token, page=1: FakeResponse(json_data=payload)
        )
        return resolver

    def test_single_file_link_is_returned(self):
        # Regression: gofile.io/d/<code> for a single file returns the "file"
        # node at the top level, with no "children" key.
        payload = {
            "status": "ok",
            "data": {
                "type": "file",
                "name": "Game.rar",
                "link": "https://store-eu-par-1.gofile.io/download/web/id/Game.rar",
            },
            "metadata": {},
        }
        resolver = self._resolver(payload)
        url, cookies = resolver.resolve("https://gofile.io/d/eSnLfXR8")
        self.assertEqual(url, payload["data"]["link"])
        self.assertEqual(cookies["accountToken"], "guest-token")

    def test_folder_walks_nested_children(self):
        payload = {
            "status": "ok",
            "data": {
                "type": "folder",
                "children": {
                    "sub": {
                        "type": "folder",
                        "children": {
                            "f": {"type": "file", "link": "https://store/file.rar"}
                        },
                    }
                },
            },
            "metadata": {},
        }
        resolver = self._resolver(payload)
        url, _ = resolver.resolve("https://gofile.io/d/ABC123")
        self.assertEqual(url, "https://store/file.rar")

    def test_not_found_raises_clear_error(self):
        resolver = self._resolver({"status": "error-notFound", "data": {}})
        with self.assertRaises(Exception) as ctx:
            resolver.resolve("https://gofile.io/d/gone123")
        self.assertIn("not found", str(ctx.exception))

    def test_rate_limit_renews_token_and_retries(self):
        resolver = LinkResolver()
        resolver._get_gofile_token = lambda: "fresh-token"
        calls = []

        def fake_fetch(content_id, token, page=1):
            calls.append(token)
            if len(calls) == 1:
                return FakeResponse(json_data={"status": "error-rateLimit", "data": {}})
            return FakeResponse(json_data={
                "status": "ok",
                "data": {"type": "file", "link": "https://store/ok.rar"},
                "metadata": {},
            })

        resolver._fetch_gofile_contents = fake_fetch
        with patch("network.resolver.time.sleep", lambda *_: None):
            url, _ = resolver.resolve("https://gofile.io/d/ABC123")
        self.assertEqual(url, "https://store/ok.rar")
        self.assertEqual(len(calls), 2)


class MegaDbResolverTests(unittest.TestCase):
    def test_direct_cdn_url_is_returned_without_http(self):
        url = "https://fs1.megadb.xyz/abc/Game.rar?download_token=deadbeef"
        resolver = LinkResolver()
        with patch("network.resolver.requests.get") as get_mock:
            resolved, _ = resolver.resolve(url)
        self.assertEqual(resolved, url)
        get_mock.assert_not_called()

    def test_new_token_flow_returns_cdn_redirect(self):
        page = (
            "var finalDownloadUrl = "
            "'https://megadb.net/abc?pt=TOKEN';"
        )
        resolver = LinkResolver(referer="https://steamrip.com/")
        resolver._get_page_text = lambda *args, **kwargs: page
        redirect = "https://fs1.megadb.xyz/abc/Game.rar?download_token=1"
        response = FakeResponse(status_code=302, headers={"Location": redirect})
        with patch("network.resolver.time.sleep", lambda *_: None), \
                patch("network.resolver.requests.get", return_value=response) as get_mock:
            resolved, _ = resolver.resolve("https://megadb.net/abc")
        self.assertEqual(resolved, redirect)
        self.assertEqual(get_mock.call_count, 1)

    def test_meta_refresh_is_followed(self):
        page = 'var finalDownloadUrl = "https://megadb.net/abc?pt=TOKEN";'
        resolver = LinkResolver(referer="https://steamrip.com/")
        resolver._get_page_text = lambda *args, **kwargs: page
        body = (
            '<meta http-equiv="refresh" content="0;url='
            "'https://fs1.megadb.xyz/abc/Game.rar?download_token=2'" + '" />'
        )
        response = FakeResponse(status_code=200, text=body)
        with patch("network.resolver.time.sleep", lambda *_: None), \
                patch("network.resolver.requests.get", return_value=response):
            resolved, _ = resolver.resolve("https://megadb.net/abc")
        self.assertEqual(
            resolved, "https://fs1.megadb.xyz/abc/Game.rar?download_token=2"
        )

    def test_binary_page_is_treated_as_direct_file(self):
        resolver = LinkResolver()
        response = FakeResponse(
            status_code=200,
            headers={"content-type": "application/octet-stream"},
            body=b"\x00\x01",
        )
        with patch("network.resolver.requests.get", return_value=response):
            self.assertIsNone(resolver._get_page_text("https://x.test/file"))

    def test_page_without_link_raises(self):
        resolver = LinkResolver()
        resolver._get_page_text = lambda *args, **kwargs: "<html>nope</html>"
        with self.assertRaises(Exception) as ctx:
            resolver.resolve("https://megadb.net/abc")
        self.assertIn("CDN link not found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
