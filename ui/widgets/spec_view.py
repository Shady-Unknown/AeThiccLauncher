"""Vista strutturata per "System Requirements" e "Game Info".

Il controller chiama ``setHtml(html)`` esattamente come faceva con il
``QTextEdit``: se l'HTML è riconoscibile viene mostrata una griglia di item
icona+label+valore, altrimenti si ricade sull'HTML originale.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QScrollArea, QStackedLayout, QTextBrowser,
                             QVBoxLayout, QWidget)

from core.spec_parser import looks_structured, parse_spec_html
from ui.widgets.glass import (css_rgba, disable_scrollbar_menu, SCROLLBAR_QSS, style_context_menu, theme_accent)
from ui.widgets.icons import fallback_glyph, spec_icon_pixmap

TWO_COLUMN_MIN_WIDTH = 300
ICON_SIZE = 26


class SpecItem(QFrame):
    """Riga con icona, label piccola e valore."""

    def __init__(self, theme: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SpecItem")
        self._theme = theme or {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 7, 11, 7)
        layout.setSpacing(9)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(ICON_SIZE + 2, ICON_SIZE + 2)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        self.icon_label.hide()
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        self.label_widget = QLabel(self)
        self.label_widget.setStyleSheet(
            f"background: transparent; border: none; color: {css_rgba('#ffffff', 0.55)};"
            " font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )
        self.label_widget.hide()
        text_layout.addWidget(self.label_widget)

        self.value_widget = QLabel(self)
        self.value_widget.setWordWrap(True)
        self.value_widget.setStyleSheet(
            "background: transparent; border: none; color: #ffffff; font-size: 12px;"
        )
        text_layout.addWidget(self.value_widget)

        layout.addLayout(text_layout, 1)
        self.apply_theme(self._theme)

    def apply_theme(self, theme: dict):
        self._theme = theme or {}
        self.setStyleSheet(
            "QFrame#SpecItem {"
            f"  background-color: {css_rgba('#ffffff', 0.045)};"
            f"  border: 1px solid {css_rgba('#ffffff', 0.07)};"
            "  border-radius: 8px;"
            "}"
        )

    def set_entry(self, entry: dict):
        icon_key = entry.get("icon") or ""
        label = entry.get("label") or ""
        value = entry.get("value") or ""
        self.value_widget.setText(value)
        self.label_widget.setText(label.upper() if label else "")
        self.label_widget.setVisible(bool(label))
        pixmap = spec_icon_pixmap(icon_key, ICON_SIZE, QColor("#ffffff")) if icon_key else None
        if pixmap is not None and not pixmap.isNull():
            self.icon_label.setPixmap(pixmap)
            self.icon_label.setStyleSheet("background: transparent; border: none;")
            self.icon_label.show()
        elif icon_key:
            self.icon_label.setText(fallback_glyph(icon_key))
            self.icon_label.setStyleSheet(
                "background: transparent; border: none; color:"
                f" {theme_accent(self._theme)}; font-size: 16px;"
            )
            self.icon_label.show()
        else:
            self.icon_label.clear()
            self.icon_label.hide()


class SpecGrid(QWidget):
    """Griglia adattiva di :class:`SpecItem` (1 o 2 colonne)."""

    def __init__(self, theme: dict, parent: QWidget | None = None, max_columns: int = 2):
        super().__init__(parent)
        self._theme = theme or {}
        self._items: list[dict] = []
        self._max_columns = max(1, int(max_columns))
        self._columns = min(2, self._max_columns)
        self._cards: list[SpecItem] = []
        self._row_stretch = 0
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(8)
        self._layout.setVerticalSpacing(8)
        self._layout.setColumnStretch(0, 1)
        self._layout.setColumnStretch(1, 1)
        self.setStyleSheet("background: transparent;")

    def set_theme(self, theme: dict):
        self._theme = theme or {}
        for card in self._cards:
            card.apply_theme(self._theme)

    def set_items(self, items: list[dict]):
        self._items = list(items or [])
        self._rebuild()

    def clear(self):
        self._items = []
        self._rebuild()

    def _rebuild(self):
        while self._layout.count():
            child = self._layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards = []
        for index, entry in enumerate(self._items):
            card = SpecItem(self._theme, self)
            card.set_entry(entry)
            row, column = divmod(index, self._columns)
            self._layout.addWidget(card, row, column)
            self._cards.append(card)
        rows = (len(self._items) + self._columns - 1) // self._columns if self._items else 0
        if self._row_stretch:
            self._layout.setRowStretch(self._row_stretch, 0)
        self._row_stretch = rows
        if rows:
            self._layout.setRowStretch(rows, 1)

    def _wanted_columns(self) -> int:
        if self._max_columns < 2:
            return 1
        return 2 if self.width() >= TWO_COLUMN_MIN_WIDTH else 1

    def resizeEvent(self, event):
        super().resizeEvent(event)
        wanted = self._wanted_columns()
        if wanted != self._columns:
            self._columns = wanted
            self._rebuild()


class SpecView(QWidget):
    """Drop-in per ``req_display`` / ``info_display`` (``setHtml``/``clear``)."""

    def __init__(self, theme: dict, columns: int = 2, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme or {}
        self.setObjectName("SpecView")
        self.setStyleSheet("QWidget#SpecView { background: transparent; }")
        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            + SCROLLBAR_QSS
        )
        self.scroll.viewport().setObjectName("SpecViewport")
        self.scroll.viewport().setStyleSheet(
            "QWidget#SpecViewport { background: transparent; }"
        )
        self.grid_host = QWidget(self.scroll)
        self.grid_host.setObjectName("SpecHost")
        self.grid_host.setStyleSheet("QWidget#SpecHost { background: transparent; }")
        host_layout = QVBoxLayout(self.grid_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        self.grid = SpecGrid(self._theme, self.grid_host, max_columns=columns)
        host_layout.addWidget(self.grid)
        host_layout.addStretch(1)
        self.scroll.setWidget(self.grid_host)
        disable_scrollbar_menu(self.scroll)
        self._stack.addWidget(self.scroll)

        self.browser = QTextBrowser(self)
        self.browser.setOpenExternalLinks(False)
        self.browser.setFrameShape(QFrame.Shape.NoFrame)
        self.browser.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.browser.customContextMenuRequested.connect(self._show_browser_menu)
        disable_scrollbar_menu(self.browser)
        self.browser.setStyleSheet(
            "QTextBrowser { background: transparent; border: none;"
            " font-size: 13px; color: #ffffff; }"
        )
        self.browser.document().setDocumentMargin(2)
        self._stack.addWidget(self.browser)

        self._stack.setCurrentWidget(self.scroll)

    def _show_browser_menu(self, pos):
        """Fallback HTML: stesso menu copia/seleziona in stile vetro."""
        menu = self.browser.createStandardContextMenu()
        style_context_menu(menu)
        menu.exec(self.browser.mapToGlobal(pos))
        menu.deleteLater()

    # ── API compatibile con QTextEdit ────────────────────────────────

    def setHtml(self, html_content: str):
        items = parse_spec_html(html_content)
        if looks_structured(items):
            self.grid.set_items(items)
            self._stack.setCurrentWidget(self.scroll)
            return
        self.browser.setHtml(html_content or "")
        self._stack.setCurrentWidget(self.browser)

    def clear(self):
        self.grid.clear()
        self.browser.clear()
        self._stack.setCurrentWidget(self.scroll)

    def set_theme(self, theme: dict):
        self._theme = theme or {}
        self.grid.set_theme(self._theme)

    def is_structured(self) -> bool:
        return self._stack.currentWidget() is self.scroll

    def setStyleSheet(self, sheet: str):  # noqa: N802 - override intenzionale
        super().setStyleSheet(sheet)
