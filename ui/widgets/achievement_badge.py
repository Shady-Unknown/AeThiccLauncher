"""Badge icon helper and card used by the Badges view."""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QProgressBar,
    QVBoxLayout, QWidget,
)


def _initials(text):
    parts = [part for part in str(text or "").split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "?"


def badge_pixmap(badge, size=64):
    """Load ``data/achievements/<id>.png`` or draw a monogram fallback."""
    hidden_locked = bool(badge.get("hidden_locked"))
    if not hidden_locked:
        path = str(badge.get("icon_path") or "")
        if path:
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                return pixmap.scaled(
                    size, size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(pixmap.rect()).adjusted(1, 1, -1, -1)
    path = QPainterPath()
    path.addRoundedRect(rect, 12, 12)
    painter.setClipPath(path)

    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#4b2a66"))
    gradient.setColorAt(1.0, QColor("#b0006e"))
    painter.fillRect(rect, QBrush(gradient))
    painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
    painter.drawRoundedRect(rect, 12, 12)
    painter.setPen(QPen(QColor("#ffffff")))
    font = QFont(painter.font().family(), int(size * 0.30))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        rect,
        int(Qt.AlignmentFlag.AlignCenter),
        "?" if hidden_locked else _initials(badge.get("title")),
    )
    painter.end()
    return pixmap


class AchievementCard(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, badge, theme=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AchievementCard")
        self.badge = dict(badge or {})
        self._theme = dict(theme or {})
        self.setFixedSize(330, 150)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            f"{self.badge.get('title') or ''}\n{self.badge.get('desc') or ''}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(64, 64)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setPixmap(badge_pixmap(self.badge, 64))
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.category_label = QLabel(
            str(self.badge.get("category") or "").upper()
        )
        self.category_label.setObjectName("AchievementCategory")
        self.status_label = QLabel("")
        self.status_label.setObjectName("AchievementStatus")
        top.addWidget(self.category_label)
        top.addWidget(self.status_label)
        top.addStretch(1)
        text_layout.addLayout(top)

        self.title_label = QLabel("")
        self.title_label.setObjectName("AchievementTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setMaximumHeight(34)
        self.desc_label = QLabel("")
        self.desc_label.setObjectName("AchievementDesc")
        self.desc_label.setWordWrap(True)
        self.desc_label.setMaximumHeight(32)
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.desc_label)

        self.detail_label = QLabel("")
        self.detail_label.setObjectName("AchievementDetail")
        self.detail_label.setWordWrap(True)
        self.detail_label.setMaximumHeight(16)
        text_layout.addWidget(self.detail_label)

        self.progress = QProgressBar()
        self.progress.setObjectName("AchievementProgress")
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(10)
        self.progress.setVisible(False)
        text_layout.addWidget(self.progress)
        text_layout.addStretch(1)

        layout.addLayout(text_layout, 1)
        for child in self.findChildren(QWidget):
            child.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )
        self._apply_badge()
        self.set_theme(theme)

    def _apply_badge(self):
        hidden_locked = bool(self.badge.get("hidden_locked"))
        unlocked = bool(self.badge.get("unlocked"))

        if hidden_locked:
            self.title_label.setText("???")
            self.desc_label.setText("Hidden achievement")
            self.detail_label.setText("Unlock it to reveal the name.")
            self.status_label.setText("LOCKED")
            self.category_label.setText("HIDDEN")
        else:
            self.title_label.setText(str(self.badge.get("title") or "Unknown"))
            self.desc_label.setText(str(self.badge.get("desc") or ""))
            self.status_label.setText("UNLOCKED" if unlocked else "LOCKED")
            self.category_label.setText(
                str(self.badge.get("category") or "").upper()
            )
            if unlocked and self.badge.get("unlocked_at"):
                self.detail_label.setText(
                    "Unlocked " + time.strftime(
                        "%Y-%m-%d",
                        time.localtime(int(self.badge["unlocked_at"])),
                    )
                )
            else:
                self.detail_label.setText(
                    str(self.badge.get("pack_title") or "")
                )

        if not unlocked and not hidden_locked:
            effect = QGraphicsOpacityEffect(self.icon_label)
            effect.setOpacity(0.38)
            self.icon_label.setGraphicsEffect(effect)
        else:
            self.icon_label.setGraphicsEffect(None)

        progress = self.badge.get("progress")
        if not unlocked and not hidden_locked and isinstance(progress, dict):
            target = float(progress.get("target") or 0)
            current = float(progress.get("current") or 0)
            if target > 0:
                percent = max(0, min(100, int(round(current / target * 100))))
                self.progress.setValue(percent)
                self.progress.setFormat(f"{current:.0f} / {target:.0f}")
                self.progress.setVisible(True)
                return
        self.progress.setVisible(False)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        theme = self._theme
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        border = theme.get("main_accent_color") or "#733d91"
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        green = theme.get("status_green") or "#00ff88"
        unlocked = bool(self.badge.get("unlocked"))
        card_border = accent if unlocked else border
        self.setStyleSheet(f"""
            QFrame#AchievementCard {{
                background: {panel};
                border: 1px solid {card_border};
                border-radius: 12px;
            }}
            QLabel#AchievementCategory {{
                color: {accent if not self.badge.get('hidden_locked') else muted};
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#AchievementStatus {{
                color: {green if unlocked else muted};
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#AchievementTitle {{
                color: {text};
                font-size: 14px;
                font-weight: bold;
                border: none;
            }}
            QLabel#AchievementDesc {{
                color: {muted};
                font-size: 11px;
                border: none;
            }}
            QLabel#AchievementDetail {{
                color: {muted};
                font-size: 10px;
                border: none;
            }}
            QProgressBar#AchievementProgress {{
                background: {theme.get('main_background_color') or '#120a1c'};
                border: none;
                border-radius: 5px;
                color: {text};
                font-size: 8px;
                text-align: center;
            }}
            QProgressBar#AchievementProgress::chunk {{
                background: {accent};
                border-radius: 5px;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(dict(self.badge))
        super().mousePressEvent(event)
