"""Compact metric card for the Stats hero grid."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout


class StatCard(QFrame):
    def __init__(self, title, value="—", subtitle="", theme=None,
                 accent=None, parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self._accent = accent
        self._title_label = QLabel(str(title).upper())
        self._title_label.setObjectName("StatTitle")
        self._title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._value_label = QLabel(str(value))
        self._value_label.setObjectName("StatValue")
        self._value_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._subtitle_label = QLabel(str(subtitle))
        self._subtitle_label.setObjectName("StatSubtitle")
        self._subtitle_label.setWordWrap(True)
        self._subtitle_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)
        layout.addWidget(self._title_label)
        layout.addWidget(self._value_label)
        layout.addWidget(self._subtitle_label)
        layout.addStretch(1)

        self.setMinimumHeight(74)
        self.set_theme(theme)

    def set_value(self, value, subtitle=None):
        self._value_label.setText(str(value))
        if subtitle is not None:
            self._subtitle_label.setText(str(subtitle))

    def set_theme(self, theme, accent=None):
        theme = dict(theme or {})
        if accent is not None:
            self._accent = accent
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        border = theme.get("main_accent_color") or "#733d91"
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        accent = self._accent or theme.get("bright_neon_accent") or "#ff00a0"
        self.setStyleSheet(f"""
            QFrame#StatCard {{
                background: {panel};
                border: 1px solid {border};
                border-top: 2px solid {accent};
                border-radius: 10px;
            }}
            QLabel#StatTitle {{
                color: {muted};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
                border: none;
            }}
            QLabel#StatValue {{
                color: {text};
                font-size: 20px;
                font-weight: bold;
                border: none;
            }}
            QLabel#StatSubtitle {{
                color: {muted};
                font-size: 11px;
                border: none;
            }}
        """)
