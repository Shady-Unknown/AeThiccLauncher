"""Background library coordinator: queue state and subtitle wiring."""
import unittest
from unittest import mock

from tests.qt_app import ensure_app

ensure_app()

from PyQt6.QtCore import QObject

from ui.background_scan_coordinator import BackgroundScanCoordinator


class _LibManager:
    def __init__(self):
        self.items = {}
        self.calls = []

    def _find_item_by_title(self, title):
        return self.items.get(title)

    def _update_library_item(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _Parent(QObject):
    def __init__(self, games=None):
        super().__init__()
        self.installed_games = games or {}
        self.lib_manager = _LibManager()
        self.master_catalog = None
        self.current_game_data = {}


class QueueTests(unittest.TestCase):
    def test_request_check_deduplicates_and_starts_once(self):
        coordinator = BackgroundScanCoordinator(_Parent({"A": {}, "B": {}}))
        calls = []

        def fake_process():
            coordinator._running = True
            calls.append(True)

        coordinator._process_next_bg_check = fake_process

        coordinator.request_check("A")
        coordinator.request_check("A")
        coordinator.request_check("B")
        coordinator.request_check("  ")

        self.assertEqual(calls, [True])
        self.assertEqual(coordinator._queue, ["A", "B"])

    def test_start_fills_the_queue_from_installed_games(self):
        coordinator = BackgroundScanCoordinator(_Parent({"A": {}, "B": {}}))
        coordinator._process_next_bg_check = mock.MagicMock()

        coordinator.start_background_update_checker()

        self.assertEqual(coordinator._queue, ["A", "B"])
        coordinator._process_next_bg_check.assert_called_once()

    def test_empty_queue_does_not_start_the_walk(self):
        coordinator = BackgroundScanCoordinator(_Parent({}))

        with mock.patch(
            "ui.background_scan_coordinator.QTimer.singleShot"
        ) as timer:
            coordinator._process_next_bg_check()

        self.assertFalse(coordinator._running)
        timer.assert_not_called()


class SubtitleTests(unittest.TestCase):
    def test_missing_setup_subtitle_is_still_rendered(self):
        item = object()
        parent = _Parent({"Game": {"source_url": "https://steamrip.com/game"}})
        parent.lib_manager.items["Game"] = item
        parent.master_catalog = mock.Mock()
        parent.master_catalog.get_entry.return_value = {
            "url": "https://steamrip.com/game", "version": "Release Base",
        }
        parent.master_catalog.get_version_status.return_value = {}
        coordinator = BackgroundScanCoordinator(parent)

        with mock.patch(
            "ui.background_scan_coordinator.QTimer.singleShot"
        ), mock.patch(
            "ui.background_scan_coordinator.compute_version_state",
            return_value={},
        ):
            coordinator._evaluate_bg_check(
                "Game", "https://steamrip.com/game",
                parent.installed_games["Game"],
            )

        self.assertTrue(parent.lib_manager.calls)
        args, kwargs = parent.lib_manager.calls[-1]
        self.assertEqual(args[0], item)
        self.assertEqual(args[1], "Game")
        self.assertEqual(args[2], "(Missing: SAVES - STEAM)")
        self.assertEqual(kwargs["sub_color"], "#ffcc00")


if __name__ == "__main__":
    unittest.main()
