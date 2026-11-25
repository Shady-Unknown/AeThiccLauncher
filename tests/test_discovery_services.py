"""DiscoveryManager with explicit services: no window, no stale references."""
import tempfile
import unittest
from pathlib import Path

from core.paths import PathManager
from managers.discovery_manager import DiscoveryManager, DiscoveryServices
from tests.discovery_fakes import FakeCatalog


def _manager(services):
    return DiscoveryManager(services)


class DynamicCatalogTests(unittest.TestCase):
    def test_catalog_replacement_is_visible_to_matching(self):
        holder = {"catalog": FakeCatalog({"DOOM Eternal": {"steam_id": "1"}})}
        manager = _manager(DiscoveryServices(
            installed_games=lambda: {},
            catalog=lambda: holder["catalog"],
            sync_folder=lambda: "",
            cover_path=lambda _title: "cover.png",
        ))

        first = manager._match_games([{"name": "DOOM Eternal", "steam_appid": ""}])
        self.assertEqual([card["title"] for card in first], ["DOOM Eternal"])

        holder["catalog"] = FakeCatalog({"Quake Champions": {"steam_id": "2"}})
        second = manager._match_games([{"name": "Quake Champions", "steam_appid": ""}])

        self.assertEqual([card["title"] for card in second], ["Quake Champions"])
        self.assertEqual(second[0]["cover_path"], "cover.png")

    def test_catalog_order_cache_is_invalidated_by_a_new_catalog(self):
        holder = {"catalog": FakeCatalog({"A": {}, "B": {}})}
        manager = _manager(DiscoveryServices(
            installed_games=lambda: {},
            catalog=lambda: holder["catalog"],
            sync_folder=lambda: "",
            cover_path=lambda _title: "",
        ))

        self.assertEqual(manager._catalog_title_order(), ["A", "B"])

        holder["catalog"] = FakeCatalog({"A": {}, "B": {}, "C": {}})

        self.assertEqual(manager._catalog_title_order(), ["A", "B", "C"])


class DynamicInstalledGamesTests(unittest.TestCase):
    def test_installed_games_replacement_is_visible(self):
        holder = {"installed": {"Game A": {}}}
        manager = _manager(DiscoveryServices(
            installed_games=lambda: holder["installed"],
            catalog=lambda: FakeCatalog({"Game A": {}, "Game B": {}}),
            sync_folder=lambda: "",
            cover_path=lambda title: f"{title}.jpg",
        ))

        first = manager._catalog_cards(["Game A", "Game B"])
        self.assertEqual(first[0]["cover_path"], "Game A.jpg")

        holder["installed"] = {"Game B": {}}
        self.assertEqual(manager.installed_games, {"Game B": {}})
        second = manager._catalog_cards(["Game B"])
        self.assertEqual(second[0]["cover_path"], "Game B.jpg")


class LifecycleTests(unittest.TestCase):
    def test_staging_until_savegames_manager_exists(self):
        holder = {"sync": ""}
        manager = _manager(DiscoveryServices(
            installed_games=lambda: {},
            catalog=lambda: None,
            sync_folder=lambda: holder["sync"],
            cover_path=lambda _title: "",
        ))

        self.assertEqual(
            manager._sync_folder(), str(PathManager.get_staging_dir())
        )

        with tempfile.TemporaryDirectory() as tmp:
            holder["sync"] = tmp
            self.assertEqual(manager._sync_folder(), tmp)


class ProviderErrorTests(unittest.TestCase):
    def test_cover_provider_error_does_not_break_the_card(self):
        def broken_cover(_title):
            raise RuntimeError("boom")

        manager = _manager(DiscoveryServices(
            installed_games=lambda: {},
            catalog=lambda: FakeCatalog({"Game": {}}),
            sync_folder=lambda: "",
            cover_path=broken_cover,
        ))

        with self.assertLogs("managers.discovery_manager", level="ERROR"):
            cards = manager._match_games([{"name": "Game", "steam_appid": ""}])

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["cover_path"], "")

    def test_sync_provider_error_falls_back_to_staging(self):
        def broken_sync():
            raise RuntimeError("boom")

        manager = _manager(DiscoveryServices(
            installed_games=lambda: {},
            catalog=lambda: None,
            sync_folder=broken_sync,
            cover_path=lambda _title: "",
        ))

        with self.assertLogs("managers.discovery_manager", level="ERROR"):
            folder = manager._sync_folder()

        self.assertEqual(folder, str(PathManager.get_staging_dir()))


if __name__ == "__main__":
    unittest.main()
