"""PlaytimeManager with dynamic providers and callbacks that may fail."""
import unittest
from unittest import mock

from managers.playtime_manager import PlaytimeManager


def _manager(games, config, **callbacks):
    return PlaytimeManager(lambda: games, lambda: config, **callbacks)


class DynamicProviderTests(unittest.TestCase):
    def test_config_replacement_is_picked_up(self):
        config = {"playtime": {"auto_sync": False}}
        manager = _manager({"Game": {"steam_grid_id": "123"}}, config)
        manager.refresh_steam_stats_cache = lambda: None
        manager.read_steam_playtime = lambda account=None: {}
        manager.sync_game = mock.MagicMock()

        manager.sync_all()
        manager.sync_game.assert_not_called()

        config["playtime"]["auto_sync"] = True
        manager.sync_all()
        manager.sync_game.assert_called_once()

    def test_installed_games_replacement_is_picked_up(self):
        holder = {"games": {"Old Game": {"steam_grid_id": "1"}}}
        manager = PlaytimeManager(
            lambda: holder["games"], lambda: {"playtime": {"auto_sync": True}}
        )
        manager.refresh_steam_stats_cache = lambda: None
        manager.read_steam_playtime = lambda account=None: {}
        manager.sync_game = mock.MagicMock()

        holder["games"] = {"New Game": {"steam_grid_id": "2"}}
        manager.sync_all()

        called_titles = [call.args[0] for call in manager.sync_game.call_args_list]
        self.assertEqual(called_titles, ["New Game"])

    def test_empty_or_orphan_games_are_skipped(self):
        manager = _manager(
            {"Orphan": {"is_orphan": True, "steam_grid_id": "1"}},
            {"playtime": {"auto_sync": True}},
        )
        manager.refresh_steam_stats_cache = lambda: None
        manager.sync_game = mock.MagicMock()

        manager.sync_all()

        manager.sync_game.assert_not_called()


class SessionPersistenceTests(unittest.TestCase):
    def test_stub_callback_error_keeps_the_session(self):
        calls = []

        def broken(title, meta):
            calls.append(title)
            raise RuntimeError("boom")

        games = {"Game": {"steam_grid_id": "123", "playtime_seconds": 60}}
        manager = _manager(games, {}, on_stub_updated=broken)

        with self.assertLogs("managers.playtime_manager", level="ERROR"):
            manager.record_session("Game", 30)

        self.assertEqual(games["Game"]["playtime_seconds"], 90)
        self.assertTrue(games["Game"]["playtime_sync_pending"])
        self.assertEqual(calls, ["Game"])

    def test_session_after_a_library_reload_updates_the_new_mapping(self):
        holder = {"games": {"Old Game": {"playtime_seconds": 10}}}
        manager = PlaytimeManager(lambda: holder["games"], lambda: {})

        holder["games"] = {
            "Old Game": {"playtime_seconds": 10},
            "New Game": {"playtime_seconds": 5},
        }
        manager.record_session("New Game", 45)

        self.assertEqual(holder["games"]["New Game"]["playtime_seconds"], 50)
        self.assertEqual(holder["games"]["Old Game"]["playtime_seconds"], 10)

    def test_stub_refresh_error_does_not_stop_the_sync_loop(self):
        def broken():
            raise RuntimeError("boom")

        manager = _manager(
            {"Game": {"steam_grid_id": "123"}},
            {"playtime": {"auto_sync": True}},
            on_stubs_refresh=broken,
        )
        manager.refresh_steam_stats_cache = lambda: None
        manager.read_steam_playtime = lambda account=None: {}
        manager.sync_game = mock.MagicMock()

        with self.assertLogs("managers.playtime_manager", level="ERROR"):
            manager.sync_all()

        manager.sync_game.assert_called_once()


if __name__ == "__main__":
    unittest.main()
