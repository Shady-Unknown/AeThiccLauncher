import os
import time
import math
import functools
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QVariantAnimation, QTimer, QPoint,
    QRect, QSize, pyqtProperty, pyqtSignal
)
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QScrollBar, QSizePolicy, QFrame, QLineEdit, QComboBox, QCheckBox,
    QGraphicsOpacityEffect, QPushButton
)

from ui.widgets.discovery_card import (
    DiscoveryCard, GLOW_INSET, GLOW_PAD, CARD_TITLE_H
)
from ui.widgets.alpha_mask import MaskOverlay
from core import secrets
from core.mini_cover_cache import MiniCoverManager


CARD_SPACING = 6
FADE_MARGIN = 0
TRANSITION_DURATION = 300
TITLE_BAND = 62
SNAP_DURATION = 260
FOCUS_TOLERANCE = 3
CLICK_DRAG_THRESHOLD = 6      # px prima di considerarlo un drag
MOMENTUM_SNAP_VELOCITY = 60   # px/s: sotto questa soglia si ferma
MAX_MOMENTUM_VELOCITY = 2200  # px/s: tetto per non "decolare"
DRAG_VELOCITY_SCALE = 0.85    # ammorbidisce il lancio dopo il drag
HOVER_RESUME_DELAY = 0        # ms prima di riabilitare l'hover
WHEEL_ROW_STEP = 120          # accumulo minimo per cambiare riga
ROW_SWITCH_COOLDOWN = 0.35    # s tra due cambi di riga consecutivi

SORT_CHOICES = (
    ("", "Default order"),
    ("title_asc", "Title A → Z"),
    ("title_desc", "Title Z → A"),
    ("added_desc", "Added: newest first"),
    ("added_asc", "Added: oldest first"),
    ("release_desc", "Release: newest first"),
    ("release_asc", "Release: oldest first"),
    ("rating_desc", "Rating: best first"),
)
PROGRESSIVE_CHUNK = 10
INITIAL_CARD_BUDGET = 40
GROW_CHUNK = 10
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
                    print(f"[PERF] {name}: {dt:.0f}ms")
        return wrapper
    return deco


class PhysicsScrollArea(QScrollArea):

    click_released = pyqtSignal(object)

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
        self._drag_moved = False
        self._press_start = None
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
            if not vp.underMouse():
                return
            pos = vp.mapFromGlobal(QCursor.pos())
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
            self._drag_moved = False
            self._press_start = pos
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
            if not self._drag_moved and self._press_start is not None:
                if (abs(pos.x() - self._press_start.x()) <= CLICK_DRAG_THRESHOLD
                        and abs(pos.y() - self._press_start.y()) <= CLICK_DRAG_THRESHOLD):
                    # Micromovimento: e' ancora un click, non scrollare.
                    self._drag_last = pos
                    return True
                # Appena superata la soglia ripartiamo da qui: il tratto
                # "morto" non si accumula in uno scatto.
                self._drag_moved = True
                self._drag_last = pos
                self._drag_time = time.monotonic()
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
            self._is_dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if not self._drag_moved:
                card = self._pressed_card
                self._pressed_card = None
                self._drag_moved = False
                self._press_start = None
                self.click_released.emit(card)
                return True
            self._pressed_card = None
            if self._pending_dx:
                self._apply_scroll_delta(self._pending_dx)
                self._pending_dx = 0
            if abs(self._velocity) > 50:
                self._momentum_timer.start()
                # L'hover riappare quando finisce lo snap (vedi _finish_motion).
            else:
                self._snap_to_nearest_gap()
                if self._snap_anim is None:
                    self._release_hover_pause(HOVER_RESUME_DELAY)
            return True

        return super().eventFilter(obj, event)

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


class CategoryRow(QWidget):

    card_clicked = pyqtSignal(object)

    def __init__(self, title, cards, parent=None, theme=None, progressive=False):
        super().__init__(parent)
        self._title_text = title
        self._cards = []
        self._card_data = []
        self._pending = []
        self._auto_budget = INITIAL_CARD_BUDGET
        self._theme = theme or {}
        self._themed = bool(theme)
        self._card_w = 200
        self._card_h = 310
        self._cover_loaded = 0
        self._hover_paused = False
        self._build_timer = None
        self.setObjectName("CategoryRow")
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(FADE_MARGIN, 8, FADE_MARGIN, 8)
        layout.setSpacing(6)

        self._title_label = QLabel(title.upper())
        self._title_label.setObjectName("CategoryTitle")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_color = QColor(
            self._theme.get('text_primary_white', '#ffffff')
        )
        self._title_style_base = (
            "font-size: 28px; font-weight: bold; letter-spacing: 2px; "
            "background: transparent;"
        )
        self._focused = False
        self._apply_title_style()
        self._top_opacity = 1.0
        self._top_effect = QGraphicsOpacityEffect(self._title_label)
        self._top_effect.setOpacity(1.0)
        self._title_label.setGraphicsEffect(self._top_effect)
        layout.addWidget(self._title_label)

        self._h_scroll = PhysicsScrollArea()
        self._h_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._h_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._h_scroll.setWidgetResizable(False)
        self._h_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._h_scroll.setFixedHeight(340)
        self._h_scroll.click_released.connect(self._on_click_released)
        self._h_scroll.set_hover_pause_cb(self._set_hover_paused)

        self._cards_container = QWidget()
        self._cards_container.setStyleSheet("background: transparent;")

        self._section_label = QLabel(self._cards_container)
        self._section_label.setText(self._title_text.upper())
        self._section_label.setWordWrap(True)
        self._section_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        _hdr_color = QColor(self._theme.get('text_primary_white', '#ffffff')
                            if self._theme else '#ffffff')
        _hdr_color.setAlpha(225)
        self._section_label.setStyleSheet(
            f"color: rgba({_hdr_color.red()}, {_hdr_color.green()}, "
            f"{_hdr_color.blue()}, 235); font-size: 40px; font-weight: bold;"
            f" letter-spacing: 2px; background: transparent;")
        self._section_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        self._section_label.hide()

        self._cards_layout = QHBoxLayout(self._cards_container)
        self._cards_layout.setContentsMargins(4, 34, 4, 34)
        self._cards_layout.setSpacing(CARD_SPACING)
        self._cards_layout.addStretch()

        self._h_scroll.setWidget(self._cards_container)
        self._h_scroll.set_resize_cb(self._on_viewport_resized)
        self._h_scroll.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._update_lead_state())
        layout.addWidget(self._h_scroll)

        initial = list(cards)
        if progressive and len(initial) > PROGRESSIVE_CHUNK:
            # Costruzione progressiva: creare centinaia di widget in un colpo
            # bloccava la UI per ~500ms quando la riga entrava in scena.
            self._pending = initial[PROGRESSIVE_CHUNK:]
            initial = initial[:PROGRESSIVE_CHUNK]
        for card_data in initial:
            self._card_data.append(card_data)
            self.add_card(card_data)
        self._update_container_width()
        if self._pending:
            self._build_timer = QTimer(self)
            self._build_timer.setSingleShot(True)
            self._build_timer.setInterval(0)
            self._build_timer.timeout.connect(self._build_next_chunk)
            self._build_timer.start()

    def _update_lead_state(self):
        """Titolo sopra: fade-in man mano che il titolo laterale esce."""
        try:
            v = self._h_scroll.horizontalScrollBar().value()
        except RuntimeError:
            return
        pad = max(1, getattr(self, '_pad', 0))
        if self._section_label.isHidden():
            target = 1.0
        else:
            start = pad * 0.30
            span = max(1.0, pad * 0.55)
            target = max(0.0, min(1.0, (v - start) / span))
        if abs(target - self._top_opacity) > 0.02:
            self._top_opacity = target
            try:
                self._top_effect.setOpacity(target)
            except RuntimeError:
                pass

    def _on_viewport_resized(self):
        if not self._cards:
            return
        self._update_container_width()

    def _build_next_chunk(self):
        if not self._pending:
            return
        chunk = self._pending[:PROGRESSIVE_CHUNK]
        del self._pending[:PROGRESSIVE_CHUNK]
        self.append_cards(chunk)
        # Ci fermiamo al budget: le altre card si costruiscono quando si
        # scorre verso destra (evita raffiche di decode immagine all'apertura).
        if self._pending and len(self._card_data) < self._auto_budget:
            self._build_timer.start()

    def build_more(self, count=GROW_CHUNK):
        if not self._pending:
            return False
        self._auto_budget = max(self._auto_budget, len(self._card_data)) + count
        if self._build_timer is not None and not self._build_timer.isActive():
            self._build_timer.start()
        return True

    def has_pending(self):
        return bool(self._pending)

    def build_until(self, n):
        """Costruisce solo le card necessarie a coprire una posizione di
        scroll (evita di creare 200 card quando ne servono 12)."""
        if self._build_timer is not None:
            self._build_timer.stop()
        n = max(0, int(n))
        need = n - len(self._card_data)
        if need > 0 and self._pending:
            chunk = self._pending[:need]
            del self._pending[:need]
            self.append_cards(chunk)
        if self._pending:
            self._build_timer.start()

    def add_card(self, card_data):
        return self._make_card(card_data)

    def _make_card(self, card_data, insert_at=None):
        title = card_data.get("title", "")
        cover = card_data.get("cover_path")
        theme = self._theme or None
        card = DiscoveryCard(title=title, cover_path=cover, theme=theme)
        card._meta = card_data.get("meta", {})
        card._igdb = card_data.get("igdb", {})
        card.set_card_size(getattr(self, '_card_w', 200), getattr(self, '_card_h', 310))
        card.installEventFilter(self._h_scroll)
        if self._hover_paused:
            card.set_hover_paused(True)
        if insert_at is None:
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)
        else:
            self._cards_layout.insertWidget(insert_at, card)
            self._cards.insert(insert_at, card)
        return card

    def original_cards(self):
        return self._cards[:len(self._card_data)]

    @_perf("append_cards")
    def append_cards(self, card_data_list):
        if not card_data_list:
            return
        old_n = len(self._card_data)
        fit_count = self._fit_count()
        self._card_data.extend(card_data_list)
        insert_at = old_n
        for card_data in card_data_list:
            self._make_card(card_data, insert_at)
            insert_at += 1
        self._update_container_width()

    def _prune_after(self, n):
        keep = self._cards[:n]
        drop = self._cards[n:]
        self._cards = keep
        for card in drop:
            self._cards_layout.removeWidget(card)
            card.setParent(None)
            card.deleteLater()

    def _set_hover_paused(self, paused):
        self._hover_paused = bool(paused)
        for card in self._cards:
            card.set_hover_paused(paused)

    @_perf("row_click")
    def _on_click_released(self, card):
        if card is None:
            return
        self.card_clicked.emit(card)

    def _fit_count(self):
        viewport_w = max(self._h_scroll.viewport().width(), 800)
        card_w = getattr(self, '_card_w', 200) or 200
        return max(6, math.ceil(viewport_w / (card_w + CARD_SPACING)) + 1)

    def _add_wrap_duplicates(self):
        fit_count = self._fit_count()
        n = len(self._card_data)
        for i in range(min(fit_count, n)):
            card_data = self._card_data[i]
            card = DiscoveryCard(
                title=card_data.get("title", ""),
                cover_path=card_data.get("cover_path"),
                theme=self._theme or None,
            )
            card.set_card_size(
                getattr(self, '_card_w', 200),
                getattr(self, '_card_h', 310),
            )
            card.installEventFilter(self._h_scroll)
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards.append(card)

    def set_card_size(self, w, h):
        self._card_w = w
        self._card_h = h
        for card in self._cards:
            card.set_card_size(w, h)
        self._h_scroll.setFixedHeight(h + 68)
        self._update_container_width()

    def _update_container_width(self):
        count = len(self._cards)
        if not count:
            return
        card_w = getattr(self, '_card_w', 200)
        card_h = getattr(self, '_card_h', 310)
        vw = max(self._h_scroll.width(), self._h_scroll.viewport().width(), 600)
        old_pad = getattr(self, '_pad', -1)
        step = card_w + CARD_SPACING
        # A riposo: titolo a sinistra + 4 card intere (due a cavallo del centro).
        pad = max(120, vw - 4 * step - 24)
        if pad != old_pad:
            self._pad = pad
            self._cards_layout.setContentsMargins(pad, 34, pad, 34)
            try:
                sb = self._h_scroll.horizontalScrollBar()
                if not getattr(self, '_scroll_initialized', False):
                    if vw > 650:
                        sb.setValue(0)
                        self._scroll_initialized = True
                elif old_pad >= 0:
                    if sb.value() <= old_pad + 2:
                        # ancora all'inizio (titolo + prima card al centro)
                        sb.setValue(0)
                    else:
                        # resize: mantieni la stessa posizione visiva
                        sb.setValue(sb.value() + (pad - old_pad))
            except RuntimeError:
                pass
        total_w = count * card_w + (count - 1) * CARD_SPACING + 2 * pad
        if self._cards_layout.isEnabled():
            self._cards_container.setFixedWidth(total_w)
        self._cards_container.setFixedHeight(card_h + 68)
        self._h_scroll.set_wrap_width(total_w)

        # Titolo della categoria nel lead-in (a sinistra delle card): cosi'
        # l'inizio della riga ha un contenuto e si capisce che a sinistra
        # non c'e' altro da scorrere.
        hdr_h = 170
        hdr_w = max(0, min(pad, 2 * step))
        if hdr_w < 110:
            self._section_label.hide()
        else:
            self._section_label.setFixedWidth(hdr_w)
            self._section_label.setFixedHeight(hdr_h)
            self._section_label.setGeometry(
                0, max(0, (card_h + 68 - hdr_h) // 2), hdr_w, hdr_h)
            self._section_label.show()
            self._section_label.raise_()
        self._update_lead_state()

    def set_theme(self, theme):
        self._theme = theme or {}
        self._themed = True
        self._title_color = QColor(theme.get('text_primary_white', '#ffffff'))
        self._apply_title_style()
        for card in self._cards:
            card.set_theme(theme)

    def set_focused(self, focused):
        self._focused = focused
        self._apply_title_style()

    def _apply_title_style(self):
        color = QColor(self._title_color)
        if not self._focused:
            color.setAlpha(90)
        self._title_label.setStyleSheet(
            f"{self._title_style_base} "
            f"color: rgba({color.red()}, {color.green()}, {color.blue()}, {color.alpha()});"
        )


class InfiniteCategoryScroller(QWidget):

    row_focus_changed = pyqtSignal()

    def __init__(self, category_rows, parent=None):
        super().__init__(parent)
        self._pool = list(category_rows)
        self._focus_idx = 1
        self._transition_offset = 0.0
        self._is_transitioning = False
        self._transition_anim = None
        self._row_h = 400
        self._single = False
        self._wheel_accum = 0
        self._wheel_last = 0.0
        self._last_switch = 0.0

        for row in self._pool:
            row.setParent(self)
            self._attach_row(row)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _attach_row(self, row):
        pass

    def set_single(self, single):
        """True = una sola riga a schermo (niente righe sopra/sotto)."""
        self._single = bool(single)
        self._position_slots()

    def set_pool(self, rows):
        rows = list(rows)
        old = list(self._pool)
        self._pool = rows
        for row in rows:
            row.setParent(self)
            self._attach_row(row)
        if not self._pool:
            return
        self._focus_idx = min(self._focus_idx, len(self._pool) - 1)
        for row in old:
            if row not in self._pool:
                row.hide()
        self._position_slots()

    def _get_transition_offset(self):
        return self._transition_offset

    def _set_transition_offset(self, val):
        self._transition_offset = val
        self._position_slots()

    transition_offset = pyqtProperty(
        float,
        fget=_get_transition_offset,
        fset=_set_transition_offset,
    )

    def set_card_size(self, w, h):
        for row in self._pool:
            row.set_card_size(w, h)

    def _compute_row_height(self):
        if hasattr(self, '_last_card_h') and self._last_card_h > 0:
            self._row_h = self._last_card_h + 68 + TITLE_BAND
        elif self._pool:
            sh = self._pool[0].sizeHint().height()
            if sh > 50:
                self._row_h = sh
        return self._row_h

    def _get_slot_positions(self):
        H = self.height()
        row_h = self._compute_row_height()
        gap = 10

        center_y = (H - row_h) // 2
        top_y = center_y - row_h - gap
        bottom_y = center_y + row_h + gap

        return [int(top_y), int(center_y), int(bottom_y)]

    def _get_slot_assignments(self):
        n = len(self._pool)
        return [
            self._pool[(self._focus_idx - 1) % n],
            self._pool[self._focus_idx % n],
            self._pool[(self._focus_idx + 1) % n],
        ]

    @_perf("position_slots")
    def _position_slots(self):
        row_h = self._compute_row_height()

        if self._single:
            if not self._pool:
                return
            self._focus_idx %= len(self._pool)
            current = self._pool[self._focus_idx]
            for row in self._pool:
                if row is not current:
                    row.hide()
            y = (self.height() - row_h) // 2
            current.setGeometry(0, int(y), self.width(), row_h)
            current.set_focused(True)
            current.show()
            return

        slot_y = self._get_slot_positions()
        rows = self._get_slot_assignments()

        for row in self._pool:
            row.hide()
        for i, row in enumerate(rows):
            y = slot_y[i] - self._transition_offset
            row.setGeometry(0, int(y), self.width(), row_h)
            row.set_focused(i == 1)
            row.show()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        if self._is_transitioning:
            self._wheel_accum = 0
            return

        # I touchpad mandano tanti delta piccoli: senza soglia una carezza
        # faceva saltare di riga. Accumuliamo finche' non e' uno scatto vero.
        now = time.monotonic()
        if now - self._wheel_last > 0.4:
            # Un accumulo vecchio non deve far scattare la riga piu' tardi.
            self._wheel_accum = 0
        self._wheel_last = now
        self._wheel_accum += delta
        if abs(self._wheel_accum) < WHEEL_ROW_STEP:
            event.accept()
            return

        direction = 1 if self._wheel_accum < 0 else -1
        self._wheel_accum = 0

        if self._single:
            if now - self._last_switch < ROW_SWITCH_COOLDOWN:
                return
            self._last_switch = now
        self._start_transition(direction)

    def _start_transition(self, direction):
        if self._is_transitioning:
            return

        if self._single:
            n = len(self._pool)
            if n == 0:
                return
            self._focus_idx = (self._focus_idx + direction) % n
            self._transition_offset = 0.0
            self._position_slots()
            self.row_focus_changed.emit()
            return

        self._is_transitioning = True
        row_h = self._compute_row_height()
        target_offset = row_h * direction

        self._transition_anim = QPropertyAnimation(self, b"transition_offset")
        self._transition_anim.setDuration(TRANSITION_DURATION)
        self._transition_anim.setStartValue(0.0)
        self._transition_anim.setEndValue(float(target_offset))
        self._transition_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._transition_anim.finished.connect(lambda: self._finish_transition(direction))
        self._transition_anim.start()

    @_perf("finish_transition")
    def _finish_transition(self, direction):
        n = len(self._pool)
        self._focus_idx = (self._focus_idx + direction) % n
        self._transition_offset = 0.0
        self._is_transitioning = False
        self._position_slots()
        self.row_focus_changed.emit()

    def focus_index(self, index):
        """Teletrasporta la vista su una riga (senza animazione)."""
        n = len(self._pool)
        if n == 0:
            return
        try:
            index = int(index) % n
        except (TypeError, ValueError):
            return
        self._focus_idx = index
        self._transition_offset = 0.0
        self._is_transitioning = False
        self._position_slots()
        self.row_focus_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._compute_row_height()
        self._position_slots()


COVER_WINDOW_ROWS = 6
COVER_WINDOW_CARDS = 15


class DiscoveryTab(QWidget):

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._category_rows = []
        self._categories = []
        self._category_keys = []
        self._row_loading = {}
        self._load_cooldown = {}
        self._search_mode = False
        self._search_row = None
        self._saved_focus = 1
        self._meta_requested_keys = set()
        self._dirty_keys = set()
        self._sort_value = ""
        self.setObjectName("DiscoveryTab")
        self._init_ui()

    def _discovery(self):
        return getattr(self.mw, 'discovery_manager', None)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._categories = [
            {"title": "\U0001f4da My Library", "provider": self._library_provider},
        ]

        discovery = self._discovery()
        if discovery is not None:
            for cat in discovery.categories():
                self._categories.append({"title": cat["title"], "provider": None})
                self._category_keys.append(cat["key"])

        self._build_filter_bar(root_layout)
        self._build_notice_bar(root_layout)
        self._build_category_rows()

        self._scroller = InfiniteCategoryScroller(self._category_rows)
        self._scroller.set_single(True)
        self._scroller.row_focus_changed.connect(self._update_cover_window)
        self._scroller.row_focus_changed.connect(self._sync_jump_combo)
        self._scroller.row_focus_changed.connect(self._sync_sort_combo)
        root_layout.addWidget(self._scroller)
        self._sync_jump_combo()
        self._sync_sort_combo()
        self._rebuild_visible_rows()

        self._mask_overlay = MaskOverlay(self)
        self._mask_overlay.raise_()
        if hasattr(self, '_filter_bar_widget'):
            self._filter_bar_widget.raise_()

        self._recalc_layout()
        self._apply_theme()
        if discovery is not None and not secrets.has_igdb_credentials():
            self.show_config_notice(
                "IGDB not configured",
                "Trending and New Releases are hidden. Add your IGDB credentials "
                "in Settings → API Keys (optional).",
            )
        QTimer.singleShot(0, self._recalc_layout)

        if discovery is not None:
            discovery.categories_changed.connect(self._on_discovery_changed)
            discovery.page_changed.connect(self._on_discovery_page_changed)
            discovery.cover_ready.connect(self._on_cover_ready)
            discovery.meta_ready.connect(self._on_meta_ready)
            discovery.start()

        # I trailer si caricano solo quando l'utente interagisce (focus row).

    def _build_category_rows(self):
        for i, cat in enumerate(self._categories):
            if i == 0:
                key = None
                cards_data = cat["provider"]()
            else:
                key = self._category_keys[i - 1]
                # Lazy: le card della riga si costruiscono solo quando è visibile.
                cards_data = []
            row = CategoryRow(cat["title"], cards_data,
                               theme=self._theme_data(), progressive=True)
            row._category_key = key
            self._category_rows.append(row)
            self._wire_row(row)
        self._dirty_keys = set(self._category_keys)

    # ── filters ──────────────────────────────────────────────────────

    def _build_filter_bar(self, root_layout):
        theme = self._theme_data() or {}
        muted = theme.get('text_muted', '#8a7fa4')
        text = theme.get('text_primary_white', '#ffffff')
        accent = theme.get('main_accent_color', '#733d91')
        neon = theme.get('bright_neon_accent', '#ff00a0')
        panel = theme.get('surface_panel_purple', '#2b1a42')

        self._filter_bar_widget = QWidget()
        self._filter_bar_widget.setFixedHeight(64)
        self._filter_bar_widget.setSizePolicy(QSizePolicy.Policy.Preferred,
                                              QSizePolicy.Policy.Fixed)
        bar = QHBoxLayout(self._filter_bar_widget)
        bar.setContentsMargins(18, 10, 18, 8)
        bar.setSpacing(10)

        self.filter_search = QLineEdit()
        self.filter_search.setPlaceholderText("Search games...")
        self.filter_search.setClearButtonEnabled(True)
        self.filter_search.setMinimumHeight(38)
        self.filter_search.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.07); color: {text};"
            f" border: 1px solid {accent}; border-radius: 9px;"
            f" padding: 6px 14px; font-size: 15px; }}"
            f"QLineEdit:focus {{ border: 1px solid {neon};"
            f" background: rgba(255,255,255,0.11); }}"
        )
        self.filter_search.textChanged.connect(self._schedule_filters)
        bar.addWidget(self.filter_search, 1)

        caption = QLabel("GO TO")
        caption.setStyleSheet(
            f"color: {muted}; font-size: 11px; font-weight: bold;"
            f" letter-spacing: 1px; background: transparent;")
        bar.addWidget(caption)

        self.filter_jump = QComboBox()
        self.filter_jump.addItem("Jump to category...", None)
        for i, cat in enumerate(self._categories):
            self.filter_jump.addItem(cat["title"], i)
        self.filter_jump.setMinimumWidth(250)
        self.filter_jump.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,0.06); color: {text};"
            f" border: 1px solid rgba(255,255,255,0.14); border-radius: 7px;"
            f" padding: 5px 10px; font-size: 12px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text};"
            f" selection-background-color: {accent}; outline: none; }}"
        )
        self.filter_jump.activated.connect(self._on_jump_selected)
        bar.addWidget(self.filter_jump)

        self.filter_sort = QComboBox()
        for value, label in SORT_CHOICES:
            self.filter_sort.addItem(label, value)
        self.filter_sort.setMinimumWidth(175)
        self.filter_sort.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,0.06); color: {text};"
            f" border: 1px solid rgba(255,255,255,0.14); border-radius: 7px;"
            f" padding: 5px 10px; font-size: 12px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox:disabled {{ color: {muted};"
            f" border-color: rgba(255,255,255,0.07); }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text};"
            f" selection-background-color: {accent}; outline: none; }}"
        )
        self.filter_sort.activated.connect(self._on_sort_selected)
        self.filter_sort.setToolTip(
            "Sorting applies to rows that do not already follow a date order.")
        bar.addWidget(self.filter_sort)

        self.filter_hide_installed = QCheckBox("Hide installed")
        self.filter_hide_installed.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self.filter_hide_installed.toggled.connect(self._schedule_filters)
        bar.addWidget(self.filter_hide_installed)

        root_layout.addWidget(self._filter_bar_widget)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(350)
        self._filter_timer.timeout.connect(self._apply_filters)

        # Watchdog: rileva freeze del thread grafico anche quando nessuno
        # slot supera la soglia (es. paint storm durante il drag).
        self._wd_last = time.monotonic()
        self._wd_grace = time.monotonic() + 6.0   # ignora la fase di avvio
        self._wd_timer = QTimer(self)
        self._wd_timer.setInterval(100)
        self._wd_timer.timeout.connect(self._watchdog_tick)
        self._wd_timer.start()

    def _build_notice_bar(self, root_layout):
        """Dismissible banner for non-fatal network/config notices."""
        self._notice_bar = QWidget()
        self._notice_bar.setVisible(False)
        self._notice_kind = None
        self._notice_bar.setStyleSheet(
            "background: rgba(255, 180, 0, 0.10);"
            "border-bottom: 1px solid rgba(255, 180, 0, 0.45);"
        )
        row = QHBoxLayout(self._notice_bar)
        row.setContentsMargins(18, 8, 18, 8)
        row.setSpacing(12)
        self._notice_label = QLabel("")
        self._notice_label.setWordWrap(True)
        self._notice_label.setStyleSheet("color: #ffcc66; font-size: 12px;")
        row.addWidget(self._notice_label, 1)
        btn = QPushButton("Dismiss")
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #ffcc66;"
            " border: 1px solid #ffcc66; border-radius: 5px; padding: 3px 10px; }"
        )
        btn.clicked.connect(self._notice_bar.hide)
        row.addWidget(btn)
        root_layout.addWidget(self._notice_bar)

    def _show_notice(self, kind, title, body=""):
        if not hasattr(self, "_notice_bar"):
            return
        self._notice_kind = kind
        text = f"<b>{title}</b>"
        if body:
            text += (
                "<br><span style='color:#9c9c9c;'>"
                + str(body).replace("<", "&lt;").replace(">", "&gt;")
                + "</span>"
            )
        self._notice_label.setText(text)
        self._notice_bar.show()
        if hasattr(self, "_mask_overlay"):
            QTimer.singleShot(0, self._layout_fades)

    def show_network_notice(self, message):
        detail = str(message or "").strip()
        self._show_notice(
            "network",
            "⚠ Unable to reach SteamRip. Check your internet connection or add "
            "an exception for the launcher in your antivirus/firewall.",
            detail,
        )

    def show_config_notice(self, title, body=""):
        self._show_notice("config", title, body)

    def hide_network_notice(self):
        if self._notice_kind != "network":
            return
        if hasattr(self, "_notice_bar") and self._notice_bar.isVisible():
            self._notice_bar.hide()
            if hasattr(self, "_mask_overlay"):
                QTimer.singleShot(0, self._layout_fades)

    def _watchdog_tick(self):
        now = time.monotonic()
        gap = now - self._wd_last
        self._wd_last = now
        if now < self._wd_grace:
            return
        if gap > 0.25:
            print(f"[HITCH] ui blocked {gap:.2f}s at {now:.2f}")

    def _schedule_filters(self, *args):
        if hasattr(self, '_filter_timer'):
            self._filter_timer.start()

    def _on_sort_selected(self, _index):
        self._sort_value = self.filter_sort.currentData() or ""
        self._apply_filters()
        if self._search_mode:
            return
        # Ordine nuovo: si riparte dall'inizio della riga.
        for row in self._visible_rows():
            sb = row._h_scroll.horizontalScrollBar()
            if sb is not None:
                sb.setValue(0)

    def _sort_key(self):
        """Chiave della riga in focus (None = My Library)."""
        if self._search_mode:
            return None
        rows = self._category_rows
        idx = self._scroller._focus_idx
        if rows and 0 <= idx < len(rows):
            return getattr(rows[idx], '_category_key', None)
        return None

    def _sync_sort_combo(self):
        if not hasattr(self, "filter_sort"):
            return
        key = self._sort_key()
        discovery = self._discovery()
        if self._search_mode:
            sortable = False
        elif key is None:
            sortable = True          # My Library: ordinabile in locale
        else:
            sortable = bool(discovery is not None
                            and discovery.is_sortable_key(key))

        self.filter_sort.blockSignals(True)
        try:
            if sortable:
                idx = self.filter_sort.findData(self._sort_value)
                self.filter_sort.setCurrentIndex(max(0, idx))
                self.filter_sort.setEnabled(True)
                self.filter_sort.setToolTip(
                    "Sorting applies to this row. Date-based rows keep "
                    "their own order.")
            else:
                self.filter_sort.setCurrentIndex(0)
                self.filter_sort.setEnabled(False)
                self.filter_sort.setToolTip(
                    "This row keeps its own order (date or site order).")
        finally:
            self.filter_sort.blockSignals(False)

    def _on_jump_selected(self, _index):
        idx = self.filter_jump.currentData()
        if idx is None:
            return
        self._scroller.focus_index(idx)

    def _sync_jump_combo(self):
        if not hasattr(self, 'filter_jump') or self._search_mode:
            return
        focus = self._scroller._focus_idx
        self.filter_jump.blockSignals(True)
        self.filter_jump.setCurrentIndex(focus + 1)
        self.filter_jump.blockSignals(False)

    def _is_searching(self):
        return len(self.filter_search.text().strip()) >= 2

    def _apply_filters(self):
        discovery = self._discovery()
        if discovery is None:
            return
        discovery.set_filters({
            "search": self.filter_search.text(),
            "hide_installed": self.filter_hide_installed.isChecked(),
            "sr_category": None,
            "igdb_genre": None,
            "sort": self._sort_value,
        })
        if self._is_searching():
            self._show_search_results()
            return
        self._exit_search_mode()
        self._mark_all_rows_dirty()
        self._rebuild_visible_rows()

    def _show_search_results(self):
        """Una sola riga dinamica "Search Results" al posto delle categorie."""
        discovery = self._discovery()
        if discovery is None:
            return
        if not self._search_mode:
            self._search_mode = True
            self._saved_focus = self._scroller._focus_idx
            if hasattr(self, "filter_jump"):
                self.filter_jump.setEnabled(False)

        cards = discovery.search_cards()
        old = self._search_row
        row = CategoryRow("Search Results", cards,
                          theme=self._theme_data(), progressive=True)
        row.set_card_size(*self._current_card_size())
        row._category_key = None
        self._search_row = row
        self._wire_row(row)
        self._scroller.set_pool([row])
        self._scroller.set_single(True)
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self._dirty_keys.clear()
        self._sync_sort_combo()
        QTimer.singleShot(0, self._update_cover_window)

    def _exit_search_mode(self):
        if not self._search_mode:
            return
        self._search_mode = False
        if hasattr(self, "filter_jump"):
            self.filter_jump.setEnabled(True)
        old = self._search_row
        self._search_row = None
        self._scroller.set_pool(self._category_rows)
        self._scroller.set_single(True)
        self._scroller.focus_index(getattr(self, "_saved_focus", 1))
        self._sync_sort_combo()
        if old is not None:
            old.setParent(None)
            old.deleteLater()

    @_perf("on_meta_ready")
    def _on_meta_ready(self, results):
        if not results:
            return
        if self._search_mode:
            self._show_search_results()
            return
        # Niente reset di _cover_loaded: ri-enqueued copriva le stesse cover
        # a ogni batch di metadati (download + decode ripetuti).

    def _mark_all_rows_dirty(self):
        self._dirty_keys = set(self._category_keys)

    def _visible_rows(self):
        pool = self._scroller._pool
        if not pool:
            return []
        return [pool[self._scroller._focus_idx % len(pool)]]

    def _visible_keys(self):
        return {
            getattr(row, '_category_key', None)
            for row in self._visible_rows()
            if getattr(row, '_category_key', None)
        }

    def _rebuild_visible_rows(self):
        """Ricostruisce solo le righe visibili: evita il freeze da full rebuild."""
        keys = []
        for row in self._visible_rows():
            key = getattr(row, '_category_key', None)
            if key is None:
                if (not self._search_mode and self._category_rows
                        and row is self._category_rows[0]):
                    new_row = CategoryRow(
                        self._categories[0]["title"], self._library_provider(),
                        theme=self._theme_data(), progressive=True)
                    self._replace_row(0, new_row)
                continue
            if key in self._dirty_keys and key not in keys:
                keys.append(key)
        for key in keys:
            idx = self._index_of_key(key)
            if idx is None:
                continue
            new_row = CategoryRow(self._discovery_title(key), self._cards_for_key(key),
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)
            self._dirty_keys.discard(key)
        QTimer.singleShot(0, self._update_cover_window)

    def _cards_for_key(self, key):
        discovery = self._discovery()
        if discovery is None:
            return []
        return discovery.get_cards(key)

    def _discovery_title(self, key):
        discovery = self._discovery()
        if discovery is None:
            return ""
        for cat in discovery.categories():
            if cat["key"] == key:
                return cat["title"]
        return ""

    def _index_of_key(self, key):
        try:
            return self._category_keys.index(key) + 1
        except ValueError:
            return None

    def _wire_row(self, row):
        sb = row._h_scroll.horizontalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(lambda v, r=row: self._maybe_load_more(r, v))
            # Lo scorrimento orizzontale deve far scaricare le cover delle
            # card che entrano in scena (prima si fermava alle prime 15).
            sb.valueChanged.connect(
                lambda _v: self._schedule_cover_window())
        row.card_clicked.connect(self._on_card_clicked)

    def _on_card_clicked(self, card):
        title = str(getattr(card, "_title", "") or "").strip()
        if not title:
            return
        if time.monotonic() < getattr(self.mw, "_discovery_click_guard", 0):
            return
        self._open_details(title)

    def _open_details(self, title):
        popup = getattr(self, "_detail_popup", None)
        if popup is None:
            from ui.widgets.game_detail_popup import GameDetailPopup
            popup = GameDetailPopup(self.mw)
            self._detail_popup = popup
        popup.show_game(title)

    @_perf("replace_row")
    def _replace_row(self, idx, new_row):
        old_row = self._category_rows[idx]
        prev_scroll = old_row._h_scroll.horizontalScrollBar().value()
        new_row._category_key = old_row._category_key
        new_row.setParent(self._scroller)
        self._category_rows[idx] = new_row
        self._scroller._pool[idx] = new_row
        self._scroller._attach_row(new_row)
        card_w, card_h = self._current_card_size()
        new_row.set_card_size(card_w, card_h)
        self._scroller._last_card_h = card_h
        if not getattr(new_row, '_themed', False):
            theme = self._theme_data()
            if theme:
                new_row.set_theme(theme)
        self._wire_row(new_row)
        self._scroller._position_slots()
        if prev_scroll > 0:
            step = max(1, card_w + CARD_SPACING)
            vw = new_row._h_scroll.viewport().width() or 800
            needed = int((prev_scroll + vw) / step) + 4
            new_row.build_until(needed)
        new_row._h_scroll.horizontalScrollBar().setValue(prev_scroll)
        old_row.setParent(None)
        old_row.deleteLater()

    def refresh_library(self):
        if not self._category_rows:
            return
        new_row = CategoryRow(self._categories[0]["title"], self._library_provider(),
                              theme=self._theme_data())
        self._replace_row(0, new_row)

    def _library_provider(self):
        games = self.mw.installed_games if hasattr(self.mw, 'installed_games') else {}
        sync_folder = ""
        if hasattr(self.mw, 'app_config'):
            sync_folder = self.mw.app_config.get('savegames', {}).get('sync_folder', '')

        cards = []
        for title in sorted(games.keys()):
            cover = MiniCoverManager.resolve_cover_path(title, games, sync_folder)
            cards.append({
                "title": title,
                "cover_path": cover,
                "meta": dict(games.get(title) or {}),
            })
        discovery = self._discovery()
        if discovery is not None:
            cards = discovery.sort_cards(cards)
        return cards

    # ── discovery manager wiring ────────────────────────────────────

    @_perf("discovery_changed")
    def _on_discovery_changed(self):
        if self._search_mode:
            self._show_search_results()
            return
        self._mark_all_rows_dirty()
        self._rebuild_visible_rows()

    @_perf("page_changed")
    def _on_discovery_page_changed(self, key):
        self.hide_network_notice()
        self._row_loading.pop(key, None)
        self._meta_requested_keys.discard(key)
        if key not in self._visible_keys():
            # Riga fuori schermo: niente rebuild adesso, la calcoliamo quando entra.
            self._dirty_keys.add(key)
            return
        idx = self._index_of_key(key)
        if idx is None:
            return

        discovery = self._discovery()
        if discovery is not None and discovery.row_sort_active(key):
            new_row = CategoryRow(self._discovery_title(key),
                                  self._cards_for_key(key),
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)
            self._dirty_keys.discard(key)
            self._update_cover_window()
            return

        # Append incrementale: non ricreiamo la riga (evita il softlock durante il drag).
        row = self._category_rows[idx]
        new_cards = self._cards_for_key(key)
        existing = {c.get("title") for c in getattr(row, '_card_data', []) if c.get("title")}
        added = [c for c in new_cards if c.get("title") not in existing]

        if added:
            row.append_cards(added)
        elif not getattr(row, '_card_data', None):
            # Riga vuota (prima visualizzazione): serve il rebuild completo.
            new_row = CategoryRow(self._discovery_title(key), new_cards,
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)

        self._dirty_keys.discard(key)
        self._update_cover_window()

    @_perf("cover_ready")
    def _on_cover_ready(self, title, path):
        discovery = self._discovery()
        if discovery is not None:
            discovery.set_cover_path(title, path)
        rows = list(self._visible_rows())
        if self._search_row is not None and self._search_row not in rows:
            rows.append(self._search_row)
        for row in rows:
            for card in row._cards:
                if card._title == title:
                    card.set_cover(path)

    def _ensure_row_capacity(self, row):
        if not hasattr(row, "has_pending") or not row.has_pending():
            return
        card_w, _h = self._current_card_size()
        step = max(1, card_w + CARD_SPACING)
        vw = row._h_scroll.viewport().width()
        value = max(0, row._h_scroll.horizontalScrollBar().value()
                    - getattr(row, '_pad', 0))
        built = max(1, len(row._card_data))
        if value + vw >= built * step * 0.8:
            row.build_more()

    @_perf("maybe_load_more")
    def _maybe_load_more(self, row, value):
        key = getattr(row, '_category_key', None)
        if key is not None:
            self._ensure_row_capacity(row)
        discovery = self._discovery()
        if key is None or discovery is None:
            return
        wrap = getattr(row._h_scroll, '_wrap_width', 0)
        vw = row._h_scroll.viewport().width()
        if wrap <= 0 or vw <= 0:
            return
        pad = getattr(row, '_pad', 0)
        if value < (wrap - pad) - vw - 60:
            return
        if self._row_loading.get(key):
            return
        now = time.monotonic()
        if now < self._load_cooldown.get(key, 0):
            return  # evita cascate di pagine mentre si trascina all'estremo
        if not discovery.request_more(key):
            return
        self._row_loading[key] = True
        self._load_cooldown[key] = now + 0.9
        self._update_cover_window()

    def _window_rows(self):
        pool = self._scroller._pool
        n = len(pool)
        if n == 0:
            return []
        if n == 1:
            return [pool[0]]
        focus = self._scroller._focus_idx
        return [pool[(focus + i) % n] for i in range(-3, 3)]

    def _cover_target_count(self, row):
        step = self._current_card_size()[0] + CARD_SPACING
        n_cards = len(row.original_cards())
        if step <= 0:
            return 0
        value = max(0, row._h_scroll.horizontalScrollBar().value()
                    - getattr(row, '_pad', 0))
        seen = int(value / step) if value > 0 else 0
        return max(0, min(n_cards, COVER_WINDOW_CARDS + seen))

    def _schedule_cover_window(self):
        timer = getattr(self, "_cover_window_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(180)
            timer.timeout.connect(self._update_cover_window)
            self._cover_window_timer = timer
        if not timer.isActive():
            timer.start()

    @_perf("cover_window")
    def _update_cover_window(self):
        discovery = self._discovery()
        if discovery is None:
            return
        pool = self._scroller._pool
        n = len(pool)
        if n == 0:
            return
        focus = self._scroller._focus_idx
        # Una sola riga visibile: niente prefetch delle righe adiacenti
        # (era una valanga di download/decode di cover a ogni cambio).
        window = self._visible_rows()
        focus_row = pool[focus]

        # Righe sporche (filtri/categorie cambiate): ricostruisci solo le visibili.
        dirty_slots = any(getattr(r, '_category_key', None) in self._dirty_keys
                          for r in self._visible_rows())
        if dirty_slots:
            self._rebuild_visible_rows()
            return

        if not hasattr(self, '_cover_active_keys'):
            self._cover_active_keys = set()
        active = {getattr(r, '_category_key', None) for r in window}
        active.discard(None)
        gone = self._cover_active_keys - active
        if gone:
            titles = set()
            for row in self._category_rows:
                if getattr(row, '_category_key', None) in gone:
                    for card in row.original_cards():
                        if getattr(card, '_title', ''):
                            titles.add(card._title)
            if titles:
                discovery.cancel_covers(sorted(titles))
        self._cover_active_keys = active

        for row in window:
            is_focus = row is focus_row
            target = self._cover_target_count(row)
            start = getattr(row, '_cover_loaded', 0)
            if target > start:
                row._cover_loaded = target
                # Use the card dicts directly: search rows are generated on
                # the fly and are not present in discovery._data, so the
                # title-based lookup used to enqueue nothing for them.
                card_data = getattr(row, '_card_data', [])
                window_cards = [
                    card for card in card_data[start:target]
                    if isinstance(card, dict) and card.get("title")
                ]
                if window_cards:
                    import os as _os
                    allow = is_focus and _os.environ.get("SRP_COVER_SCRAPE") == "1"
                    discovery.enqueue_cards(
                        window_cards, high=is_focus, allow_scrape=allow,
                        allow_page_fetch=is_focus)

        # Arricchimento IGDB della riga in focus (una volta per riga/sessione).
        focus_key = getattr(focus_row, '_category_key', None)
        if focus_key and focus_key not in self._meta_requested_keys:
            self._meta_requested_keys.add(focus_key)
            titles = [c._title for c in
                      focus_row.original_cards()[:COVER_WINDOW_CARDS + 15]
                      if c._title]
            if titles:
                discovery.enqueue_igdb_meta(titles)

    # ── trailer previews ─────────────────────────────────────────────

    def _current_card_size(self):
        vw = self.width()
        vh = self.height()
        if vw <= 0:
            vw = 1400
        if vh <= 0:
            vh = 800

        available_w = vw - 2 * FADE_MARGIN
        card_w = max(140, int((available_w - 5 * CARD_SPACING) / 6))
        card_h = max(
            200,
            int((card_w - 2 * GLOW_INSET) * 1.5)
            + GLOW_PAD + CARD_TITLE_H,
        )
        return card_w, card_h

    def _theme_data(self):
        if hasattr(self.mw, 'app_config'):
            return self.mw.app_config.get('theme', {})
        return {}

    @_perf("recalc_layout")
    def _recalc_layout(self):
        if not self._category_rows:
            return

        card_w, card_h = self._current_card_size()

        self._scroller.set_card_size(card_w, card_h)
        self._scroller._last_card_h = card_h
        self._scroller._position_slots()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc_layout()
        self._layout_fades()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'mw') and hasattr(self.mw, 'installed_games'):
            if self.mw.installed_games and not self._category_rows[0]._cards:
                self.refresh_library()
            self._update_cover_window()

    def _layout_fades(self):
        w, h = self.width(), self.height()
        top = 0
        if hasattr(self, '_filter_bar_widget') and self._filter_bar_widget.isVisible():
            top = self._filter_bar_widget.height()
        if hasattr(self, '_notice_bar') and self._notice_bar.isVisible():
            top += self._notice_bar.height()
        self._mask_overlay.setGeometry(0, top, w, max(0, h - top))
        self._mask_overlay.raise_()
        if hasattr(self, '_notice_bar'):
            self._notice_bar.raise_()
        if hasattr(self, '_filter_bar_widget'):
            self._filter_bar_widget.raise_()

    def _apply_theme(self):
        if not hasattr(self.mw, 'app_config'):
            return
        theme = self.mw.app_config.get('theme', {})
        bg = theme.get('main_background_color',
                        theme.get('background_dark_purple', '#120a1c'))
        self.setStyleSheet(f"background-color: {bg};")
        self._mask_overlay.set_fade_color(bg)
        for row in self._category_rows:
            row.set_theme(theme)

    def apply_theme(self):
        self._apply_theme()
