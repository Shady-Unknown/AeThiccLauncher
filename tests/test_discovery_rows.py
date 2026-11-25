"""First-page refresh of the SteamRip rows.

The rows used to append every fetched page to the cached cards, so the newest
arrivals landed at the end of the shelf and the visible head never changed:
"Recently Added" kept showing the same old games even though the site had new
posts. The first page is now a snapshot that replaces the row.
"""
import time
import unittest
from unittest import mock

from core import secrets
from managers.discovery_manager import CACHE_SCHEMA, DiscoveryManager
from tests.discovery_fakes import empty_services


def _entry(cards=(), next_page=5, fetched_at=0, catalog_size=10):
    return {
        "title": "Recently Added",
        "source": "steamrip",
        "games": [],
        "next_page": next_page,
        "has_more": True,
        "fetched_at": fetched_at,
        "schema": CACHE_SCHEMA,
        "cards": list(cards),
        "catalog_size": catalog_size,
    }


class NativeRowResetTests(unittest.TestCase):
    def _manager(self, entry):
        manager = DiscoveryManager(empty_services())
        manager._data = {"recent_added": entry}
        return manager

    def test_first_page_replaces_the_snapshot(self):
        manager = self._manager(_entry(cards=[{"title": "Old"}], fetched_at=1))
        resets = []
        manager.row_reset.connect(resets.append)
        fresh = [{"title": "New A", "url": "a"}, {"title": "New B", "url": "b"}]
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(
                    manager, "_match_games",
                    side_effect=lambda posts, source="igdb": list(posts)), \
                mock.patch.object(manager, "_catalog_size", return_value=42), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_native_page("recent_added", fresh, 1, 7)

        entry = manager._data["recent_added"]
        self.assertEqual([c["title"] for c in entry["cards"]],
                         ["New A", "New B"])
        self.assertEqual(entry["next_page"], 2)
        self.assertTrue(entry["has_more"])
        self.assertEqual(entry["catalog_size"], 42)
        self.assertGreater(entry["fetched_at"], 1)
        self.assertEqual(resets, ["recent_added"])

    def test_empty_first_page_keeps_the_cache(self):
        manager = self._manager(_entry(cards=[{"title": "Old"}], fetched_at=123))
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_native_page("recent_added", [], 1, 0)

        entry = manager._data["recent_added"]
        self.assertEqual(entry["cards"], [{"title": "Old"}])
        self.assertEqual(entry["fetched_at"], 123)

    def test_next_page_appends_without_touching_freshness(self):
        entry = _entry(cards=[{"title": "Old"}], fetched_at=777, next_page=2)
        manager = self._manager(entry)
        pages = []
        manager.page_changed.connect(pages.append)

        def fake_append(key, games, source="igdb"):
            entry["cards"].extend(games)
            return len(games)

        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_append_matched",
                                  side_effect=fake_append), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_native_page("recent_added",
                                    [{"title": "Older", "url": "u1"}], 2, 9)

        self.assertEqual(entry["next_page"], 3)
        self.assertEqual(entry["fetched_at"], 777)
        self.assertEqual([c["title"] for c in entry["cards"]],
                         ["Old", "Older"])
        self.assertEqual(pages, ["recent_added"])

    def test_duplicate_posts_are_ignored(self):
        entry = _entry(cards=[], next_page=3)
        entry["games"] = [{"title": "A", "url": "u"}]
        manager = self._manager(entry)
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_append_matched", return_value=0), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_native_page("recent_added",
                                    [{"title": "A", "url": "u"}], 3, 9)

        self.assertEqual(len(entry["games"]), 1)


class NativeFreshnessTests(unittest.TestCase):
    def _manager(self, fetched_at, catalog_size):
        manager = DiscoveryManager(empty_services())
        manager._data = {"recent_added": _entry(
            fetched_at=fetched_at, catalog_size=catalog_size)}
        return manager

    def test_catalog_change_forces_a_refetch(self):
        manager = self._manager(time.time(), catalog_size=10)
        with mock.patch.object(manager, "_catalog_size", return_value=11):
            self.assertTrue(manager._needs_fetch_native("recent_added"))

    def test_fresh_row_with_the_same_catalog_is_kept(self):
        manager = self._manager(time.time(), catalog_size=10)
        with mock.patch.object(manager, "_catalog_size", return_value=10):
            self.assertFalse(manager._needs_fetch_native("recent_added"))

    def test_expired_row_is_refetched(self):
        manager = self._manager(time.time() - 25 * 3600, catalog_size=10)
        with mock.patch.object(manager, "_catalog_size", return_value=10):
            self.assertTrue(manager._needs_fetch_native("sr_26"))


class CategoryRowOrderTests(unittest.TestCase):
    """A page appended while cards are still pending must not jump the queue."""
    def _row_class(self):
        from ui.tabs.discovery_tab import CategoryRow
        return CategoryRow

    def test_new_cards_wait_behind_pending_data(self):
        row_class = self._row_class()

        class Stub:
            _pending = [{"title": "P"}]
            _card_data = [{"title": "A"}]

            def _make_card(self, *args, **kwargs):
                raise AssertionError("must not build while data is pending")

        stub = Stub()
        row_class.append_cards(stub, [{"title": "N"}])
        self.assertEqual([c["title"] for c in stub._pending], ["P", "N"])
        self.assertEqual([c["title"] for c in stub._card_data], ["A"])

    def test_new_cards_are_built_in_order_at_the_end(self):
        row_class = self._row_class()

        class Stub:
            _pending = []
            _card_data = [{"title": "A"}]

            def __init__(self):
                self.built = []

            def _make_card(self, card_data, insert_at=None):
                self.built.append((card_data["title"], insert_at))

            def _update_container_width(self):
                pass

        stub = Stub()
        stub._build_cards = lambda lst: row_class._build_cards(stub, lst)
        row_class.append_cards(stub, [{"title": "B"}, {"title": "C"}])
        self.assertEqual([c["title"] for c in stub._card_data],
                         ["A", "B", "C"])
        self.assertEqual(stub.built, [("B", 1), ("C", 2)])


class RecentlyUpdatedRowTests(unittest.TestCase):
    """Recently Updated mirrors the site's curated page, not the REST feed."""

    def test_curated_page_replaces_the_row(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"recent_updates": _entry(
            cards=[{"title": "Old"}], next_page=2)}
        resets = []
        manager.row_reset.connect(resets.append)
        entries = [{"title": "A", "url": "a"}, {"title": "B", "url": "b"}]
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(
                    manager, "_match_games",
                    side_effect=lambda posts, source="igdb": list(posts)), \
                mock.patch.object(manager, "_catalog_size", return_value=42), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_updates_ready(entries)

        entry = manager._data["recent_updates"]
        self.assertEqual([c["title"] for c in entry["cards"]], ["A", "B"])
        self.assertFalse(entry["has_more"])
        self.assertIsNone(entry["next_page"])
        self.assertEqual(entry["catalog_size"], 42)
        self.assertGreater(entry["fetched_at"], 0)
        self.assertEqual(resets, ["recent_updates"])

    def test_empty_page_keeps_the_cache(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"recent_updates": _entry(
            cards=[{"title": "Old"}], fetched_at=123)}
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_save_cache"):
            manager._on_updates_ready([])

        entry = manager._data["recent_updates"]
        self.assertEqual(entry["cards"], [{"title": "Old"}])
        self.assertEqual(entry["fetched_at"], 123)

    def test_row_is_not_part_of_the_rest_batch(self):
        manager = DiscoveryManager(empty_services())
        with mock.patch.object(secrets, "has_igdb_credentials", return_value=False), \
                mock.patch.object(manager, "_needs_fetch_native", return_value=True):
            keys = [job[0] for job in manager._native_jobs_to_fetch()]
        self.assertIn("recent_added", keys)
        self.assertNotIn("recent_updates", keys)

    def test_request_more_is_disabled(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"recent_updates": _entry(next_page=2)}
        self.assertFalse(manager.request_more("recent_updates"))


class CoverJobSourcesTests(unittest.TestCase):
    """Cover jobs use the fast sources before the throttled SGDB fallbacks."""

    def test_card_page_url_is_used_without_a_catalog_entry(self):
        manager = DiscoveryManager(empty_services())
        job = manager._job_for_card(
            {"title": "Brand New", "url": "https://steamrip.com/brand-new"})
        self.assertEqual(job["url"], "https://steamrip.com/brand-new")

    def test_card_igdb_meta_cover_is_used(self):
        manager = DiscoveryManager(empty_services())
        job = manager._job_for_card(
            {"title": "New",
             "igdb_meta": {"cover_url": "https://images.igdb.com/x.jpg"}})
        self.assertEqual(job["igdb_cover_url"],
                         "https://images.igdb.com/x.jpg")

    def test_protocol_relative_igdb_cover_is_normalised(self):
        manager = DiscoveryManager(empty_services())
        job = manager._job_for_card(
            {"title": "New", "igdb": {"cover_url": "//images.igdb.com/x.jpg"}})
        self.assertEqual(job["igdb_cover_url"],
                         "https://images.igdb.com/x.jpg")


class CoverUpgradeTests(unittest.TestCase):
    """A horizontal fallback is upgraded once the vertical cover is known."""

    class _StubWorker:
        def __init__(self):
            self.jobs = []

        def enqueue(self, jobs, high=False):
            self.jobs.extend(jobs)

    def _manager(self):
        manager = DiscoveryManager(empty_services())
        manager._cover_worker = self._StubWorker()
        manager._data = {
            "row": {"cards": [{
                "title": "Game",
                "url": "https://steamrip.com/game",
                "cover_path": "horizontal.jpg",
            }]}
        }
        return manager

    def test_horizontal_cover_is_upgraded(self):
        manager = self._manager()
        with mock.patch(
            "managers.discovery_manager.MiniCoverManager.is_vertical_cover",
            return_value=False,
        ):
            manager.enqueue_covers(["Game"])
        self.assertEqual([job["title"] for job in manager._cover_worker.jobs], ["Game"])

    def test_force_bypasses_the_one_upgrade_guard(self):
        manager = self._manager()
        manager._cover_upgrade_attempted.add("Game")
        with mock.patch(
            "managers.discovery_manager.MiniCoverManager.is_vertical_cover",
            return_value=False,
        ):
            manager.enqueue_covers(["Game"], force=True)
        self.assertEqual(len(manager._cover_worker.jobs), 1)

    def test_vertical_cover_is_not_reenqueued(self):
        manager = self._manager()
        with mock.patch(
            "managers.discovery_manager.MiniCoverManager.is_vertical_cover",
            return_value=True,
        ):
            manager.enqueue_covers(["Game"], force=True)
        self.assertEqual(manager._cover_worker.jobs, [])


if __name__ == "__main__":
    unittest.main()
