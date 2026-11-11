"""Expanded achievement card, opened from the Achievements grid."""
from __future__ import annotations

import time

from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QProgressBar, QVBoxLayout,
)

from ui.widgets.achievement_badge import badge_pixmap


class AchievementPopup(QDialog):
    def __init__(self, parent, badge, theme=None):
        super().__init__(parent)
        self.badge = dict(badge or {})
        self._theme = dict(theme or {})
        self.setObjectName("AchievementPopup")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setModal(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._overlay = QFrame()
        self._overlay.setObjectName("AchievementOverlay")
        self._overlay.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        self._overlay.installEventFilter(self)
        outer.addWidget(self._overlay)

        overlay_layout = QVBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(24, 24, 24, 24)
        card = QFrame()
        card.setObjectName("AchievementPopupCard")
        card.setFixedWidth(640)
        # Clicks inside the card must not fall through to the close handler.
        card.setAttribute(
            Qt.WidgetAttribute.WA_NoMousePropagation, True
        )
        overlay_layout.addWidget(
            card, 0, Qt.AlignmentFlag.AlignCenter
        )

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(22)
        icon = QLabel()
        icon.setFixedSize(168, 168)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(badge_pixmap(self.badge, 168))
        accent = self._theme.get("bright_neon_accent") or "#ff00a0"
        glow = QGraphicsDropShadowEffect(icon)
        glow.setBlurRadius(42)
        glow.setOffset(0, 0)
        glow.setColor(QColor(accent))
        icon.setGraphicsEffect(glow)
        top.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(5)
        kicker = QHBoxLayout()
        kicker.setSpacing(10)
        category = QLabel(
            ("HIDDEN" if self.badge.get("hidden_locked")
             else str(self.badge.get("category") or "").upper())
        )
        category.setObjectName("PopupCategory")
        status = QLabel("")
        status.setObjectName(
            "PopupStatusUnlocked"
            if self.badge.get("unlocked") else "PopupStatusLocked"
        )
        kicker.addWidget(category)
        kicker.addWidget(status)
        kicker.addStretch(1)
        info.addLayout(kicker)

        title = QLabel(
            "???" if self.badge.get("hidden_locked")
            else str(self.badge.get("title") or "Unknown")
        )
        title.setObjectName("PopupTitle")
        title.setWordWrap(True)
        info.addWidget(title)

        desc = QLabel(
            "Hidden achievement"
            if self.badge.get("hidden_locked")
            else str(self.badge.get("desc") or "")
        )
        desc.setObjectName("PopupDesc")
        desc.setWordWrap(True)
        info.addWidget(desc)

        detail = QLabel("")
        detail.setObjectName("PopupDetail")
        detail.setWordWrap(True)
        info.addWidget(detail)

        progress = QProgressBar()
        progress.setObjectName("PopupProgress")
        progress.setRange(0, 100)
        progress.setTextVisible(True)
        progress.setFixedHeight(12)
        progress.setVisible(False)
        info.addWidget(progress)
        info.addStretch(1)
        top.addLayout(info, 1)
        layout.addLayout(top)

        unlocked = bool(self.badge.get("unlocked"))
        if unlocked:
            status.setText("UNLOCKED")
            if self.badge.get("unlocked_at"):
                detail.setText(
                    "Unlocked "
                    + time.strftime(
                        "%Y-%m-%d %H:%M",
                        time.localtime(int(self.badge["unlocked_at"])),
                    )
                )
            else:
                detail.setText(str(self.badge.get("pack_title") or ""))
        else:
            status.setText("LOCKED")
            if self.badge.get("hidden_locked"):
                detail.setText("Unlock it to reveal the name.")
            else:
                detail.setText(str(self.badge.get("pack_title") or ""))
                progress_data = self.badge.get("progress")
                if isinstance(progress_data, dict):
                    target = float(progress_data.get("target") or 0)
                    current = float(progress_data.get("current") or 0)
                    if target > 0:
                        progress.setValue(
                            max(0, min(100, int(round(current / target * 100))))
                        )
                        progress.setFormat(f"{current:.0f} / {target:.0f}")
                        progress.setVisible(True)

        self._apply_theme()
        self.adjustSize()
        self._cover_parent()

    def _apply_theme(self):
        theme = self._theme
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        background = theme.get("main_background_color") or "#120a1c"
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        green = theme.get("status_green") or "#00ff88"
        self.setStyleSheet(f"""
            QDialog#AchievementPopup {{
                background: transparent;
            }}
            QFrame#AchievementOverlay {{
                background: rgba(5, 3, 9, 200);
                border: none;
            }}
            QFrame#AchievementPopupCard {{
                background: {panel};
                border: 1px solid {accent};
                border-radius: 16px;
            }}
            QLabel#PopupCategory {{
                color: {accent};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#PopupStatusUnlocked {{
                color: {green};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#PopupStatusLocked {{
                color: {muted};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#PopupTitle {{
                color: {text};
                font-size: 24px;
                font-weight: bold;
            }}
            QLabel#PopupDesc {{
                color: {text};
                font-size: 13px;
            }}
            QLabel#PopupDetail {{
                color: {muted};
                font-size: 11px;
            }}
            QProgressBar#PopupProgress {{
                background: {background};
                border: none;
                border-radius: 6px;
                color: {text};
                font-size: 9px;
                text-align: center;
            }}
            QProgressBar#PopupProgress::chunk {{
                background: {accent};
                border-radius: 6px;
            }}
        """)

    def _cover_parent(self):
        parent = self.parentWidget()
        if parent is None:
            return
        window = parent.window()
        if window is None:
            return
        origin = window.mapToGlobal(QPoint(0, 0))
        self.setGeometry(
            origin.x(), origin.y(), window.width(), window.height()
        )

    def showEvent(self, event):
        super().showEvent(event)
        self._cover_parent()
        self.setFocus()

    def eventFilter(self, obj, event):
        if (
            obj is getattr(self, "_overlay", None)
            and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.reject()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
