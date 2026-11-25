"""Scroll physics and momentum for the Discovery carousel."""

import functools
import logging
import os
import time

from PyQt6.QtCore import QEasingCurve, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QFrame, QScrollArea

from ui.widgets.discovery_card import DiscoveryCard

log = logging.getLogger(__name__)


SNAP_DURATION = 260
FOCUS_TOLERANCE = 3
CLICK_DRAG_THRESHOLD = 6      # px prima di considerarlo un drag
VERTICAL_DRAG_DEAD_ZONE = 10  # px di micromovimento ignorati
VERTICAL_DRAG_DOMINANCE = 1.5 # dy/dx necessario per bloccare l'asse verticale
MOMENTUM_SNAP_VELOCITY = 60   # px/s: sotto questa soglia si ferma
MAX_MOMENTUM_VELOCITY = 2200  # px/s: tetto per non "decolare"
DRAG_VELOCITY_SCALE = 0.85    # ammorbidisce il lancio dopo il drag
HOVER_RESUME_DELAY = 0        # ms prima di riabilitare l'hover
PERF_LOG_MS = 40
PERF_DEBUG = os.environ.get('SRP_DEBUG_PERF') == '1'


def _perf(name):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                dt = (time.perf_counter() - t0) * 1000.0
                if PERF_DEBUG and dt > PERF_LOG_MS:
                    log.debug(f"[PERF] {name}: {dt:.0f}ms")
        return wrapper
    return deco


class PhysicsScrollArea(QScrollArea):

    click_released = pyqtSignal(object)
    vertical_drag_started = pyqtSignal()
    vertical_drag_moved = pyqtSignal(float)
    vertical_drag_finished = pyqtSignal(float)
    vertical_drag_cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setMouseTracking(True)
        self.viewport().setAutoFillBackground(False)
        self.viewport().setStyleSheet("background: transparent;")
        self.viewport().installEventFilter(self)

        self._drag_start = None
        self._drag_last = None
        self._drag_time = 0.0
        self._is_dragging = False
        self._drag_axis = None
        self._drag_moved = False
        self._press_start = None
        self._vertical_last_y = 0.0
        self._vertical_last_time = 0.0
        self._vertical_velocity = 0.0
        self._pressed_card = None
        self._momentum_timer = QTimer(self)
        self._momentum_timer.setInterval(16)
        self._momentum_timer.timeout.connect(self._tick_momentum)
        self._velocity = 0.0
        self._friction = 0.90
        self._pending_dx = 0
        self._last_apply = 0.0
        self._wrap_width = 0
        self._snap_anim = None
        self._hover_resume_timer = QTimer(self)
        self._hover_resume_timer.setSingleShot(True)
        self._hover_resume_timer.timeout.connect(self._finish_motion)
        self._hover_pause_cb = None
        self._resize_cb = None
        self._hover_paused = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_hover_pause_cb(self, cb):
        self._hover_pause_cb = cb

    def set_resize_cb(self, cb):
        self._resize_cb = cb

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cb = self._resize_cb
        if cb is not None:
            cb()

    def _set_hover_paused(self, paused):
        self._hover_paused = bool(paused)
        cb = self._hover_pause_cb
        if cb is not None:
            cb(paused)

    def _release_hover_pause(self, delay_ms=450):
        """Riabilita il glow quando il movimento (drag/momentum/snap) è finito."""
        self._hover_resume_timer.start(max(0, int(delay_ms)))

    def _finish_motion(self):
        self._set_hover_paused(False)
        self._refresh_hover_under_cursor()

    def _refresh_hover_under_cursor(self):
        """Riattiva l'hover sulla card sotto il cursore.

        Dopo un drag la riga scorre sotto un mouse fermo: Qt non genera
        enterEvent, quindi la card non si evidenziava piu'.
        """
        try:
            vp = self.viewport()
            global_pos = QCursor.pos()
            if not vp.rect().contains(vp.mapFromGlobal(global_pos)):
                return
            pos = vp.mapFromGlobal(global_pos)
        except RuntimeError:
            return
        card = self._card_at(pos)
        if card is not None:
            card.force_hover(True)

    def set_wrap_width(self, w):
        self._wrap_width = w

    def _wrap_value(self, val):
        # Scroll lineare: niente salti "a carosello" quando si trascina
        # oltre l'inizio/fine riga (era la causa dei teletrasporti).
        try:
            top = int(self.horizontalScrollBar().maximum())
        except RuntimeError:
            return int(val)
        return max(0, min(int(val), max(0, top)))

    def _event_pos(self, obj, event):
        pos = event.pos()
        if obj is not self.viewport():
            try:
                pos = obj.mapTo(self.viewport(), pos)
            except (RuntimeError, TypeError):
                return None
        return pos

    @_perf("eventFilter")
    def eventFilter(self, obj, event):
        et = event.type()
        if et in (event.Type.UngrabMouse, event.Type.WindowDeactivate):
            if self._is_dragging:
                if self._drag_axis == "vertical":
                    self.vertical_drag_cancelled.emit()
                self._reset_drag_state()
            return super().eventFilter(obj, event)
        if et not in (event.Type.MouseButtonPress, event.Type.MouseMove,
                      event.Type.MouseButtonRelease):
            return super().eventFilter(obj, event)
        if obj is not self.viewport() and not isinstance(obj, DiscoveryCard):
            return super().eventFilter(obj, event)

        pos = self._event_pos(obj, event)
        if pos is None:
            return super().eventFilter(obj, event)

        if et == event.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = pos
            self._drag_last = pos
            self._drag_time = time.monotonic()
            self._is_dragging = True
            self._drag_axis = None
            self._drag_moved = False
            self._press_start = pos
            self._vertical_velocity = 0.0
            self._pressed_card = self._card_at(pos)
            self._momentum_timer.stop()
            self._stop_snap_anim()
            self._velocity = 0.0
            self._pending_dx = 0
            self.setFocus()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._set_hover_paused(True)
            return True

        elif et == event.Type.MouseMove and self._is_dragging:
            if self._drag_axis is None and self._press_start is not None:
                dx = pos.x() - self._press_start.x()
                dy = pos.y() - self._press_start.y()
                if (abs(dx) <= CLICK_DRAG_THRESHOLD
                        and abs(dy) <= VERTICAL_DRAG_DEAD_ZONE):
                    # Micromovimento: e' ancora un click, non scrollare.
                    self._drag_last = pos
                    return True
                self._drag_moved = True
                if abs(dy) >= abs(dx) * VERTICAL_DRAG_DOMINANCE:
                    self._drag_axis = "vertical"
                    self._vertical_last_y = pos.y()
                    self._vertical_last_time = time.monotonic()
                    self._drag_last = pos
                    self.vertical_drag_started.emit()
                    self.vertical_drag_moved.emit(float(dy))
                    return True
                if abs(dx) >= abs(dy) * VERTICAL_DRAG_DOMINANCE:
                    self._drag_axis = "horizontal"
                    self._drag_last = pos
                    self._drag_time = time.monotonic()
                    return True
                # Diagonal ambiguous: wait for one axis to dominate.
                self._drag_last = pos
                return True

            if self._drag_axis == "vertical":
                dy = pos.y() - self._press_start.y()
                now = time.monotonic()
                dt = max(now - self._vertical_last_time, 0.001)
                delta_y = pos.y() - self._vertical_last_y
                self._vertical_velocity = delta_y / dt
                self._vertical_last_y = pos.y()
                self._vertical_last_time = now
                self._drag_last = pos
                self.vertical_drag_moved.emit(float(dy))
                return True

            delta = pos.x() - self._drag_last.x()
            self._pending_dx -= delta
            now = time.monotonic()
            # Massimo ~120 aggiornamenti al secondo: con mouse a 1000 Hz
            # ogni evento ridisegnava la viewport.
            if now - self._last_apply >= 0.008:
                step = self._pending_dx
                self._pending_dx = 0
                self._last_apply = now
                self._apply_scroll_delta(step)
            dt = max(now - self._drag_time, 0.001)
            velocity = -delta / dt * DRAG_VELOCITY_SCALE
            self._velocity = max(-MAX_MOMENTUM_VELOCITY,
                                 min(MAX_MOMENTUM_VELOCITY, velocity))
            self._drag_last = pos
            self._drag_time = now
            return True

        elif et == event.Type.MouseButtonRelease and self._is_dragging:
            axis = self._drag_axis
            moved = self._drag_moved
            card = self._pressed_card
            pending_dx = self._pending_dx
            velocity = self._velocity
            vertical_velocity = self._vertical_velocity
            self._reset_drag_state()
            if axis == "vertical":
                self._release_hover_pause(300)
                self.vertical_drag_finished.emit(float(vertical_velocity))
                return True
            if not moved:
                self.click_released.emit(card)
                self._release_hover_pause(HOVER_RESUME_DELAY)
                return True
            if axis is None:
                self._release_hover_pause(HOVER_RESUME_DELAY)
                return True
            if pending_dx:
                self._apply_scroll_delta(pending_dx)
            if abs(velocity) > 50:
                self._momentum_timer.start()
                # L'hover riappare quando finisce lo snap (vedi _finish_motion).
            else:
                self._snap_to_nearest_gap()
                if self._snap_anim is None:
                    self._release_hover_pause(HOVER_RESUME_DELAY)
            return True

        return super().eventFilter(obj, event)

    def _reset_drag_state(self):
        self._is_dragging = False
        self._drag_axis = None
        self._drag_moved = False
        self._press_start = None
        self._pressed_card = None
        self._pending_dx = 0
        self._velocity = 0.0
        self._vertical_velocity = 0.0
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _card_at(self, pos):
        w = self.viewport().childAt(pos)
        while w is not None and w is not self.viewport():
            if isinstance(w, DiscoveryCard):
                return w
            w = w.parentWidget()
        return None

    def wheelEvent(self, event):
        if event.angleDelta().y() == 0:
            event.ignore()
            return

        self._momentum_timer.stop()
        self._stop_snap_anim()
        self._set_hover_paused(True)
        self._release_hover_pause(300)

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            sb = self.horizontalScrollBar()
            new_val = self._wrap_value(sb.value() - int(delta * 1.5))
            sb.setValue(new_val)
            self._snap_to_nearest_gap()
            event.accept()
        else:
            event.ignore()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._momentum_timer.stop()
            self._stop_snap_anim()
            m = self._layout_metrics()
            if m is not None:
                card_w, spacing, left_margin, orig_w, vw = m
                step = card_w + spacing
                sb = self.horizontalScrollBar()
                delta = step if event.key() == Qt.Key.Key_Right else -step
                sb.setValue(self._wrap_value(sb.value() + delta))
                self._snap_to_nearest_gap()
            event.accept()
            return
        super().keyPressEvent(event)

    @_perf("apply_scroll")
    def _apply_scroll_delta(self, delta):
        if not delta:
            return
        sb = self.horizontalScrollBar()
        sb.setValue(self._wrap_value(sb.value() + int(delta)))

    def _tick_momentum(self):
        self._velocity *= self._friction
        if abs(self._velocity) < MOMENTUM_SNAP_VELOCITY:
            self._momentum_timer.stop()
            self._velocity = 0.0
            self._snap_to_nearest_gap()
            if self._snap_anim is None:
                self._release_hover_pause(HOVER_RESUME_DELAY)
            return
        sb = self.horizontalScrollBar()
        new_val = sb.value() + int(self._velocity * 0.016)
        new_val = self._wrap_value(new_val)
        sb.setValue(new_val)

    def _layout_metrics(self):
        widget = self.widget()
        if widget is None:
            return None
        layout = widget.layout()
        if layout is None or self._wrap_width <= 0:
            return None
        card_w = 0
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is not None:
                card_w = w.width()
                break
        vw = self.viewport().width()
        if card_w <= 0 or vw <= 0:
            return None
        spacing = layout.spacing()
        left_margin = layout.contentsMargins().left()
        return card_w, spacing, left_margin, self._wrap_width, vw

    def _wrapped_delta(self, a, b):
        return b - a

    def _focus_targets(self, card):
        m = self._layout_metrics()
        if m is None:
            return None, None
        card_w, spacing, left_margin, orig_w, vw = m
        layout = self.widget().layout()
        p = layout.indexOf(card)
        if p < 0:
            return None, None
        left = left_margin + p * (card_w + spacing)
        t2 = self._wrap_value(int(left + card_w + spacing / 2 - vw / 2))
        t3 = self._wrap_value(int(left - spacing / 2 - vw / 2))
        return t2, t3

    def is_card_on_focus(self, card):
        m = self._layout_metrics()
        if m is None:
            return False
        card_w, spacing, left_margin, orig_w, vw = m
        layout = self.widget().layout()
        p = layout.indexOf(card)
        if p < 0:
            return False
        left = left_margin + p * (card_w + spacing)
        v = self.horizontalScrollBar().value()
        left_screen = left - v
        center = vw / 2
        gap_after = left_screen + card_w + spacing / 2
        gap_before = left_screen - spacing / 2
        return (
            abs(gap_after - center) <= FOCUS_TOLERANCE
            or abs(gap_before - center) <= FOCUS_TOLERANCE
        )

    def snap_card_to_focus(self, card):
        t2, t3 = self._focus_targets(card)
        if t2 is None:
            return
        v = self.horizontalScrollBar().value()
        d2 = abs(self._wrapped_delta(v, t2))
        d3 = abs(self._wrapped_delta(v, t3))
        self._animate_to(t2 if d2 <= d3 else t3)

    @_perf("snap_to_gap")
    def _snap_to_nearest_gap(self):
        m = self._layout_metrics()
        if m is None:
            return
        card_w, spacing, left_margin, orig_w, vw = m
        step = card_w + spacing
        orig_n = (orig_w - 2) // step
        if orig_n <= 0:
            return
        v = self.horizontalScrollBar().value()
        best_t = None
        best_d = None
        for p in range(orig_n):
            left = left_margin + p * step
            t = self._wrap_value(int(left + card_w + spacing / 2 - vw / 2))
            d = abs(self._wrapped_delta(v, t))
            if best_d is None or d < best_d:
                best_d = d
                best_t = t
        if best_t is not None and best_d is not None:
            # Solo micro-allineamento: un salto lungo (es. wrap a fine riga)
            # sembrava un teletrasporto.
            if best_d <= step * 1.25:
                self._animate_to(best_t)

    def _stop_snap_anim(self):
        anim = self._snap_anim
        self._snap_anim = None
        if anim is None:
            return
        try:
            anim.stop()
            anim.deleteLater()
        except RuntimeError:
            pass

    def _animate_to(self, target):
        self._stop_snap_anim()
        sb = self.horizontalScrollBar()
        start = self._wrap_value(sb.value())
        target = self._wrap_value(int(target))
        d = self._wrapped_delta(start, target)
        if abs(d) < 1:
            return
        end = start + d
        anim = QVariantAnimation(self)
        anim.setDuration(SNAP_DURATION)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutBack)
        anim.valueChanged.connect(lambda val: sb.setValue(self._wrap_value(int(val))))
        anim.finished.connect(lambda a=anim, t=target: self._snap_finished(a, t))
        self._snap_anim = anim
        anim.start()

    def _snap_finished(self, anim, target):
        if self._snap_anim is not anim:
            return
        self.horizontalScrollBar().setValue(self._wrap_value(int(target)))
        self._snap_anim = None
        anim.deleteLater()
        self._release_hover_pause(HOVER_RESUME_DELAY)

