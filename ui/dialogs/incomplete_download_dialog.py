"""Shown when a download finished without a complete archive."""
from __future__ import annotations

import html

from PyQt6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)


class IncompleteDownloadDialog(QDialog):
    def __init__(self, entries, theme_config=None, on_open=None, parent=None):
        super().__init__(parent)
        self._on_open = on_open
        self.setWindowTitle("Incomplete Downloads")
        self.setMinimumWidth(600)

        theme = dict(theme_config or {})
        background = theme.get("background_dark_purple") or "#1a0f2b"
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        border = theme.get("main_accent_color") or "#733d91"
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        pink = theme.get("text_secondary_pink") or "#f3c6ff"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        warning = theme.get("status_yellow") or "#ffcc00"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {background};
                color: {text};
            }}
            QLabel {{
                color: {text};
                font-size: 12px;
            }}
            QFrame#IncompleteRow {{
                background-color: {panel};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QPushButton {{
                background-color: {accent};
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 7px 14px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {pink};
                color: #120a1c;
            }}
            QPushButton#CloseButton {{
                background-color: transparent;
                color: {text};
                border: 1px solid {border};
            }}
            QPushButton#CloseButton:hover {{
                border-color: {accent};
                color: {accent};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        heading = QLabel("⚠️ Incomplete downloads")
        heading.setStyleSheet(
            f"color: {warning}; font-size: 18px; font-weight: bold;"
        )
        layout.addWidget(heading)

        intro = QLabel(
            "These archives were not fully downloaded, so they cannot be "
            "extracted and were skipped. The partial files were kept on disk: "
            "starting the same download again will resume them."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {muted};")
        layout.addWidget(intro)

        for entry in entries or []:
            layout.addWidget(self._build_row(entry, theme))

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.setObjectName("CloseButton")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

    def _build_row(self, entry, theme):
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        accent = theme.get("bright_neon_accent") or "#ff00a0"

        row = QFrame()
        row.setObjectName("IncompleteRow")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 9, 12, 9)
        row_layout.setSpacing(12)

        info = QVBoxLayout()
        info.setSpacing(2)
        title = html.escape(str(entry.get("title") or "Unknown game"))
        title_label = QLabel(f"<b>{title}</b>")
        title_label.setStyleSheet(f"color: {text}; font-size: 13px;")
        info.addWidget(title_label)

        provider = (
            entry.get("provider")
            or entry.get("provider_domain")
            or "not recorded"
        )
        provider_label = QLabel(f"Provider used: {html.escape(str(provider))}")
        provider_label.setStyleSheet(f"color: {muted}; font-size: 11px;")
        info.addWidget(provider_label)

        page_url = str(entry.get("page_url") or "").strip()
        target = page_url if page_url and page_url != "N/A" else "Explorer search"
        target_label = QLabel(
            f"Open: {html.escape(target)}"
        )
        target_label.setStyleSheet(f"color: {accent}; font-size: 11px;")
        info.addWidget(target_label)
        row_layout.addLayout(info, 1)

        open_btn = QPushButton("Open in Explorer")
        open_btn.clicked.connect(
            lambda _checked=False, item=entry: self._open(item)
        )
        row_layout.addWidget(open_btn)
        return row

    def _open(self, entry):
        self.accept()
        if self._on_open is not None:
            self._on_open(entry)
