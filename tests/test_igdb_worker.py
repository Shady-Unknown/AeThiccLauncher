"""Apicalypse bodies for the time-based IGDB rows.

"New Releases" sorted every planned date, so the shelf opened with placeholder
releases dated 2029-2032. "Trending" sorted an all-time follower count, which
pinned the same classics to the row for years.
"""
import unittest

from network.igdb_worker import IGDB_CATEGORIES, build_query

CATEGORIES = {cat["key"]: cat for cat in IGDB_CATEGORIES}


class IgdbQueryTests(unittest.TestCase):
    NOW = 1_800_000_000

    def test_new_releases_stop_at_the_present(self):
        query = build_query(CATEGORIES["new_releases"], 0, now=self.NOW)
        self.assertIn(f"first_release_date <= {self.NOW}", query)
        self.assertIn("sort first_release_date desc", query)

    def test_trending_uses_a_rolling_window(self):
        query = build_query(CATEGORIES["trending"], 0, now=self.NOW)
        self.assertIn(f"first_release_date >= {self.NOW - 365 * 86400}", query)
        self.assertIn("sort hypes desc", query)

    def test_plain_rows_keep_their_filter_and_offset(self):
        query = build_query(
            {"key": "horror", "filter": "genres = [5]", "sort": "hypes desc"},
            50, now=self.NOW)
        self.assertIn("genres = [5]", query)
        self.assertIn("offset 50", query)
        self.assertNotIn("first_release_date <=", query)
        self.assertNotIn("first_release_date >=", query)


if __name__ == "__main__":
    unittest.main()
