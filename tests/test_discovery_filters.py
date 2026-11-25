"""Characterization of Discovery filters, search and sorting (G2A scope).

These are pure transformations over cards plus the filter state; the tests pin
the ranking and the "missing data goes last" rule before the matching split.
"""
import unittest

from managers.discovery_manager import DiscoveryManager
from tests.discovery_fakes import FakeCatalog, fake_services


def _manager(catalog, installed=None):
    return DiscoveryManager(fake_services(
        catalog=catalog,
        installed=installed or {},
        cover=lambda title: f"covers/{title}.jpg",
    ))


class FilterStateTests(unittest.TestCase):
    def test_set_filters_normalises_and_clamps(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        manager.set_filters({
            "search": "  DOOM  ",
            "hide_installed": 1,
            "sr_category": 5,
            "igdb_genre": 3,
            "sort": "not-a-mode",
        })
        self.assertEqual(manager._filters, {
            "search": "doom",
            "hide_installed": True,
            "sr_category": 5,
            "igdb_genre": 3,
            "sort": "",
        })
        manager.set_filters({"sort": "title_asc"})
        self.assertEqual(manager._filters["sort"], "title_asc")
        self.assertFalse(manager._filters_active())

    def test_any_active_filter_is_reported(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        self.assertFalse(manager._filters_active())
        manager.set_filters({"hide_installed": True})
        self.assertTrue(manager._filters_active())


class CardMatchTests(unittest.TestCase):
    def test_search_is_a_lowercase_substring(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        manager.set_filters({"search": "doom"})
        self.assertTrue(manager._card_matches({"title": "DOOM Eternal"}))
        self.assertFalse(manager._card_matches({"title": "Quake"}))

    def test_hide_installed_uses_the_provider(self):
        manager = DiscoveryManager(fake_services(
            catalog=FakeCatalog({}), installed={"DOOM": {}}))
        manager.set_filters({"hide_installed": True})
        self.assertFalse(manager._card_matches({"title": "DOOM"}))
        self.assertTrue(manager._card_matches({"title": "Quake"}))

    def test_steamrip_category_uses_card_or_meta(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        manager.set_filters({"sr_category": 5})
        self.assertTrue(manager._card_matches({
            "title": "A", "steamrip_categories": [5]}))
        self.assertTrue(manager._card_matches({
            "title": "B", "meta": {"steamrip_categories": [5]}}))
        self.assertFalse(manager._card_matches({
            "title": "C", "steamrip_categories": [3]}))

    def test_igdb_genre_uses_card_or_meta(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        manager.set_filters({"igdb_genre": 7})
        self.assertTrue(manager._card_matches({
            "title": "A", "igdb_meta": {"genre_ids": [7]}}))
        self.assertTrue(manager._card_matches({
            "title": "B", "meta": {"igdb_meta": {"genre_ids": [7]}}}))
        self.assertFalse(manager._card_matches({"title": "C"}))


class SearchTests(unittest.TestCase):
    def test_ranking_is_exact_then_prefix_then_word_then_substring(self):
        catalog = FakeCatalog({
            "DOOM": {},
            "DOOM Eternal": {},
            "The DOOM": {},
            "Random DOOM Game": {},
            "Unrelated": {},
        })
        manager = _manager(catalog)
        manager.set_filters({"search": "doom"})
        titles = [card["title"] for card in manager.search_cards()]
        self.assertEqual(titles,
                         ["DOOM", "DOOM Eternal", "Random DOOM Game", "The DOOM"])

    def test_limit_is_120(self):
        catalog = FakeCatalog({f"Game {index:03d}": {} for index in range(130)})
        manager = _manager(catalog)
        manager.set_filters({"search": "game"})
        self.assertEqual(len(manager.search_cards()), 120)


class SortTests(unittest.TestCase):
    def _manager(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        manager._post_date_for = lambda title: {
            "A": "2026-01-01", "B": "2026-02-01"}.get(title, "")
        return manager

    def test_title_sort_and_undated_cards(self):
        manager = self._manager()
        cards = [{"title": "B"}, {"title": "A"}, {"title": "C"}]
        manager.set_filters({"sort": "title_asc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["A", "B", "C"])
        manager.set_filters({"sort": "title_desc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["C", "B", "A"])

    def test_added_sort_puts_undated_last(self):
        manager = self._manager()
        cards = [{"title": "C"}, {"title": "A"}, {"title": "B"}]
        manager.set_filters({"sort": "added_desc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["B", "A", "C"])
        manager.set_filters({"sort": "added_asc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["A", "B", "C"])

    def test_release_and_rating_sorts_put_missing_last(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({})))
        cards = [
            {"title": "A", "igdb": {"first_release_date": 10,
                                    "total_rating": 70}},
            {"title": "B", "igdb": {"first_release_date": 20,
                                    "total_rating": 90}},
            {"title": "C"},
        ]
        manager.set_filters({"sort": "release_desc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["B", "A", "C"])
        manager.set_filters({"sort": "rating_desc"})
        self.assertEqual([c["title"] for c in manager.sort_cards(cards)],
                         ["B", "A", "C"])

    def test_no_sort_returns_a_new_list(self):
        manager = self._manager()
        cards = [{"title": "B"}, {"title": "A"}]
        result = manager.sort_cards(cards)
        self.assertEqual(result, cards)
        self.assertIsNot(result, cards)


if __name__ == "__main__":
    unittest.main()
