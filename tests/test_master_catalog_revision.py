"""Incremental match revision in MasterCatalog (G2-Fix 3)."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.master_catalog import MasterCatalog


class MatchRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "master_catalog.json"
        self.catalog = MasterCatalog(str(self.path))
        self.addCleanup(self.catalog._save_timer.stop)
        # These tests only inspect the revision; keep the debounce timer quiet.
        self._timer = mock.patch.object(self.catalog._save_timer, "start")
        self._timer.start()
        self.addCleanup(self._timer.stop)

    def test_new_title_and_url_change_bump(self):
        before = self.catalog.match_revision
        self.catalog.set_url("Game", "https://x/game")
        self.assertGreater(self.catalog.match_revision, before)
        before = self.catalog.match_revision
        self.catalog.set_url("Game", "https://x/other")
        self.assertGreater(self.catalog.match_revision, before)

    def test_setting_the_same_url_does_not_bump(self):
        self.catalog.set_url("Game", "https://x/game")
        before = self.catalog.match_revision
        self.catalog.set_url("Game", "https://x/game")
        self.assertEqual(self.catalog.match_revision, before)

    def test_identity_fields_bump_only_when_the_value_changes(self):
        self.catalog.set_url("Game", "https://x/game")
        before = self.catalog.match_revision
        self.catalog.set_game_field("Game", "steam_id", "42")
        self.assertGreater(self.catalog.match_revision, before)
        before = self.catalog.match_revision
        self.catalog.set_game_field("Game", "steam_id", "42")
        self.assertEqual(self.catalog.match_revision, before)
        self.catalog.set_game_field("Game", "raw_title", "Raw")
        self.assertGreater(self.catalog.match_revision, before)

    def test_sgdb_appid_bumps_but_artwork_does_not(self):
        self.catalog.set_url("Game", "https://x/game")
        self.catalog.set_game_field("Game", "sgdb_assets", {
            "steam_app_id": "42", "grid": "a"})
        self.catalog.set_game_field("Game", "sgdb_assets", {
            "steam_app_id": "42", "grid": "b"})
        before = self.catalog.match_revision
        self.catalog.set_game_field("Game", "sgdb_assets", {
            "steam_app_id": "42", "grid": "c"})
        self.assertEqual(self.catalog.match_revision, before)
        self.catalog.set_game_field("Game", "sgdb_assets", {
            "steam_app_id": "43", "grid": "c"})
        self.assertGreater(self.catalog.match_revision, before)

    def test_enrichment_fields_do_not_bump(self):
        self.catalog.set_url("Game", "https://x/game")
        before = self.catalog.match_revision
        self.catalog.set_game_field("Game", "igdb_meta", {"rating": 90})
        self.catalog.set_game_field("Game", "prices", {"steam": 10})
        self.catalog.set_version_status("Game", {"posted_build": 1})
        self.assertEqual(self.catalog.match_revision, before)

    def test_batch_refresh_bumps_once_and_ignores_noops(self):
        self.catalog.refresh_from_remote({"Game": "https://x/game"})
        before = self.catalog.match_revision
        self.catalog.refresh_from_remote({"Game": "https://x/game"})
        self.assertEqual(self.catalog.match_revision, before)
        self.catalog.refresh_from_remote({"Game": "https://x/other"})
        self.assertGreater(self.catalog.match_revision, before)
        before = self.catalog.match_revision
        self.catalog.refresh_from_remote({
            "Game": {"url": "https://x/other", "igdb_meta": {"rating": 80}},
        })
        self.assertEqual(self.catalog.match_revision, before)

    def test_store_urls_batch_is_idempotent(self):
        self.catalog.set_store_urls("Game", "https://store.steampowered.com/app/42/", "")
        first = self.catalog.match_revision
        self.catalog.set_store_urls("Game", "https://store.steampowered.com/app/42/", "")
        self.assertEqual(self.catalog.match_revision, first)
        # The existing steam_id wins, so a different store URL alone does not
        # change the match projection (current behaviour, not a revision bug).
        self.catalog.set_store_urls("Game", "https://store.steampowered.com/app/43/", "")
        self.assertEqual(self.catalog.match_revision, first)
        self.catalog.set_game_field("Game", "steam_id", "43")
        self.assertGreater(self.catalog.match_revision, first)


if __name__ == "__main__":
    unittest.main()
