"""Small unlock toasts, queued bottom-right."""
from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout

from ui.widgets.achievement_badge import badge_pixmap


class AchievementToast(QFrame):
    clicked = pyqtSignal()
    closed = pyqtSignal(object)

    def __init__(self, badge, theme=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AchievementToast")
        self.badge = dict(badge or {})
        self.setFixedWidth(360)
        self.setMinimumHeight(78)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 12, 8)
        layout.setSpacing(10)

        icon = QLabel()
        icon.setFixedSize(48, 48)
        icon.setPixmap(badge_pixmap(self.badge, 48))
        layout.addWidget(icon)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        title = QLabel(
            "Achievement unlocked" if self.badge.get("id")
            else "Achievements unlocked"
        )
        title.setObjectName("ToastKicker")
        name = QLabel(
            str(self.badge.get("title") or self.badge.get("summary") or "")
        )
        name.setObjectName("ToastTitle")
        name.setWordWrap(True)
        desc = QLabel(str(self.badge.get("desc") or ""))
        desc.setObjectName("ToastDesc")
        desc.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(name)
        text.addWidget(desc)
        layout.addLayout(text, 1)

        theme = dict(theme or {})
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        text_color = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        self.setStyleSheet(f"""
            QFrame#AchievementToast {{
                background: {panel};
                border: 1px solid {accent};
                border-radius: 10px;
            }}
            QLabel#ToastKicker {{
                color: {accent};
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#ToastTitle {{
                color: {text_color};
                font-size: 13px;
                font-weight: bold;
                border: none;
            }}
            QLabel#ToastDesc {{
                color: {muted};
                font-size: 10px;
                border: none;
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def closeEvent(self, event):
        self.closed.emit(self)
        super().closeEvent(event)


class AchievementToastManager(QObject):
    open_badges = pyqtSignal()

    def __init__(self, parent, theme_provider=None, max_visible=3):
        super().__init__(parent)
        self._parent = parent
        self._theme_provider = theme_provider or (lambda: {})
        self._max_visible = max(1, int(max_visible))
        self._visible = []
        self._queue = []

    def show_badge(self, badge):
        self._enqueue(dict(badge or {}))

    def show_summary(self, badges):
        badges = [badge for badge in (badges or []) if isinstance(badge, dict)]
        if not badges:
            return
        if len(badges) == 1:
            self.show_badge(badges[0])
            return
        titles = [str(badge.get("title") or "") for badge in badges[:3]]
        summary = {
            "title": f"{len(badges)} achievements unlocked",
            "desc": "From your history: " + " · ".join(title for title in titles if title),
            "icon_path": badges[0].get("icon_path") or "",
            "id": "",
        }
        if len(badges) > 3:
            summary["desc"] += f" · and {len(badges) - 3} more"
        self._enqueue(summary)

    def _enqueue(self, badge):
        self._queue.append(badge)
        self._show_next()

    def _show_next(self):
        while self._queue and len(self._visible) < self._max_visible:
            badge = self._queue.pop(0)
            toast = AchievementToast(
                badge, self._theme_provider(), self._parent
            )
            toast.clicked.connect(self.open_badges.emit)
            toast.closed.connect(self._on_closed)
            self._visible.append(toast)
            self._position()
            toast.show()
            toast.raise_()
            QTimer.singleShot(4800, toast.close)

    def _on_closed(self, toast):
        if toast in self._visible:
            self._visible.remove(toast)
        toast.deleteLater()
        self._position()
        self._show_next()

    def _position(self):
        parent = self._parent
        if parent is None:
            return
        margin = 20
        y = parent.height() - margin
        for toast in reversed(self._visible):
            toast.adjustSize()
            x = parent.width() - toast.width() - margin
            y -= toast.height()
            toast.move(max(0, x), max(0, y))
            y -= 8
