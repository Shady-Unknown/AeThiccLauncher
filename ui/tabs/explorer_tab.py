import os
import sys
from PyQt6.QtCore import Qt, QStringListModel, QSize
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QCompleter, 
                             QProgressBar, QSplitter, QLabel, QListWidget, QPushButton, 
                             QTextEdit, QScrollArea, QFrame)
from PyQt6.QtGui import QCursor, QIcon, QColor

from PyQt6.QtWebEngineWidgets import QWebEngineView
from core.config import get_base_dir
from ui.widgets.status_pill import StatusPill

class ExplorerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self.widgets = main_window.widgets
        self.init_ui()

    def init_ui(self):
        explorer_layout = QVBoxLayout(self)
        explorer_layout.setContentsMargins(0, 10, 0, 0)
        
        # --- SEARCH LAYER ---
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit() 
        self.search_input.setPlaceholderText("Paste SteamRIP URL or type game name...")
        
        # --- SETUP-AUTOCOMPLETAMENTO ---
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
        
        # Aggiungiamo i widget al layout UNA SOLA VOLTA
        search_layout.addWidget(self.search_input)
        explorer_layout.addLayout(search_layout)
        
        # --- LOADING BAR (Animata e Indeterminata) ---
        self.db_progress = QProgressBar()
        self.db_progress.setRange(0, 0) # Modalità indeterminata (animazione di caricamento)
        self.db_progress.setFixedHeight(3) # Super sottile, stile "Cyberpunk"
        self.db_progress.setTextVisible(False)
        self.db_progress.setStyleSheet(f"QProgressBar {{ background-color: transparent; border: none; }} QProgressBar::chunk {{ background-color: {self.mw.app_config['theme'].get('progress_bar_color', '#00ff88')}; }}")
        self.db_progress.hide()
        explorer_layout.addWidget(self.db_progress)
        
        # --- SPLITTER: Libreria a SX | Contenuto a DX ---
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        explorer_layout.addWidget(self.content_splitter, stretch=1)
        
        # --- SIDEBAR LIBRERIA ---
        self.sidebar_widget = QWidget()
        self.sidebar_widget.setObjectName("SidebarWidget")
        
        lib_layout = QVBoxLayout(self.sidebar_widget)
        lib_layout.setContentsMargins(0, 0, 5, 0)
        
        self.lib_search = QLineEdit()
        self.lib_search.setPlaceholderText("Search library...")
        self.lib_search.textChanged.connect(lambda text: self.mw.lib_manager.filter_library(text))
        lib_layout.addWidget(self.lib_search)
        
        # --- INTESTAZIONE CON ICONE INDICATORI ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(8, 8, 8, 8)
        header_layout.setSpacing(0)

        # Batch Steam (solo Windows): aggiunge tutti i giochi mancanti con un solo restart
        media_dir = os.path.join(get_base_dir(), ".media")
        self.btn_add_all_steam = QPushButton()
        self.btn_add_all_steam.setObjectName("BtnAddAllSteam")
        self.btn_add_all_steam.setFixedSize(26, 26)
        self.btn_add_all_steam.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_add_all_steam.setToolTip("Add all installed games to Steam (single Steam restart)")
        self.btn_add_all_steam.setIcon(QIcon(os.path.join(media_dir, "steam.svg")))
        self.btn_add_all_steam.setIconSize(QSize(20, 20))
        self.btn_add_all_steam.setStyleSheet("""
            QPushButton#BtnAddAllSteam {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 0px;
            }
            QPushButton#BtnAddAllSteam:hover {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.25);
            }
        """)
        # Nota: game_launcher viene creato dopo init_ui(), quindi risolviamo l'attributo al click
        self.btn_add_all_steam.clicked.connect(lambda: self.mw.game_launcher.sync_all_games_to_steam())
        self.btn_add_all_steam.setVisible(sys.platform == 'win32')
        header_layout.addWidget(self.btn_add_all_steam)
        header_layout.addSpacing(6)
        
        # Colonna 1: etichetta "Game"
        self.lbl_library_header = QLabel("Library")
        self.lbl_library_header.setStyleSheet(f"color: {self.mw.app_config['theme']['text_muted']}; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;")
        header_layout.addWidget(self.lbl_library_header, 1)  # Expandable
        
        # Spacing minimo
        header_layout.addSpacing(4)
        
        # Colonne fisse per le icone: 32px ciascuna + 4px spacing
        indicators_info = [
            ('update_bw.svg', 'Update Status'),
            ('json_bw.svg', 'Metadata'),
            ('save_bw.svg', 'Save Path'),
            ('steam_bw.svg', 'Steam')
        ]
        
        self.header_icon_labels = {}
        icon_color = self.mw.app_config['theme']['text_muted']
        for icon_file, tooltip_text in indicators_info:
            lbl = QLabel()
            lbl.setFixedSize(24, 32)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            lbl.setToolTip(tooltip_text)
            lbl.setCursor(QCursor(Qt.CursorShape.WhatsThisCursor))
            lbl.setTextFormat(Qt.TextFormat.RichText)  # Abilita rendering HTML
            # Carica l'SVG colorato (compattato per allinearsi ai pallini)
            svg_html = self.mw._load_colored_svg(icon_file, icon_color, 20)
            lbl.setText(svg_html)
            header_layout.addWidget(lbl)
            if icon_file != 'steam_bw.svg':  # Non aggiungere spacing dopo l'ultimo
                header_layout.addSpacing(2)
            self.header_icon_labels[icon_file] = lbl
            
        # Aggiungi uno spazio extra a destra che corrisponde (approssimativamente) alla larghezza della scrollbar
        # per allineare le icone con i pallini della QListWidget. Tipicamente la scrollbar è ~14-16px.
        from PyQt6.QtWidgets import QSpacerItem, QSizePolicy
        self.header_scrollbar_spacer = QSpacerItem(16, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        header_layout.addItem(self.header_scrollbar_spacer)
        
        self.header_widget = QWidget()
        self.header_widget.setLayout(header_layout)
        self.header_widget.setStyleSheet(f"background-color: {self.mw.app_config['theme']['surface_panel_purple']}; border-bottom: 2px solid {self.mw.app_config['theme']['main_accent_color']}; border-top-left-radius: 4px; border-top-right-radius: 4px;")
        self.header_widget.setFixedHeight(48)
        lib_layout.addWidget(self.header_widget)
        
        self.lib_list = QListWidget()
        self.lib_list.setObjectName("LibraryList")
        self.lib_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.lib_list.customContextMenuRequested.connect(self.mw.show_library_context_menu)
        self.lib_list.itemClicked.connect(self.mw.on_library_item_clicked)
        # Assicuriamoci di collegare anche il doppio click!
        self.lib_list.itemDoubleClicked.connect(self.mw.on_library_item_double_clicked) 
        # Forza la scrollbar in modo che non crei scatti nell'allineamento quando appare/scompare
        self.lib_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        lib_layout.addWidget(self.lib_list)
        
        self.content_splitter.addWidget(self.sidebar_widget)
        
        # --- PANNELLO CENTRALE ---
        self.center_panel = QWidget()
        self.center_panel.setObjectName("CentralContentPanel")
        center_layout = QVBoxLayout(self.center_panel)
        center_layout.setContentsMargins(10, 10, 10, 10)
        
        self.upper_layout = QHBoxLayout()
        self.text_header_layout = QVBoxLayout()
        # FIX: Sbatte tutto violentemente in alto, niente più spazi vuoti se ingrandisci la finestra!
        self.text_header_layout.setAlignment(Qt.AlignmentFlag.AlignTop) 
        
        # --- 1. RIGA IN ALTO: TITOLO + TASTO PLAY ---
        self.title_play_layout = QHBoxLayout()
        
        self.lbl_game_title = QLabel("")
        self.lbl_game_title.setObjectName("GameTitle")
        self.lbl_game_title.setWordWrap(True)
        # FIX: Diciamo al titolo di NON espandersi in verticale
        from PyQt6.QtWidgets import QSizePolicy
        self.lbl_game_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        
        self.title_play_layout.addWidget(self.lbl_game_title)
        self.title_play_layout.addStretch() 
        
        self.btn_play_main = QPushButton("▶ PLAY")
        self.btn_play_main.setObjectName("BtnPlayMain")
        self.btn_play_main.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_play_main.setFixedSize(120, 40)
        self.btn_play_main.hide()
        
        self.play_buttons_layout = QVBoxLayout()
        self.play_buttons_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.play_buttons_layout.setContentsMargins(0, 0, 0, 0)
        self.play_buttons_layout.addWidget(self.btn_play_main)
        
        self.btn_steam_optional = QPushButton("➕ Add to Steam (Optional)")
        self.btn_steam_optional.setObjectName("BtnSteamOptional")
        self.btn_steam_optional.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_steam_optional.hide()
        self.play_buttons_layout.addWidget(self.btn_steam_optional)

        self.lbl_playtime = QLabel("")
        self.lbl_playtime.setObjectName("LblPlaytime")
        self.lbl_playtime.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_playtime.setStyleSheet("color: #8a7fa4; font-size: 12px; font-weight: bold;")
        self.lbl_playtime.hide()
        self.play_buttons_layout.addWidget(self.lbl_playtime)
        
        self.title_play_layout.addLayout(self.play_buttons_layout)
        
        self.text_header_layout.addLayout(self.title_play_layout)
        
        # --- 2. RIGA DI MEZZO: VERSIONE + STATO ---
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
        self.version_row_layout.addWidget(self.btn_version_status, 0, Qt.AlignmentFlag.AlignTop)

        # --- 2a. PEGI / ESRB (IGDB): badge compatti accanto allo stato ---
        _theme = self.mw.app_config.get('theme', {}) if hasattr(self.mw, 'app_config') else {}
        _badge_text = _theme.get('text_primary_white', '#ffffff')
        _badge_accent = _theme.get('main_accent_color', '#733d91')
        _badge_qss = (
            f"QLabel {{ color: {_badge_text}; background: rgba(255,255,255,0.06);"
            f" border: 1px solid {_badge_accent}; border-radius: 4px;"
            f" padding: 1px 7px; font-size: 11px; font-weight: bold; }}"
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

        # --- 2b. PANNELLO VERSION STATUS (inline, collassabile) ---
        self.version_status_panel = QFrame()
        self.version_status_panel.setObjectName("VersionStatusPanel")
        self.version_status_panel.setMaximumWidth(460)
        self.version_status_panel.setStyleSheet(f"""
            QFrame#VersionStatusPanel {{
                background-color: {self.mw.app_config['theme'].get('surface_panel_purple', '#2b1a42')};
                border: 1px solid {self.mw.app_config['theme'].get('main_accent_color', '#733d91')};
                border-radius: 8px;
            }}
            QFrame#VersionStatusPanel QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        panel_layout = QVBoxLayout(self.version_status_panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        panel_layout.setSpacing(6)

        vs_title = QLabel("Version Status")
        vs_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffffff; background: transparent;")
        panel_layout.addWidget(vs_title)

        self.vs_rows = {}
        vs_grid = QGridLayout()
        vs_grid.setContentsMargins(0, 4, 0, 0)
        vs_grid.setHorizontalSpacing(12)
        vs_grid.setVerticalSpacing(4)
        vs_rows_def = (
            ("installed", "Installed Build"),
            ("posted", "SteamRIP Build"),
            ("steam", "Steam Latest Build"),
        )
        for row_idx, (row_key, row_label) in enumerate(vs_rows_def):
            name_lbl = QLabel(row_label)
            name_lbl.setStyleSheet("color: #a99bbf; font-size: 11px; background: transparent;")

            value_lbl = QLabel("—")
            value_lbl.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: bold; background: transparent;")
            value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            date_lbl = QLabel("")
            date_lbl.setStyleSheet("color: #8a7fa4; font-size: 10px; background: transparent;")
            date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            vs_grid.addWidget(name_lbl, row_idx, 0)
            vs_grid.addWidget(value_lbl, row_idx, 1)
            vs_grid.addWidget(date_lbl, row_idx, 2)
            self.vs_rows[row_key] = (value_lbl, date_lbl)

        panel_layout.addLayout(vs_grid)

        self.vs_alert = QLabel("")
        self.vs_alert.setWordWrap(True)
        self.vs_alert.setStyleSheet("font-size: 11px; background: transparent; color: #ffffff;")
        panel_layout.addWidget(self.vs_alert)

        self.vs_subtext = QLabel("")
        self.vs_subtext.setWordWrap(True)
        self.vs_subtext.setStyleSheet("font-size: 10px; background: transparent; color: #8a7fa4;")
        panel_layout.addWidget(self.vs_subtext)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 2, 0, 0)
        meta_row.setSpacing(8)
        self.vs_meta = QLabel("")
        self.vs_meta.setWordWrap(True)
        self.vs_meta.setStyleSheet("font-size: 10px; background: transparent; color: #8a7fa4;")
        meta_row.addWidget(self.vs_meta, 1)

        self.btn_vs_refresh = QPushButton("↻ Refresh")
        self.btn_vs_refresh.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_vs_refresh.setToolTip("Refresh the latest build available on Steam")
        self.btn_vs_refresh.setStyleSheet("""
            QPushButton {
                background: transparent; color: #a99bbf; border: 1px solid rgba(255,255,255,0.18);
                border-radius: 4px; padding: 3px 8px; font-size: 10px;
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
                background: rgba(255,255,255,0.04); color: #ffffff; border: 1px solid rgba(255,255,255,0.14);
                border-radius: 5px; padding: 5px 8px; font-size: 11px;
            }
            QPushButton:hover { background: rgba(255,255,255,0.10); border-color: rgba(255,255,255,0.35); }
        """)
        self.btn_vs_steamdb.clicked.connect(self.mw.open_version_status_steamdb)
        panel_layout.addWidget(self.btn_vs_steamdb)

        self.text_header_layout.addWidget(self.version_status_panel)
        self.version_status_panel.hide()

        # --- 3. NUOVO DIVISORE E TITOLO STATICO ---
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.Shape.HLine)
        self.divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.05); margin-top: 5px; margin-bottom: 5px;")
        self.text_header_layout.addWidget(self.divider)
        
                
        # --- 4. RIGA IN BASSO: DESCRIZIONE ---
        self.desc_display = QTextEdit()
        self.desc_display.setReadOnly(True)
        self.desc_display.setStyleSheet("background: transparent; border: none; font-size: 13px; color: #e0e0e0;")
        self.text_header_layout.addWidget(self.desc_display)
        
        self.upper_layout.addLayout(self.text_header_layout, stretch=4)
        
        # --- PANNELLO DESTRO: COVER GIGANTE E ICONE STORE ---
        self.right_header_layout = QVBoxLayout()
        self.right_header_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        
        self.cover_image = QWebEngineView()

        self.cover_image.setFixedSize(280, 410) 
        self.cover_image.page().setBackgroundColor(QColor(0, 0, 0, 0))

        self.right_header_layout.addWidget(self.cover_image)
        
        # 2. CONTENITORE ICONE STORE
        self.store_icons_layout = QHBoxLayout()
        self.store_icons_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter) # Perfettamente al centro!
        self.store_icons_layout.setSpacing(25) # Più spazio tra loro
        
        media_dir = os.path.join(get_base_dir(), ".media") 
        
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
        
        self.upper_layout.addLayout(self.right_header_layout)
        
        
        # --- TASK & DOWNLOAD BAR (Integrata in basso) ---
        self.task_panel = QWidget()
        self.task_panel.setFixedHeight(60) 
        self.task_panel.setStyleSheet(f"""
            QWidget {{ background-color: {self.mw.app_config['theme']['surface_panel_purple']}; border-radius: 6px; border: 1px solid rgba(255,255,255,0.1); }}
        """)
        task_layout = QHBoxLayout(self.task_panel)
        task_layout.setContentsMargins(15, 10, 15, 10) 

        self.task_label = QLabel("Idle: Ready for action")
        self.task_label.setStyleSheet(f"color: {self.mw.app_config['theme']['text_primary_white']}; font-weight: bold; border: none; background: transparent;")
        
        # ---> ECCO LA VARIABILE CHE ERA SPARITA! <---
        self.task_progress = QProgressBar()
        self.task_progress.setTextVisible(True)
        self.task_progress.setFixedHeight(22) 
        self.task_progress.setStyleSheet(f"""
            QProgressBar {{ border: 1px solid {self.mw.app_config['theme']['main_accent_color']}; border-radius: 5px; text-align: center; color: white; font-weight: bold; background: transparent; }}
            QProgressBar::chunk {{ background-color: {self.mw.app_config['theme'].get('progress_bar_color', '#00ff88')}; border-radius: 4px; }}
        """)
        self.task_progress.hide()

        # NUOVI TASTI CONTROLLO DOWNLOAD (Icone SVG)
        self.btn_pause_dl = QPushButton()
        self.btn_stop_dl = QPushButton()
        
        media_dir = os.path.join(get_base_dir(), ".media")
        
        # USIAMO IL NUOVO METODO PER DARE VITA ALLE ICONE
        self.icon_pause = self.mw._colorize_icon(os.path.join(media_dir, "pause_round.svg"))
        self.icon_play = self.mw._colorize_icon(os.path.join(media_dir, "play_round.svg"))
        self.icon_stop = self.mw._colorize_icon(os.path.join(media_dir, "stop_round.svg"))

        self.btn_pause_dl.setIcon(self.icon_pause)
        self.btn_stop_dl.setIcon(self.icon_stop)
        
        for btn in [self.btn_pause_dl, self.btn_stop_dl]:
            btn.setFixedSize(40, 40) 
            btn.setIconSize(QSize(32, 32)) 
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent; 
                    border: 1px solid {self.mw.app_config['theme']['main_accent_color']}; 
                    border-radius: 20px; 
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.15); 
                }}
            """)
            btn.hide()
            
        self.btn_pause_dl.clicked.connect(self.mw.toggle_dl_pause)
        self.btn_stop_dl.clicked.connect(self.mw.dl_manager.stop_current)

        task_layout.addWidget(self.task_label, stretch=4)
        task_layout.addWidget(self.task_progress, stretch=2) 
        task_layout.addSpacing(15) 
        task_layout.addWidget(self.btn_pause_dl)
        task_layout.addSpacing(5)  
        task_layout.addWidget(self.btn_stop_dl)
        
        # Le 3 Colonne
        self.columns_layout = QHBoxLayout()
        
        col1_layout = QVBoxLayout()
        lbl_req = QLabel("⚙️ SYSTEM REQUIREMENTS")
        lbl_req.setObjectName("LinksTitle")
        self.req_display = QTextEdit()
        self.req_display.setReadOnly(True)
        col1_layout.addWidget(lbl_req)
        col1_layout.addWidget(self.req_display)
        
        col2_layout = QVBoxLayout()
        lbl_info = QLabel("📊 GAME INFO")
        lbl_info.setObjectName("LinksTitle")
        self.info_display = QTextEdit()
        self.info_display.setReadOnly(True)
        col2_layout.addWidget(lbl_info)
        col2_layout.addWidget(self.info_display)
        
        col3_layout = QVBoxLayout()
        lbl_links = QLabel("⬇️ DOWNLOAD LINKS")
        lbl_links.setObjectName("LinksTitle")
        self.links_scroll = QScrollArea()
        self.links_scroll.setObjectName("DownloadScrollArea")
        self.links_scroll.setWidgetResizable(True)
        self.links_scroll_content = QWidget()
        self.links_scroll_content.setStyleSheet("background-color: transparent;")
        self.links_container = QVBoxLayout(self.links_scroll_content)
        self.links_container.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.links_scroll.setWidget(self.links_scroll_content)
        col3_layout.addWidget(lbl_links)
        col3_layout.addWidget(self.links_scroll)
        
        self.columns_layout.addLayout(col1_layout, stretch=1)
        self.columns_layout.addLayout(col2_layout, stretch=1)
        self.columns_layout.addLayout(col3_layout, stretch=1)

        # Creiamo due "scatole" per contenere i layout superiore e inferiore
        upper_widget = QWidget()
        upper_widget.setLayout(self.upper_layout)
        
        columns_widget = QWidget()
        columns_widget.setLayout(self.columns_layout)
        
        # Creiamo il divisore trascinabile!
        self.v_splitter = QSplitter(Qt.Orientation.Vertical)
        self.v_splitter.addWidget(upper_widget)
        self.v_splitter.addWidget(columns_widget)
        
        # Impostiamo le grandezze iniziali preferite (es. 450px sopra, 300px sotto)
        self.v_splitter.setSizes([450, 300]) 
        
        # Diamo alla maniglia un look discreto in tema cyberpunk
        self.v_splitter.setStyleSheet("""
            QSplitter::handle { 
                background-color: rgba(255,255,255,0.05); 
                height: 2px; 
                margin: 4px 0px; 
            }
        """)
        
        # Aggiungiamo lo splitter e la task bar fissa in basso al layout centrale
        center_layout.addWidget(self.v_splitter, stretch=1)
        center_layout.addWidget(self.task_panel)
        
        self.content_splitter.addWidget(self.center_panel)
        self.content_splitter.setSizes([250, 850]) 

        # --- ETHICAL PIRACY DISCLAIMER PANEL (Bottom Anchor) ---
        self.lbl_disclaimer = QLabel(
            "If you like the game and the software house / publisher aren't aggressive monetizers, "
            "if they are pro-users, if they support the customer, "
            f"<b style='color:{self.mw.app_config['theme']['bright_neon_accent']}; text-transform: uppercase;'>BUY THE GAME. THEY DESERVE IT.</b>"
        )
        self.lbl_disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_disclaimer.setWordWrap(True)
        self.lbl_disclaimer.setStyleSheet("font-size: 11px; color: #8a7fa4; padding: 4px 10px; background-color: rgba(0,0,0,0.1); border-radius: 4px; margin-top: 5px;")
        explorer_layout.addWidget(self.lbl_disclaimer)

        # Bind properties to main window
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



