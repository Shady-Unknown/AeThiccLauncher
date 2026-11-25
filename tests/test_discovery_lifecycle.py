"""Characterization of Discovery callbacks, cleanup and shutdown.

Worker extraction is out of scope for G2, so these tests pin the asynchronous
contract the manager keeps: identity-based cleanup, signal emission and an
ordered, tolerant shutdown.
"""
import unittest
from unittest import mock

from managers.discovery_manager import (
    CACHE_SCHEMA, DiscoveryManager, MAX_OFFSET,
)
from tests.discovery_fakes import FakeCatalog, empty_services, fake_services


class CleanupIdentityTests(unittest.TestCase):
    def setUp(self):
        self.manager = DiscoveryManager(empty_services())

    def test_old_native_worker_does_not_clear_the_new_batch(self):
        old, new = object(), object()
        self.manager._native_workers = [old, new]
        self.manager._native_batch = new
        self.manager._native_loading = {"row"}
        self.manager._cleanup_native_worker(old)
        self.assertEqual(self.manager._native_workers, [new])
        self.assertIs(self.manager._native_batch, new)
        self.assertEqual(self.manager._native_loading, {"row"})
        self.manager._cleanup_native_worker(new)
        self.assertIsNone(self.manager._native_batch)

    def test_old_index_worker_does_not_clear_the_new_one(self):
        old, new = object(), object()
        self.manager._index_worker = new
        self.manager._cleanup_index_worker(old)
        self.assertIs(self.manager._index_worker, new)
        self.manager._cleanup_index_worker(new)
        self.assertIsNone(self.manager._index_worker)

    def test_old_updates_worker_does_not_clear_the_new_one(self):
        old, new = object(), object()
        self.manager._updates_worker = new
        self.manager._cleanup_updates_worker(old)
        self.assertIs(self.manager._updates_worker, new)
        self.manager._cleanup_updates_worker(new)
        self.assertIsNone(self.manager._updates_worker)

    def test_old_meta_worker_does_not_clear_the_new_one(self):
        class Worker:
            _jobs = [("Old Game", "")]

        old, new = Worker(), object()
        self.manager._meta_worker = new
        self.manager._meta_inflight = {"Old Game"}
        self.manager._meta_queue = []
        with mock.patch("managers.discovery_manager.QTimer.singleShot") as shot:
            self.manager._cleanup_meta_worker(old)
        self.assertIs(self.manager._meta_worker, new)
        self.assertEqual(self.manager._meta_inflight, set())
        shot.assert_not_called()


class PageTests(unittest.TestCase):
    def setUp(self):
        self.manager = DiscoveryManager(empty_services())
        self.manager._data = {"trending": {
            "title": "Trending",
            "source": "igdb",
            "games": [],
            "next_offset": 0,
            "has_more": True,
            "fetched_at": 1,
            "schema": CACHE_SCHEMA,
            "cards": [],
        }}

    def test_empty_page_stops_pagination(self):
        pages = []
        self.manager.page_changed.connect(pages.append)
        with mock.patch.object(self.manager, "_append_matched", return_value=0), \
                mock.patch.object(self.manager, "_save_cache"):
            self.manager._on_page("trending", [], has_more=True)
        entry = self.manager._data["trending"]
        self.assertFalse(entry["has_more"])
        self.assertEqual(entry["next_offset"], MAX_OFFSET)
        self.assertEqual(pages, ["trending"])

    def test_non_empty_page_advances_the_offset(self):
        pages = []
        self.manager.page_changed.connect(pages.append)
        with mock.patch.object(self.manager, "_append_matched", return_value=2), \
                mock.patch.object(self.manager, "_save_cache"):
            self.manager._on_page("trending", [{"name": "A"}, {"name": "B"}],
                                  has_more=False)
        entry = self.manager._data["trending"]
        self.assertEqual(entry["next_offset"], 2)
        self.assertFalse(entry["has_more"])
        self.assertEqual(entry["games"], [{"name": "A"}, {"name": "B"}])
        self.assertEqual(pages, ["trending"])


class CategoriesTests(unittest.TestCase):
    def test_empty_igdb_response_keeps_the_cached_row(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"trending": {
            "games": [{"name": "Cached"}],
            "cards": [{"title": "Cached"}],
            "fetched_at": 123,
        }}
        with mock.patch.object(manager, "_save_cache"), \
                mock.patch.object(manager, "rebuild_all"), \
                mock.patch("managers.discovery_manager.QTimer.singleShot") as shot:
            manager._on_categories({"trending": []})
            manager._on_categories({"trending": []})
        entry = manager._data["trending"]
        self.assertEqual(entry["cards"], [{"title": "Cached"}])
        self.assertEqual(entry["fetched_at"], 0)
        self.assertEqual(entry["schema"], CACHE_SCHEMA)
        # A row with cached cards is not considered empty: no retry is queued.
        self.assertFalse(manager._igdb_retry_scheduled)
        shot.assert_not_called()

    def test_truly_empty_row_queues_one_retry(self):
        manager = DiscoveryManager(empty_services())
        manager._data = {"trending": {"games": [], "cards": []}}
        with mock.patch.object(manager, "_save_cache"), \
                mock.patch.object(manager, "rebuild_all"), \
                mock.patch("managers.discovery_manager.QTimer.singleShot") as shot:
            manager._on_categories({"trending": []})
            manager._on_categories({"trending": []})
        self.assertTrue(manager._igdb_retry_scheduled)
        self.assertEqual(shot.call_count, 1)


class MetaTests(unittest.TestCase):
    def test_meta_results_update_catalog_and_card(self):
        catalog = FakeCatalog({"Game": {}})
        manager = DiscoveryManager(fake_services(catalog=catalog))
        card = {"title": "Game", "meta": {}}
        manager._data = {"row": {"cards": [card]}}
        seen = []
        manager.meta_ready.connect(lambda data: seen.append(data))
        meta = {"steam_appid": "42", "total_rating": 91}

        manager._on_meta_ready({"Game": meta})

        self.assertEqual(catalog.get_game_field("Game", "igdb_meta"), meta)
        self.assertEqual(catalog.get_game_field("Game", "steam_id"), "42")
        self.assertEqual(card["igdb_meta"], meta)
        self.assertEqual(card["meta"]["igdb_meta"], meta)
        self.assertEqual(seen, [{"Game": meta}])

    def test_taxonomy_is_forwarded_only_when_present(self):
        manager = DiscoveryManager(empty_services())
        seen = []
        manager.taxonomy_ready.connect(lambda data: seen.append(data))
        manager._on_taxonomy({})
        manager._on_taxonomy({"genres": {"1": "Action"}})
        self.assertEqual(seen, [{"genres": {"1": "Action"}}])
        self.assertEqual(manager.taxonomy_genres(), [("1", "Action")])


class ShutdownTests(unittest.TestCase):
    class FakeTimer:
        def __init__(self):
            self.stops = 0

        def stop(self):
            self.stops += 1

    class FakeWorker:
        def __init__(self, running=True, stop_error=False):
            self.running = running
            self.stop_error = stop_error
            self.stops = 0
            self.waits = []

        def isRunning(self):
            return self.running

        def stop(self):
            self.stops += 1
            if self.stop_error:
                raise RuntimeError("already gone")
            self.running = False

        def wait(self, ms):
            self.waits.append(ms)
            self.running = False
            return True

    def _manager(self):
        manager = DiscoveryManager(empty_services())
        for name in ("_save_timer", "_posts_timer", "_native_refresh_timer",
                     "_igdb_refresh_timer"):
            setattr(manager, name, self.FakeTimer())
        manager._posts_worker = self.FakeWorker()
        manager._updates_worker = self.FakeWorker()
        manager._cover_worker = self.FakeWorker()
        manager._worker = self.FakeWorker()
        manager._native_workers = [self.FakeWorker()]
        manager._index_worker = self.FakeWorker()
        manager._meta_worker = self.FakeWorker()
        return manager

    def test_shutdown_stops_timers_and_workers(self):
        manager = self._manager()
        manager.shutdown()
        for name in ("_save_timer", "_posts_timer", "_native_refresh_timer",
                     "_igdb_refresh_timer"):
            self.assertEqual(getattr(manager, name).stops, 1, name)
        self.assertEqual(manager._posts_worker.waits, [3000])
        self.assertEqual(manager._updates_worker.waits, [1500])
        self.assertEqual(manager._cover_worker.waits, [3000])
        self.assertEqual(manager._worker.waits, [3000])
        self.assertEqual(manager._native_workers[0].waits, [2000])
        self.assertEqual(manager._index_worker.waits, [2000])
        self.assertEqual(manager._meta_worker.waits, [3000])

    def test_shutdown_is_idempotent(self):
        manager = self._manager()
        manager.shutdown()
        manager.shutdown()
        self.assertEqual(manager._posts_worker.waits, [3000])

    def test_shutdown_tolerates_a_worker_that_is_already_stopped(self):
        manager = self._manager()
        manager._posts_worker = self.FakeWorker(stop_error=True)
        manager.shutdown()

    def test_shutdown_makes_the_last_cache_attempt(self):
        manager = self._manager()
        manager._cache_unsaved = True
        with mock.patch("managers.discovery_store.save_rows") as save:
            manager.shutdown()
        save.assert_called_once()
        self.assertFalse(manager._cache_unsaved)

    def test_shutdown_does_not_schedule_another_retry(self):
        manager = self._manager()
        manager._cache_unsaved = True
        with mock.patch("managers.discovery_store.save_rows",
                        side_effect=OSError("disk full")), \
                mock.patch.object(manager._cache_retry_timer, "start") as start:
            with self.assertLogs("managers.discovery_manager", level="ERROR"):
                manager.shutdown()
        start.assert_not_called()
        self.assertTrue(manager._cache_unsaved)


if __name__ == "__main__":
    unittest.main()
