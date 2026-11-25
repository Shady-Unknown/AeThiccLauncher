"""Characterization tests for the Discovery carousel (G1).

These tests pin observable behaviour and the Qt ownership/lifecycle invariants
that must survive the module extraction. They avoid network, personal config
and sleep-based timing.
"""
import unittest
from unittest import mock

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from tests.qt_app import ensure_app
from ui.tabs.discovery_tab import (
    CategoryRow, DiscoveryTab, InfiniteCategoryScroller, PhysicsScrollArea,
)


class FakeDiscovery(QObject):
    categories_changed = pyqtSignal()
    page_changed = pyqtSignal(str)
    row_reset = pyqtSignal(str)
    cover_ready = pyqtSignal(str, str)
    meta_ready = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.started = False
        self.cards = {"row": [{"title": "Game A", "url": "u"}]}

    def categories(self):
        return [{"key": "row", "title": "Row"}]

    def start(self):
        self.started = True

    def get_cards(self, key):
        return list(self.cards.get(key, []))

    def sort_cards(self, cards):
        return list(cards)

    def is_sortable_key(self, key):
        return True

    def row_sort_active(self, key):
        return False

    def set_filters(self, filters):
        self.filters = filters

    def search_cards(self):
        return []

    def request_more(self, key):
        return False

    def refresh_if_stale(self):
        self.refreshed = True

    def cancel_covers(self, titles):
        pass

    def enqueue_covers(self, titles, high=False, force=False):
        pass

    def set_cover_path(self, title, path):
        pass

    def enqueue_igdb_meta(self, titles):
        pass

    def enqueue_cards(self, cards, high=False, allow_scrape=False,
                      allow_page_fetch=False, force=False):
        pass


class FakeWindow:
    def __init__(self, discovery=None):
        self.app_config = {
            "theme": {
                "text_primary_white": "#ffffff",
                "main_background_color": "#120a1c",
                "text_muted": "#8a7fa4",
                "bright_neon_accent": "#ff00a0",
                "main_accent_color": "#733d91",
                "surface_panel_purple": "#2b1a42",
            }
        }
        self.installed_games = {}
        self.discovery_manager = discovery


def _flush_deferred():
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


class PhysicsScrollAreaTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.area = PhysicsScrollArea()
        self.area.resize(640, 240)

    def tearDown(self):
        if self.area is not None:
            self.area.deleteLater()
            _flush_deferred()

    def test_construction_and_wrap_clamp(self):
        self.area.set_wrap_width(500)
        self.assertEqual(self.area._wrap_value(100), 0)  # no content: clamped
        self.assertFalse(self.area._momentum_timer.isActive())

    def test_click_released_signal(self):
        seen = []
        self.area.click_released.connect(seen.append)
        self.area.click_released.emit("card")
        self.assertEqual(seen, ["card"])

    def test_deferred_delete_really_destroys_the_widget(self):
        area = self.area
        timer = area._momentum_timer
        timer.start(16)
        area.deleteLater()
        _flush_deferred()
        self.assertTrue(sip.isdeleted(area))
        self.assertTrue(sip.isdeleted(timer))
        self.area = None


class CategoryRowTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.row = CategoryRow("Row", [], theme={"text_primary_white": "#fff"})

    def tearDown(self):
        self.row.deleteLater()
        _flush_deferred()

    def test_empty_row_state(self):
        self.assertEqual(self.row.data_cards(), [])
        self.assertEqual(self.row.original_cards(), [])
        self.assertFalse(self.row.has_pending())

    def test_append_cards_and_click_signal(self):
        seen = []
        self.row.card_clicked.connect(seen.append)
        self.row.append_cards([{"title": "Game A", "url": "u"}])
        self.assertEqual([card["title"] for card in self.row.data_cards()],
                         ["Game A"])
        self.row._on_click_released("payload")
        self.assertEqual(seen, ["payload"])

    def test_theme_and_focus_are_reversible(self):
        self.row.set_focused(True)
        self.assertTrue(self.row._focused)
        self.row.set_focused(False)
        self.assertFalse(self.row._focused)
        self.row.set_card_size(120, 200)
        self.assertEqual((self.row._card_w, self.row._card_h), (120, 200))


class ScrollerTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.rows = [CategoryRow("A", []), CategoryRow("B", [])]
        self.scroller = InfiniteCategoryScroller(self.rows)

    def tearDown(self):
        if self.scroller is not None:
            self.scroller.deleteLater()
            _flush_deferred()

    def test_rows_are_reparented_and_pool_is_exact(self):
        self.assertIs(self.rows[0].parent(), self.scroller)
        self.assertEqual(self.scroller._pool, self.rows)

    def test_repeated_set_pool_and_single_keep_ownership(self):
        extra = CategoryRow("C", [])
        self.scroller.set_pool([self.rows[1], extra])
        self.scroller.set_single(True)
        self.scroller.set_single(False)
        self.assertEqual(self.scroller._pool, [self.rows[1], extra])
        self.assertIs(self.rows[1].parent(), self.scroller)
        self.assertIs(extra.parent(), self.scroller)
        self.scroller.focus_index(1)
        self.assertEqual(self.scroller._focus_idx, 1)

    def test_deferred_delete(self):
        scroller = self.scroller
        rows = list(self.rows)
        scroller.deleteLater()
        _flush_deferred()
        self.assertTrue(sip.isdeleted(scroller))
        self.assertTrue(all(sip.isdeleted(row) for row in rows))
        self.scroller = None


class DiscoveryTabTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.discovery = FakeDiscovery()
        self.window = FakeWindow(self.discovery)
        self.patches = [
            mock.patch(
                "ui.tabs.discovery_tab.secrets.has_igdb_credentials",
                return_value=False,
            )
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in self.patches:
            patcher.stop()

    def _tab(self):
        tab = DiscoveryTab(self.window)
        self.addCleanup(tab.deleteLater)
        return tab

    def test_construction_wires_rows_and_starts_manager(self):
        tab = self._tab()
        self.assertTrue(self.discovery.started)
        self.assertEqual(len(tab._category_rows), 2)  # My Library + Row
        self.assertIs(tab._scroller.parent(), tab)
        self.assertFalse(tab._filter_timer.isActive())

    def test_row_reset_and_page_changed_rebuild_without_crash(self):
        tab = self._tab()
        tab._on_discovery_row_reset("row")
        tab._on_discovery_page_changed("row")
        tab._on_cover_ready("Game A", "file:///tmp/cover.jpg")
        tab._on_meta_ready({"Game A": {"rating": 90}})
        self.assertEqual(len(tab._category_rows), 2)

    def test_refresh_library_uses_installed_games(self):
        tab = self._tab()
        self.window.installed_games = {"My Game": {}}
        tab.refresh_library()
        self.assertEqual(tab._category_rows[0].data_cards()[-1]["title"], "My Game")

    def test_row_reset_replaces_rows_without_losing_ownership(self):
        tab = self._tab()
        old_rows = list(tab._category_rows)
        tab._on_discovery_row_reset("row")
        tab._on_discovery_changed()
        _flush_deferred()
        self.assertIs(tab._scroller._pool[1], tab._category_rows[1])
        self.assertIs(tab._category_rows[1].parent(), tab._scroller)
        self.assertTrue(all(sip.isdeleted(row) for row in old_rows if row not in tab._category_rows))

    def test_deferred_delete_stops_the_tab(self):
        tab = DiscoveryTab(self.window)
        timer = tab._filter_timer
        scroller = tab._scroller
        timer.start(10)
        tab.deleteLater()
        _flush_deferred()
        self.assertTrue(sip.isdeleted(tab))
        self.assertTrue(sip.isdeleted(timer))
        self.assertTrue(sip.isdeleted(scroller))


if __name__ == "__main__":
    unittest.main()
