"""Helpers for the Explorer status strip (reviews pill)."""
import unittest

from ui.tabs.explorer_tab import format_igdb_rating, metacritic_search_url


class ReviewsPillTests(unittest.TestCase):
    def test_metacritic_search_url(self):
        self.assertEqual(
            metacritic_search_url("Enotria: The Last Song"),
            "https://www.metacritic.com/search/Enotria%20The%20Last%20Song/",
        )

    def test_metacritic_search_url_strips_punctuation(self):
        self.assertEqual(
            metacritic_search_url("  Baldur's Gate 3!  "),
            "https://www.metacritic.com/search/Baldur%20s%20Gate%203/",
        )

    def test_igdb_rating_is_scaled_to_ten(self):
        self.assertEqual(format_igdb_rating(85.3), "8.5 / 10")
        self.assertEqual(format_igdb_rating(70), "7.0 / 10")
        self.assertEqual(format_igdb_rating(0), "")
        self.assertEqual(format_igdb_rating(None), "")
        self.assertEqual(format_igdb_rating(""), "")


if __name__ == "__main__":
    unittest.main()
