import os
import time
import PyQt6.sip as sip
from PyQt6.QtCore import (
    QRect, Qt, QPropertyAnimation, QEasingCurve, QSize, QTimer, QUrl,
    pyqtProperty, pyqtSignal
)
from PyQt6.QtGui import QPixmap, QColor, QPainter, QImage, QCursor
from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink
from PyQt6.QtWidgets import (
    QFrame, QLabel, QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
    QMenu, QSlider, QToolButton,
)

PLACEHOLDER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", ".media", "cover_placeholder.png"
)

# Cache delle cover già decodificate/scalate: evita di rileggere il JPEG
# (e di rifare lo scale smooth) quando la riga viene ricostruita o duplicata.
_SCALED_CACHE = {}
_SCALED_CACHE_MAX = 64
_PLACEHOLDER_CACHE = {}


def _scaled_cover(path, target_size):
    # Include mtime/size: the same minicover path is overwritten when a
    # horizontal SteamRIP fallback is upgraded to the vertical IGDB/SGDB box
    # art. Without the stamp the cache would keep serving the old pixmap.
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = (0, 0)
    key = (path, target_size.width(), target_size.height(), stamp)
    cached = _SCALED_CACHE.get(key)
    if cached is not None:
        return cached

    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    scaled = pixmap.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if len(_SCALED_CACHE) >= _SCALED_CACHE_MAX:
        _SCALED_CACHE.pop(next(iter(_SCALED_CACHE)))
    _SCALED_CACHE[key] = scaled
    return scaled


def _placeholder_cover(target_size):
    key = (target_size.width(), target_size.height())
    cached = _PLACEHOLDER_CACHE.get(key)
    if cached is not None:
        return cached
    base = QPixmap(PLACEHOLDER_PATH)
    if base.isNull():
        return base
    scaled = base.scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    _PLACEHOLDER_CACHE[key] = scaled
    return scaled

CARD_TITLE_H = 44
GLOW_PAD = 30
GLOW_INSET = 10
GLOW_BLUR = 40
HOLD_MS = 1000

CONTROLS_H = 30
CONTROLS_IDLE_MS = 2500
CONTROLS_FADE_MS = 180


class CoverLabel(QLabel):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self._fill_color = QColor("#120a1c")
        self._fast_scale = False
        self._zoom = 1.0

    def set_fast_scale(self, fast):
        self._fast_scale = bool(fast)

    def set_zoom(self, zoom):
        self._zoom = float(zoom)
        self.update()

    def set_fill_color(self, color):
        self._fill_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        import time as _t
        _t0 = _t.perf_counter()
        try:
            self._paint(event)
        finally:
            import os
            dt = (_t.perf_counter() - _t0) * 1000.0
            if dt > 8 and os.environ.get("SRP_DEBUG_PERF") == "1":
                print(f"[PERF] cover_paint: {dt:.0f}ms")

    def _paint(self, event):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        painter.fillRect(0, 0, w, h, self._fill_color)

        # Il pixmap è già scalato alla dimensione della label (vedi _load_cover):
        # riscalarlo qui a ogni repaint era il costo principale del drag.
        if pixmap.width() != w and pixmap.height() != h:
            pixmap = pixmap.scaled(
                QSize(w, h),
                Qt.AspectRatioMode.KeepAspectRatio,
                (Qt.TransformationMode.FastTransformation
                 if self._fast_scale
                 else Qt.TransformationMode.SmoothTransformation),
            )
        zoom = self._zoom
        if zoom and abs(zoom - 1.0) > 0.001:
            dw = int(pixmap.width() * zoom)
            dh = int(pixmap.height() * zoom)
            x = (w - dw) // 2
            y = (h - dh) // 2
            painter.drawPixmap(QRect(x, y, dw, dh), pixmap)
        else:
            x = (w - pixmap.width()) // 2
            y = (h - pixmap.height()) // 2
            painter.drawPixmap(x, y, pixmap)
        painter.end()


class VideoSurface(QWidget):

    play_toggle_requested = pyqtSignal()
    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image = None
        self._frame_interval = 1.0 / 30.0
        self._last_frame_at = 0.0
        self._active = False
        self._seeking = False
        self._paused = False
        self._duration_ms = 0
        self._play_handler = None
        self._seek_handler = None
        self._menu_builder = None
        self._fade_anim = None
        self._volume_handler = None
        self._volume_release_handler = None
        self._mute_handler = None
        self._fullscreen_handler = None
        self._muted = False
        self._syncing_seek = False
        self._seek_preview = 0

        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

        self._bar = QFrame(self)
        self._bar.setObjectName("VideoBar")

        self._play_btn = QToolButton(self._bar)
        self._play_btn.setObjectName("VideoControlBtn")
        self._play_btn.setText("▶")
        self._play_btn.setAutoRaise(True)
        self._play_btn.clicked.connect(self._on_play_clicked)

        self._seek = QSlider(Qt.Orientation.Horizontal, self._bar)
        self._seek.setObjectName("SeekSlider")
        self._seek.setRange(0, 0)
        self._seek.setEnabled(False)
        self._seek.sliderPressed.connect(self._seek_pressed)
        self._seek.sliderReleased.connect(self._seek_released)
        self._seek.valueChanged.connect(self._seek_value_changed)
        self._seek_debounce = QTimer(self)
        self._seek_debounce.setSingleShot(True)
        self._seek_debounce.setInterval(320)
        self._seek_debounce.timeout.connect(self._flush_seek)

        self._time_label = QLabel("0:00 / 0:00", self._bar)
        self._time_label.setObjectName("VideoTimeLabel")

        self._res_btn = QToolButton(self._bar)
        self._res_btn.setObjectName("ResButton")
        self._res_btn.setText("Auto ▾")
        self._res_btn.setAutoRaise(True)
        self._res_btn.clicked.connect(self._open_res_menu)

        self._mute_btn = QToolButton(self._bar)
        self._mute_btn.setObjectName("VideoControlBtn")
        self._mute_btn.setText("🔊")
        self._mute_btn.setAutoRaise(True)
        self._mute_btn.clicked.connect(self._on_mute_clicked)

        self._volume = QSlider(Qt.Orientation.Horizontal, self._bar)
        self._volume.setObjectName("VolumeSlider")
        self._volume.setRange(0, 100)
        self._volume.setValue(75)
        self._volume.setFixedWidth(64)
        self._volume.valueChanged.connect(self._on_volume_changed)
        self._volume.sliderReleased.connect(self._on_volume_released)

        self._fs_btn = QToolButton(self._bar)
        self._fs_btn.setObjectName("VideoControlBtn")
        self._fs_btn.setText("⛶")
        self._fs_btn.setAutoRaise(True)
        self._fs_btn.clicked.connect(self._on_fullscreen_clicked)

        bar_layout = QHBoxLayout(self._bar)
        bar_layout.setContentsMargins(8, 0, 8, 0)
        bar_layout.setSpacing(8)
        bar_layout.addWidget(self._play_btn)
        bar_layout.addWidget(self._seek, 1)
        bar_layout.addWidget(self._time_label)
        bar_layout.addWidget(self._mute_btn)
        bar_layout.addWidget(self._volume)
        bar_layout.addWidget(self._res_btn)
        bar_layout.addWidget(self._fs_btn)

        self._bar.installEventFilter(self)

        self._fade_effect = QGraphicsOpacityEffect(self._bar)
        self._fade_effect.setOpacity(0.0)
        self._bar.setGraphicsEffect(self._fade_effect)
        self._bar.hide()

        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(CONTROLS_IDLE_MS)
        self._idle_timer.timeout.connect(self._on_idle)

    # ── frame rendering ──────────────────────────────────────────────

    def set_frame(self, frame):
        now = time.monotonic()
        if now - self._last_frame_at < self._frame_interval:
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._last_frame_at = now
        self._image = image
        self.update()

    def clear_frame(self):
        self._image = None
        self.update()

    def has_frame(self):
        return self._image is not None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        image = self._image
        if image is not None and not image.isNull():
            scaled = image.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        painter.end()

    # ── input: the video area is its own reign ───────────────────────

    def mousePressEvent(self, event):
        self._bump_controls()
        event.accept()

    def mouseMoveEvent(self, event):
        self._bump_controls()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._bump_controls()
        event.accept()

    def wheelEvent(self, event):
        event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._bar.setGeometry(0, self.height() - CONTROLS_H,
                              self.width(), CONTROLS_H)

    # ── control bar (YouTube style) ──────────────────────────────────

    def set_theme(self, colors):
        self._theme = colors or {}
        accent = self._theme.get('accent', '#733d91')
        neon = self._theme.get('neon', '#ff00a0')
        text = self._theme.get('text', '#ffffff')
        muted = self._theme.get('muted', '#8a7fa4')
        self._bar.setStyleSheet(f"""
            QFrame#VideoBar {{
                background: rgba(0, 0, 0, 170);
            }}
            QToolButton#VideoControlBtn, QToolButton#ResButton {{
                color: {text};
                background: transparent;
                border: none;
                padding: 2px 6px;
            }}
            QToolButton#VideoControlBtn {{
                font-size: 13px;
            }}
            QToolButton#ResButton {{
                font-size: 11px;
            }}
            QToolButton#VideoControlBtn:hover, QToolButton#ResButton:hover {{
                color: {neon};
            }}
            QSlider#SeekSlider {{ background: transparent; }}
            QSlider#SeekSlider::groove:horizontal {{
                height: 4px;
                background: rgba(255, 255, 255, 60);
                border-radius: 2px;
            }}
            QSlider#SeekSlider::sub-page:horizontal {{
                height: 4px;
                background: {neon};
                border-radius: 2px;
            }}
            QSlider#SeekSlider::handle:horizontal {{
                width: 10px;
                height: 10px;
                margin: -3px 0;
                border-radius: 5px;
                background: {neon};
            }}
            QLabel#VideoTimeLabel {{
                color: {muted};
                font-size: 11px;
                background: transparent;
            }}
            QSlider#VolumeSlider {{ background: transparent; }}
            QSlider#VolumeSlider::groove:horizontal {{
                height: 3px;
                background: rgba(255, 255, 255, 60);
                border-radius: 2px;
            }}
            QSlider#VolumeSlider::sub-page:horizontal {{
                height: 3px;
                background: {neon};
                border-radius: 2px;
            }}
            QSlider#VolumeSlider::handle:horizontal {{
                width: 9px;
                height: 9px;
                margin: -3px 0;
                border-radius: 5px;
                background: {neon};
            }}
        """)

    def set_active(self, active):
        self._active = bool(active)
        if not active:
            self._idle_timer.stop()
            self._seeking = False
            self._bar.hide()

    def show_controls_now(self):
        if not self._active:
            return
        self._bar.show()
        self._animate_opacity(1.0)
        self._restart_idle()

    def hide_controls(self):
        if not self._bar.isVisible() or not self._active:
            return
        if self._paused or self._bar_contains_pointer():
            self._restart_idle()
            return
        self._animate_opacity(0.0)

    def _bump_controls(self):
        if not self._active:
            return
        if self._fade_effect.opacity() < 0.99:
            self._bar.show()
            self._animate_opacity(1.0)
        self._restart_idle()

    def _restart_idle(self):
        if not self._active or self._paused:
            return
        self._idle_timer.start()

    def _on_idle(self):
        if not self._active:
            return
        if self._paused or self._bar_contains_pointer():
            self._restart_idle()
            return
        self._animate_opacity(0.0)

    def _bar_contains_pointer(self):
        return self._bar.rect().contains(self._bar.mapFromGlobal(QCursor.pos()))

    def _animate_opacity(self, target):
        if self._fade_anim is not None and not sip.isdeleted(self._fade_anim):
            self._fade_anim.stop()
            self._fade_anim = None
        anim = QPropertyAnimation(self._fade_effect, b"opacity")
        anim.setDuration(CONTROLS_FADE_MS)
        anim.setStartValue(self._fade_effect.opacity())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        if target <= 0.0:
            anim.finished.connect(self._bar.hide)
        self._fade_anim = anim
        anim.start()

    def eventFilter(self, obj, event):
        if obj is self._bar or self._bar.isAncestorOf(obj):
            if event.type() == event.Type.MouseMove:
                self._bump_controls()
        return super().eventFilter(obj, event)

    # ── playback feedback ────────────────────────────────────────────

    def set_playing_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._paused = state == QMediaPlayer.PlaybackState.PausedState
        self._play_btn.setText("❚❚" if playing else "▶")
        if self._paused:
            self.show_controls_now()
        elif state == QMediaPlayer.PlaybackState.StoppedState:
            self.hide_controls()
        elif playing:
            self._bump_controls()

    def update_duration(self, ms):
        self._duration_ms = int(ms)
        self._seek.setRange(0, max(self._duration_ms, 0))
        self._seek.setEnabled(self._duration_ms > 0)
        self._update_time()

    def update_position(self, ms):
        if not self._seeking:
            self._syncing_seek = True
            self._seek.setValue(int(ms))
            self._syncing_seek = False
        self._update_time()

    def _update_time(self, position=None):
        pos = self._seek.value() if position is None else int(position)
        self._time_label.setText(
            f"{self._fmt(pos)} / {self._fmt(self._duration_ms)}"
        )

    @staticmethod
    def _fmt(ms):
        s = max(0, int(ms // 1000))
        return f"{s // 60}:{s % 60:02d}"

    def reset_controls(self):
        self._seeking = False
        self._paused = False
        self._duration_ms = 0
        self._idle_timer.stop()
        self._seek.setRange(0, 0)
        self._seek.setValue(0)
        self._seek.setEnabled(False)
        self._time_label.setText("0:00 / 0:00")
        self._play_btn.setText("▶")
        self._res_btn.setText("Auto ▾")
        self._bar.hide()

    def set_resolution_label(self, label):
        self._res_btn.setText(f"{label} ▾")

    # ── wiring ───────────────────────────────────────────────────────

    def set_play_toggle_handler(self, cb):
        self._play_handler = cb

    def set_volume_handler(self, cb):
        self._volume_handler = cb

    def set_volume_release_handler(self, cb):
        self._volume_release_handler = cb

    def set_mute_handler(self, cb):
        self._mute_handler = cb

    def set_fullscreen_handler(self, cb):
        self._fullscreen_handler = cb

    def set_volume(self, volume):
        value = max(0, min(100, int(round(float(volume) * 100))))
        self._volume.blockSignals(True)
        self._volume.setValue(value)
        self._volume.blockSignals(False)

    def set_seek_enabled(self, enabled):
        self._seek.setEnabled(bool(enabled))

    def set_muted(self, muted):
        self._muted = bool(muted)
        self._mute_btn.setText("🔇" if self._muted else "🔊")

    def _on_volume_changed(self, value):
        if self._volume_handler:
            self._volume_handler(int(value) / 100.0)

    def _on_volume_released(self):
        if self._volume_release_handler:
            self._volume_release_handler(int(self._volume.value()) / 100.0)

    def _on_mute_clicked(self):
        if self._mute_handler:
            self._mute_handler()

    def _on_fullscreen_clicked(self):
        if self._fullscreen_handler:
            self._fullscreen_handler()

    def set_seek_handler(self, cb):
        self._seek_handler = cb

    def set_menu_builder(self, cb):
        self._menu_builder = cb

    def _on_play_clicked(self):
        if self._play_handler:
            self._play_handler()

    def _seek_pressed(self):
        self._seeking = True
        self._seek_debounce.stop()

    def _seek_value_changed(self, value):
        if self._syncing_seek:
            return
        self._seek_preview = int(value)
        self._update_time(int(value))
        self._seek_debounce.start()

    def _flush_seek(self):
        if not self._seeking and self._seek_handler:
            self._seek_handler(self._seek_preview)

    def _seek_released(self):
        self._seeking = False
        self._seek_debounce.stop()
        if self._seek_handler:
            self._seek_handler(int(self._seek.value()))

    def _open_res_menu(self):
        if self._menu_builder is None:
            return
        menu = self._menu_builder()
        if menu is None:
            return
        menu.popup(self._res_btn.mapToGlobal(self._res_btn.rect().bottomLeft()))


class DiscoveryCard(QFrame):
    """Card cover+titolo. Click -> popup dettagli (gestito dalla tab)."""

    hover_changed = pyqtSignal(object, bool)

    def __init__(self, title="", cover_path=None, parent=None, theme=None):
        super().__init__(parent)
        self._title = title
        self._cover_path = cover_path
        self._hover_active = False
        self._hover_paused = False
        self._cover_target_size = QSize(200, 300)
        self._cover_load_scheduled = False
        self._zoom = 1.0
        self._zoom_anim = None
        self._hover_shadow = None

        if theme:
            self._theme_colors = {
                'bg': theme.get('surface_panel_purple', '#2b1a42'),
                'accent': theme.get('main_accent_color', '#733d91'),
                'neon': theme.get('bright_neon_accent', '#ff00a0'),
                'text': theme.get('text_primary_white', '#ffffff'),
                'muted': theme.get('text_muted', '#8a7fa4'),
            }
            self._glow_color = self._theme_colors['neon']

        self.setObjectName("DiscoveryCard")
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._build_ui()
        self._apply_base_style()
        if theme:
            page_bg = theme.get('main_background_color',
                            theme.get('background_dark_purple', '#120a1c'))
            self._cover_label.set_fill_color(page_bg)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(GLOW_INSET, GLOW_PAD, GLOW_INSET, 0)
        layout.setSpacing(0)

        self._cover_label = CoverLabel()
        self._cover_label.setObjectName("CoverLabel")
        layout.addWidget(self._cover_label, 1)

        self._title_label = QLabel(self._title)
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setObjectName("TitleLabel")
        self._title_label.setWordWrap(True)
        self._title_label.setMaximumHeight(CARD_TITLE_H)
        layout.addWidget(self._title_label, 1)

        self._schedule_cover_load()

    # ── cover (caricamento lazy con cache) ────────────────────

    def _schedule_cover_load(self):
        """Carica la cover in un tick successivo: costruire una riga da 180
        card non deve bloccare la UI per centinaia di ms."""
        if self._cover_load_scheduled:
            return
        self._cover_load_scheduled = True
        QTimer.singleShot(0, self._deferred_cover_load)

    def _deferred_cover_load(self):
        self._cover_load_scheduled = False
        if sip.isdeleted(self):
            return
        self._load_cover()

    def _load_cover(self):
        path = self._cover_path
        if path and path.startswith("file://"):
            # file:///C:/... -> C:/... ; file://C:\... (legacy) -> C:\...
            path = QUrl(path).toLocalFile() or path[7:]

        target_size = self._cover_target_size
        if not target_size.isValid() or target_size.width() <= 1:
            target_size = QSize(200, 300)

        if path and os.path.isfile(path):
            scaled = _scaled_cover(path, target_size)
            if scaled is not None and not scaled.isNull():
                self._cover_label.setPixmap(scaled)
                return

        self._cover_label.setPixmap(_placeholder_cover(target_size))

    def set_cover(self, cover_path):
        self._cover_path = cover_path
        self._schedule_cover_load()

    def set_card_size(self, w, h):
        """Dimensione BASE: la cover viene decodificata a questa misura."""
        self.setFixedSize(w, h)
        cw = max(0, w - 2 * GLOW_INSET)
        ch = max(0, h - GLOW_PAD - CARD_TITLE_H)
        self._cover_label.setFixedSize(cw, ch)
        self._cover_target_size = QSize(cw, ch)
        self._schedule_cover_load()

    def set_display_size(self, w, h):
        """Dimensione a schermo (carosello): ridimensiona senza ri-decodificare."""
        if (w, h) == (self.width(), self.height()):
            return
        self.setFixedSize(w, h)
        cw = max(0, w - 2 * GLOW_INSET)
        ch = max(0, h - GLOW_PAD - CARD_TITLE_H)
        self._cover_label.setFixedSize(cw, ch)

    def _cover_size(self):
        return QSize(
            max(0, self.width() - 2 * GLOW_INSET),
            max(0, int((self.width() - 2 * GLOW_INSET) * 1.5)),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_cover()

    def _reflow_cover(self):
        if self._cover_label is None:
            return
        cw = max(0, self.width() - 2 * GLOW_INSET)
        ch = max(0, self.height() - GLOW_PAD - CARD_TITLE_H)
        if (cw, ch) != (self._cover_label.width(), self._cover_label.height()):
            self._cover_label.setFixedSize(cw, ch)

    # ── theme ──────────────────────────────────────────────

    def set_theme(self, theme):
        bg = theme.get('surface_panel_purple', '#2b1a42')
        accent = theme.get('main_accent_color', '#733d91')
        neon = theme.get('bright_neon_accent', '#ff00a0')
        text = theme.get('text_primary_white', '#ffffff')
        muted = theme.get('text_muted', '#8a7fa4')

        self._theme_colors = {
            'bg': bg, 'accent': accent, 'neon': neon,
            'text': text, 'muted': muted,
        }
        page_bg = theme.get('main_background_color',
                            theme.get('background_dark_purple', '#120a1c'))
        self._cover_label.set_fill_color(page_bg)
        self._apply_base_style()

    def _title_qss(self, color):
        return (f"color: {color}; font-size: 14px; font-weight: bold; "
                "padding: 4px 6px; background: transparent;")

    def _apply_base_style(self):
        c = getattr(self, '_theme_colors', None)
        text = c['text'] if c else '#ffffff'
        neon = c['neon'] if c else '#ff00a0'

        self.setStyleSheet(f"""
            QFrame#DiscoveryCard {{
                background-color: transparent;
                border: none;
            }}
            QLabel#TitleLabel {{
                {self._title_qss(text)}
            }}
        """)
        self._glow_color = neon

    # ── hover glow ─────────────────────────────────────────

    def set_hover_paused(self, paused):
        self._hover_paused = bool(paused)
        if paused and self._hover_active:
            self._hover_active = False
            self.hover_changed.emit(self, False)

    def set_fast_scale(self, fast):
        self._cover_label.set_fast_scale(fast)

    def force_hover(self, active=True):
        """Attiva/disattiva l'hover senza aspettare enterEvent.

        Serve dopo drag e inerzia: la card scorre sotto un mouse fermo e
        Qt non manda un nuovo enterEvent, quindi l'evidenziazione mancava.
        """
        if active:
            if self._hover_paused:
                return
            if QApplication.mouseButtons() != Qt.MouseButton.NoButton:
                return
            if self._hover_active:
                return
            self._hover_active = True
            self.raise_()
            self._attach_hover_shadow()
            self._set_title_hover(True)
            self._animate_zoom(1.08)
            self.hover_changed.emit(self, True)
            return
        if not self._hover_active:
            return
        self._hover_active = False
        self._set_title_hover(False)
        self._animate_zoom(1.0)
        self.hover_changed.emit(self, False)

    def enterEvent(self, event):
        self.force_hover(True)

    def leaveEvent(self, event):
        self.force_hover(False)

    # ?? push forward ?????????????????????????????????????????

    def _get_zoom(self):
        return self._zoom

    def _set_zoom(self, value):
        self._zoom = float(value)
        self._cover_label.set_zoom(self._zoom)

    hover_zoom = pyqtProperty(float, fget=_get_zoom, fset=_set_zoom)

    def _set_title_hover(self, active):
        theme = getattr(self, "_theme_colors", None)
        if not theme:
            return
        color = self._glow_color if active else theme["text"]
        try:
            self._title_label.setStyleSheet(self._title_qss(color))
        except RuntimeError:
            pass

    def _animate_zoom(self, target):
        anim = self._zoom_anim
        if anim is not None and not sip.isdeleted(anim):
            try:
                anim.stop()
            except RuntimeError:
                pass
        anim = QPropertyAnimation(self, b"hover_zoom", self)
        anim.setDuration(150)
        anim.setStartValue(self._zoom)
        anim.setEndValue(float(target))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._zoom_anim = anim
        anim.start()

    def _attach_hover_shadow(self):
        try:
            if self._hover_shadow is None or sip.isdeleted(self._hover_shadow):
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(26)
                shadow.setOffset(0, 8)
                shadow.setColor(QColor(0, 0, 0, 190))
                self._hover_shadow = shadow
            self.setGraphicsEffect(self._hover_shadow)
        except RuntimeError:
            self._hover_shadow = None

    def flash_feedback(self):
        theme = getattr(self, '_theme_colors', None)
        if not theme:
            return
        neon = self._glow_color
        text = theme['text']
        self._title_label.setStyleSheet(self._title_qss(neon))
        QTimer.singleShot(120, lambda: self._title_label.setStyleSheet(
            self._title_qss(text)))
