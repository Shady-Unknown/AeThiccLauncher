import unittest

from managers.igdb_batch_manager import IGDBBatchManager
from network.igdb_details_worker import parse_details


class FakeDetails:
    def __init__(self, cached=None):
        self.cached = cached or {}
        self.saved = {}

    def get(self, title):
        return self.cached.get(title)

    def set(self, title, details):
        self.saved[title] = details


class FakeCatalog:
    def __init__(self, appids=None, igdb_ids=None, meta=None):
        self.appids = appids or {}
        self.igdb_ids = igdb_ids or {}
        self.meta = meta or {}
        self.fields = {}

    def get_game_field(self, title, field, default=None):
        if field == "igdb_meta":
            return self.meta.get(title, default)
        if field == "steam_id":
            return self.appids.get(title, "")
        return default

    def get_steam_app_id(self, title):
        return self.appids.get(title, "")

    def set_game_field(self, title, field, value):
        self.fields[(title, field)] = value


class ParseDetailsTests(unittest.TestCase):
    def test_parses_batch_fields(self):
        game = {
            "id": 42,
            "name": "Game",
            "first_release_date": 1730000000,
            "total_rating": 88.44,
            "summary": "A summary",
            "genres": [{"name": "RPG"}],
            "themes": [{"name": "Fantasy"}],
            "game_modes": [{"name": "Single player"}],
            "involved_companies": [
                {"company": {"name": "Dev"}, "developer": True},
                {"company": {"name": "Pub"}, "publisher": True},
            ],
            "cover": {"url": "//images.igdb.com/t_thumb/co1.jpg"},
            "screenshots": [{"url": "//images.igdb.com/t_thumb/s1.jpg"}],
            "videos": [{"video_id": "abc"}],
            "age_ratings": [{"organization": 2, "rating_category": 12}],
            "external_games": [
                {"category": 1, "uid": "123"},
                {"category": 2, "uid": "other"},
            ],
        }
        details = parse_details(game)
        self.assertEqual(details["igdb_id"], 42)
        self.assertEqual(details["release_year"], 2024)
        self.assertEqual(details["steam_appid"], "123")
        self.assertEqual(details["genres"], ["RPG"])
        self.assertEqual(details["themes"], ["Fantasy"])
        self.assertEqual(details["modes"], ["Single player"])
        self.assertEqual(details["developers"], ["Dev"])
        self.assertEqual(details["publishers"], ["Pub"])
        self.assertTrue(details["cover_url"].startswith("https:"))
        self.assertTrue(details["screenshots"][0].endswith("t_screenshot_big/s1.jpg"))
        self.assertEqual(details["age_ratings"], {"pegi": "18"})
        self.assertEqual(details["rating"], 88.4)


class IGDBBatchManagerTests(unittest.TestCase):
    def setUp(self):
        self.installed = {
            "A": {"playtime_seconds": 100, "steam_grid_id": "1"},
            "B": {"playtime_seconds": 500, "steam_grid_id": "2"},
            "C": {"playtime_seconds": 1000},
            "D": {"playtime_seconds": 2000, "steam_grid_id": "4"},
            "E": {"playtime_seconds": 50},
        }
        self.catalog = FakeCatalog(
            appids={"E": "5"},
            igdb_ids={"C": "99"},
            meta={"C": {"igdb_id": 99}},
        )
        self.details = FakeDetails(cached={"D": {"summary": "already there"}})
        self.manager = IGDBBatchManager(
            installed_games=self.installed,
            catalog=self.catalog,
            details=self.details,
            max_titles=2,
        )

    def test_collect_jobs_prioritises_playtime_and_skips_cached(self):
        jobs = self.manager.collect_jobs()
        self.assertEqual([job["title"] for job in jobs], ["C", "B"])
        self.assertEqual(jobs[0]["igdb_id"], "99")
        self.assertEqual(jobs[1]["appid"], "2")

        self.manager._max_titles = 3
        jobs = self.manager.collect_jobs()
        self.assertEqual([job["title"] for job in jobs], ["C", "B", "A"])
        self.assertEqual(jobs[2]["appid"], "1")

    def test_update_catalog_preserves_existing_fields(self):
        self.catalog.meta["A"] = {"genre_ids": [12], "themes": ["Old"]}
        self.manager._update_catalog("A", {
            "genres": ["RPG"],
            "themes": ["Fantasy"],
            "modes": ["Single player"],
            "rating": 90,
            "release_year": 2024,
            "release_date": "2024-01-01",
            "cover_url": "https://img",
            "igdb_id": 7,
            "steam_appid": "1",
        })
        merged = self.catalog.fields[("A", "igdb_meta")]
        self.assertEqual(merged["genre_ids"], [12])
        self.assertEqual(merged["genres"], ["RPG"])
        self.assertEqual(merged["themes"], ["Fantasy"])
        self.assertEqual(merged["rating"], 90)
        self.assertEqual(merged["igdb_id"], 7)

    def test_negative_cache_only_for_checked_titles(self):
        class FakeWorker:
            checked_titles = {"A"}

        self.manager._jobs = [{"title": "A"}, {"title": "B"}]
        self.manager._worker = FakeWorker()
        self.manager._on_ready({})
        self.assertEqual(self.details.saved.get("A"), {})
        self.assertNotIn("B", self.details.saved)

    def test_start_without_jobs_does_nothing(self):
        self.manager._max_titles = 0
        self.manager.start()
        self.assertFalse(self.manager.running)


if __name__ == "__main__":
    unittest.main()
