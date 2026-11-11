import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QGraphicsDropShadowEffect

from core.config import get_base_dir
from ui.styles.sale_style import SALE_GREEN, SaleStyle
from ui.styles.gold_tier_style import GOLD_PRIMARY, GoldTierStyle


class StructuralTheme:
    """Centralized structural theme application for AppWindow.

    All 4 theme variants (default, gold, sale, gold_sale) plus
    store-button and download-button helpers live here so that
    cached-data and live-data code paths produce identical visuals.
    """

    def __init__(self, window):
        self.w = window

    # ── Main routing methods ──────────────────────────────────────

    def apply_default(self):
        t = self.w.app_config['theme']
        self.w.center_panel.setStyleSheet("")
        self.w.req_display.setStyleSheet("")
        self.w.info_display.setStyleSheet("")
        self.w.lbl_game_version.setStyleSheet("")
        self._restore_library_sidebar(t)
        self.update_store_buttons("default")

    def apply_gold(self):
        t = self.w.app_config['theme']
        self.w.center_panel.setStyleSheet(
            f"QWidget#CentralContentPanel {{ border: 2px solid {GOLD_PRIMARY}; }}"
        )
        self.w.req_display.setStyleSheet(
            f"background-color: {t['background_dark_purple']}; "
            f"border: 1px solid {GOLD_PRIMARY}; border-radius: 8px; padding: 12px; "
            f"font-size: 13px; color: #ffffff;"
        )
        self.w.info_display.setStyleSheet(
            f"background-color: {t['background_dark_purple']}; "
            f"border: 1px solid {GOLD_PRIMARY}; border-radius: 8px; padding: 12px; "
            f"font-size: 13px; color: #ffffff;"
        )
        self.w.lbl_game_version.setStyleSheet(
            f"color: {GOLD_PRIMARY}; font-weight: bold; font-size: 14px; "
            f"margin-bottom: 5px; margin-top: 2px;"
        )
        self._apply_gold_library_sidebar(t)
        self.update_store_buttons("gold")

    def apply_sale(self):
        t = self.w.app_config['theme']
        self.w.center_panel.setStyleSheet(
            f"QWidget#CentralContentPanel {{ border: 2px solid {SALE_GREEN}; }}"
        )
        self.w.req_display.setStyleSheet(
            f"background-color: {t['background_dark_purple']}; "
            f"border: 1px solid {SALE_GREEN}; border-radius: 8px; padding: 12px; "
            f"font-size: 13px; color: #ffffff;"
        )
        self.w.info_display.setStyleSheet(
            f"background-color: {t['background_dark_purple']}; "
            f"border: 1px solid {SALE_GREEN}; border-radius: 8px; padding: 12px; "
            f"font-size: 13px; color: #ffffff;"
        )
        self.w.lbl_game_version.setStyleSheet(
            f"color: {SALE_GREEN}; font-weight: bold; font-size: 14px; "
            f"margin-bottom: 5px; margin-top: 2px;"
        )
        self._apply_sale_library_sidebar(t)
        self.update_store_buttons("default")

    def apply_gold_sale(self):
        """Structural stays gold; store buttons get per-platform overlay elsewhere."""
        self.apply_gold()

    # ── Store buttons (base style, no per-platform glow) ──────────

    def update_store_buttons(self, theme_type):
        """Set store button icons and base QSS (no glow — applied per-platform)."""
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
            self.w.btn_steam_store.setIcon(
                QIcon(os.path.join(media_dir, "steam.svg"))
            )
            self.w.btn_gog_store.setIcon(
                QIcon(os.path.join(media_dir, "gog.svg"))
            )
            self.w.btn_steam_store.setStyleSheet("")
            self.w.btn_gog_store.setStyleSheet("")

    # ── Store button per-platform sale overlay ────────────────────
    # Called from display_game_data (cached prices) and
    # _on_sale_data_found (live data) for individual buttons.

    def apply_store_sale_glow(self, btn):
        glow = QGraphicsDropShadowEffect(self.w)
        glow.setBlurRadius(35)
        glow.setColor(QColor(SALE_GREEN))
        glow.setOffset(0, 0)
        btn.setGraphicsEffect(glow)
        btn.setStyleSheet(SaleStyle.sale_button_glow())

    # ── Download buttons ──────────────────────────────────────────

    def apply_download_button(self, btn, is_gold, is_sale):
        t = self.w.app_config['theme']
        if is_gold:
            glow = GoldTierStyle.gold_download_glow()
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(
                GoldTierStyle.gold_download_button_qss(t)
            )
        elif is_sale:
            glow = QGraphicsDropShadowEffect(self.w)
            glow.setBlurRadius(20)
            glow.setColor(QColor(SALE_GREEN))
            glow.setOffset(0, 0)
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(f"""
                QPushButton {{ 
                    text-align: left; padding: 12px; 
                    background-color: {t['background_dark_purple']}; 
                    border: 2px solid transparent; 
                    border-image: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 {SALE_GREEN}, stop:1 #00cc6a) 1 stretch;
                    font-weight: bold; color: {SALE_GREEN}; 
                    margin: 8px;
                }}
                QPushButton:hover {{ 
                    background-color: {t['surface_panel_purple']}; 
                    color: {SALE_GREEN}; 
                }}
            """)
        else:
            neon_start = t.get('neon_gradient_start', '#8d0bb1')
            neon_end = t.get('neon_gradient_end', '#00d2ff')
            glow = QGraphicsDropShadowEffect(self.w)
            glow.setBlurRadius(15)
            glow.setColor(QColor(neon_end))
            glow.setOffset(0, 0)
            btn.setGraphicsEffect(glow)
            btn.setStyleSheet(f"""
                QPushButton {{ 
                    text-align: left; padding: 12px; 
                    background-color: {t['background_dark_purple']}; 
                    border: 2px solid transparent; 
                    border-image: qlineargradient(x1:0, y1:0, x2:1, y2:1, 
                        stop:0 {neon_start}, stop:1 {neon_end}) 1 stretch;
                    font-weight: bold; color: white; 
                    margin: 8px;
                }}
                QPushButton:hover {{ 
                    background-color: {t['surface_panel_purple']}; 
                    color: {neon_end}; 
                }}
            """)

    # ── Library sidebar helpers ───────────────────────────────────

    def _restore_library_sidebar(self, t):
        self.w.tab_explorer.lib_search.setStyleSheet("")
        self.w.tab_explorer.header_widget.setStyleSheet(
            f"background-color: {t['surface_panel_purple']};"
            f" border-bottom: 2px solid {t['main_accent_color']};"
            " border-top-left-radius: 4px; border-top-right-radius: 4px;"
        )
        self.w.tab_explorer.lbl_library_header.setStyleSheet(
            f"color: {t['text_muted']};"
            " font-size: 11px; font-weight: bold;"
            " text-transform: uppercase; letter-spacing: 1px;"
        )
        for icon_file, lbl in self.w.tab_explorer.header_icon_labels.items():
            lbl.setText(
                self.w._load_colored_svg(icon_file, t['text_muted'], 26)
            )
        self.w.tab_explorer.lib_list.setStyleSheet("")

    def _apply_gold_library_sidebar(self, t):
        self.w.tab_explorer.lib_search.setStyleSheet(
            "QLineEdit {"
            "  background-color: transparent;"
            "  border: none;"
            f"  color: {t['text_muted']};"
            "  padding: 6px;"
            "  font-size: 12px;"
            "}"
        )
        self.w.tab_explorer.header_widget.setStyleSheet(
            f"background-color: {t['surface_panel_purple']};"
            "  border-bottom: 2px solid #FFD700;"
            "  border-top-left-radius: 4px;"
            "  border-top-right-radius: 4px;"
        )
        self.w.tab_explorer.lbl_library_header.setStyleSheet(
            "color: #FFD700; font-size: 11px; font-weight: bold;"
            " text-transform: uppercase; letter-spacing: 1px;"
        )
        for icon_file, lbl in self.w.tab_explorer.header_icon_labels.items():
            lbl.setText(
                self.w._load_colored_svg(icon_file, "#FFD700", 26)
            )
        self.w.tab_explorer.lib_list.setStyleSheet(
            "QListWidget#LibraryList {"
            "  background-color: transparent;"
            "  border: 2px solid #FFD700;"
            "  border-top: none;"
            "  border-radius: 0px 0px 6px 6px;"
            "  padding: 5px;"
            "}"
            "QListWidget#LibraryList::item {"
            "  padding: 2px;"
            "  border-bottom: none;"
            "  border-radius: 4px;"
            "}"
            "QListWidget#LibraryList::item:hover {"
            f"  background-color: {t['surface_panel_purple']};"
            "}"
            "QListWidget#LibraryList::item:selected {"
            f"  background-color: {t['surface_panel_purple']};"
            "  border-left: 3px solid #FFD700;"
            "  border-radius: 2px;"
            "}"
        )

    def _apply_sale_library_sidebar(self, t):
        self.w.tab_explorer.lib_search.setStyleSheet("")
        self.w.tab_explorer.header_widget.setStyleSheet(
            f"background-color: {t['surface_panel_purple']};"
            "  border-bottom: 2px solid #00ff88;"
            "  border-top-left-radius: 4px;"
            "  border-top-right-radius: 4px;"
        )
        self.w.tab_explorer.lbl_library_header.setStyleSheet(
            "color: #00ff88; font-size: 11px; font-weight: bold;"
            " text-transform: uppercase; letter-spacing: 1px;"
        )
        for icon_file, lbl in self.w.tab_explorer.header_icon_labels.items():
            lbl.setText(
                self.w._load_colored_svg(icon_file, "#00ff88", 26)
            )
        self.w.tab_explorer.lib_list.setStyleSheet(
            "QListWidget#LibraryList {"
            "  background-color: transparent;"
            "  border: 2px solid #00ff88;"
            "  border-top: none;"
            "  border-radius: 0px 0px 6px 6px;"
            "  padding: 5px;"
            "}"
            "QListWidget#LibraryList::item {"
            "  padding: 2px;"
            "  border-bottom: none;"
            "  border-radius: 4px;"
            "}"
            "QListWidget#LibraryList::item:hover {"
            f"  background-color: {t['surface_panel_purple']};"
            "}"
            "QListWidget#LibraryList::item:selected {"
            f"  background-color: {t['surface_panel_purple']};"
            "  border-left: 3px solid #00ff88;"
            "  border-radius: 2px;"
            "}"
        )
