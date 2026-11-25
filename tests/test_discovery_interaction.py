"""Interaction-level Discovery tests: real wheel events through Qt widgets."""
import unittest

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QWheelEvent
from PyQt6.QtWidgets import QApplication

from tests.qt_app import ensure_app
from ui.tabs.discovery_rows import CategoryRow, InfiniteCategoryScroller


def wheel(delta_y, pixel_y=0):
    return QWheelEvent(
        QPointF(100, 100), QPointF(100, 100),
        QPoint(0, pixel_y), QPoint(0, delta_y),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )


def deliver_like_qt(widget, event):
    """Deliver through the widget chain like Qt's wheel propagation."""
    current = widget
    while current is not None:
        QApplication.sendEvent(current, event)
        if event.isAccepted():
            return current
        current = current.parentWidget()
    return None


class WheelPropagationTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.rows = [CategoryRow(f"Row {i}", []) for i in range(3)]
        self.scroller = InfiniteCategoryScroller(self.rows)
        self.scroller.resize(900, 500)
        self.scroller.set_single(True)
        self.scroller.show()
        QApplication.processEvents()

    def tearDown(self):
        self.scroller.deleteLater()
        QApplication.processEvents()

    def _viewport(self):
        row = self.scroller._pool[self.scroller._focus_idx % len(self.scroller._pool)]
        return row._h_scroll.viewport()

    def test_wheel_from_viewport_changes_category_once(self):
        start = self.scroller._focus_idx
        deliver_like_qt(self._viewport(), wheel(-360))
        QApplication.processEvents()
        self.assertNotEqual(self.scroller._focus_idx, start)
        self.assertEqual(self.scroller._focus_idx, (start + 1) % 3)

    def test_wheel_from_card_child_changes_category_once(self):
        rows = [CategoryRow("A", [{"title": "Game", "url": "u"}]),
                CategoryRow("B", [])]
        scroller = InfiniteCategoryScroller(rows)
        scroller.resize(900, 500)
        scroller.set_single(True)
        scroller.show()
        QApplication.processEvents()
        try:
            start = scroller._focus_idx
            deliver_like_qt(rows[0]._cards[0], wheel(-360))
            QApplication.processEvents()
            self.assertEqual(scroller._focus_idx, (start + 1) % 2)
        finally:
            scroller.deleteLater()
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()


class HoverLifecycleTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        from unittest import mock
        from tests.test_discovery_widgets import FakeDiscovery, FakeWindow
        from ui.tabs.discovery_tab import DiscoveryTab

        self._patches = [
            mock.patch("ui.tabs.discovery_tab.secrets.has_igdb_credentials",
                       return_value=False),
            mock.patch.object(DiscoveryTab, "_open_details", lambda *args: None),
        ]
        for patcher in self._patches:
            patcher.start()
        discovery = FakeDiscovery()
        discovery.cards = {"row": [{"title": "Game A", "url": "u"},
                                   {"title": "Game B", "url": "u2"}]}
        self.tab = DiscoveryTab(FakeWindow(discovery))
        self.tab.resize(1200, 700)
        self.tab.show()
        QApplication.processEvents()
        self.row = self.tab._category_rows[1]
        self.card = self.row._cards[0]

    def tearDown(self):
        for patcher in self._patches:
            patcher.stop()
        self.tab.deleteLater()
        QApplication.processEvents()

    def _wait(self, ms):
        from PyQt6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def _enter(self, card):
        from PyQt6.QtGui import QEnterEvent
        QApplication.sendEvent(card, QEnterEvent(
            QPointF(10, 10), QPointF(10, 10), QPointF(10, 10)))
        QApplication.processEvents()

    def _click(self, card):
        from PyQt6.QtGui import QMouseEvent
        from PyQt6.QtCore import QEvent
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress, QPointF(10, 10), QPointF(10, 10),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(10, 10), QPointF(10, 10),
            Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(card, press)
        QApplication.sendEvent(card, release)
        QApplication.processEvents()

    def _place_cursor_on(self, card):
        from unittest import mock
        from PyQt6.QtGui import QCursor
        position = card.mapToGlobal(card.rect().center())
        patcher = mock.patch("ui.tabs.discovery_scroll.QCursor.pos",
                             return_value=position)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initial_hover_and_leave(self):
        self._enter(self.card)
        self._wait(200)
        self.assertTrue(self.card._hover_active)
        self.assertGreater(self.card._zoom, 1.0)
        self.assertIn("#ff00a0", self.card._title_label.styleSheet())
        QApplication.sendEvent(self.card, __import__(
            "PyQt6.QtCore", fromlist=["QEvent"]).QEvent(
            __import__("PyQt6.QtCore", fromlist=["QEvent"]).QEvent.Type.Leave))
        QApplication.processEvents()
        self._wait(200)
        self.assertFalse(self.card._hover_active)
        self.assertAlmostEqual(self.card._zoom, 1.0, places=2)
        self.assertIn("#ffffff", self.card._title_label.styleSheet())

    def test_direct_transition_between_two_cards(self):
        second = self.row._cards[1]
        self._enter(self.card)
        self._wait(100)
        QApplication.sendEvent(self.card, __import__(
            "PyQt6.QtCore", fromlist=["QEvent"]).QEvent(
            __import__("PyQt6.QtCore", fromlist=["QEvent"]).QEvent.Type.Leave))
        self._enter(second)
        self._wait(100)
        self.assertFalse(self.card._hover_active)
        self.assertTrue(second._hover_active)
        self.assertIn("#ff00a0", second._title_label.styleSheet())

    def test_click_release_restores_hover_without_leaving_the_card(self):
        self._place_cursor_on(self.card)
        self._enter(self.card)
        self._wait(100)
        self._click(self.card)
        self._wait(100)
        self.assertTrue(self.card._hover_active)
        self.assertIn("#ff00a0", self.card._title_label.styleSheet())

    def test_wheel_finish_restores_hover_when_cursor_is_over_the_card(self):
        self._place_cursor_on(self.card)
        self._enter(self.card)
        self._wait(100)
        deliver_like_qt(self.row._h_scroll.viewport(), wheel(-120))
        QApplication.processEvents()
        self.assertTrue(self.card._hover_paused)
        self._wait(450)
        self.assertFalse(self.card._hover_paused)
        self.assertTrue(self.card._hover_active)


class WheelSensitivityTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.rows = [CategoryRow(f"Row {i}", []) for i in range(4)]
        self.scroller = InfiniteCategoryScroller(self.rows)
        self.scroller.resize(900, 500)
        self.scroller.set_single(True)
        self.scroller.show()
        QApplication.processEvents()

    def tearDown(self):
        self.scroller.deleteLater()
        QApplication.processEvents()

    def _send(self, delta=0, pixel=0, phase=Qt.ScrollPhase.NoScrollPhase):
        event = QWheelEvent(
            QPointF(100, 100), QPointF(100, 100), QPoint(0, pixel),
            QPoint(0, delta), Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier, phase, False)
        row = self.scroller._pool[self.scroller._focus_idx % len(self.scroller._pool)]
        deliver_like_qt(row._h_scroll.viewport(), event)

    def test_one_notch_does_not_change_category(self):
        start = self.scroller._focus_idx
        self._send(-120)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_three_notches_change_category_once(self):
        start = self.scroller._focus_idx
        for _ in range(3):
            self._send(-120)
        self.assertEqual(self.scroller._focus_idx, (start + 1) % 4)

    def test_high_resolution_pixel_events_are_supported(self):
        start = self.scroller._focus_idx
        for _ in range(3):
            self._send(pixel=-120)
        self.assertEqual(self.scroller._focus_idx, (start + 1) % 4)

    def test_pixel_and_angle_are_not_summed(self):
        start = self.scroller._focus_idx
        self._send(delta=-120, pixel=-60)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_direction_inversion_resets_the_accumulator(self):
        start = self.scroller._focus_idx
        self._send(-120)
        self._send(-120)
        self._send(120)
        self._send(-120)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_scroll_end_clears_a_partial_accumulator(self):
        start = self.scroller._focus_idx
        self._send(-120)
        self._send(-120)
        self._send(phase=Qt.ScrollPhase.ScrollEnd)
        self._send(-120)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_idle_timeout_resets_a_partial_accumulator(self):
        from unittest import mock
        start = self.scroller._focus_idx
        with mock.patch("ui.tabs.discovery_rows.time.monotonic",
                        side_effect=[1.0, 2.0, 3.0]):
            self._send(-120)
            self._send(-120)
            self._send(-120)
        self.assertEqual(self.scroller._focus_idx, start)


class VerticalDragNavigationTests(unittest.TestCase):
    def setUp(self):
        ensure_app()
        self.rows = [CategoryRow(f"Row {i}", []) for i in range(4)]
        self.scroller = InfiniteCategoryScroller(self.rows)
        self.scroller.resize(900, 500)
        self.scroller.set_single(True)
        self.scroller.show()
        QApplication.processEvents()
        self.row_h = self.scroller._compute_row_height()

    def tearDown(self):
        self.scroller.deleteLater()
        QApplication.processEvents()

    def _mouse(self, kind, x, y, buttons=Qt.MouseButton.LeftButton,
               button=Qt.MouseButton.LeftButton):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtGui import QMouseEvent
        event = QMouseEvent(
            kind, QPointF(x, y), QPointF(x, y), button, buttons,
            Qt.KeyboardModifier.NoModifier)
        row = self.scroller._pool[self.scroller._focus_idx % len(self.scroller._pool)]
        QApplication.sendEvent(row._h_scroll.viewport(), event)
        QApplication.processEvents()

    def _press(self, y=250):
        from PyQt6.QtCore import QEvent
        self._mouse(QEvent.Type.MouseButtonPress, 400, y)

    def _move(self, y, x=402):
        from PyQt6.QtCore import QEvent
        self._mouse(QEvent.Type.MouseMove, x, y)

    def _release(self, y):
        from PyQt6.QtCore import QEvent
        self._mouse(QEvent.Type.MouseButtonRelease, 402, y,
                    buttons=Qt.MouseButton.NoButton)

    def _wait(self, ms):
        from PyQt6.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def test_vertical_drag_is_progressive_and_completes(self):
        start = self.scroller._focus_idx
        self._press()
        self._move(360)
        self.assertTrue(self.scroller._vertical_drag_active)
        self.assertGreater(abs(self.scroller._vertical_offset), 0)
        base = (self.scroller.height() - self.row_h) // 2
        current = self.scroller._pool[start % 4]
        previous = self.scroller._pool[(start - 1) % 4]
        self.assertTrue(previous.isVisible())
        self.assertEqual(current.y(), int(base + self.scroller._vertical_offset))
        self.assertEqual(previous.y(),
                         int(base - self.row_h + self.scroller._vertical_offset))
        self._move(500)
        self._release(500)
        self._wait(350)
        self.assertFalse(self.scroller._vertical_drag_active)
        self.assertEqual(self.scroller._vertical_offset, 0)
        self.assertEqual(self.scroller._focus_idx, (start - 1) % 4)

    def test_vertical_drag_does_not_emit_a_card_click(self):
        seen = []
        row = self.scroller._pool[self.scroller._focus_idx % len(self.scroller._pool)]
        row._h_scroll.click_released.connect(lambda card: seen.append(card))
        self._press()
        self._move(500)
        self._release(500)
        self._wait(350)
        self.assertEqual(seen, [])

    def test_window_deactivate_cancels_the_drag(self):
        from PyQt6.QtCore import QEvent
        start = self.scroller._focus_idx
        self._press()
        self._move(400)
        row = self.scroller._pool[start % len(self.scroller._pool)]
        QApplication.sendEvent(row._h_scroll.viewport(),
                               QEvent(QEvent.Type.WindowDeactivate))
        QApplication.processEvents()
        self.assertFalse(self.scroller._vertical_drag_active)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_lost_mouse_grab_cancels_the_drag(self):
        from PyQt6.QtCore import QEvent
        start = self.scroller._focus_idx
        self._press()
        self._move(400)
        row = self.scroller._pool[start % len(self.scroller._pool)]
        QApplication.sendEvent(row._h_scroll.viewport(),
                               QEvent(QEvent.Type.UngrabMouse))
        QApplication.processEvents()
        self.assertFalse(self.scroller._vertical_drag_active)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_horizontal_drag_does_not_change_category(self):
        start = self.scroller._focus_idx
        self._press(250)
        self._move(252, x=500)
        self._release(252)
        self._wait(50)
        self.assertFalse(self.scroller._vertical_drag_active)
        self.assertEqual(self.scroller._focus_idx, start)

    def test_small_vertical_drag_snaps_back(self):
        start = self.scroller._focus_idx
        self._press()
        self._move(275)
        self._release(275)
        self._wait(350)
        self.assertEqual(self.scroller._focus_idx, start)
        self.assertEqual(self.scroller._vertical_offset, 0)

    def test_direction_can_be_inverted_during_the_same_gesture(self):
        start = self.scroller._focus_idx
        self._press()
        self._move(350)
        self.assertGreater(self.scroller._vertical_offset, 0)
        self._move(150)
        self.assertLess(self.scroller._vertical_offset, 0)
        self._release(150)
        self._wait(350)
        self.assertEqual(self.scroller._focus_idx, (start + 1) % 4)

    def test_simple_click_still_emits_the_card_click(self):
        seen = []
        row = self.scroller._pool[self.scroller._focus_idx % len(self.scroller._pool)]
        row._h_scroll.click_released.connect(lambda card: seen.append(card))
        start = self.scroller._focus_idx
        self._press()
        self._release(250)
        self.assertEqual(self.scroller._focus_idx, start)
        self.assertEqual(len(seen), 1)

    def test_hover_is_paused_during_the_drag_and_restored_after(self):
        rows = [CategoryRow("A", [{"title": "Game", "url": "u"}]),
                CategoryRow("B", [])]
        scroller = InfiniteCategoryScroller(rows)
        scroller.resize(900, 500)
        scroller.set_single(True)
        scroller.show()
        QApplication.processEvents()
        try:
            card = rows[0]._cards[0]
            from PyQt6.QtCore import QEvent
            from PyQt6.QtGui import QMouseEvent
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress, QPointF(400, 250),
                QPointF(400, 250), Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(rows[0]._h_scroll.viewport(), press)
            QApplication.processEvents()
            self.assertTrue(rows[0]._hover_paused)
            self.assertTrue(card._hover_paused)
            move = QMouseEvent(
                QEvent.Type.MouseMove, QPointF(402, 350),
                QPointF(402, 350), Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(rows[0]._h_scroll.viewport(), move)
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease, QPointF(402, 350),
                QPointF(402, 350), Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(rows[0]._h_scroll.viewport(), release)
            self._wait(350)
            self.assertFalse(rows[0]._hover_paused)
            self.assertFalse(card._hover_active)
        finally:
            scroller.deleteLater()
            QApplication.processEvents()
