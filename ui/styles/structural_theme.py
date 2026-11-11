"""Routing del tema "di stato" (default / saldo / gold) sulla tab Explorer.

La skin vetro vive nei `GlassPanel`; qui si decide solo *quale* accento usare e
si rifiniscono i controlli che restano fuori dai pannelli (bottoni store,
bottoni download, etichetta versione). Tutte le funzioni sono idempotenti:
`display_game_data` le richiama più volte per lo stesso gioco.
"""

import os

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

from core.config import get_base_dir
from ui.styles.gold_tier_style import GOLD_PRIMARY, GoldTierStyle
from ui.styles.sale_style import SALE_GREEN, SaleStyle
from ui.widgets.glass import (STATE_DEFAULT, STATE_GOLD, STATE_GOLD_SALE,
                              STATE_SALE, accent_for, css_rgba)


class StructuralTheme:
    """Applica lo stato visivo del gioco alla tab Explorer."""

    def __init__(self, window):
        self.w = window

    # ── Entry point ──────────────────────────────────────────────────

    def apply_state(self, state: str):
        theme = self.w.app_config['theme']
        tab = getattr(self.w, "tab_explorer", None)
        if tab is not None:
            tab.set_theme(theme)
            tab.set_state(state)

        accent, _glow = accent_for(theme, state)
        if state in (STATE_GOLD, STATE_GOLD_SALE):
            self.update_store_buttons("gold")
            self.w.lbl_game_version.setStyleSheet(
                f"color: {accent.name()}; font-weight: bold; font-size: 14px;"
                " margin-bottom: 5px; margin-top: 2px;"
            )
        elif state == STATE_SALE:
            self.update_store_buttons("default")
            self.w.lbl_game_version.setStyleSheet(
                f"color: {accent.name()}; font-weight: bold; font-size: 14px;"
                " margin-bottom: 5px; margin-top: 2px;"
            )
        else:
            self.update_store_buttons("default")
            self.w.lbl_game_version.setStyleSheet("")

    def apply_default(self):
        self.apply_state(STATE_DEFAULT)

    def apply_gold(self):
        self.apply_state(STATE_GOLD)

    def apply_sale(self):
        self.apply_state(STATE_SALE)

    def apply_gold_sale(self):
        """Gold + saldo: bordo oro, glow verde."""
        self.apply_state(STATE_GOLD_SALE)

    # ── Store buttons (base, senza glow per-piattaforma) ─────────────

    def update_store_buttons(self, theme_type):
        media_dir = os.path.join(get_base_dir(), ".media")
        self.w.btn_steam_store.setGraphicsEffect(None)
        self.w.btn_gog_store.setGraphicsEffect(None)

        if theme_type in ("gold", "gold_sale"):
            self.w.btn_steam_store.setIcon(
                self.w._gold_icon(os.path.join(media_dir, "steam_bw.svg"))
            )
            self.w.btn_gog_store.setIcon(
                self.w._gold_icon(os.path.join(media_dir, "gog.svg"))
            )
            qss = (
                "QPushButton#BtnSteamStore, QPushButton#BtnGogStore {"
                "  background-color: transparent;"
                "  border: 1px solid transparent;"
                "  border-radius: 25px;"
                "}"
                "QPushButton#BtnSteamStore:hover, QPushButton#BtnGogStore:hover {"
                "  background-color: rgba(255, 215, 0, 0.2);"
                "  border: 1px solid #FFD700;"
                "}"
            )
            self.w.btn_steam_store.setStyleSheet(qss)
            self.w.btn_gog_store.setStyleSheet(qss)
        else:
            from PyQt6.QtGui import QIcon

            self.w.btn_steam_store.setIcon(
                QIcon(os.path.join(media_dir, "steam.svg"))
            )
            self.w.btn_gog_store.setIcon(
                QIcon(os.path.join(media_dir, "gog.svg"))
            )
            self.w.btn_steam_store.setStyleSheet("")
            self.w.btn_gog_store.setStyleSheet("")

    # ── Glow per piattaforma in saldo ────────────────────────────────

    def apply_store_sale_glow(self, btn):
        glow = QGraphicsDropShadowEffect(self.w)
        glow.setBlurRadius(35)
        glow.setColor(QColor(SALE_GREEN))
        glow.setOffset(0, 0)
        btn.setGraphicsEffect(glow)
        btn.setStyleSheet(SaleStyle.sale_button_glow())

    # ── Download buttons (stesse dimensioni, veste vetro) ────────────

    def apply_download_button(self, btn, is_gold, is_sale):
        theme = self.w.app_config['theme']
        if is_gold:
            glow = GoldTierStyle.gold_download_glow()
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 12px;
                    background-color: rgba(255, 215, 0, 0.10);
                    border: 1px solid {GOLD_PRIMARY};
                    border-radius: 10px;
                    font-weight: bold; color: #FFE44D;
                    margin: 8px;
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 215, 0, 0.20);
                    color: {GOLD_PRIMARY};
                }}
            """)
        elif is_sale:
            glow = QGraphicsDropShadowEffect(self.w)
            glow.setBlurRadius(20)
            glow.setColor(QColor(SALE_GREEN))
            glow.setOffset(0, 0)
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 12px;
                    background-color: rgba(0, 255, 136, 0.10);
                    border: 1px solid {SALE_GREEN};
                    border-radius: 10px;
                    font-weight: bold; color: {SALE_GREEN};
                    margin: 8px;
                }}
                QPushButton:hover {{
                    background-color: rgba(0, 255, 136, 0.20);
                    color: #b6ffdd;
                }}
            """)
        else:
            neon_end = theme.get('neon_gradient_end', '#00d2ff')
            accent = theme.get('main_accent_color', '#733d91')
            glow = QGraphicsDropShadowEffect(self.w)
            glow.setBlurRadius(15)
            glow.setColor(QColor(neon_end))
            glow.setOffset(0, 0)
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 12px;
                    background-color: {css_rgba('#ffffff', 0.05)};
                    border: 1px solid {css_rgba(accent, 0.75)};
                    border-radius: 10px;
                    font-weight: bold; color: #ffffff;
                    margin: 8px;
                }}
                QPushButton:hover {{
                    background-color: {css_rgba('#ffffff', 0.12)};
                    border-color: {css_rgba(neon_end, 0.9)};
                    color: {neon_end};
                }}
            """)
