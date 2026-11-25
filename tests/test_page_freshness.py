import unittest
from unittest import mock

from network import resolver


class PageFreshnessTests(unittest.TestCase):
    def _check(self, response=None, error=None, **kwargs):
        with mock.patch.object(resolver.requests, "head",
                               side_effect=error, return_value=response):
            return resolver.check_page_freshness("https://example.test/page", **kwargs)

    def test_304_is_fresh(self):
        response = mock.Mock(status_code=304, headers={})
        self.assertTrue(self._check(response, etag="abc"))

    def test_same_etag_is_fresh(self):
        response = mock.Mock(status_code=200, headers={"ETag": "abc"})
        self.assertTrue(self._check(response, etag="abc"))

    def test_changed_page_is_stale(self):
        response = mock.Mock(status_code=200, headers={"ETag": "new"})
        self.assertFalse(self._check(response, etag="old"))

    def test_network_error_is_unknown_not_fresh(self):
        self.assertIsNone(self._check(error=OSError("offline"), etag="abc"))

    def test_without_validators_is_fresh(self):
        self.assertTrue(self._check(mock.Mock(status_code=200, headers={})))


if __name__ == "__main__":
    unittest.main()
