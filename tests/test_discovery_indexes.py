"""Characterization of Discovery derived indexes (G2C).

The catalog match index is rebuilt from a token made of catalog identity,
length and file mtime. The tests below pin that rule and every place that
invalidates the card lookup or the catalog order, so the ownership move does
not lose an invalidation path.
"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core import secrets
from core.master_catalog import MasterCatalog
from managers.discovery_manager import CACHE_SCHEMA, DiscoveryManager
from tests.discovery_fakes import FakeCatalog, empty_services, fake_services


class MatchIndexTokenTests(unittest.TestCase):
    def test_same_token_reuses_the_same_index_object(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({"A": {}})))
        first = manager._build_index()
        second = manager._build_index()
        self.assertIs(second, first)

    def test_catalog_replacement_rebuilds_and_clears_the_match_cache(self):
        holder = {"catalog": FakeCatalog({"A": {}})}
        manager = DiscoveryManager(fake_services(catalog=None))
        manager._services = type(manager._services)(
            installed_games=lambda: {},
            catalog=lambda: holder["catalog"],
            sync_folder=lambda: "",
            cover_path=lambda _title: "",
        )
        first = manager._build_index()
        manager._match_index.cache["doom"] = "A"
        holder["catalog"] = FakeCatalog({"B": {}})
        second = manager._build_index()
        self.assertIsNot(second, first)
        self.assertEqual(manager._match_index.cache, {})

    def test_length_change_rebuilds_the_index(self):
        catalog = FakeCatalog({"A": {}})
        manager = DiscoveryManager(fake_services(catalog=catalog))
        first = manager._build_index()
        catalog._entries["B"] = {}
        second = manager._build_index()
        self.assertIsNot(second, first)

    def test_mtime_change_rebuilds_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.db"
            db.write_text("v1", encoding="utf-8")
            catalog = FakeCatalog({"A": {}}, db_path=str(db))
            manager = DiscoveryManager(fake_services(catalog=catalog))
            first = manager._build_index()
            os.utime(db, (time.time() + 10, time.time() + 10))
            second = manager._build_index()
        self.assertIsNot(second, first)

    def test_content_change_without_revision_or_mtime_keeps_the_token(self):
        # Fallback for a catalog-like provider without ``match_revision``: the
        # cached index keeps its previous mapping until the file mtime changes.
        # The real MasterCatalog now supplies the revision (see
        # CatalogRevisionTests), so this path is not used by the app.
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.db"
            db.write_text("v1", encoding="utf-8")
            catalog = FakeCatalog({"A": {}}, db_path=str(db))
            manager = DiscoveryManager(fake_services(catalog=catalog))
            first = manager._build_index()
            catalog._entries["A"]["steam_id"] = "42"
            second = manager._build_index()
        self.assertIs(second, first)
        self.assertNotIn("42", second["appid"])

    def test_the_token_keeps_a_strong_reference_to_the_catalog(self):
        catalog = FakeCatalog({"A": {}})
        manager = DiscoveryManager(fake_services(catalog=catalog))
        manager._build_index()
        self.assertIs(manager._match_index.catalog_ref, catalog)

    def test_the_manager_has_no_parallel_match_state(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({"A": {}})))
        for name in ("_index", "_index_token", "_match_cache", "_token_catalog"):
            self.assertFalse(hasattr(manager, name), name)

    def test_invalidate_drops_the_index_and_cache(self):
        manager = DiscoveryManager(fake_services(catalog=FakeCatalog({"A": {}})))
        manager._build_index()
        manager._match_index.cache["x"] = "A"
        manager._match_index.invalidate()
        self.assertIsNone(manager._match_index.catalog_ref)
        self.assertEqual(manager._match_index.cache, {})
        manager._build_index()


class CardIndexInvalidationTests(unittest.TestCase):
    def _manager(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"row": {"cards": [{"title": "Old"}]}}
        manager._card_index = {"Old": {"title": "Old"}}
        return manager

    def test_append_matched_invalidates(self):
        manager = self._manager()
        with mock.patch.object(manager, "_match_games",
                               return_value=[{"title": "New"}]), \
                mock.patch.object(manager, "_catalog_size", return_value=1):
            manager._append_matched("row", [{"name": "New"}])
        self.assertIsNone(manager._card_index)

    def test_rebuild_matched_invalidates(self):
        manager = self._manager()
        manager._data["row"]["games"] = [{"name": "Old"}]
        with mock.patch.object(manager, "_match_games", return_value=[]):
            manager._rebuild_matched("row")
        self.assertIsNone(manager._card_index)

    def test_native_first_page_invalidates(self):
        manager = self._manager()
        manager._data["row"]["source"] = "steamrip"
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_match_games", return_value=[]), \
                mock.patch.object(manager, "_catalog_size", return_value=1), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_native_page("row", [{"title": "New"}], 1, 1)
        self.assertIsNone(manager._card_index)

    def test_updates_ready_invalidates(self):
        manager = self._manager()
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_match_games", return_value=[]), \
                mock.patch.object(manager, "_catalog_size", return_value=1), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_updates_ready([{"title": "New"}])
        self.assertIsNone(manager._card_index)

    def test_load_cache_starts_without_an_index(self):
        manager = self._manager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch(
                "managers.discovery_store.cache_path", return_value=str(path)
            ):
                manager._load_cache()
        self.assertIsNone(manager._card_index)


class CatalogOrderInvalidationTests(unittest.TestCase):
    def test_posts_index_ready_resets_the_order_and_all_games(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"all_games": {
            "cards": [{"title": "Old"}],
            "next_offset": 20,
            "has_more": False,
            "_dirty": False,
            "schema": CACHE_SCHEMA,
        }}
        manager._catalog_order = ["A", "B"]
        manager._catalog_order_token = (1, 2, 3)
        seen = []
        manager.categories_changed.connect(lambda: seen.append(True))
        manager._on_posts_index_ready({"posts": {"u": {"date": "2026-01-01"}}})
        self.assertEqual(manager._posts_index,
                         {"u": {"date": "2026-01-01"}})
        self.assertIsNone(manager._catalog_order)
        entry = manager._data["all_games"]
        self.assertEqual(entry["cards"], [])
        self.assertEqual(entry["next_offset"], 0)
        self.assertTrue(entry["has_more"])
        self.assertTrue(entry["_dirty"])
        self.assertEqual(seen, [True])


class CatalogRevisionTests(unittest.TestCase):
    def test_match_fields_invalidate_but_enrichment_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = MasterCatalog(str(Path(tmp) / "catalog.json"))
            try:
                with mock.patch.object(catalog._save_timer, "start"):
                    catalog.set_url("Game", "https://x/game")
                manager = DiscoveryManager(fake_services(catalog=catalog))
                first = manager._build_index()
                self.assertEqual(first["urls"]["https://x/game"], "Game")

                with mock.patch.object(catalog._save_timer, "start"):
                    catalog.set_game_field("Game", "igdb_meta", {"rating": 90})
                self.assertIs(manager._build_index(), first)

                with mock.patch.object(catalog._save_timer, "start"):
                    catalog.set_game_field("Game", "steam_id", "42")
                second = manager._build_index()
                self.assertIsNot(second, first)
                self.assertEqual(second["appid"]["42"], "Game")

                with mock.patch.object(catalog._save_timer, "start"):
                    catalog.set_url("Game", "https://x/other")
                third = manager._build_index()
                self.assertIsNot(third, second)
                self.assertEqual(third["urls"]["https://x/other"], "Game")
            finally:
                catalog._save_timer.stop()


if __name__ == "__main__":
    unittest.main()
