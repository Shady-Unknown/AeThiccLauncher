"""Main window shell: layout, tabs, theme and shutdown orchestration.

Domain behaviour lives in the cohesive mixins under ``ui/controllers``.
"""

import sys
import os
import base64
import tempfile
import json
import shutil
import subprocess
import time
import requests
#import traceback
#import random
#from datetime import datetime
import re
import html
from PyQt6.QtCore import Qt, QUrl, QTimer #QSize, pyqtSignal, QThread, QStringListModel
from PyQt6.QtGui import QColor, QDesktopServices, QPixmap, QIcon #QPainter, QPainterPath, QIcon
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QTabWidget, QGraphicsDropShadowEffect,
                             QPushButton,
                             QLabel, QMenuBar, QDialog, QComboBox, QMessageBox,
                             QMenu, QFileDialog, QInputDialog) #QHBoxLayout, QLineEdit, QTextEdit,
                             #QScrollArea, QColorDialog,
                             #QFormLayout, QListWidget, QStackedWidget, QFileDialog, 
                             #QListWidgetItem,  QSpinBox, 
                             #QSplitter, QSizeGrip, QCompleter, QProgressBar,  QFrame 
#from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from PyQt6.QtNetwork import QNetworkCookie

from ui.tabs.downloads_tab import DownloadManagerTab
from network.scraper import SteamRIPScraper
#from core.installer import GameInstaller
from managers.save_manager import SavegamesManager, safe_game_folder_name
from managers.playtime_manager import PlaytimeManager
from ui.tabs.badges_view import BadgesView
from ui.tabs.savegames_tab import SavegamesTab
from ui.tabs.stats_tab import StatsTab
from core import config as style_settings
from core.config import get_base_dir
from core.mini_cover_cache import MiniCoverManager

from ui.dialogs.orphan_dialog import OrphanDialog
from ui.shell.widgets import WindowWidgets
#from ui.dialogs.linux_wrapper_dialog import LinuxWrapperDialog
#from ui.widgets.profile_card import ProfileCard
#from ui.dialogs.steam_account_dialog import SteamAccountDialog
#from ui.dialogs.password_dialog import PasswordDialog
from network.store_links_worker import StoreLinksWorker
from network.page_freshness_worker import PageFreshnessWorker, PageHeadersWorker, VersionStatusWorker

_PAGE_TTL = 6 * 3600  # 6 ore: soglia dopo cui un link è considerato potenzialmente stale
from network.sales_worker import SalesWorker
from ui.styles.sale_style import SaleStyle, SALE_GREEN, SALE_GLOW_CSS
from ui.dialogs.settings_dialog import SettingsDialog
from managers.library_manager import LibraryManager, is_local_install
from managers.igdb_details_cache import IgdbDetailsCache
from managers.igdb_batch_manager import IGDBBatchManager
from managers.stats_manager import StatsManager
from managers.achievements_manager import AchievementsManager
from ui.widgets.achievement_toast import AchievementToastManager
from network.igdb_details_worker import IGDBDetailsWorker
from managers.background_scanner import BackgroundScanner
from core.game_launcher import GameLauncher
from core.installer_flow import InstallerFlowCoordinator
from core import platform_info, steam_integration
from core.events import (
    EVENT_BACKUP_CREATED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_LAUNCHER_START,
    EVENT_PRICE_REFRESHED,
    EVENT_SESSION_END,
)
from core.gold_tier import GoldTierManager
from ui.styles.gold_tier_style import GoldTierStyle, GOLD_PRIMARY
from core.version_status import (
    compute_version_state,
    STATE_UPDATE_AVAILABLE,
    STATE_STEAMRIP_OK_STEAM_AHEAD,
    STATE_UPDATED,
    STATE_VERSION_ONLY,
    STATE_UNKNOWN,
)
from network.steam_build_worker import SteamBuildWorker

from ui.dialogs.steam_restart_dialog import SteamRestartDialog
from ui.styles.structural_theme import StructuralTheme
from core.cover_cache import CoverCache

# --- APP WINDOW PRINCIPALE ---

from ui.controllers.base import BaseController
from ui.controllers.catalog import CatalogController
from ui.controllers.library import LibraryController
from ui.controllers.detail import GameDetailController
from ui.controllers.version import VersionController
from ui.controllers.downloads import DownloadsController
from ui.controllers.session import SessionController
from ui.controllers.settings import SettingsController


class AppWindow(
    QMainWindow,
    BaseController,
    CatalogController,
    LibraryController,
    GameDetailController,
    VersionController,
    DownloadsController,
    SessionController,
    SettingsController,
):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ae Thicc Launcher")
        self.resize(1100, 750)
        self.setWindowFlags(Qt.WindowType.Window)

        from core.paths import PathManager
        from core.app_context import AppContext

        self.context = AppContext()
        self.app_config = self.context.config
        self.temp_folder = self.app_config.get("default_temp_folder") or str(PathManager.get_cache_dir())

        self.current_game_data = {}
        self.installed_games = {}
        self.current_game_data = {}
        self.installed_games = {}
        self.is_task_running = False
        self._pending_auto_populate_folder = None
        self._update_check_game = None
        self.store_worker = None

        self.offline_cache_file = str(PathManager.get_dbs_dir() / "offline_cache.json")

        # --- CARICAMENTO DELLA CACHE IMMORTALE ---
        self.session_cache = {}
        if os.path.exists(self.offline_cache_file):
            try:
                with open(self.offline_cache_file, 'r', encoding='utf-8') as f:
                    self.session_cache = json.load(f)
                print(f"[App] Offline cache caricata: {len(self.session_cache)} schede gioco.")
            except Exception: pass

        self.master_catalog = self.context.master_catalog
        self.events = self.context.events
        self.tasks = self.context.tasks
        self._theme = StructuralTheme(self)
        self._active_sales_workers = []
        self._active_sgdb_workers = []
        self._active_page_workers = []
        self._sgdb_inflight_titles = set()
        self._version_status_backfill = set()
        self._active_steam_build_workers = []
        self._steam_build_queue = []
        self._steam_build_inflight = set()
        self._version_status_state = None
        self._version_status_current_title = ""
        self._current_web_version = ""
        self._version_status_steamdb_url = ""
        self._version_status_app_id = ""
        self._version_panel_open = False
        self._version_status_rendered_title = ""
        self._pending_banner = None
        self._cover_fallback_html = ""
        self._cover_fallback_title = ""
        self._cover_fallback_used = False
        
        self.scraper = SteamRIPScraper(self)
        self.scraper.extract_started.connect(self.on_extraction_started)
        self.scraper.extract_ready.connect(self.display_game_data)
        self.scraper.error_occurred.connect(self.handle_error)
        self.scraper.download_requested_signal.connect(self.handle_webengine_download)
        self.scraper.catalog_ready.connect(self._on_catalog_ready)
        self.scraper.sgdb_assets_ready.connect(self._on_sgdb_assets_ready)

        self.bg_scraper = SteamRIPScraper(self)
        self.bg_scraper.silent_update_ready.connect(self._on_silent_update_ready)
        
        self.bg_update_queue = []
        self.is_bg_checking = False
        self.download_queue = []
        self.active_downloader = None
        
        self.global_cookies = {}
        self.main_profile = QWebEngineProfile.defaultProfile()
        self.cookie_store = self.main_profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self._on_global_cookie_added)
        self.cookie_store.loadAllCookies()

        from managers.discovery_manager import DiscoveryManager
        self.discovery_manager = DiscoveryManager(self)
        self.discovery_manager.scrape_requested.connect(self.scraper.fetch_cover)
        self.scraper.cover_fetched.connect(self.discovery_manager.on_scrape_cover_fetched)
        self.discovery_manager.error_occurred.connect(self._on_discovery_error)

        self.init_ui()
        # Instanziamo i manager UNA sola volta: il vecchio blocco duplicato
        # sovrascriveva le istanze e rendeva imprevedibile la connessione dei segnali.
        self.lib_manager = LibraryManager(self)
        self.bg_scanner = BackgroundScanner(self)
        self.game_launcher = GameLauncher(self)
        self.installer_flow = InstallerFlowCoordinator(self)

        # Stats engine (S1): reads local history only; the UI arrives in S2.
        self.stats_manager = StatsManager(
            state_store=self.context.state_store,
            installed_games=self.installed_games,
            catalog=self.master_catalog,
            details=IgdbDetailsCache,
            gold_tier=lambda title: (
                self.master_catalog.is_gold_tier(title)
                or GoldTierManager.is_gold_tier(title)
            ),
            steam_stats=lambda: self.playtime_manager.load_steam_stats_cache(),
            config=lambda: self.app_config,
        )
        self.stats_manager.bind(self.events)

        self.achievements_manager = AchievementsManager(
            state_store=self.context.state_store,
            installed_games=self.installed_games,
            stats_provider=lambda: self.stats_manager.snapshot(),
            config=lambda: self.app_config,
            events=self.events,
            parent=self,
        )
        self.toast_manager = AchievementToastManager(
            self,
            theme_provider=lambda: self.app_config.get("theme", {}),
        )
        self.achievements_manager.achievement_unlocked.connect(
            self.toast_manager.show_badge
        )
        self.achievements_manager.retroactive_unlocked.connect(
            self.toast_manager.show_summary
        )
        self.toast_manager.open_badges.connect(self.open_badges)

        self.stats_tab = StatsTab(self, self.stats_manager)
        self.tabs.addTab(self.stats_tab, "📊 Stats")
        self.achievements_tab = BadgesView(self, self.achievements_manager)
        self.tabs.addTab(self.achievements_tab, "🏆 Achievements")
        QTimer.singleShot(3000, self._start_achievements)

        # Background IGDB backfill for the installed library, prioritised by
        # playtime. One batch per launch keeps the request budget small.
        self.igdb_batch_manager = IGDBBatchManager(
            installed_games=self.installed_games,
            catalog=self.master_catalog,
            details=IgdbDetailsCache,
            events=self.events,
            max_titles=50,
            parent=self,
        )
        QTimer.singleShot(20000, self.igdb_batch_manager.start)

        self.apply_current_theme(self.app_config["theme"])

        # Ponte temporaneo per i manager legacy che ricevono ancora la window.
        # Rimosso in R4 insieme al binding implicito dei widget.
        self.context.bind_window(self)

        # Signal routing
        self.bg_scanner.update_found.connect(self._update_library_item_from_bg)
        self.installer_flow.install_complete.connect(self._on_install_flow_complete)
        self.installer_flow.task_update.connect(self._on_task_update)

        # Heavy startup work (library scan, stats refresh, discovery, catalog
        # fetch) is deferred until the window is visible: see start_background_init().
        self._background_init_done = False

        QTimer.singleShot(3000, self.check_initial_setup)
        QTimer.singleShot(10000, self.start_savegames_timer)

        # Refresh automatico del lato Steam (build) ogni 6h + primo giro all'avvio
        self._steam_build_timer = QTimer(self)
        self._steam_build_timer.setInterval(6 * 60 * 60 * 1000)
        self._steam_build_timer.timeout.connect(self._auto_refresh_steam_builds)
        self._steam_build_timer.start()
        QTimer.singleShot(8000, self._auto_refresh_steam_builds)

        # The window is fully built: announce the launch after the core
        # managers exist. Stats/achievements will subscribe in S0/S4.
        self.events.emit_event(
            EVENT_LAUNCHER_START,
            os=sys.platform,
            flatpak=platform_info.is_flatpak(),
        )

    def start_background_init(self):
        """Run the heavy startup work once the window is already visible."""
        if getattr(self, "_background_init_done", False):
            return
        self._background_init_done = True
        t0 = time.monotonic()

        self.lib_manager.scan_library()
        # The first scan is a baseline and emits no install events, so the
        # Stats cache built before it would stay empty. Invalidate and refresh.
        self.stats_manager.invalidate()
        if hasattr(self, 'stats_tab'):
            self.stats_tab.refresh()
        if hasattr(self, 'tab_discovery'):
            self.tab_discovery.refresh_library()

        # --- SYNC INIZIALE DEL PLAY TIME CON STEAM (merge massimo) ---
        if hasattr(self, 'playtime_manager'):
            try:
                self.playtime_manager.sync_all()
                self.playtime_manager.try_flush_pending()
            except Exception as e:
                print(f"[Playtime] Errore sync iniziale: {e}")

        # --- LOGICA DI COOLDOWN DEL MASTER CATALOG (12 ORE) ---
        if self._should_update_catalog():
            self.fetch_master_catalog()
        else:
            print("[App] Master Catalog recente. Salto il download e avvio lo scanner background.")
            # Lo scanner usa QWebEngine sul thread grafico: lo rimandiamo per
            # non bloccare i primi secondi di utilizzo della UI.
            self._schedule_background_scan()

        elapsed = (time.monotonic() - t0) * 1000.0
        print(f"[STARTUP] background init: {elapsed:.0f}ms")

    def _on_discovery_error(self, message):
        """Non-fatal network failure: surface it instead of failing silently."""
        text = str(message or "").strip()
        print(f"[Network] {text}")
        if getattr(self, "_network_notice_shown", False):
            return
        self._network_notice_shown = True
        if hasattr(self, "tab_discovery") and hasattr(self.tab_discovery, "show_network_notice"):
            self.tab_discovery.show_network_notice(text)

    def _start_achievements(self):
        try:
            # Make sure progress is computed from the library scanned at
            # startup, not from the empty snapshot cached before the scan.
            self.stats_manager.invalidate()
            self.achievements_manager.start()
            if hasattr(self, "achievements_tab"):
                self.achievements_tab.refresh()
        except Exception as exc:
            print(f"[Achievements] startup evaluation failed: {exc}")

    def open_badges(self):
        self.tabs.setCurrentWidget(self.achievements_tab)
        if hasattr(self.achievements_tab, "refresh"):
            self.achievements_tab.refresh()

    def _on_install_flow_complete(self, path):
        pass

    def _on_task_update(self, text, style):
        self.task_label.setText(text)
        if style:
            self.task_label.setStyleSheet(style)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 0, 10, 10) # Rimosso il margine superiore per il logo
        main_layout.setSpacing(0) # Eliminato lo spazio vuoto tra logo e tab system
        
        # --- MENU BAR ---
        self.menu_bar = QMenuBar()
        
        # Menu Settings
        settings_menu = self.menu_bar.addMenu("Settings")
        action_open_settings = settings_menu.addAction("Configuration Panel")
        action_open_settings.triggered.connect(self.open_settings_dialog)
        
        # Menu Library
        library_menu = self.menu_bar.addMenu("Library")
        action_rescan = library_menu.addAction("Rescan Game Libraries")
        action_rescan.triggered.connect(lambda: (self.lib_manager.scan_library(), self.tab_discovery.refresh_library() if hasattr(self, 'tab_discovery') else None))
        
        # --- IL NUOVO TASTO VA QUI! Immediatamente sotto la creazione del menu ---
        action_update_db = library_menu.addAction("🔄 Update Database (Fetch Catalog)")
        action_update_db.triggered.connect(self.fetch_master_catalog)

        if sys.platform == "win32":
            library_menu.addSeparator()
            action_add_all_steam = library_menu.addAction(
                "🎮 Add All to Steam Library"
            )
            # game_launcher is created after init_ui, so resolve it lazily.
            action_add_all_steam.triggered.connect(
                lambda: self.game_launcher.sync_all_games_to_steam()
            )
        
        self.setMenuBar(self.menu_bar)

        # --- LOGO (Overlay) ---
        self.logo_label = QLabel(self)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setContentsMargins(0, 0, 0, 0)
        logo_path = os.path.join(get_base_dir(), ".media", "ATL_Logo.png")
        if os.path.exists(logo_path):
            pixmap = QPixmap(logo_path)
            if not pixmap.isNull():
                target_height = 80
                scaled_pixmap = pixmap.scaledToHeight(target_height, Qt.TransformationMode.SmoothTransformation)
                self.logo_label.setPixmap(scaled_pixmap)
                self.logo_label.setFixedSize(scaled_pixmap.width(), target_height)

        # --- TAB SYSTEM ---
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Instantiation
        self.dl_manager = DownloadManagerTab(self.app_config['theme'])
        self.dl_manager.set_state_file(os.path.join(self.temp_folder, "dl_state.json"))
        
        from ui.tabs.explorer_tab import ExplorerTab
        self.widgets = WindowWidgets()
        self.tab_explorer = ExplorerTab(self)
        self.cover_image.loadFinished.connect(self._on_cover_page_loaded)
        
        # Adding tabs
        from ui.tabs.discovery_tab import DiscoveryTab
        self.tab_discovery = DiscoveryTab(self)
        self.tabs.insertTab(0, self.tab_discovery, "🎬 Discovery")
        self.tabs.addTab(self.tab_explorer, "🌐 Explorer")
        self.tabs.addTab(self.dl_manager, "⬇️ Downloads")

        self.savegames_manager = SavegamesManager(self.app_config, self.installed_games, self)
        self.savegames_tab = SavegamesTab(self.app_config, self.installed_games, self.savegames_manager, self)
        self.tabs.addTab(self.savegames_tab, "💾 Saves Manager")
        self.playtime_manager = PlaytimeManager(self)

        self.savegames_timer = QTimer(self)
        self.savegames_timer.timeout.connect(self._perform_scheduled_savegame_sync)
        
        # Connessione Segnali del Manager
        self.dl_manager.progress_updated.connect(self._sync_dl_progress)
        self.dl_manager.download_started.connect(self._sync_dl_started)
        self.dl_manager.download_completed.connect(self._sync_dl_completed)
        self.dl_manager.download_error.connect(self._sync_dl_error)
        self.dl_manager.queue_empty.connect(self._sync_dl_empty)
        self.dl_manager.cf_bypass_requested.connect(self._trigger_cf_bypass)
        self.dl_manager.gate_fallback_requested.connect(self._trigger_gate_fallback)
        self.dl_manager.resume_detected.connect(self._on_download_resumed)
        self.dl_manager.extraction_cancel_requested.connect(self._cancel_extraction)
        self.scraper.cf_bypassed.connect(self._on_cf_bypassed)
        self.dl_manager.pause_state_changed.connect(self._sync_global_pause_state)

        # Recovery pending extraction al primo idle
        QTimer.singleShot(1500, self._recover_pending_extractions)

    def toggle_sidebar(self):
        if self.lib_container.isVisible():
            self.lib_container.hide()
            self.btn_toggle_sidebar.setText("▶")
            # Forziamo lo splitter a chiudersi fino a occupare solo i 20px della linguetta.
            # Il resto dello spazio della finestra viene assegnato interamente al pannello centrale.
            self.content_splitter.setSizes([20, self.width() - 20])
        else:
            self.lib_container.show()
            self.btn_toggle_sidebar.setText("◀")
            # Aumentiamo a 300px per dare più spazio ai nomi e ai pallini indicatori
            self.content_splitter.setSizes([300, self.width() - 300])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'logo_label') and self.logo_label.pixmap():
            # Centra orizzontalmente
            x = (self.width() - self.logo_label.width()) // 2
            y = 0 # Offset Y: spostato in alto per evitare che le tab lo taglino troppo
            self.logo_label.move(x, y)
            
            # Gestione asse Z (Ordine di overlay)
            if hasattr(self, 'menu_bar'):
                self.menu_bar.lower()  # Menu dietro al logo
            self.logo_label.raise_()   # Logo davanti al menu...
            
            central_widget = self.centralWidget()
            if central_widget:
                central_widget.raise_() # ...ma le Tab (nel centralWidget) davanti al logo!

    def closeEvent(self, event):
        print(f"[App] Spegnimento sicuro in corso... (spontaneous={getattr(event, 'spontaneous', lambda: '?')()})")
        
        # CONTROLLO MONITORAGGI ATTIVI
        if hasattr(self, 'game_launcher'):
            active = [m for m in self.game_launcher.active_monitors if m.isRunning()]
            if active:
                reply = QMessageBox.warning(
                    self, "Game Running",
                    "A game is still running. If you close the launcher, the automatic save backup won't be executed.\n\nClose anyway?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.No:
                    event.ignore()
                    return

        # 1. Ferma eventuali download in corso attendendo max 1 secondo
        if hasattr(self, 'dl_manager'):
            if self.dl_manager.active_worker:
                self.dl_manager.active_worker.cancel()
                self.dl_manager.active_worker.wait(1000)
            self.dl_manager.save_state() 
            
        # 2. Chiudi il browser fantasma di QtWebEngine
        if hasattr(self, 'scraper') and hasattr(self.scraper, 'browser'):
            self.scraper.browser.force_close = True
            self.scraper.browser.close()
            
        # 3. Flush any pending Master Catalog writes
        if hasattr(self, 'master_catalog'):
            self.master_catalog.save_immediate()

        # 3b. Ferma i thread del Discovery manager
        if hasattr(self, 'discovery_manager'):
            try:
                self.discovery_manager.shutdown()
            except Exception as e:
                print(f"[Discovery] shutdown error: {e}")

        if hasattr(self, 'igdb_batch_manager'):
            try:
                self.igdb_batch_manager.stop()
            except Exception as e:
                print(f"[IGDBBatch] shutdown error: {e}")

        # 4. Flush playtime pending (solo se Steam è chiuso)
        if hasattr(self, 'playtime_manager'):
            try:
                self.playtime_manager.try_flush_pending()
            except Exception as e:
                print(f"[Playtime] Errore flush alla chiusura: {e}")

        event.accept()

        # 4. LA GHIGLIOTTINA: Uccide istantaneamente il processo bypassando il Garbage Collector.
        # Evita il deadlock di curl_cffi e i Core Dump di QtWebEngine alla chiusura.
        os._exit(0)


# Read-only compatibility proxies for the legacy ``self.<widget>`` access.
# New code should use ``self.widgets.<widget>``.
for _widget_name in WindowWidgets.__slots__:
    setattr(
        AppWindow,
        _widget_name,
        property(lambda self, _name=_widget_name: getattr(self.widgets, _name)),
    )
