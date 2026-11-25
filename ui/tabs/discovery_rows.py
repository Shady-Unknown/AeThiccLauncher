"""Discovery carousel rows and the infinite scroller."""

import math
import time

from PyQt6.QtCore import (
    QEasingCurve, QPropertyAnimation, Qt, QTimer, QVariantAnimation,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from ui.widgets.discovery_card import DiscoveryCard
from ui.tabs.discovery_scroll import PhysicsScrollArea, _perf


CARD_SPACING = 6
FADE_MARGIN = 0
TRANSITION_DURATION = 300
TITLE_BAND = 62
# Wheel normalisation: one standard notch is 120 angle-delta units. A category
# switch needs ~2.5 notches; high-resolution/touchpad pixel deltas are mapped
# with the same scale. Reset after a short idle period or a direction reversal.
WHEEL_NOTCH_STEP = 120.0
WHEEL_PIXEL_STEP = 120.0
WHEEL_CATEGORY_THRESHOLD = 2.5
WHEEL_ACCUMULATOR_RESET_S = 0.35
ROW_SWITCH_COOLDOWN = 0.35    # s tra due cambi di riga consecutivi
VERTICAL_DRAG_COMPLETE_RATIO = 0.25
VERTICAL_DRAG_COMPLETE_VELOCITY = 500.0
VERTICAL_SNAP_DURATION = 240
PROGRESSIVE_CHUNK = 10
INITIAL_CARD_BUDGET = 40
GROW_CHUNK = 10


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
        self._build_cards(chunk)
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
            self._build_cards(chunk)
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

    def data_cards(self):
        """Card dicts in site order, built or still pending."""
        return list(self._card_data) + list(self._pending)

    @_perf("append_cards")
    def append_cards(self, card_data_list):
        """Add new card dicts (a page) after everything already queued."""
        if not card_data_list:
            return
        if self._pending:
            # The data order is authoritative: new cards belong after the ones
            # still waiting to be built. Building them now would insert the new
            # widgets before the pending chunk and scramble the site order.
            self._pending.extend(card_data_list)
            return
        self._build_cards(card_data_list)

    def _build_cards(self, card_data_list):
        if not card_data_list:
            return
        old_n = len(self._card_data)
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
        self._vertical_drag_active = False
        self._vertical_offset = 0.0
        self._vertical_start_idx = 0
        self._vertical_row_h = 0.0
        self._vertical_anim = None

        for row in self._pool:
            row.setParent(self)
            self._attach_row(row)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _attach_row(self, row):
        scroll = row._h_scroll
        scroll.vertical_drag_started.connect(self._begin_vertical_drag)
        scroll.vertical_drag_moved.connect(self._vertical_drag_move)
        scroll.vertical_drag_finished.connect(self._end_vertical_drag)
        scroll.vertical_drag_cancelled.connect(self._cancel_vertical_drag)

    def set_single(self, single):
        """True = una sola riga a schermo (niente righe sopra/sotto)."""
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
        self._single = bool(single)
        self._position_slots()

    def set_pool(self, rows):
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
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
            if self._vertical_drag_active or self._vertical_anim is not None:
                self._position_slots_vertical()
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

    def _position_slots_vertical(self):
        n = len(self._pool)
        if not n:
            return
        idx = self._focus_idx % n
        row_h = self._vertical_row_h or self._compute_row_height()
        base = (self.height() - row_h) // 2
        offset = self._vertical_offset
        current = self._pool[idx]
        previous = self._pool[(idx - 1) % n]
        following = self._pool[(idx + 1) % n]
        for row in self._pool:
            row.hide()
        current.setGeometry(0, int(base + offset), self.width(), row_h)
        current.set_focused(True)
        current.show()
        if offset > 0 and n > 1:
            previous.setGeometry(0, int(base - row_h + offset),
                                 self.width(), row_h)
            previous.set_focused(False)
            previous.show()
        elif offset < 0 and n > 1:
            following.setGeometry(0, int(base + row_h + offset),
                                  self.width(), row_h)
            following.set_focused(False)
            following.show()

    def _begin_vertical_drag(self):
        if not self._pool or self._is_transitioning:
            return
        if self._vertical_anim is not None:
            self._vertical_anim.stop()
            self._vertical_anim.deleteLater()
            self._vertical_anim = None
        self._vertical_drag_active = True
        self._vertical_start_idx = self._focus_idx % len(self._pool)
        self._vertical_row_h = self._compute_row_height()
        self._vertical_offset = 0.0
        self._position_slots()

    def _vertical_drag_move(self, dy):
        if not self._vertical_drag_active:
            return
        row_h = self._vertical_row_h or self._compute_row_height()
        self._vertical_offset = max(-row_h, min(row_h, float(dy)))
        self._position_slots()

    def _end_vertical_drag(self, velocity):
        if not self._vertical_drag_active:
            return
        self._vertical_drag_active = False
        row_h = self._vertical_row_h or self._compute_row_height()
        offset = self._vertical_offset
        direction = -1 if offset > 0 else 1
        velocity_agrees = (offset == 0 or velocity * offset > 0)
        complete = (abs(offset) >= row_h * VERTICAL_DRAG_COMPLETE_RATIO
                    or (abs(velocity) >= VERTICAL_DRAG_COMPLETE_VELOCITY
                        and velocity_agrees))
        if complete and len(self._pool) > 1:
            target_idx = (self._vertical_start_idx + direction) % len(self._pool)
            target_offset = -direction * row_h
            self._animate_vertical(target_offset, target_idx)
        else:
            self._animate_vertical(0.0, None)

    def _animate_vertical(self, target_offset, target_idx):
        if self._vertical_anim is not None:
            self._vertical_anim.stop()
            self._vertical_anim.deleteLater()
        anim = QVariantAnimation(self)
        anim.setDuration(VERTICAL_SNAP_DURATION)
        anim.setStartValue(float(self._vertical_offset))
        anim.setEndValue(float(target_offset))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.valueChanged.connect(self._set_vertical_offset)
        anim.finished.connect(lambda: self._finish_vertical_anim(target_idx))
        self._vertical_anim = anim
        anim.start()

    def _set_vertical_offset(self, value):
        self._vertical_offset = float(value)
        self._position_slots()

    def _finish_vertical_anim(self, target_idx):
        anim = self._vertical_anim
        self._vertical_anim = None
        if anim is not None:
            anim.deleteLater()
        if target_idx is not None:
            self._focus_idx = target_idx
        self._vertical_offset = 0.0
        self._position_slots()
        if target_idx is not None:
            self.row_focus_changed.emit()
        for row in self._pool:
            row._h_scroll._release_hover_pause(0)

    def _cancel_vertical_drag(self):
        self._vertical_drag_active = False
        if self._vertical_anim is not None:
            self._vertical_anim.stop()
            self._vertical_anim.deleteLater()
            self._vertical_anim = None
        self._vertical_offset = 0.0
        self._position_slots()
        for row in self._pool:
            row._h_scroll._release_hover_pause(0)

    @staticmethod
    def _wheel_delta_notches(event):
        """Normalise one wheel event to fractional standard notches.

        ``pixelDelta`` is used for high-resolution/touchpad input when present;
        ``angleDelta`` is the fallback. The two are never summed.
        """
        pixel = event.pixelDelta().y()
        if pixel:
            return pixel / WHEEL_PIXEL_STEP
        return event.angleDelta().y() / WHEEL_NOTCH_STEP

    def wheelEvent(self, event):
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
            event.accept()
            return
        delta = self._wheel_delta_notches(event)
        if not delta:
            cancel_phase = getattr(Qt.ScrollPhase, "ScrollCancel", None)
            if event.phase() in (Qt.ScrollPhase.ScrollEnd, cancel_phase):
                self._wheel_accum = 0.0
            event.accept()
            return
        if self._is_transitioning:
            # A pending change must not fire after the user already stopped.
            self._wheel_accum = 0.0
            self._wheel_last = time.monotonic()
            event.accept()
            return

        now = time.monotonic()
        if now - self._wheel_last > WHEEL_ACCUMULATOR_RESET_S:
            # Un accumulo vecchio non deve far scattare la riga piu' tardi.
            self._wheel_accum = 0.0
        if self._wheel_accum and (self._wheel_accum > 0) != (delta > 0):
            # An inversion starts a new gesture in the other direction.
            self._wheel_accum = 0.0
        self._wheel_last = now
        self._wheel_accum += delta
        if abs(self._wheel_accum) < WHEEL_CATEGORY_THRESHOLD:
            event.accept()
            return

        direction = 1 if self._wheel_accum < 0 else -1
        self._wheel_accum = 0.0

        if self._single:
            if now - self._last_switch < ROW_SWITCH_COOLDOWN:
                event.accept()
                return
            self._last_switch = now
        self._start_transition(direction)
        event.accept()

    def _start_transition(self, direction):
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
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
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
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
        if self._vertical_drag_active or self._vertical_anim is not None:
            self._cancel_vertical_drag()
        self._compute_row_height()
        self._position_slots()

