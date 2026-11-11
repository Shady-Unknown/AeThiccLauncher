import time
import unittest
from unittest import mock

from core import secrets
from managers.discovery_manager import CACHE_SCHEMA, DiscoveryManager


class DiscoveryCacheTests(unittest.TestCase):
    def test_empty_igdb_row_is_refetched(self):
        manager = DiscoveryManager(None)
        manager._data = {
            "new_releases": {
                "source": "igdb",
                "schema": CACHE_SCHEMA,
                "fetched_at": time.time(),
                "games": [],
                "cards": [],
            }
        }
        self.assertTrue(manager._needs_fetch("new_releases"))

    def test_sortable_policy(self):
        self.assertTrue(DiscoveryManager.is_sortable_key("trending"))
        self.assertTrue(DiscoveryManager.is_sortable_key("sr_33"))
        self.assertTrue(DiscoveryManager.is_sortable_key("sr_52"))
        self.assertFalse(DiscoveryManager.is_sortable_key("new_releases"))
        self.assertFalse(DiscoveryManager.is_sortable_key("recent_updates"))
        self.assertFalse(DiscoveryManager.is_sortable_key("recent_added"))
        self.assertFalse(DiscoveryManager.is_sortable_key("all_games"))
        self.assertFalse(DiscoveryManager.is_sortable_key(None))

    def test_non_empty_fresh_igdb_row_stays_cached(self):
        manager = DiscoveryManager(None)
        manager._data = {
            "new_releases": {
                "source": "igdb",
                "schema": CACHE_SCHEMA,
                "fetched_at": time.time(),
                "games": [{"name": "A"}],
                "cards": [{"title": "A"}],
            }
        }
        self.assertFalse(manager._needs_fetch("new_releases"))

    def test_start_igdb_without_credentials_is_noop(self):
        manager = DiscoveryManager(None)
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False):
            manager._start_igdb()
        self.assertIsNone(manager._worker)

    def test_categories_hide_igdb_without_credentials(self):
        manager = DiscoveryManager(None)
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False):
            keys = [cat["key"] for cat in manager.categories()]
        self.assertNotIn("trending", keys)
        self.assertNotIn("new_releases", keys)


if __name__ == "__main__":
    unittest.main()
