"""Glass UI primitives for the Explorer tab.

Un solo posto per la palette "vetro" derivata dal tema attivo, così la skin
resta coerente fra tab, sidebar e pannelli e i temi utente (che non hanno
chiavi nuove) continuano a funzionare.
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QWidget

from ui.styles.gold_tier_style import GOLD_PRIMARY
from ui.styles.sale_style import SALE_GREEN

STATE_DEFAULT = "default"
STATE_SALE = "sale"
STATE_GOLD = "gold"
STATE_GOLD_SALE = "gold_sale"

#: Scrollbar discreta per i contenitori vetro (QSS applicabile a ogni area).
SCROLLBAR_QSS = """
QScrollBar:vertical { background: transparent; width: 8px; margin: 0px; }
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 46);
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: rgba(255, 255, 255, 77); }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 8px; margin: 0px; }
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 46);
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
"""


def with_alpha(color, alpha: float) -> QColor:
    """QColor da stringa/QColor con alpha 0..1."""
    qcolor = QColor(color)
    qcolor.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return qcolor


def css_rgba(color, alpha: float) -> str:
    qcolor = with_alpha(color, alpha)
    # Qt QSS accetta alpha 0-255 (o percentuale): i float tipo 0.160 vengono
    # scartati dal parser e la regola sparisce silenziosamente.
    alpha_int = int(round(max(0.0, min(1.0, float(alpha))) * 255))
    return f"rgba({qcolor.red()}, {qcolor.green()}, {qcolor.blue()}, {alpha_int})"


def theme_accent(theme: dict) -> str:
    return (theme or {}).get("main_accent_color", "#733d91")


def accent_for(theme: dict, state: str) -> tuple[QColor, QColor | None]:
    """Colore bordo + colore glow per lo stato del gioco."""
    if state == STATE_GOLD_SALE:
        return QColor(GOLD_PRIMARY), QColor(SALE_GREEN)
    if state == STATE_GOLD:
        return QColor(GOLD_PRIMARY), QColor(GOLD_PRIMARY)
    if state == STATE_SALE:
        return QColor(SALE_GREEN), QColor(SALE_GREEN)
    return QColor(theme_accent(theme)), None


def glass_qss(theme: dict, accent=None, alpha: float = 0.42, radius: int = 14,
              object_name: str = "GlassPanel", border_alpha: float = 0.10) -> str:
    """QSS di base per un pannello vetro."""
    theme = theme or {}
    surface = theme.get("surface_panel_purple", "#2b1a42")
    border = css_rgba(accent, 0.85) if accent is not None else css_rgba("#ffffff", border_alpha)
    return (
        f"QFrame#{object_name} {{"
        f"  background-color: {css_rgba(surface, alpha)};"
        f"  border: 1px solid {border};"
        f"  border-radius: {radius}px;"
        "}"
    )


def disable_scrollbar_menu(scroll_area):
    """Toglie il menu "Scroll here / Top / Bottom..." dalle scrollbar."""
    try:
        for bar in (scroll_area.verticalScrollBar(), scroll_area.horizontalScrollBar()):
            bar.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    except Exception:
        pass


def disable_widget_scrollbars_menu(widget):
    """Stessa cosa ma partendo da un widget con scrollbar interne (QTextEdit)."""
    try:
        disable_scrollbar_menu(widget)
    except Exception:
        pass


def menu_qss(theme: dict | None = None) -> str:
    """QSS del context menu: vetro grigio neutro (niente accento di tema)."""
    return (
        "QMenu {"
        "  background-color: rgba(35, 37, 44, 235);"
        "  border: 1px solid rgba(255, 255, 255, 40);"
        "  border-radius: 12px;"
        "  padding: 6px;"
        "  color: #e7e7ea;"
        "}"
        "QMenu::item {"
        "  padding: 7px 28px 7px 14px;"
        "  border-radius: 8px;"
        "  color: #e7e7ea;"
        "  font-size: 12px;"
        "}"
        "QMenu::item:selected { background-color: rgba(255, 255, 255, 28); color: #ffffff; }"
        "QMenu::item:disabled { color: rgba(255, 255, 255, 120); font-size: 10px; padding: 5px 12px; }"
        "QMenu::separator { height: 1px; background: rgba(255, 255, 255, 26); margin: 5px 8px; }"
    )


def style_context_menu(menu, theme: dict | None = None):
    """Applica al QMenu il look vetro grigio (angoli arrotondati, niente frame)."""
    menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    menu.setWindowFlags(
        menu.windowFlags()
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.NoDropShadowWindowHint
    )
    menu.setStyleSheet(menu_qss(theme))


class GlassPanel(QFrame):
    """Pannello traslucido con bordo, sheen superiore e glow opzionale."""

    def __init__(self, theme: dict, parent: QWidget | None = None,
                 object_name: str = "GlassPanel", radius: int = 14,
                 alpha: float = 0.42, shadow: bool = True):
        super().__init__(parent)
        self._theme = theme or {}
        self._radius = int(radius)
        self._alpha = float(alpha)
        self._accent: QColor | None = None
        self._glow: QColor | None = None
        self._sheen = True
        self._shadow_enabled = bool(shadow)
        self._shadow: QGraphicsDropShadowEffect | None = None
        self.setObjectName(object_name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if shadow:
            self._install_shadow()
        self.refresh()

    # ── API ──────────────────────────────────────────────────────────

    def set_theme(self, theme: dict):
        self._theme = theme or {}
        self.refresh()

    def set_accent(self, accent: QColor | str | None, glow: QColor | str | None = None):
        self._accent = QColor(accent) if accent is not None else None
        self._glow = QColor(glow) if glow is not None else None
        self.refresh()

    def set_alpha(self, alpha: float):
        self._alpha = float(alpha)
        self.refresh()

    def set_sheen(self, enabled: bool):
        self._sheen = bool(enabled)
        self.update()

    def refresh(self):
        self.setStyleSheet(glass_qss(
            self._theme,
            accent=self._accent,
            alpha=self._alpha,
            radius=self._radius,
            object_name=self.objectName() or "GlassPanel",
            border_alpha=0.14,
        ))
        if self._shadow is not None:
            if self._glow is not None:
                color = QColor(self._glow)
                color.setAlpha(90)
                self._shadow.setColor(color)
                self._shadow.setBlurRadius(16)
            else:
                self._shadow.setColor(QColor(0, 0, 0, 150))
                self._shadow.setBlurRadius(22)

    # ── Effetti ──────────────────────────────────────────────────────

    def _install_shadow(self):
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setOffset(0, 4)
        shadow.setBlurRadius(22)
        shadow.setColor(QColor(0, 0, 0, 150))
        self._shadow = shadow
        self.setGraphicsEffect(shadow)
        self._shadow_enabled = True

    def shadow_effect(self) -> QGraphicsDropShadowEffect | None:
        return self._shadow

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._sheen:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), self._radius, self._radius)
        painter.setClipPath(path)
        sheen_height = min(30, max(12, self.height() // 8))
        gradient = QLinearGradient(0, 0, 0, sheen_height)
        highlight = with_alpha("#ffffff", 0.07)
        gradient.setColorAt(0.0, highlight)
        gradient.setColorAt(1.0, with_alpha("#ffffff", 0.0))
        painter.fillRect(QRectF(0, 0, self.width(), sheen_height), gradient)
        painter.end()
