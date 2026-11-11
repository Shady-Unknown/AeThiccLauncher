"""Badges page for the Stats tab: filters, cards and progress."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from ui.widgets.achievement_badge import AchievementCard
from ui.widgets.achievement_popup import AchievementPopup
from ui.widgets.flow_layout import FlowLayout


class BadgesView(QWidget):
    def __init__(self, app_window, achievements_manager=None, parent=None):
        super().__init__(parent)
        self.setObjectName("AchievementsRoot")
        self.app_window = app_window
        self.manager = achievements_manager
        self._filter = "all"
        self._cards = []
        self._build_ui()
        if self.manager is not None:
            self.manager.state_changed.connect(self.refresh)
            self.manager.achievement_unlocked.connect(
                lambda _badge: self.refresh()
            )
        self.apply_theme()

    def _config_theme(self):
        config = getattr(self.app_window, "app_config", {}) or {}
        return config.get("theme", {}) or {}

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)

        title = QLabel("ACHIEVEMENTS")
        title.setObjectName("AchievementsTitle")
        subtitle = QLabel("Launcher badges, tiers and milestones.")
        subtitle.setObjectName("AchievementsSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.count_label = QLabel("")
        self.count_label.setObjectName("AchievementsCount")
        header.addWidget(self.count_label)
        header.addStretch(1)

        self.filter_group = QButtonGroup(self)
        self.filter_group.setExclusive(True)
        self._filter_buttons = {}
        for key, label in (("all", "All"), ("unlocked", "Unlocked"), ("locked", "Locked")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("StatsMode")
            self.filter_group.addButton(button)
            self._filter_buttons[key] = button
            button.clicked.connect(lambda _checked=False, k=key: self._set_filter(k))
            header.addWidget(button)
        self._filter_buttons["all"].setChecked(True)

        layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("BadgesScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.content = QWidget()
        self.content.setObjectName("BadgesContent")
        self.flow = FlowLayout(self.content, margin=4, hspacing=10, vspacing=10)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

    def _set_filter(self, key):
        self._filter = key
        self.refresh()

    def _clear_cards(self):
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._cards = []

    def refresh(self):
        self._clear_cards()
        if self.manager is None:
            self.count_label.setText("Achievements engine not available")
            return

        badges = self.manager.badges()
        unlocked = self.manager.unlocked_count()
        total = len(badges)
        self.count_label.setText(f"{unlocked} / {total} unlocked")

        badges.sort(key=lambda badge: (
            not bool(badge.get("unlocked")),
            -(int(badge.get("unlocked_at") or 0))
            if badge.get("unlocked") else 0,
            str(badge.get("title") or "").lower(),
        ))
        if self._filter == "unlocked":
            badges = [badge for badge in badges if badge.get("unlocked")]
        elif self._filter == "locked":
            badges = [badge for badge in badges if not badge.get("unlocked")]

        theme = self._config_theme()
        for badge in badges:
            card = AchievementCard(badge, theme)
            card.clicked.connect(self._show_achievement)
            self._cards.append(card)
            self.flow.addWidget(card)

        if not badges:
            empty = QLabel(
                "No achievements match this filter."
                if total else "No achievements defined."
            )
            empty.setObjectName("EmptyState")
            empty.setWordWrap(True)
            self.flow.addWidget(empty)

    def _show_achievement(self, badge):
        popup = AchievementPopup(
            self.window(), badge, self._config_theme()
        )
        popup.exec()

    def apply_theme(self):
        theme = self._config_theme()
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        border = theme.get("main_accent_color") or "#733d91"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        self.setStyleSheet(f"""
            QWidget#AchievementsRoot, QWidget#BadgesContent {{
                background: transparent;
            }}
            QLabel#AchievementsTitle {{
                color: {text};
                font-size: 24px;
                font-weight: bold;
                letter-spacing: 2px;
            }}
            QLabel#AchievementsSubtitle {{
                color: {muted};
                font-size: 11px;
            }}
            QLabel#AchievementsCount {{
                color: {muted};
                font-size: 11px;
            }}
            QPushButton#StatsMode {{
                background: transparent;
                color: {muted};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 11px;
                font-weight: bold;
            }}
            QPushButton#StatsMode:hover {{
                color: {text};
                border-color: {accent};
            }}
            QPushButton#StatsMode:checked {{
                background: {accent};
                color: #ffffff;
                border-color: {accent};
            }}
            QPushButton#StatsAction {{
                background: transparent;
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: bold;
            }}
            QPushButton#StatsAction:hover {{
                border-color: {accent};
                color: {accent};
            }}
            QLabel#EmptyState {{
                color: {muted};
                font-size: 14px;
                padding: 18px;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
        """)
        self.refresh()
