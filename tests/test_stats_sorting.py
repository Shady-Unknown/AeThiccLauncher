import unittest

from managers.stats_sorting import (
    bar_context,
    bar_ratio,
    filter_games,
    format_primary_value,
    format_secondary_values,
    normalize_sort_key,
    relative_age,
    sort_games,
    sort_spec,
)

NOW = 1_700_000_000

GAMES = [
    {
        "title": "Neon Drift", "installed": True, "hours_total": 82.5,
        "sessions": 18, "yield_hours_per_dollar": 2.08, "reference_currency": "USD",
        "last_played": NOW - 3 * 86400,
    },
    {
        "title": "Ashen Circuit", "installed": True, "hours_total": 45.0,
        "sessions": 9, "yield_hours_per_dollar": 0.80, "reference_currency": "USD",
        "last_played": NOW - 5 * 86400,
    },
    {
        "title": "Void Garden", "installed": True, "hours_total": 27.0,
        "sessions": 5, "yield_hours_per_dollar": 0.45, "reference_currency": "USD",
        "last_played": NOW - 9 * 86400,
    },
    {
        "title": "Glass Harbor", "installed": False, "hours_total": 63.0,
        "sessions": 12, "yield_hours_per_dollar": 1.26, "reference_currency": "USD",
        "last_played": NOW - 1 * 86400,
    },
    {
        "title": "No Price", "installed": False, "hours_total": 0.0,
        "sessions": 0, "yield_hours_per_dollar": None, "reference_currency": "",
        "last_played": 0,
    },
]


def titles(games):
    return [game["title"] for game in games]


class StatsSortingTests(unittest.TestCase):
    def test_filter_installed_only(self):
        visible = filter_games(GAMES, installed_only=True)
        self.assertEqual(titles(visible), ["Neon Drift", "Ashen Circuit", "Void Garden"])
        self.assertEqual(len(filter_games(GAMES, installed_only=False)), 5)

    def test_installed_first_keeps_installed_group_on_top(self):
        ordered = sort_games(GAMES, "installed_first")
        self.assertEqual(
            titles(ordered),
            ["Neon Drift", "Ashen Circuit", "Void Garden", "Glass Harbor", "No Price"],
        )

    def test_playtime_orders(self):
        self.assertEqual(
            titles(sort_games(GAMES, "playtime_desc")),
            ["Neon Drift", "Glass Harbor", "Ashen Circuit", "Void Garden", "No Price"],
        )
        self.assertEqual(
            titles(sort_games(GAMES, "playtime_asc")),
            ["No Price", "Void Garden", "Ashen Circuit", "Glass Harbor", "Neon Drift"],
        )

    def test_yield_orders_put_unknown_last(self):
        self.assertEqual(
            titles(sort_games(GAMES, "yield_best")),
            ["Neon Drift", "Glass Harbor", "Ashen Circuit", "Void Garden", "No Price"],
        )
        self.assertEqual(
            titles(sort_games(GAMES, "yield_worst")),
            ["Void Garden", "Ashen Circuit", "Glass Harbor", "Neon Drift", "No Price"],
        )

    def test_other_orders(self):
        self.assertEqual(
            titles(sort_games(GAMES, "sessions_desc")),
            ["Neon Drift", "Glass Harbor", "Ashen Circuit", "Void Garden", "No Price"],
        )
        self.assertEqual(
            titles(sort_games(GAMES, "last_played_desc")),
            ["Glass Harbor", "Neon Drift", "Ashen Circuit", "Void Garden", "No Price"],
        )
        self.assertEqual(
            titles(sort_games(GAMES, "title_asc")),
            ["Ashen Circuit", "Glass Harbor", "Neon Drift", "No Price", "Void Garden"],
        )

    def test_sort_key_normalisation(self):
        self.assertEqual(normalize_sort_key("nope"), "installed_first")
        self.assertEqual(sort_spec("yield_best")["primary"], "yield")
        self.assertEqual(sort_spec("last_played_desc")["primary"], "last_played")

    def test_bar_ratio_for_hours_and_yield(self):
        hours_context = bar_context(GAMES, "hours", now=NOW)
        self.assertAlmostEqual(bar_ratio(GAMES[0], "hours", hours_context), 1.0)
        self.assertAlmostEqual(bar_ratio(GAMES[2], "hours", hours_context), 27.0 / 82.5)
        self.assertEqual(bar_ratio(GAMES[-1], "hours", hours_context), 0.0)

        yield_context = bar_context(GAMES, "yield", now=NOW)
        self.assertAlmostEqual(bar_ratio(GAMES[0], "yield", yield_context), 1.0)
        self.assertAlmostEqual(bar_ratio(GAMES[1], "yield", yield_context), 0.80 / 2.08)
        self.assertEqual(bar_ratio(GAMES[-1], "yield", yield_context), 0.0)

    def test_bar_ratio_for_last_played_is_recency(self):
        context = bar_context(GAMES, "last_played", now=NOW)
        recent = bar_ratio(GAMES[3], "last_played", context)
        older = bar_ratio(GAMES[0], "last_played", context)
        never = bar_ratio(GAMES[-1], "last_played", context)
        self.assertGreater(recent, older)
        self.assertAlmostEqual(never, 0.0)

    def test_primary_and_secondary_formatting(self):
        self.assertEqual(format_primary_value(GAMES[0], "hours", NOW), "82.5h")
        self.assertEqual(format_primary_value(GAMES[0], "yield", NOW), "2.08 h/$")
        self.assertEqual(format_primary_value(GAMES[0], "sessions", NOW), "18")
        self.assertEqual(format_primary_value(GAMES[0], "last_played", NOW), "3d ago")
        self.assertEqual(format_primary_value(GAMES[-1], "yield", NOW), "\u2014")

        playtime = sort_spec("playtime_desc")
        self.assertEqual(
            format_secondary_values(GAMES[0], playtime, NOW),
            ["2.08 h/$", "18 sess"],
        )
        value = sort_spec("yield_best")
        self.assertEqual(
            format_secondary_values(GAMES[0], value, NOW),
            ["82.5h", "18 sess"],
        )

    def test_relative_age(self):
        self.assertEqual(relative_age(0, NOW), "never")
        self.assertEqual(relative_age(NOW - 3600, NOW), "today")
        self.assertEqual(relative_age(NOW - 3 * 86400, NOW), "3d ago")
        self.assertEqual(relative_age(NOW - 21 * 86400, NOW), "3w ago")


if __name__ == "__main__":
    unittest.main()
