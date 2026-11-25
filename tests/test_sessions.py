"""One session normaliser for Stats and Achievements."""
import unittest

from core.sessions import normalize_session, normalize_sessions
from managers.achievement_engine import _session_records
from managers.stats_manager import StatsManager


class NormalizeSessionTests(unittest.TestCase):
    def test_complete_record(self):
        session = normalize_session({
            "id": "abc", "title": " Game A ", "start": 100, "end": 200,
            "seconds": 100, "source": "launcher",
        })

        self.assertEqual(session, {
            "id": "abc", "title": "Game A", "start": 100, "end": 200,
            "seconds": 100, "source": "launcher",
        })

    def test_missing_start_is_filled_from_the_end(self):
        session = normalize_session({"title": "Game", "end": 500, "seconds": 60})
        self.assertEqual(session["start"], 440)

    def test_seconds_are_inferred_from_start_and_end(self):
        session = normalize_session({"title": "Game", "start": 100, "end": 250})
        self.assertEqual(session["seconds"], 150)

    def test_at_is_used_when_end_is_missing(self):
        session = normalize_session({"title": "Game", "at": 900, "seconds": 30})
        self.assertEqual(session["end"], 900)

    def test_invalid_records_are_dropped(self):
        self.assertIsNone(normalize_session(None))
        self.assertIsNone(normalize_session({"title": ""}))
        self.assertIsNone(normalize_session({"title": "Game", "seconds": 0}))
        self.assertIsNone(normalize_session({"title": "Game", "end": 0}))

    def test_list_keeps_order_and_drops_invalid(self):
        records = [
            {"title": "A", "start": 0, "end": 10},
            {"title": ""},
            {"title": "B", "start": 20, "end": 40},
        ]

        self.assertEqual(
            [session["title"] for session in normalize_sessions(records)],
            ["A", "B"],
        )


class ConsumersAgreeTests(unittest.TestCase):
    def test_stats_and_achievements_normalise_identically(self):
        records = [
            {"id": "1", "title": "Game A", "start": 100, "end": 200,
             "seconds": 100, "source": "launcher"},
            {"title": "Game B", "end": 500, "seconds": 60},
            {"title": "Game C", "start": 10, "end": 40},
            {"title": "Game D", "end": 0},
        ]

        self.assertEqual(
            StatsManager._session_list(records), _session_records(records)
        )


if __name__ == "__main__":
    unittest.main()
