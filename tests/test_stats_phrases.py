import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.paths import PathManager
from managers.stats_phrases import StatsPhrases
from tests.stats_fixture import build_stub_snapshot


class StatsPhrasesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user = Path(self.tmp.name)
        (self.user / "data").mkdir(parents=True)
        repo = Path(__file__).resolve().parent.parent
        shutil.copy(repo / "data" / "stats_phrases.json", self.user / "data" / "stats_phrases.json")
        self.patch = mock.patch.object(
            PathManager, "get_app_data_dir", return_value=self.user
        )
        self.patch.start()
        self.snapshot = build_stub_snapshot("90d", "US", now=1730000000)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _played_game(self, title=None):
        games = [
            game for game in self.snapshot["games"]
            if game["installed"] and game["hours_total"] > 0 and game.get("price_cents")
        ]
        if title is not None:
            return next(game for game in games if game["title"] == title)
        return games[0]

    def test_every_category_formats_without_placeholders(self):
        phrases = StatsPhrases(seed=1)
        game = self._played_game()
        categories = [
            "cost_per_hour",
            "hours_per_dollar",
            "value_vs_cinema",
            "still_sealed",
            "marathon",
            "night_owl",
            "streak",
            "library_value",
            "genre_devotion",
            "game_generic",
            "nerd_hours",
            "nerd_value",
            "generic",
        ]
        for category in categories:
            phrase = phrases.next(self.snapshot, category=category, game=game)
            self.assertTrue(phrase["text"])
            self.assertNotIn("{", phrase["text"])
            self.assertNotIn("}", phrase["text"])
            self.assertEqual(phrase["category"], category)
            self.assertTrue(phrase["category_label"])

    def test_rotation_does_not_repeat_immediately(self):
        phrases = StatsPhrases(seed=7)
        previous = None
        for _ in range(10):
            phrase = phrases.next(self.snapshot, category="generic")
            self.assertNotEqual(phrase["id"], previous)
            previous = phrase["id"]

    def test_invalid_template_falls_back_to_default(self):
        phrases = StatsPhrases(data={
            "phrases": [
                {
                    "id": "too_long",
                    "category": "cost_per_hour",
                    "when": {"hours_min": 9999},
                    "text": "{hours}h",
                }
            ]
        }, seed=1)
        phrase = phrases.next(self.snapshot, category="cost_per_hour")
        self.assertNotIn("{", phrase["text"])
        self.assertTrue(phrase["text"])

    def test_selected_unplayed_game_uses_its_title(self):
        game = next(
            item for item in self.snapshot["games"]
            if item["installed"] and item["hours_total"] <= 0
        )
        phrase = StatsPhrases(seed=3).next(
            self.snapshot, category="still_sealed", game=game
        )
        self.assertIn(game["title"], phrase["text"])

    def test_selected_played_game_uses_its_price_and_hours(self):
        game = next(
            item for item in self.snapshot["games"]
            if item["installed"] and item["hours_total"] > 0
        )
        phrase = StatsPhrases(seed=3).next(
            self.snapshot, category="cost_per_hour", game=game
        )
        self.assertIn(game["title"], phrase["text"])
        self.assertIn("$", phrase["text"])

    def test_unknown_placeholder_is_skipped(self):
        phrases = StatsPhrases(data={
            "phrases": [
                {
                    "id": "broken",
                    "category": "generic",
                    "when": {},
                    "text": "{not_a_real_placeholder}",
                },
                {
                    "id": "good",
                    "category": "generic",
                    "when": {},
                    "text": "{hours}h total",
                },
            ]
        }, seed=1)
        phrase = phrases.next(self.snapshot, category="generic")
        self.assertNotIn("{", phrase["text"])
        self.assertIn("h total", phrase["text"])

    def test_hours_per_dollar_and_cinema_multiple(self):
        game = self._played_game("Neon Drift")
        data = {
            "phrases": [
                {
                    "id": "hpd",
                    "category": "hours_per_dollar",
                    "when": {
                        "selected": True,
                        "price_positive": True,
                        "hours_per_dollar_known": True,
                        "game_hours_min": 1,
                    },
                    "text": "{hours_per_dollar}|{cinema_multiple}|{minutes_per_dollar}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot,
            context={"cinema_rate": 6.0},
            category="hours_per_dollar",
            game=game,
        )
        hours_dollar, cinema, minutes = phrase["text"].split("|")
        expected = game["hours_total"] / (game["price_cents"] / 100.0)
        self.assertAlmostEqual(float(hours_dollar), expected, delta=0.06)
        self.assertAlmostEqual(float(cinema), expected * 6.0, delta=0.2)
        self.assertAlmostEqual(float(minutes), expected * 60.0, delta=1.0)

    def test_better_value_produces_higher_hours_per_dollar(self):
        data = {
            "phrases": [
                {
                    "id": "hpd",
                    "category": "hours_per_dollar",
                    "when": {
                        "selected": True,
                        "price_positive": True,
                        "hours_per_dollar_known": True,
                        "game_hours_min": 1,
                    },
                    "text": "{hours_per_dollar}",
                }
            ]
        }
        phrases = StatsPhrases(data=data, seed=1)

        def value_for(title):
            phrase = phrases.next(
                self.snapshot, category="hours_per_dollar",
                game=self._played_game(title),
            )
            return float(phrase["text"])

        self.assertGreater(value_for("Neon Drift"), value_for("Void Garden"))

    def test_free_to_play_skips_per_dollar_phrase(self):
        game = dict(self._played_game())
        game["price_cents"] = 0
        game["cost_per_hour"] = 0.0
        data = {
            "phrases": [
                {
                    "id": "hpd",
                    "category": "hours_per_dollar",
                    "when": {
                        "selected": True,
                        "price_positive": True,
                        "hours_per_dollar_known": True,
                        "game_hours_min": 1,
                    },
                    "text": "{hours_per_dollar}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot, category="hours_per_dollar", game=game
        )
        self.assertNotEqual(phrase["id"], "hpd")
        self.assertNotIn("{", phrase["text"])

    def test_library_wide_phrase_without_selection(self):
        data = {
            "phrases": [
                {
                    "id": "lib_hours",
                    "category": "nerd_hours",
                    "when": {"selected": False, "hours_min": 3},
                    "text": "{hours}h / {portal_runs_library}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot, category="nerd_hours"
        )
        self.assertEqual(phrase["id"], "lib_hours")
        self.assertNotIn("{", phrase["text"])

    def test_selected_only_phrase_is_not_used_without_selection(self):
        data = {
            "phrases": [
                {
                    "id": "sel_only",
                    "category": "nerd_hours",
                    "when": {"selected": True, "game_hours_min": 1},
                    "text": "{hours_game}h",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot, category="nerd_hours"
        )
        self.assertNotEqual(phrase["id"], "sel_only")

    def test_refs_override(self):
        data = {
            "phrases": [
                {
                    "id": "portal",
                    "category": "nerd_hours",
                    "when": {"selected": True, "game_hours_min": 1},
                    "text": "{portal_runs}",
                }
            ],
            "refs": {"portal_hours": 82.5},
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot, category="nerd_hours",
            game=self._played_game("Neon Drift"),
        )
        self.assertEqual(phrase["text"], "1")

    def test_weak_currency_unit_skips_per_dollar_phrase(self):
        game = dict(self._played_game("Neon Drift"))
        game.update({
            "title": "JP Game",
            "price_cents": 600000,
            "price_currency": "JPY",
            "hours_total": 60.0,
        })
        data = {
            "phrases": [
                {
                    "id": "hpd",
                    "category": "hours_per_dollar",
                    "when": {
                        "selected": True,
                        "price_positive": True,
                        "hours_per_dollar_known": True,
                        "hours_per_dollar_min": 0.1,
                        "game_hours_min": 1,
                    },
                    "text": "{hours_per_dollar}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot,
            context={"currency": "JPY"},
            category="hours_per_dollar",
            game=game,
        )
        self.assertNotEqual(phrase["id"], "hpd")

    def test_copies_of_game_gate(self):
        game = dict(self._played_game("Neon Drift"))
        game.update({
            "title": "JP Game",
            "price_cents": 600000,
            "price_currency": "JPY",
        })
        data = {
            "phrases": [
                {
                    "id": "copies",
                    "category": "nerd_value",
                    "when": {
                        "library_value_known": True,
                        "price_positive": True,
                        "copies_of_game_min": 1,
                    },
                    "text": "{copies_of_game}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot,
            context={"currency": "JPY"},
            category="nerd_value",
            game=game,
        )
        self.assertNotEqual(phrase["id"], "copies")

    def test_tiny_budget_percentage_is_readable(self):
        data = {
            "phrases": [
                {
                    "id": "budget",
                    "category": "nerd_value",
                    "when": {"library_value_known": True},
                    "text": "{rohirrim_percent}",
                }
            ]
        }
        phrase = StatsPhrases(data=data, seed=1).next(
            self.snapshot, category="nerd_value"
        )
        self.assertEqual(phrase["text"], "under 0.01")


if __name__ == "__main__":
    unittest.main()
