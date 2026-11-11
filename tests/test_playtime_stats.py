import unittest

from managers.playtime_manager import _parse_vdf_tolerant, extract_app_stats


class SteamStatsTests(unittest.TestCase):
    def test_extracts_playtime_and_last_played(self):
        data = {
            "UserLocalConfigStore": {
                "Software": {
                    "Valve": {
                        "Steam": {
                            "apps": {
                                "440": {
                                    "Playtime": "120",
                                    "LastPlayed": "1730000000",
                                    "Playtime2wks": "45",
                                },
                                "570": {"Playtime": "0"},
                            }
                        }
                    }
                }
            }
        }

        stats = extract_app_stats(data)
        self.assertEqual(stats["440"]["minutes"], 120)
        self.assertEqual(stats["440"]["last_played"], 1730000000)
        self.assertEqual(stats["440"]["playtime_2wks"], 45)
        self.assertNotIn("570", stats)

    def test_compact_vdf_is_tolerated(self):
        text = (
            '"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { '
            '"apps" { "730" { "Playtime" "60" "LastPlayed" "1730003600" } } } } } }'
        )
        stats = extract_app_stats(_parse_vdf_tolerant(text))
        self.assertEqual(stats["730"]["minutes"], 60)
        self.assertEqual(stats["730"]["last_played"], 1730003600)
        self.assertEqual(stats["730"]["playtime_2wks"], 0)

    def test_missing_apps_is_empty(self):
        self.assertEqual(extract_app_stats({}), {})


if __name__ == "__main__":
    unittest.main()
