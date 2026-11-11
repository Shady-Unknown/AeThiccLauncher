import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
