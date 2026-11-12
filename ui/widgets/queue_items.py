from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtWidgets import (
    QWidget, QLabel, QListWidget, QFrame, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QSizePolicy, QPushButton,
)
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient

from core.download_queue_model import (
    KIND_EXTRACT,
    STATE_DOWNLOADING,
    STATE_EXTRACTING,
    state_color,
)


class QueueListWidget(QListWidget):
    order_changed = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class NeonGraphWidget(QWidget):
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.base_color = QColor(color_hex)
        self.data_points = [0] * 50
        self.max_val = 10.0
        self.current_speed = 0.0
        self.metric_label = "BANDWIDTH"

    def set_mode(self, hex_color, metric_name):
        self.base_color = QColor(hex_color)
        self.metric_label = metric_name
        self.update()

    def clear(self):
        self.data_points = [0] * 50
        self.max_val = 10.0
        self.current_speed = 0.0
        self.update()

    def add_value(self, value):
        self.current_speed = value
        if value > self.max_val:
            self.max_val = value * 1.1

        self.data_points.pop(0)
        self.data_points.append(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        path = QPainterPath()
        step = w / (len(self.data_points) - 1)

        path.moveTo(0, h - (self.data_points[0] / self.max_val) * (h * 0.7))
        for i in range(1, len(self.data_points)):
            path.lineTo(i * step, h - (self.data_points[i] / self.max_val) * (h * 0.7))

        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(self.base_color)
        c.setAlpha(40)
        grad.setColorAt(0, c)
        c.setAlpha(0)
        grad.setColorAt(1, c)
        painter.fillPath(fill_path, grad)

        glow_pen = QPen(self.base_color)
        glow_pen.setWidth(5)
        glow_color = QColor(self.base_color)
        glow_color.setAlpha(120)
        glow_pen.setColor(glow_color)
        painter.setPen(glow_pen)
        painter.drawPath(path)

        solid_pen = QPen(self.base_color)
        solid_pen.setWidth(2)
        solid_pen.setColor(self.base_color)
        painter.setPen(solid_pen)
        painter.drawPath(path)

        painter.setPen(self.base_color)
        font = painter.font()
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(10, 20, f"{self.metric_label}: {self.current_speed:.1f} MB/s")


class SectionHeaderWidget(QWidget):
    """Small uppercase label that separates the optional queue sections."""

    def __init__(self, label, theme, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 2)
        lbl = QLabel(str(label).upper())
        muted = (theme or {}).get("text_muted", "#8a7fa4")
        lbl.setStyleSheet(
            f"color: {muted}; font-size: 10px; font-weight: bold; "
            "letter-spacing: 2px; border: none; background: transparent;"
        )
        layout.addWidget(lbl)
        layout.addStretch(1)
        self.setFixedHeight(28)


class QueueItemWidget(QFrame):
    """One queue row. The state (and its color) comes from the queue model."""

    def __init__(self, row, theme, is_active=False, play_icon=None, on_play=None):
        super().__init__()
        self.theme = theme or {}
        self.row = row
        self.is_active = is_active
        self.on_play = on_play
        self._play_icon = play_icon

        self.state_color = state_color(self.theme, row.state)
        self.text_color = self.theme.get("text_primary_white", "#ffffff")
        self.muted = self.theme.get("text_muted", "#8a7fa4")
        self.panel = self.theme.get("surface_panel_purple", "#2b1a42")
        self.background = self.theme.get("background_dark_purple", "#1a0f2b")

        self.setFixedHeight(76 if row.pct is not None else 68)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(
            f"background-color: {self.state_color}; border: none; "
            "border-top-left-radius: 7px; border-bottom-left-radius: 7px;"
        )
        outer.addWidget(accent)

        body = QWidget()
        layout = QHBoxLayout(body)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(10)
        outer.addWidget(body, 1)

        if not is_active:
            handle = QLabel("\u28ff")
            handle.setStyleSheet(
                f"color: {self.muted}; font-size: 16px; margin-right: 4px; "
                "border: none; background: transparent;"
            )
            layout.addWidget(handle)

        info = QVBoxLayout()
        info.setSpacing(1)

        title = QLabel(row.title)
        title.setStyleSheet(
            f"color: {self.text_color}; font-weight: bold; font-size: 14px; "
            "border: none; background: transparent;"
        )
        info.addWidget(title)

        meta = QHBoxLayout()
        meta.setSpacing(8)
        badge = QLabel(row.badge)
        badge.setStyleSheet(
            f"color: {self.state_color}; font-size: 10px; font-weight: bold; "
            "letter-spacing: 1px; border: none; background: transparent;"
        )
        meta.addWidget(badge)
        if row.provider:
            provider = QLabel(f"via {row.provider}")
            provider.setStyleSheet(
                f"color: {self.muted}; font-size: 10px; border: none; "
                "background: transparent;"
            )
            meta.addWidget(provider)
        meta.addStretch(1)
        info.addLayout(meta)
        layout.addLayout(info, 1)

        if row.detail:
            detail = QLabel(row.detail)
            detail.setStyleSheet(
                f"color: {self.muted}; font-size: 11px; border: none; "
                "background: transparent;"
            )
            detail.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            layout.addWidget(detail)

        if row.pct is not None and 0 <= row.pct <= 100:
            bar_wrap = QWidget()
            bar_wrap.setFixedWidth(90)
            bar_layout = QVBoxLayout(bar_wrap)
            bar_layout.setContentsMargins(0, 0, 0, 0)
            bar_layout.setSpacing(2)
            pct_label = QLabel(f"{row.pct}%")
            pct_label.setStyleSheet(
                f"color: {self.state_color}; font-size: 10px; font-weight: bold; "
                "border: none; background: transparent;"
            )
            pct_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            bar_layout.addWidget(pct_label)
            bar_bg = QFrame()
            bar_bg.setFixedHeight(3)
            bar_bg.setStyleSheet(
                "background-color: rgba(255,255,255,0.12); border: none; "
                "border-radius: 1px;"
            )
            bar_fill = QFrame(bar_bg)
            bar_fill.setFixedHeight(3)
            bar_fill.setStyleSheet(
                f"background-color: {self.state_color}; border: none; "
                "border-radius: 1px;"
            )
            bar_fill.setFixedWidth(int(90 * row.pct / 100.0))
            bar_layout.addWidget(bar_bg)
            layout.addWidget(bar_wrap)

        if on_play is not None and row.state not in (STATE_DOWNLOADING, STATE_EXTRACTING):
            play = QPushButton()
            play.setFixedSize(30, 30)
            play.setCursor(Qt.CursorShape.PointingHandCursor)
            play.setToolTip(
                "Extract this archive" if row.kind == KIND_EXTRACT
                else "Start / resume this download"
            )
            play.setStyleSheet(
                f"QPushButton {{ background-color: transparent; "
                f"border: 1px solid {self.state_color}; border-radius: 15px; }}"
                "QPushButton:hover { background-color: rgba(255,255,255,0.12); }"
            )
            if play_icon is not None and not play_icon.isNull():
                play.setIcon(play_icon)
                play.setIconSize(QSize(20, 20))
            else:
                play.setText("\u25b6")
                play.setStyleSheet(
                    play.styleSheet()
                    + f"QPushButton {{ color: {self.state_color}; font-size: 13px; }}"
                )
            play.clicked.connect(lambda _checked=False: self._play())
            layout.addWidget(play)

        if is_active:
            border = self.state_color
            self.setStyleSheet(
                f"QFrame {{ background-color: {self.panel}; "
                f"border: 1.5px solid {border}; border-radius: 8px; }}"
            )
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(15)
            glow.setColor(QColor(border))
            glow.setOffset(0, 0)
            self.setGraphicsEffect(glow)
        else:
            self.setStyleSheet(
                f"QFrame {{ background-color: {self.background}; "
                f"border: 1px solid {self.state_color}; border-radius: 8px; }}"
            )

    def _play(self):
        if callable(self.on_play):
            self.on_play(self.row)

    def mouseDoubleClickEvent(self, event):
        if not self.is_active:
            self._play()
        super().mouseDoubleClickEvent(event)
