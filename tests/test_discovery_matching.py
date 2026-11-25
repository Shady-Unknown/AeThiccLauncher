"""Characterization of Discovery matching (G2A).

These tests pin the current matching strategy before it moves to
``discovery_matching``: URL first, then exact normalized title, then appid,
then a fuzzy containment scan. Some outcomes are known to be permissive (for
example "DOOM" matches "DOOM Eternal" and "DOOM 3"); they are recorded here as
current behaviour, not as desired behaviour, and must not change during the
split.
"""
import unittest

from managers import discovery_matching
from managers.discovery_manager import DiscoveryManager
from tests.discovery_fakes import FakeCatalog, empty_services, fake_services


def _manager(catalog):
    return DiscoveryManager(fake_services(
        catalog=catalog,
        cover=lambda title: f"covers/{title}.jpg",
    ))


class UrlAndIdTests(unittest.TestCase):
    def test_url_wins_over_a_mismatched_title(self):
        catalog = FakeCatalog({
            "DOOM": {"url": "https://steamrip.com/doom"},
        })
        manager = _manager(catalog)
        cards = manager._match_games([
            {"title": "Wrong Name", "url": "https://steamrip.com/doom/"},
        ], source="steamrip")
        self.assertEqual([card["title"] for card in cards], ["DOOM"])
        self.assertEqual(cards[0]["url"], "https://steamrip.com/doom")

    def test_steam_appid_matches_before_the_name(self):
        catalog = FakeCatalog({
            "DOOM Eternal": {"steam_id": "782330"},
        })
        manager = _manager(catalog)
        cards = manager._match_games([
            {"name": "Something Else", "steam_appid": "782330"},
        ])
        self.assertEqual([card["title"] for card in cards], ["DOOM Eternal"])

    def test_sgdb_steam_app_id_is_an_appid_fallback(self):
        catalog = FakeCatalog({
            "DOOM Eternal": {"sgdb_assets": {"steam_app_id": "782330"}},
        })
        manager = _manager(catalog)
        cards = manager._match_games([
            {"name": "Something Else", "steam_appid": "782330"},
        ])
        self.assertEqual([card["title"] for card in cards], ["DOOM Eternal"])


class TitleTests(unittest.TestCase):
    def test_exact_match_uses_the_shared_normalizer(self):
        catalog = FakeCatalog({
            "Pokémon™: Deluxe Edition": {},
        })
        manager = _manager(catalog)
        cards = manager._match_games([{"name": "POKEMON", "steam_appid": ""}])
        self.assertEqual([card["title"] for card in cards],
                         ["Pokémon™: Deluxe Edition"])

    def test_raw_title_is_an_exact_alias(self):
        catalog = FakeCatalog({
            "Display Title": {"raw_title": "Raw Thing"},
        })
        manager = _manager(catalog)
        cards = manager._match_games([
            {"name": "Raw Thing", "steam_appid": ""},
        ])
        self.assertEqual([card["title"] for card in cards], ["Display Title"])

    def test_fuzzy_containment_prefers_the_longest_title(self):
        catalog = FakeCatalog({"DOOM": {}, "DOOM Eternal": {}})
        manager = _manager(catalog)
        cards = manager._match_games([
            {"name": "DOOM Eternal Deluxe", "steam_appid": ""},
        ])
        self.assertEqual([card["title"] for card in cards], ["DOOM Eternal"])

    def test_current_partial_match_is_preserved(self):
        # Not a desired rule: it is the strategy this split must preserve.
        catalog = FakeCatalog({"DOOM Eternal": {}})
        manager = _manager(catalog)
        cards = manager._match_games([{"name": "DOOM", "steam_appid": ""}])
        self.assertEqual([card["title"] for card in cards], ["DOOM Eternal"])

    def test_current_sequel_match_is_preserved(self):
        # Same as above: fuzzy containment matches "DOOM" with "DOOM 3".
        catalog = FakeCatalog({"DOOM 3": {}})
        manager = _manager(catalog)
        cards = manager._match_games([{"name": "DOOM", "steam_appid": ""}])
        self.assertEqual([card["title"] for card in cards], ["DOOM 3"])

    def test_fuzzy_entries_shorter_than_three_chars_are_ignored(self):
        catalog = FakeCatalog({"AB": {}})
        manager = _manager(catalog)
        cards = manager._match_games([{"name": "XAB", "steam_appid": ""}])
        self.assertEqual(cards, [])


class CardTests(unittest.TestCase):
    def test_duplicate_titles_are_emitted_once(self):
        catalog = FakeCatalog({"Game": {}})
        manager = _manager(catalog)
        cards = manager._match_games([
            {"name": "Game", "steam_appid": ""},
            {"name": "Game", "steam_appid": "", "url": "https://x/game"},
        ])
        self.assertEqual([card["title"] for card in cards], ["Game"])

    def test_steamrip_categories_are_merged_and_sorted(self):
        catalog = FakeCatalog({
            "Game": {"steamrip_categories": [9, 5]},
        })
        manager = _manager(catalog)
        cards = manager._match_games([{
            "title": "Game",
            "url": "https://steamrip.com/game",
            "categories": [9, 3, 9],
        }], source="steamrip")
        self.assertEqual(cards[0]["steamrip_categories"], [3, 5, 9])

    def test_igdb_cards_keep_the_payload_and_have_no_post_url(self):
        catalog = FakeCatalog({"Game": {}})
        manager = _manager(catalog)
        payload = {"name": "Game", "steam_appid": "", "total_rating": 90}
        cards = manager._match_games([payload])
        self.assertEqual(cards[0]["igdb"], payload)
        self.assertEqual(cards[0]["url"], "")
        self.assertEqual(cards[0]["source"], "igdb")

    def test_cover_and_meta_are_copied_not_aliased(self):
        entry = {"steam_id": "1", "igdb_meta": {"rating": 80}}
        catalog = FakeCatalog({"Game": entry})
        manager = _manager(catalog)
        card = manager._match_games([{"name": "Game"}])[0]
        self.assertEqual(card["meta"], entry)
        self.assertIsNot(card["meta"], entry)
        self.assertEqual(card["cover_path"], "covers/Game.jpg")


class ModuleFunctionTests(unittest.TestCase):
    """Document which functions are pure and which mutate the shared cache."""

    def test_build_index_accepts_a_missing_catalog(self):
        self.assertEqual(
            discovery_matching.build_index(None),
            {"appid": {}, "exact": {}, "cleaned": [], "urls": {}},
        )

    def test_missing_catalog_produces_no_cards(self):
        manager = DiscoveryManager(empty_services())
        self.assertEqual(manager._match_games([{"name": "Game"}]), [])

    def test_build_index_is_pure_and_does_not_mutate_the_catalog(self):
        catalog = FakeCatalog({"Game": {"steam_id": "1"}})
        first = discovery_matching.build_index(catalog)
        second = discovery_matching.build_index(catalog)
        self.assertEqual(first, second)
        self.assertEqual(dict(catalog._entries), {"Game": {"steam_id": "1"}})

    def test_match_game_memoizes_misses_in_the_given_cache(self):
        index = discovery_matching.build_index(FakeCatalog({"Game": {}}))
        cache = {}
        self.assertIsNone(
            discovery_matching.match_game({"name": "Unknown"}, index, cache))
        self.assertEqual(cache, {"unknown": ""})

    def test_build_cards_shares_the_cache_without_touching_the_input(self):
        catalog = FakeCatalog({"DOOM Eternal": {}})
        index = discovery_matching.build_index(catalog)
        games = [{"name": "DOOM", "steam_appid": ""}]
        cache = {}
        cards = discovery_matching.build_cards(
            games, index, cache, catalog, lambda title: f"covers/{title}.jpg")
        self.assertEqual([card["title"] for card in cards], ["DOOM Eternal"])
        self.assertEqual(games, [{"name": "DOOM", "steam_appid": ""}])
        self.assertEqual(cache, {"doom": "DOOM Eternal"})

    def test_match_post_uses_the_shared_cache_for_the_fuzzy_fallback(self):
        index = discovery_matching.build_index(FakeCatalog({"DOOM Eternal": {}}))
        cache = {}
        title = discovery_matching.match_post(
            {"title": "DOOM", "url": ""}, index, cache)
        self.assertEqual(title, "DOOM Eternal")
        self.assertEqual(cache, {"doom": "DOOM Eternal"})


if __name__ == "__main__":
    unittest.main()
