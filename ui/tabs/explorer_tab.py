import os
import sys

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, QStringListModel, QTimer
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter, QPalette
from PyQt6.QtWidgets import (QCompleter, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QListWidget,
                             QProgressBar, QPushButton, QScrollArea,
                             QSizePolicy, QSplitter, QTextEdit, QVBoxLayout,
                             QWidget)

from PyQt6.QtWebEngineWidgets import QWebEngineView

from core.config import get_base_dir
from network.featured_image_worker import FeaturedImageWorker
from ui.widgets.blur_background import BlurredBackground
from ui.widgets.glass import (STATE_DEFAULT, accent_for, css_rgba,
                              disable_scrollbar_menu, GlassPanel,
                              SCROLLBAR_QSS, style_context_menu, theme_accent,
                              with_alpha)
from ui.widgets.icons import search_icon_pixmap
from ui.widgets.spec_view import SpecView
from ui.widgets.status_pill import StatusPill

RAIL_WIDTH = 284
COVER_W = 260
COVER_H = 382
LIGHT_THRESHOLD = 0.60
DESC_MIN_HEIGHT = 140
DESC_MAX_HEIGHT = 520


class ExplorerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.widgets = main_window.widgets
        self._theme = dict(self.mw.app_config.get('theme', {}))
        self._state = STATE_DEFAULT
        self._blur_percent = int(
            (self.mw.app_config.get('ui', {}) or {}).get('explorer_blur', 0)
        )
        self._bg_workers = []
        self._bg_paths = {}
        self._contrast_light = False
        self.init_ui()

    # ── costruzione ──────────────────────────────────────────────────

    def init_ui(self):
        theme = self._theme

        explorer_layout = QVBoxLayout(self)
        explorer_layout.setContentsMargins(0, 8, 0, 0)
        explorer_layout.setSpacing(6)

        # --- SFONDO SFOCATO (disegnato nel paintEvent della tab) ---
        self.bg_layer = BlurredBackground(theme, self, blur_percent=self._blur_percent)
        self.bg_layer.background_changed.connect(self._apply_search_contrast)

        # --- SEARCH BAR ---
        self.search_input = QLineEdit()
        self.search_input.setObjectName("ExplorerSearch")
        self.search_input.setPlaceholderText("Paste SteamRIP URL or type game name...")
        self.search_input.setMinimumHeight(38)
        self.search_input.setClearButtonEnabled(False)

        self.completer_model = QStringListModel()
        if self.mw.master_catalog:
            self.completer_model.setStringList(list(self.mw.master_catalog.keys()))

        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(6)
        self.completer.activated.connect(lambda text: self.mw.start_search())

        self.search_input.setCompleter(self.completer)
        self.search_input.returnPressed.connect(self.mw.start_search)

        self._search_action = self.search_input.addAction(
            QIcon(search_icon_pixmap(15, "#ffffff")),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        explorer_layout.addWidget(self.search_input)

        # --- LOADING BAR ---
        self.db_progress = QProgressBar()
        self.db_progress.setRange(0, 0)
        self.db_progress.setFixedHeight(3)
        self.db_progress.setTextVisible(False)
        self.db_progress.setStyleSheet(
            "QProgressBar { background-color: transparent; border: none; }"
            f"QProgressBar::chunk {{ background-color: "
            f"{theme.get('progress_bar_color', '#00ff88')}; }}"
        )
        self.db_progress.hide()
        explorer_layout.addWidget(self.db_progress)

        # --- SPLITTER: LIBRERIA | CONTENUTO ---
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(8)
        self.content_splitter.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
            "QSplitter::handle:hover { background: rgba(255,255,255,0.06); }"
        )
        explorer_layout.addWidget(self.content_splitter, stretch=1)

        self._build_sidebar(theme)
        self._build_center(theme)
        self.content_splitter.addWidget(self.sidebar_widget)
        self.content_splitter.addWidget(self.center_panel)
        self.content_splitter.setSizes([300, 900])
        self.content_splitter.setStretchFactor(0, 0)
        self.content_splitter.setStretchFactor(1, 1)

        # --- FOOTER ETICO ---
        self.lbl_disclaimer = QLabel()
        self.lbl_disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_disclaimer.setWordWrap(True)
        self._apply_footer_contrast(False)
        explorer_layout.addWidget(self.lbl_disclaimer)

        self._apply_list_style(theme_accent(theme))
        self._panel_alphas = {
            self.sidebar_widget: 0.40,
            self.center_panel: 0.16,
            self.desc_panel: 0.34,
            self.req_panel: 0.36,
            self.info_panel: 0.36,
            self.links_panel: 0.36,
            self.task_panel: 0.34,
            self.version_status_panel: 0.34,
        }
        self._panel_light_applied = None
        self._apply_search_contrast()

        self._bind_widgets()

    # ── sidebar ──────────────────────────────────────────────────────

    def _build_sidebar(self, theme):
        self.sidebar_widget = GlassPanel(theme, radius=14, alpha=0.40)
        self.sidebar_widget.setObjectName("SidebarPanel")
        self.sidebar_widget.setMinimumWidth(240)

        lib_layout = QVBoxLayout(self.sidebar_widget)
        lib_layout.setContentsMargins(10, 10, 10, 10)
        lib_layout.setSpacing(8)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("LibraryHeader")
        self.header_widget.setStyleSheet(
            "QWidget#LibraryHeader { background: transparent; }"
        )
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.lbl_library_header = QLabel("LIBRARY")
        self.lbl_library_header.setStyleSheet(
            f"color: {css_rgba('#ffffff', 0.62)}; font-size: 11px;"
            " font-weight: bold; letter-spacing: 1px;"
            " background: transparent; border: none;"
        )
        header_layout.addWidget(self.lbl_library_header)

        self.lib_search = QLineEdit()
        self.lib_search.setObjectName("LibrarySearch")
        self.lib_search.setPlaceholderText("Search library...")
        self.lib_search.setMinimumHeight(26)
        self.lib_search.textChanged.connect(
            lambda text: self.mw.lib_manager.filter_library(text)
        )
        header_layout.addWidget(self.lib_search, 1)

        media_dir = os.path.join(get_base_dir(), ".media")
        self.btn_add_all_steam = QPushButton()
        self.btn_add_all_steam.setObjectName("BtnAddAllSteam")
        self.btn_add_all_steam.setFixedSize(26, 26)
        self.btn_add_all_steam.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_add_all_steam.setToolTip(
            "Add all installed games to Steam (single Steam restart)"
        )
        self.btn_add_all_steam.setIcon(QIcon(os.path.join(media_dir, "steam.svg")))
        self.btn_add_all_steam.setIconSize(QSize(20, 20))
        self.btn_add_all_steam.setStyleSheet("""
            QPushButton#BtnAddAllSteam {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 0px;
            }
            QPushButton#BtnAddAllSteam:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.25);
            }
        """)
        # game_launcher nasce dopo init_ui(): risolviamo l'attributo al click.
        self.btn_add_all_steam.clicked.connect(
            lambda: self.mw.game_launcher.sync_all_games_to_steam()
        )
        self.btn_add_all_steam.setVisible(sys.platform == 'win32')
        header_layout.addWidget(self.btn_add_all_steam)

        lib_layout.addWidget(self.header_widget)

        self.lib_list = QListWidget()
        self.lib_list.setObjectName("LibraryList")
        self.lib_list.setFrameShape(QFrame.Shape.NoFrame)
        self.lib_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lib_list.customContextMenuRequested.connect(self.mw.show_library_context_menu)
        self.lib_list.itemClicked.connect(self.mw.on_library_item_clicked)
        self.lib_list.itemDoubleClicked.connect(self.mw.on_library_item_double_clicked)
        self.lib_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.lib_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        disable_scrollbar_menu(self.lib_list)
        lib_layout.addWidget(self.lib_list, 1)

        # manteniamo la chiave per compatibilità con i binding storici
        self.header_icon_labels = {}

    # ── centro ───────────────────────────────────────────────────────

    def _build_center(self, theme):
        self.center_panel = GlassPanel(theme, radius=16, alpha=0.16, shadow=False)
        self.center_panel.setObjectName("CentralContentPanel")
        self.center_panel.set_sheen(False)

        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(14, 14, 14, 14)
        center_layout.setSpacing(10)

        # --- blocco superiore: header (testo + rail) | colonne a tutta larghezza ---
        self._build_header(theme)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(14)
        header_row.addLayout(self.upper_layout, 1)
        header_row.addWidget(self._build_rail(theme), 0)

        self.upper_widget = QWidget()
        self.upper_widget.setObjectName("HeaderRegion")
        self.upper_widget.setStyleSheet(
            "QWidget#HeaderRegion { background: transparent; }"
        )
        self.upper_widget.setLayout(header_row)

        # --- 3 COLONNE (larghe quanto tutta la zona centrale) ---
        columns_widget = QWidget()
        columns_widget.setObjectName("ColumnsRegion")
        columns_widget.setStyleSheet(
            "QWidget#ColumnsRegion { background: transparent; }"
        )
        self.columns_layout = QHBoxLayout(columns_widget)
        self.columns_layout.setContentsMargins(0, 0, 0, 0)
        self.columns_layout.setSpacing(10)

        self.req_panel = self._build_text_column(
            theme, "⚙️ SYSTEM REQUIREMENTS", SpecView(theme, columns=2)
        )
        self.req_display = self.req_panel.content
        self.info_panel = self._build_text_column(
            theme, "📊 GAME INFO", SpecView(theme, columns=2)
        )
        self.info_display = self.info_panel.content
        self.links_panel = self._build_links_column(theme)

        self.columns_layout.addWidget(self.req_panel, 1)
        self.columns_layout.addWidget(self.info_panel, 1)
        self.columns_layout.addWidget(self.links_panel, 1)

        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.v_splitter.setChildrenCollapsible(False)
        self.v_splitter.setHandleWidth(6)
        self.v_splitter.addWidget(self.upper_widget)
        self.v_splitter.addWidget(columns_widget)
        self.v_splitter.setStretchFactor(0, 3)
        self.v_splitter.setStretchFactor(1, 2)
        self.v_splitter.setStyleSheet(
            "QSplitter::handle { background: transparent; }"
            "QSplitter::handle:hover { background: rgba(255,255,255,0.08); }"
        )
        self.v_splitter.splitterMoved.connect(self._on_v_splitter_moved)

        self._layout_settle = QTimer(self)
        self._layout_settle.setSingleShot(True)
        self._layout_settle.setInterval(0)
        self._layout_settle.timeout.connect(self._on_layout_settle)

        center_layout.addWidget(self.v_splitter, 1)
        center_layout.addWidget(self._build_task_panel(theme), 0)

    def _build_header(self, theme):
        self.upper_layout = QVBoxLayout()
        self.upper_layout.setContentsMargins(0, 0, 0, 0)
        self.upper_layout.setSpacing(0)

        self.text_header_layout = QVBoxLayout()
        self.text_header_layout.setContentsMargins(0, 0, 0, 0)
        self.text_header_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # --- riga titolo ---
        self.title_play_layout = QHBoxLayout()
        self.title_play_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_game_title = QLabel("")
        self.lbl_game_title.setObjectName("GameTitle")
        self.lbl_game_title.setWordWrap(True)
        self.lbl_game_title.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        title_shadow = QGraphicsDropShadowEffect(self.lbl_game_title)
        title_shadow.setBlurRadius(18)
        title_shadow.setOffset(0, 2)
        title_shadow.setColor(QColor(0, 0, 0, 200))
        self.lbl_game_title.setGraphicsEffect(title_shadow)
        self.title_play_layout.addWidget(self.lbl_game_title)
        self.title_play_layout.addStretch()
        self.text_header_layout.addLayout(self.title_play_layout)

        # --- riga versione + stato ---
        self.lbl_game_version = QLabel("Select or search for a game to see details")
        self.lbl_game_version.setObjectName("GameVersion")

        self.version_row_layout = QHBoxLayout()
        self.version_row_layout.setContentsMargins(0, 0, 0, 0)
        self.version_row_layout.setSpacing(10)
        self.version_row_layout.addWidget(self.lbl_game_version)

        self.btn_version_status = StatusPill()
        self.btn_version_status.setToolTip("Show version and build status")
        self.btn_version_status.hide()
        self.btn_version_status.clicked.connect(self.mw.toggle_version_status_panel)
        self.version_row_layout.addWidget(
            self.btn_version_status, 0, Qt.AlignmentFlag.AlignTop
        )

        _badge_text = theme.get('text_primary_white', '#ffffff')
        _badge_accent = theme.get('main_accent_color', '#733d91')
        _badge_qss = (
            f"QLabel {{ color: {_badge_text}; background: rgba(255,255,255,0.06);"
            f" border: 1px solid {_badge_accent}; border-radius: 4px;"
            " padding: 1px 7px; font-size: 11px; font-weight: bold; }"
        )
        self.lbl_pegi = QLabel("")
        self.lbl_esrb = QLabel("")
        for _badge in (self.lbl_pegi, self.lbl_esrb):
            _badge.setObjectName("AgeBadge")
            _badge.setStyleSheet(_badge_qss)
            _badge.hide()
        self.version_row_layout.addWidget(self.lbl_pegi, 0, Qt.AlignmentFlag.AlignTop)
        self.version_row_layout.addWidget(self.lbl_esrb, 0, Qt.AlignmentFlag.AlignTop)
        self.version_row_layout.addStretch(1)
        self.text_header_layout.addLayout(self.version_row_layout)

        self.version_status_panel = self._build_version_status_panel(theme)
        self.text_header_layout.addWidget(self.version_status_panel)
        self.version_status_panel.hide()

        # --- descrizione ---
        self.desc_panel = GlassPanel(theme, radius=14, alpha=0.34)
        self.desc_panel.setObjectName("DescriptionPanel")
        self.desc_panel.setFixedHeight(DESC_MIN_HEIGHT)
        desc_layout = QVBoxLayout(self.desc_panel)
        desc_layout.setContentsMargins(14, 12, 14, 12)
        self.desc_display = QTextEdit()
        self.desc_display.setObjectName("DescDisplay")
        self.desc_display.setReadOnly(True)
        self.desc_display.setFrameShape(QFrame.Shape.NoFrame)
        self.desc_display.setStyleSheet(
            "QTextEdit#DescDisplay { background: transparent; border: none;"
            " font-size: 13px; color: #e0e0e0; padding: 0px; }"
        )
        # Copy/Select All resta, ma con il menu vetro; la scrollbar non ha menu.
        self.desc_display.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.desc_display.customContextMenuRequested.connect(
            self._show_description_menu
        )
        disable_scrollbar_menu(self.desc_display)
        desc_layout.addWidget(self.desc_display)
        self.text_header_layout.addWidget(self.desc_panel, 0)
        self.text_header_layout.addStretch(1)

        self.upper_layout.addLayout(self.text_header_layout)

    def _show_description_menu(self, pos):
        """Menu contestuale della descrizione (copia/seleziona) in stile vetro."""
        menu = self.desc_display.createStandardContextMenu()
        style_context_menu(menu, self._theme)
        menu.exec(self.desc_display.mapToGlobal(pos))
        menu.deleteLater()

    def _build_version_status_panel(self, theme):
        panel = GlassPanel(theme, radius=12, alpha=0.34)
        panel.setObjectName("VersionStatusPanel")
        panel.setMaximumWidth(560)

        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        panel_layout.setSpacing(8)

        vs_title = QLabel("Version Status")
        vs_title.setStyleSheet(
            "font-weight: bold; font-size: 13px; color: #ffffff;"
            " background: transparent; border: none;"
        )
        panel_layout.addWidget(vs_title)

        self.vs_rows = {}
        chips_row = QHBoxLayout()
        chips_row.setContentsMargins(0, 2, 0, 2)
        chips_row.setSpacing(8)
        vs_rows_def = (
            ("installed", "Installed Build"),
            ("posted", "SteamRIP Build"),
            ("steam", "Steam Latest Build"),
        )
        for row_key, row_label in vs_rows_def:
            chip = QFrame()
            chip.setObjectName("VsChip")
            chip.setStyleSheet(
                "QFrame#VsChip {"
                f" background-color: {css_rgba('#ffffff', 0.05)};"
                f" border: 1px solid {css_rgba('#ffffff', 0.08)};"
                " border-radius: 8px;"
                "}"
                "QFrame#VsChip QLabel { background: transparent; border: none; }"
            )
            chip_layout = QVBoxLayout(chip)
            chip_layout.setContentsMargins(10, 6, 10, 6)
            chip_layout.setSpacing(1)

            name_lbl = QLabel(row_label.upper())
            name_lbl.setStyleSheet(
                f"color: {css_rgba('#ffffff', 0.50)}; font-size: 9px;"
                " font-weight: bold; letter-spacing: 1px;"
            )
            value_lbl = QLabel("—")
            value_lbl.setStyleSheet(
                "color: #ffffff; font-size: 12px; font-weight: bold;"
            )
            date_lbl = QLabel("")
            date_lbl.setStyleSheet(
                f"color: {css_rgba('#ffffff', 0.45)}; font-size: 9px;"
            )
            chip_layout.addWidget(name_lbl)
            chip_layout.addWidget(value_lbl)
            chip_layout.addWidget(date_lbl)

            chips_row.addWidget(chip, 1)
            self.vs_rows[row_key] = (value_lbl, date_lbl)
        panel_layout.addLayout(chips_row)

        self.vs_alert = QLabel("")
        self.vs_alert.setWordWrap(True)
        self.vs_alert.setStyleSheet(
            "font-size: 11px; background: transparent; border: none; color: #ffffff;"
        )
        panel_layout.addWidget(self.vs_alert)

        self.vs_subtext = QLabel("")
        self.vs_subtext.setWordWrap(True)
        self.vs_subtext.setStyleSheet(
            f"font-size: 10px; background: transparent; border: none;"
            f" color: {css_rgba('#ffffff', 0.55)};"
        )
        panel_layout.addWidget(self.vs_subtext)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 2, 0, 0)
        meta_row.setSpacing(8)
        self.vs_meta = QLabel("")
        self.vs_meta.setWordWrap(True)
        self.vs_meta.setStyleSheet(
            f"font-size: 10px; background: transparent; border: none;"
            f" color: {css_rgba('#ffffff', 0.45)};"
        )
        meta_row.addWidget(self.vs_meta, 1)

        self.btn_vs_refresh = QPushButton("↻ Refresh")
        self.btn_vs_refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_vs_refresh.setToolTip("Refresh the latest build available on Steam")
        self.btn_vs_refresh.setStyleSheet("""
            QPushButton {
                background: transparent; color: #d8cfe8;
                border: 1px solid rgba(255,255,255,0.18);
                border-radius: 6px; padding: 3px 8px; font-size: 10px;
            }
            QPushButton:hover { color: #ffffff; border-color: #ffffff; }
            QPushButton:disabled { color: #5d5470; border-color: rgba(255,255,255,0.08); }
        """)
        self.btn_vs_refresh.clicked.connect(self.mw.refresh_version_status)
        meta_row.addWidget(self.btn_vs_refresh)
        panel_layout.addLayout(meta_row)

        self.btn_vs_steamdb = QPushButton("↗ View on SteamDB")
        self.btn_vs_steamdb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_vs_steamdb.setStyleSheet("""
            QPushButton {
                background: rgba(255,255,255,0.05); color: #ffffff;
                border: 1px solid rgba(255,255,255,0.14);
                border-radius: 6px; padding: 5px 8px; font-size: 11px;
            }
            QPushButton:hover {
                background: rgba(255,255,255,0.10);
                border-color: rgba(255,255,255,0.35);
            }
        """)
        self.btn_vs_steamdb.clicked.connect(self.mw.open_version_status_steamdb)
        panel_layout.addWidget(self.btn_vs_steamdb)

        return panel

    def _column_panel(self, theme, title_text):
        panel = GlassPanel(theme, radius=14, alpha=0.36)
        panel.setObjectName("GlassColumn")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        title = QLabel(title_text)
        title.setObjectName("LinksTitle")
        title.setStyleSheet(
            f"color: {css_rgba('#ffffff', 0.72)}; font-size: 11px;"
            " font-weight: bold; letter-spacing: 1px; background: transparent;"
            " border: none; padding: 0px;"
        )
        layout.addWidget(title)
        panel.title_label = title
        return panel

    def _build_text_column(self, theme, title_text, content_widget):
        panel = self._column_panel(theme, title_text)
        panel.content = content_widget
        panel.layout().addWidget(content_widget, 1)
        return panel

    def _build_links_column(self, theme):
        panel = self._column_panel(theme, "⬇️ DOWNLOAD LINKS")
        self.links_scroll = QScrollArea()
        self.links_scroll.setObjectName("DownloadScrollArea")
        self.links_scroll.setWidgetResizable(True)
        self.links_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.links_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            + SCROLLBAR_QSS
        )
        self.links_scroll.viewport().setObjectName("LinksViewport")
        self.links_scroll.viewport().setStyleSheet(
            "QWidget#LinksViewport { background: transparent; }"
        )
        self.links_scroll_content = QWidget()
        self.links_scroll_content.setObjectName("LinksContent")
        self.links_scroll_content.setStyleSheet(
            "QWidget#LinksContent { background: transparent; }"
        )
        self.links_container = QVBoxLayout(self.links_scroll_content)
        self.links_container.setContentsMargins(0, 0, 0, 0)
        self.links_container.setSpacing(0)
        self.links_container.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.links_scroll.setWidget(self.links_scroll_content)
        disable_scrollbar_menu(self.links_scroll)
        panel.layout().addWidget(self.links_scroll, 1)
        return panel

    # ── rail destro ──────────────────────────────────────────────────

    def _build_rail(self, theme):
        self.rail_widget = QWidget()
        self.rail_widget.setObjectName("RailPanel")
        self.rail_widget.setStyleSheet(
            "QWidget#RailPanel { background: transparent; }"
        )
        self.rail_widget.setFixedWidth(RAIL_WIDTH)

        self.right_header_layout = QVBoxLayout(self.rail_widget)
        self.right_header_layout.setContentsMargins(0, 0, 0, 0)
        self.right_header_layout.setSpacing(12)
        self.right_header_layout.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
        )

        self.cover_image = QWebEngineView()
        self.cover_image.setFixedSize(COVER_W, COVER_H)
        # Il menu contestuale Chromium (Back/Forward/Save page...) non serve
        # sulla copertina e userebbe uno stile diverso dal resto.
        self.cover_image.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.cover_image.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.right_header_layout.addWidget(
            self.cover_image, 0, Qt.AlignmentFlag.AlignHCenter
        )

        media_dir = os.path.join(get_base_dir(), ".media")
        self.store_icons_layout = QHBoxLayout()
        self.store_icons_layout.setContentsMargins(0, 0, 0, 0)
        self.store_icons_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.store_icons_layout.setSpacing(18)

        self.btn_steam_store = QPushButton()
        self.btn_steam_store.setObjectName("BtnSteamStore")
        self.btn_steam_store.setIcon(QIcon(os.path.join(media_dir, "steam.svg")))
        self.btn_steam_store.setIconSize(QSize(36, 36))
        self.btn_steam_store.setFixedSize(50, 50)
        self.btn_steam_store.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_steam_store.hide()

        self.btn_gog_store = QPushButton()
        self.btn_gog_store.setObjectName("BtnGogStore")
        self.btn_gog_store.setIcon(QIcon(os.path.join(media_dir, "gog.svg")))
        self.btn_gog_store.setIconSize(QSize(36, 36))
        self.btn_gog_store.setFixedSize(50, 50)
        self.btn_gog_store.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_gog_store.hide()

        self.store_icons_layout.addWidget(self.btn_steam_store)
        self.store_icons_layout.addWidget(self.btn_gog_store)
        self.right_header_layout.addLayout(self.store_icons_layout)

        self.btn_play_main = QPushButton("▶ PLAY")
        self.btn_play_main.setObjectName("BtnPlayMain")
        self.btn_play_main.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_play_main.setFixedHeight(46)
        self.btn_play_main.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.btn_play_main.hide()

        self.btn_steam_optional = QPushButton("➕ Add to Steam (Optional)")
        self.btn_steam_optional.setObjectName("BtnSteamOptional")
        self.btn_steam_optional.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_steam_optional.hide()

        self.lbl_playtime = QLabel("")
        self.lbl_playtime.setObjectName("LblPlaytime")
        self.lbl_playtime.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_playtime.setStyleSheet(
            f"color: {css_rgba('#ffffff', 0.62)}; font-size: 12px; font-weight: bold;"
            " background: transparent;"
        )
        self.lbl_playtime.hide()

        self.play_buttons_layout = QVBoxLayout()
        self.play_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.play_buttons_layout.setSpacing(6)
        self.play_buttons_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.play_buttons_layout.addWidget(self.btn_play_main)
        self.play_buttons_layout.addWidget(self.btn_steam_optional)
        self.play_buttons_layout.addWidget(self.lbl_playtime)
        self.right_header_layout.addLayout(self.play_buttons_layout)
        self.right_header_layout.addStretch(1)
        self.rail_widget.setVisible(False)

        return self.rail_widget

    # ── task panel ───────────────────────────────────────────────────

    def _build_task_panel(self, theme):
        self.task_panel = GlassPanel(theme, radius=14, alpha=0.34)
        self.task_panel.setObjectName("TaskPanel")
        self.task_panel.setFixedHeight(60)

        task_layout = QHBoxLayout(self.task_panel)
        task_layout.setContentsMargins(15, 10, 15, 10)
        task_layout.setSpacing(10)

        self.task_label = QLabel("Idle: Ready for action")
        self.task_label.setStyleSheet(
            "color: #ffffff; font-weight: bold; border: none; background: transparent;"
        )

        self.task_progress = QProgressBar()
        self.task_progress.setTextVisible(True)
        self.task_progress.setFixedHeight(22)
        self.task_progress.setStyleSheet(
            "QProgressBar { border: 1px solid " + theme.get('main_accent_color', '#733d91') +
            "; border-radius: 6px; text-align: center; color: white;"
            " font-weight: bold; background: transparent; }"
            "QProgressBar::chunk { background-color: " +
            theme.get('progress_bar_color', '#00ff88') + "; border-radius: 5px; }"
        )
        self.task_progress.hide()

        media_dir = os.path.join(get_base_dir(), ".media")
        self.icon_pause = self.mw._colorize_icon(os.path.join(media_dir, "pause_round.svg"))
        self.icon_play = self.mw._colorize_icon(os.path.join(media_dir, "play_round.svg"))
        self.icon_stop = self.mw._colorize_icon(os.path.join(media_dir, "stop_round.svg"))

        self.btn_pause_dl = QPushButton()
        self.btn_stop_dl = QPushButton()
        self.btn_pause_dl.setIcon(self.icon_pause)
        self.btn_stop_dl.setIcon(self.icon_stop)
        for btn in (self.btn_pause_dl, self.btn_stop_dl):
            btn.setFixedSize(40, 40)
            btn.setIconSize(QSize(32, 32))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(
                "QPushButton { background-color: transparent; border: 1px solid "
                + theme.get('main_accent_color', '#733d91') +
                "; border-radius: 20px; }"
                "QPushButton:hover { background-color: rgba(255,255,255,0.15); }"
            )
            btn.hide()

        self.btn_pause_dl.clicked.connect(self.mw.toggle_dl_pause)
        self.btn_stop_dl.clicked.connect(self.mw.dl_manager.stop_current)

        task_layout.addWidget(self.task_label, stretch=4)
        task_layout.addWidget(self.task_progress, stretch=2)
        task_layout.addSpacing(5)
        task_layout.addWidget(self.btn_pause_dl)
        task_layout.addSpacing(5)
        task_layout.addWidget(self.btn_stop_dl)
        return self.task_panel

    # ── tema / stato ─────────────────────────────────────────────────

    def fit_description_panel(self):
        """Adatta l'altezza del box descrizione al contenuto e allo spazio."""
        if not hasattr(self, "desc_panel"):
            return
        width = self.desc_display.viewport().width()
        if width < 200:
            width = max(200, self.desc_panel.width() - 30)
        header_h = getattr(self, "_last_header_height", 0)
        if header_h <= 0:
            header_h = int(self.height() * 0.54)
        max_height = int(max(DESC_MIN_HEIGHT + 60,
                             min(DESC_MAX_HEIGHT, header_h - 160)))
        if getattr(self, "_desc_fit_key", None) == (width, max_height):
            return
        self._desc_fit_key = (width, max_height)
        doc = self.desc_display.document()
        doc.setTextWidth(width)
        target = int(min(max_height, max(DESC_MIN_HEIGHT, doc.size().height() + 34)))
        self.desc_panel.setFixedHeight(target)

    def refresh_description_height(self):
        """Forza il ricalcolo dopo un cambio di contenuto."""
        self._desc_fit_key = None
        self.fit_description_panel()

    def glass_panels(self):
        """Pannelli vetro senza webview dentro (possono avere glow)."""
        return [
            self.sidebar_widget,
            self.desc_panel,
            self.req_panel,
            self.info_panel,
            self.links_panel,
            self.task_panel,
            self.version_status_panel,
        ]

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        for panel in self.glass_panels() + [self.center_panel]:
            panel.set_theme(self._theme)
        for view in (self.req_display, self.info_display):
            view.set_theme(self._theme)
        self.bg_layer.set_theme(self._theme)
        self._apply_list_style(theme_accent(self._theme))
        self._apply_search_contrast()

    def set_rail_visible(self, visible: bool):
        """La rail compare quando c'è un gioco da mostrare."""
        visible = bool(visible)
        if self.rail_widget.isVisible() != visible:
            self.rail_widget.setVisible(visible)
            self._layout_settle.start()

    def set_blur_percent(self, value: int):
        """Percentuale di sfocatura dello sfondo (0..100)."""
        value = max(0, min(100, int(value)))
        if value == self._blur_percent:
            return
        self._blur_percent = value
        self.bg_layer.set_blur_percent(value)

    def set_state(self, state):
        self._state = state
        accent, glow = accent_for(self._theme, state)
        self.center_panel.set_accent(accent, None)
        for panel in self.glass_panels():
            if state == STATE_DEFAULT:
                panel.set_accent(None, None)
            else:
                panel.set_accent(accent, glow)
        self._apply_list_style(accent.name())

    def _apply_list_style(self, accent_color):
        accent = QColor(accent_color)
        self.lib_list.setStyleSheet(
            "QListWidget#LibraryList {"
            "  background: transparent;"
            "  border: none;"
            "  padding: 2px;"
            "  outline: none;"
            "}"
            "QListWidget#LibraryList::item {"
            "  background: transparent;"
            "  border: none;"
            "  border-radius: 8px;"
            "  margin: 1px 0px;"
            "}"
            f"QListWidget#LibraryList::item:hover {{ background: {css_rgba('#ffffff', 0.05)}; }}"
            "QListWidget#LibraryList::item:selected {"
            f"  background: {css_rgba(accent, 0.16)};"
            f"  border-left: 2px solid {accent.name()};"
            "  border-radius: 8px;"
            "}"
        )
        self.lib_list.setStyleSheet(
            self.lib_list.styleSheet()
            + "QScrollBar:vertical { background: transparent; width: 8px; margin: 0px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,0.18);"
            " border-radius: 4px; min-height: 24px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }"
        )

    # ── contrasto search bar ─────────────────────────────────────────

    def _apply_search_contrast(self):
        if not hasattr(self, "search_input"):
            return
        rect = QRect(
            self.search_input.mapTo(self, QPoint(0, 0)),
            self.search_input.size(),
        )
        luma = (self.bg_layer.raw_luminance_under(rect)
                if hasattr(self, "bg_layer") else 0.0)
        light = luma >= LIGHT_THRESHOLD
        self._contrast_light = light
        if hasattr(self, "bg_layer"):
            self.bg_layer.set_light_mode(light)

        if light:
            fill = css_rgba('#ffffff', 0.72)
            border = css_rgba('#000000', 0.15)
            text_color = QColor("#14101f")
            icon_color = "#14101f"
        else:
            fill = css_rgba('#100c1a', 0.55)
            border = css_rgba('#ffffff', 0.12)
            text_color = QColor("#ffffff")
            icon_color = "#ffffff"
        placeholder_color = with_alpha(text_color, 0.55)

        self.search_input.setStyleSheet(
            "QLineEdit#ExplorerSearch {"
            f"  background-color: {fill};"
            f"  border: 1px solid {border};"
            "  border-radius: 18px;"
            "  padding: 6px 14px;"
            f"  color: {text_color.name()};"
            "  font-size: 12px;"
            "  font-weight: normal;"
            "}"
            "QLineEdit#ExplorerSearch:focus { border: 1px solid "
            f"{css_rgba(theme_accent(self._theme), 0.9)}; }}"
        )
        palette = self.search_input.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, placeholder_color)
        palette.setColor(QPalette.ColorRole.Text, text_color)
        self.search_input.setPalette(palette)
        if hasattr(self, "_search_action") and self._search_action is not None:
            self._search_action.setIcon(QIcon(search_icon_pixmap(15, icon_color)))
        self._style_library_search(light)
        self._apply_footer_contrast(light)
        if getattr(self, "_panel_light_applied", None) != light:
            self._panel_light_applied = light
            self._apply_panel_alphas(light)

    def _apply_footer_contrast(self, light: bool):
        """Anche il footer si adatta allo sfondo; l'accento resta acceso."""
        accent = self._theme.get('bright_neon_accent', '#ff00a0')
        if light:
            base_color = "#14101f"
            panel_bg = css_rgba('#ffffff', 0.55)
        else:
            base_color = css_rgba('#ffffff', 0.80)
            panel_bg = css_rgba('#000000', 0.22)
        self.lbl_disclaimer.setText(
            "If you like the game and the software house / publisher aren't aggressive monetizers, "
            "if they are pro-users, if they support the customer, "
            f"<b style='color:{accent}; text-transform: uppercase;'>"
            "BUY THE GAME. THEY DESERVE IT.</b>"
        )
        self.lbl_disclaimer.setStyleSheet(
            f"font-size: 11px; color: {base_color}; padding: 5px 10px;"
            f" background-color: {panel_bg}; border-radius: 8px;"
            " margin-top: 4px;"
        )

    def _apply_panel_alphas(self, light: bool):
        """Su sfondi chiari alza l'opacità del vetro: il testo resta leggibile."""
        boost = 0.16 if light else 0.0
        for panel, base in getattr(self, "_panel_alphas", {}).items():
            panel.set_alpha(min(0.85, base + boost))

    def _style_library_search(self, light):
        if light:
            text_color = QColor("#14101f")
            fill = css_rgba('#ffffff', 0.55)
        else:
            text_color = QColor("#ffffff")
            fill = css_rgba('#ffffff', 0.07)
        placeholder_color = with_alpha(text_color, 0.55)
        icon_color = text_color.name()
        self.lib_search.setStyleSheet(
            "QLineEdit#LibrarySearch {"
            f"  background-color: {fill};"
            f"  border: 1px solid {css_rgba('#ffffff', 0.08)};"
            "  border-radius: 8px;"
            "  padding: 3px 8px;"
            f"  color: {text_color.name()};"
            "  font-size: 11px;"
            "  font-weight: normal;"
            "}"
        )
        palette = self.lib_search.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, placeholder_color)
        palette.setColor(QPalette.ColorRole.Text, text_color)
        self.lib_search.setPalette(palette)
        if getattr(self, "_lib_search_action", None) is None:
            self._lib_search_action = self.lib_search.addAction(
                QIcon(search_icon_pixmap(12, icon_color)),
                QLineEdit.ActionPosition.LeadingPosition,
            )
        else:
            self._lib_search_action.setIcon(QIcon(search_icon_pixmap(12, icon_color)))

    # ── sfondo del gioco ─────────────────────────────────────────────

    def set_game_background(self, featured_url, title=""):
        """Sfondo: featured image SteamRIP, altrimenti splash screen."""
        url = str(featured_url or "").strip()
        if not url:
            self.bg_layer.set_source(None)
            return
        cached = self._bg_paths.get(url)
        if cached and os.path.isfile(cached):
            self.bg_layer.set_source(cached)
            return
        self._bg_workers = [worker for worker in self._bg_workers if worker.isRunning()]
        worker = FeaturedImageWorker(url, self)
        worker.ready.connect(self._on_background_ready)
        worker.finished.connect(lambda w=worker: self._cleanup_bg_worker(w))
        self._bg_workers.append(worker)
        worker.start()

    def _on_background_ready(self, url, path):
        if path:
            self._bg_paths[url] = path
            current = str((getattr(self.mw, "current_game_data", {}) or {}).get("img_url", "") or "")
            if current and current != url:
                return
            self.bg_layer.set_source(path)
        else:
            self.bg_layer.set_source(None)

    def _cleanup_bg_worker(self, worker):
        self._bg_workers = [w for w in self._bg_workers if w is not worker and w.isRunning()]

    # ── eventi ───────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        self.bg_layer.paint(painter, self.rect())
        painter.end()
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "bg_layer"):
            self.bg_layer.schedule_render()
        self._apply_rail_metrics()
        self.fit_description_panel()
        if hasattr(self, "_layout_settle"):
            self._layout_settle.start()

    def ensure_header_space(self):
        """Allarga il blocco header se il contenuto (es. version status) non ci sta."""
        if not hasattr(self, "v_splitter"):
            return
        sizes = self.v_splitter.sizes()
        if len(sizes) != 2:
            return
        need = self.upper_widget.sizeHint().height() + 24
        total = sum(sizes)
        if sizes[0] >= need or total <= 0:
            return
        header = int(min(need, max(180, total - 150)))
        self._splitter_user_moved = True
        self.v_splitter.setSizes([header, max(1, total - header)])
        self._on_layout_settle()

    def _on_layout_settle(self):
        self._update_splitter_proportions()
        self._apply_rail_metrics()
        self.refresh_description_height()

    def _on_v_splitter_moved(self, *args):
        from PyQt6.QtWidgets import QApplication

        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton:
            self._splitter_user_moved = True
        self._apply_rail_metrics()
        self.refresh_description_height()

    def _update_splitter_proportions(self):
        """Header/colonne ~62/38 finché l'utente non trascina il divisore."""
        if getattr(self, "_splitter_user_moved", False):
            return
        total = self.v_splitter.height()
        if total <= 0:
            return
        header = int(total * 0.62)
        self.v_splitter.setSizes([header, max(1, total - header)])

    def _apply_rail_metrics(self):
        """Cover e rail seguono l'altezza del blocco header."""
        if not hasattr(self, "rail_widget") or not hasattr(self, "cover_image"):
            return
        header_h = 0
        if hasattr(self, "v_splitter"):
            sizes = self.v_splitter.sizes()
            if sizes and sizes[0] > 0:
                header_h = sizes[0]
        if header_h <= 0 and hasattr(self, "upper_widget"):
            header_h = self.upper_widget.height()
        if header_h <= 0:
            header_h = int(self.height() * 0.54)
        self._last_header_height = header_h

        compact = header_h < 430
        icon_btn = 44 if compact else 50
        icon_px = 30 if compact else 36
        play_h = 40 if compact else 46
        spacing = 8 if compact else 12
        chrome = 118 if compact else 158  # icone + play + playtime + spaziature

        cover_h = int(max(150, min(392, header_h - chrome)))
        cover_w = int(round(cover_h * 0.68))
        rail_w = cover_w + 26

        if abs(self.cover_image.height() - cover_h) > 4:
            self.cover_image.setFixedSize(cover_w, cover_h)
        if abs(self.rail_widget.width() - rail_w) > 4:
            self.rail_widget.setFixedWidth(rail_w)

        if self.btn_steam_store.width() != icon_btn:
            for btn in (self.btn_steam_store, self.btn_gog_store):
                btn.setFixedSize(icon_btn, icon_btn)
                btn.setIconSize(QSize(icon_px, icon_px))
        if self.btn_play_main.height() != play_h:
            self.btn_play_main.setFixedHeight(play_h)
        if self.right_header_layout.spacing() != spacing:
            self.right_header_layout.setSpacing(spacing)

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, "bg_layer"):
            self.bg_layer.schedule_render()
            self._apply_search_contrast()
            QTimer.singleShot(0, self._on_layout_settle)

    # ── binding legacy ───────────────────────────────────────────────

    def _bind_widgets(self):
        self.widgets.search_input = self.search_input
        self.widgets.completer_model = self.completer_model
        self.widgets.completer = self.completer
        self.widgets.db_progress = self.db_progress
        self.widgets.content_splitter = self.content_splitter
        self.widgets.sidebar_widget = self.sidebar_widget
        self.widgets.lib_search = self.lib_search
        self.widgets.header_icon_labels = self.header_icon_labels
        self.widgets.lib_list = self.lib_list
        self.widgets.center_panel = self.center_panel
        self.widgets.lbl_game_title = self.lbl_game_title
        self.widgets.btn_play_main = self.btn_play_main
        self.widgets.btn_steam_optional = self.btn_steam_optional
        self.widgets.lbl_playtime = self.lbl_playtime
        self.widgets.lbl_game_version = self.lbl_game_version
        self.widgets.btn_version_status = self.btn_version_status
        self.widgets.lbl_pegi = self.lbl_pegi
        self.widgets.lbl_esrb = self.lbl_esrb
        self.widgets.version_status_panel = self.version_status_panel
        self.widgets.vs_rows = self.vs_rows
        self.widgets.vs_alert = self.vs_alert
        self.widgets.vs_subtext = self.vs_subtext
        self.widgets.vs_meta = self.vs_meta
        self.widgets.btn_vs_refresh = self.btn_vs_refresh
        self.widgets.btn_vs_steamdb = self.btn_vs_steamdb
        self.widgets.desc_display = self.desc_display
        self.widgets.cover_image = self.cover_image
        self.widgets.btn_steam_store = self.btn_steam_store
        self.widgets.btn_gog_store = self.btn_gog_store
        self.widgets.task_panel = self.task_panel
        self.widgets.task_label = self.task_label
        self.widgets.task_progress = self.task_progress
        self.widgets.btn_pause_dl = self.btn_pause_dl
        self.widgets.btn_stop_dl = self.btn_stop_dl
        self.widgets.req_display = self.req_display
        self.widgets.info_display = self.info_display
        self.widgets.links_scroll = self.links_scroll
        self.widgets.links_scroll_content = self.links_scroll_content
        self.widgets.links_container = self.links_container
        self.widgets.v_splitter = self.v_splitter
        self.widgets.lbl_disclaimer = self.lbl_disclaimer
