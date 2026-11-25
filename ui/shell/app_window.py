"""Main window shell: layout, tabs, theme and shutdown orchestration.

Domain behaviour lives in the cohesive mixins under ``ui/controllers``.
"""
import logging

import sys
import os
import base64
import tempfile
import json
import shutil
import subprocess
import threading
import time
import requests
#import traceback
#import random
#from datetime import datetime
import re
import html
from PyQt6.QtCore import Qt, QUrl, QTimer, QThreadPool, pyqtSignal #QSize, pyqtSignal, QThread, QStringListModel
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
from core.naming import safe_game_folder_name
from managers.save_manager import SavegamesManager
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
from ui.dialogs.manual_save_path_dialog import ManualSavePathDialog
from ui.dialogs.settings_dialog import SettingsDialog
from ui.library_manager import LibraryManager, is_local_install
from managers.igdb_details_cache import IgdbDetailsCache
from managers.igdb_batch_manager import IGDBBatchManager
from managers.stats_manager import StatsManager
from managers.achievements_manager import AchievementsManager
from managers.state_backup_manager import StateBackupManager
from managers.backup_service import BackupService
from ui.widgets.achievement_toast import AchievementToastManager
from network.igdb_details_worker import IGDBDetailsWorker
from ui.background_scan_coordinator import BackgroundScanCoordinator
from ui.controllers.game_launcher import GameLauncher
from ui.controllers.installer_flow import InstallerFlowCoordinator
from core import platform_info, steam_integration
from core.events import (
    EVENT_ACHIEVEMENT_UNLOCKED,
    EVENT_BACKUP_CREATED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_LAUNCHER_START,
    EVENT_PREFS_CHANGED,
    EVENT_PRICE_REFRESHED,
    EVENT_REGION_CHANGED,
    EVENT_SESSION_END,
    EVENT_STATS_INVALIDATED,
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
from ui.savegames_worker import SavegamesTaskWorker

# --- APP WINDOW PRINCIPALE ---

from ui.controllers.base import BaseController
from ui.controllers.catalog import CatalogController
from ui.controllers.library import LibraryController
from ui.controllers.detail import GameDetailController
from ui.controllers.version import VersionController
from ui.controllers.downloads import DownloadsController
from ui.controllers.session import SessionController
from ui.controllers.settings import SettingsController


from core.version import __version__

log = logging.getLogger(__name__)

# Asynchronous shutdown tuning (C4):
# - stage 2 polls tracked workers every 100 ms;
# - after SHUTDOWN_DEADLINE_MS we log once and keep polling instead of
#   terminating a QThread (residual limitation, documented in _shutdown_poll);
# - the aboutToQuit fallback uses a small bounded stop budget because the Qt
#   event loop is already ending.
SHUTDOWN_POLL_INTERVAL_MS = 100
SHUTDOWN_DEADLINE_MS = 30_000
SHUTDOWN_FALLBACK_STOP_MS = 1500


def _worker_is_running(worker):
    try:
        return bool(worker.isRunning())
    except RuntimeError:
        return False


def _best_effort_shutdown(label, callback, *args, **kwargs):
    """Run one teardown step; a failing step must not abort the others.

    Shutdown touches many independent managers, and no single failure may stop
    the window from reaching its final stage.
    """
    try:
        return callback(*args, **kwargs)
    except Exception as exc:
        log.exception(f"[Shutdown] {label}: {exc}")
        return None


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
    # Savegames notifications can be emitted from a worker thread; the queued
    # connection delivers them to the GUI thread where dialogs are legal.
    savegames_notice = pyqtSignal(str, str, str)

    def __init__(self):
        super().__init__()
        self.savegames_notice.connect(
            self._notify_savegames, Qt.ConnectionType.QueuedConnection)
        self.setWindowTitle(f"Ae Thicc Launcher {__version__}")
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

        self.offline_cache_file = str(PathManager.get_dbs_dir() / "offline_cache.json")

        # --- CARICAMENTO DELLA CACHE IMMORTALE ---
        self.session_cache = {}
        if os.path.exists(self.offline_cache_file):
            try:
                with open(self.offline_cache_file, 'r', encoding='utf-8') as f:
                    self.session_cache = json.load(f)
                log.info(f"[App] Offline cache caricata: {len(self.session_cache)} schede gioco.")
                if self._trim_session_cache():
                    self._save_offline_cache()
            except Exception:
                # best-effort: offline cache/thread wait are regenerable/best-effort
                pass

        self.master_catalog = self.context.master_catalog
        self.events = self.context.events
        self.tasks = self.context.tasks
        self._theme = StructuralTheme(self)
        self._active_sales_workers = []
        self._active_sgdb_workers = []
        self._active_page_workers = []
        self._sgdb_inflight_titles = set()
        self._cover_worker = None
        self._cover_request_id = 0
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

        self.download_queue = []
        self.active_downloader = None
        
        self.global_cookies = {}
        self.main_profile = QWebEngineProfile.defaultProfile()
        self.cookie_store = self.main_profile.cookieStore()
        self.cookie_store.cookieAdded.connect(self._on_global_cookie_added)
        self.cookie_store.loadAllCookies()

        from ui.webengine_profiles import configure_media_profile, describe_profile
        self.media_profile = configure_media_profile(QWebEngineProfile(self))
        log.info("[WebEngine] default profile: %s", describe_profile(self.main_profile))
        log.info("[WebEngine] media profile: %s", describe_profile(self.media_profile))

        from managers.discovery_manager import DiscoveryManager, DiscoveryServices

        def _discovery_sync_folder():
            manager = getattr(self, "savegames_manager", None)
            if manager is None:
                return ""
            return manager.get_sync_folder()

        def _discovery_cover_path(title):
            from core.paths import PathManager
            manager = getattr(self, "savegames_manager", None)
            sync = (manager.get_sync_folder() if manager is not None
                    else str(PathManager.get_staging_dir()))
            return MiniCoverManager.resolve_cover_path(
                title, self.installed_games, sync)

        self.discovery_manager = DiscoveryManager(
            DiscoveryServices(
                installed_games=lambda: self.installed_games,
                catalog=lambda: self.master_catalog,
                sync_folder=_discovery_sync_folder,
                cover_path=_discovery_cover_path,
            ),
            parent=self,
        )
        self.discovery_manager.scrape_requested.connect(self.scraper.fetch_cover)
        self.scraper.cover_fetched.connect(self.discovery_manager.on_scrape_cover_fetched)
        self.discovery_manager.error_occurred.connect(self._on_discovery_error)

        self.init_ui()
        # Instanziamo i manager UNA sola volta: il vecchio blocco duplicato
        # sovrascriveva le istanze e rendeva imprevedibile la connessione dei segnali.
        self.lib_manager = LibraryManager(self)
        self.bg_scan_coordinator = BackgroundScanCoordinator(self)
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
            stats_provider=lambda: self.stats_manager.snapshot(allow_build=False),
            stats_revision=lambda: self.stats_manager.snapshot_revision,
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
        self.stats_tab.snapshot_ready.connect(self._on_stats_snapshot_ready)
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
        self.bg_scan_coordinator.update_found.connect(self._update_library_item_from_bg)
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
        self._background_init_t0 = time.monotonic()

        # Deduplicate the JSONL history off the GUI thread. This never drops
        # records: it only removes duplicate ids.
        try:
            thread = threading.Thread(
                target=self.context.state_store.compact_history,
                name="jsonl-compact",
                daemon=True,
            )
            self.tasks.track_thread(thread)
            thread.start()
        except Exception as exc:
            log.exception(f"[State] compaction start failed: {exc}")

        # The scan reads every game folder in a worker; the sidebar is rebuilt
        # on the GUI thread when it finishes.
        self.lib_manager.scan_library(on_done=self._after_library_scan)

    def _after_library_scan(self):
        """Startup work that needs the populated ``installed_games``."""
        # The first scan is a baseline and emits no install events, so the
        # Stats cache built before it would stay empty. Invalidate and refresh.
        self.stats_manager.invalidate()
        if hasattr(self, 'stats_tab'):
            self.stats_tab.refresh()
        if hasattr(self, 'tab_discovery'):
            self.tab_discovery.refresh_library()

        # Steam is authoritative for shortcut identities after a library scan
        # (an installation copied from another OS can carry a stale path-based
        # grid id). One shared VDF index is used for the batch, and the batch
        # runs in a worker: it waits on the coordinator and must not block the
        # GUI thread at startup.
        self._run_savegames_task(
            lambda: self.savegames_manager.refresh_steam_identities(),
            self._on_steam_identities_refreshed,
            surface_errors=False,
        )

        # --- SYNC INIZIALE DEL PLAY TIME CON STEAM (merge massimo) ---
        if hasattr(self, 'playtime_manager'):
            try:
                self.playtime_manager.sync_all()
                self.playtime_manager.try_flush_pending()
            except Exception as e:
                log.exception(f"[Playtime] Errore sync iniziale: {e}")

        # --- LOGICA DI COOLDOWN DEL MASTER CATALOG (12 ORE) ---
        if self._should_update_catalog():
            self.fetch_master_catalog()
        else:
            log.info("[App] Master Catalog recente. Salto il download e avvio lo scanner background.")
            # Lo scanner usa QWebEngine sul thread grafico: lo rimandiamo per
            # non bloccare i primi secondi di utilizzo della UI.
            self._schedule_background_scan()

        elapsed = (time.monotonic() - getattr(self, "_background_init_t0", time.monotonic())) * 1000.0
        log.info(f"[STARTUP] background init: {elapsed:.0f}ms")

    def _on_steam_identities_refreshed(self, changed):
        if changed and hasattr(self, "savegames_tab"):
            self.savegames_tab.refresh_view()

    def _on_discovery_error(self, message):
        """Non-fatal network failure: surface it instead of failing silently."""
        text = str(message or "").strip()
        log.info(f"[Network] {text}")
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
            if hasattr(self, "stats_tab"):
                self.stats_tab.refresh()
            self.achievements_manager.start()
            if hasattr(self, "achievements_tab"):
                self.achievements_tab.refresh()
        except Exception as exc:
            log.exception(f"[Achievements] startup evaluation failed: {exc}")

    def _on_stats_snapshot_ready(self, _snapshot):
        manager = getattr(self, "achievements_manager", None)
        if manager is None or not getattr(manager, "_started", False):
            return
        try:
            manager.evaluate()
        except Exception as exc:
            log.exception(f"[Achievements] evaluation after stats refresh failed: {exc}")

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
        def _rescan_library():
            self.lib_manager.scan_library(
                on_done=lambda: self.tab_discovery.refresh_library()
                if hasattr(self, 'tab_discovery') else None)

        action_rescan.triggered.connect(_rescan_library)
        
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
        self.dl_manager = DownloadManagerTab(
            self.app_config['theme'], media_profile=self.media_profile)
        self.dl_manager.temp_folder = self.temp_folder
        self.dl_manager.set_state_file(os.path.join(self.temp_folder, ".dl_state.json"))
        
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

        self.savegames_manager = SavegamesManager(
            self.app_config, self.installed_games, self,
            prompt_save_path=self._prompt_save_path,
            notify=self._post_savegames_notification,
        )
        self.state_backup_manager = StateBackupManager.from_config(self.app_config)
        self.backup_service = BackupService(self.savegames_manager, self.state_backup_manager)
        self.savegames_tab = SavegamesTab(
            self.app_config, self.installed_games, self.savegames_manager, self,
            backup_service=self.backup_service,
        )
        self.tabs.addTab(self.savegames_tab, "💾 Saves Manager")
        self.playtime_manager = PlaytimeManager(
            installed_games=lambda: self.installed_games,
            config=lambda: self.app_config,
            on_stub_updated=lambda title, meta: (
                self.savegames_manager.ensure_playtime_stub(title, meta)),
            on_stubs_refresh=lambda: (
                self.savegames_manager.ensure_playtime_stubs()),
        )
        self._init_state_backup()

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
        self.dl_manager.pending_extraction_ready.connect(self._on_pending_extraction_ready)
        self.dl_manager.open_game_page_requested.connect(self._open_incomplete_in_explorer)
        self.dl_manager.attention_changed.connect(self._update_downloads_attention)
        self.scraper.cf_bypassed.connect(self._on_cf_bypassed)
        self.dl_manager.pause_state_changed.connect(self._sync_global_pause_state)
        self._update_downloads_attention(self.dl_manager.attention_count())

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

    # ------------------------------------------------------ launcher state
    def _init_state_backup(self):
        """Debounced backup of launcher data, triggered by user activity."""
        self._state_backup_timer = QTimer(self)
        self._state_backup_timer.setSingleShot(True)
        self._state_backup_timer.setInterval(45000)
        self._state_backup_timer.timeout.connect(self._flush_state_backup)

        for name in (
            EVENT_ACHIEVEMENT_UNLOCKED,
            EVENT_SESSION_END,
            EVENT_PREFS_CHANGED,
            EVENT_REGION_CHANGED,
            EVENT_STATS_INVALIDATED,
        ):
            self.events.subscribe(name, self._on_state_backup_event)

        if hasattr(self, 'achievements_manager'):
            self.achievements_manager.achievement_unlocked.connect(
                lambda _badge: self.schedule_state_backup()
            )

    def _on_state_backup_event(self, _payload=None):
        self.schedule_state_backup()

    def state_backup_enabled(self):
        manager = getattr(self, 'state_backup_manager', None)
        return bool(manager) and StateBackupManager.enabled_for(self.app_config)

    def schedule_state_backup(self):
        """Ask for a state backup soon; repeated events collapse into one."""
        if not self.state_backup_enabled():
            return
        if not self._state_backup_timer.isActive():
            self._state_backup_timer.start()

    def _flush_state_backup(self):
        if not self.state_backup_enabled():
            return
        try:
            self.backup_service.backup_state(push=True)
        except Exception as exc:
            log.exception(f"[State] Backup failed: {exc}")

    def _prompt_save_path(self, game_title):
        """UI callback for SavegamesManager: manual save path dialog."""
        manager = getattr(self, "savegames_manager", None)
        preview = None
        if manager is not None:
            preview = lambda value: manager.preview_linux_path(value, game_title)
        dialog = ManualSavePathDialog(game_title, self, preview=preview)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.chosen_path
        return ""

    def _post_savegames_notification(self, level, title, message):
        """Thread-safe hand-off: emit for the queued GUI-thread slot."""
        self.savegames_notice.emit(str(level), str(title), str(message))

    def _notify_savegames(self, level, title, message):
        """GUI-thread slot for SavegamesManager notifications."""
        if level == "warning":
            QMessageBox.warning(self, title, message)
        else:
            QMessageBox.information(self, title, message)

    def shutdown(self):
        """Start the asynchronous teardown shared by closeEvent/aboutToQuit.

        Stage 1 is synchronous and fast (stop timers, request cancellations);
        stage 2 polls tracked workers with a QTimer; stage 3 flushes and quits
        only once no worker is running. Idempotent: the first caller wins.
        """
        if getattr(self, "_shutdown_finished", False):
            return
        if getattr(self, "_shutdown_started", False):
            return
        self._shutdown_started = True
        # Legacy flag read by the session/installer controllers to suppress
        # UI work once shutdown has begun.
        self._shutdown_done = True
        log.info("[App] Shutdown sequence started...")
        self._shutdown_stage1()

    def on_about_to_quit(self):
        """Fallback for a quit that does not pass through ``closeEvent``.

        The Qt event loop is already ending here, so the asynchronous poll can
        no longer make progress: run a bounded synchronous variant instead.
        """
        if getattr(self, "_shutdown_finished", False):
            return
        if not getattr(self, "_shutdown_started", False):
            log.info("[App] aboutToQuit without a started teardown; "
                     "running the bounded fallback.")
            self._shutdown_started = True
            self._shutdown_done = True
            self._shutdown_stage1()

        running = self._shutdown_workers_running()
        tasks = getattr(self, "tasks", None)
        # Same rule as the staged path: a running savegames operation owns the
        # store, so stop_all() must not touch it.
        if tasks is not None and not self._shutdown_savegames_running(running):
            _best_effort_shutdown(
                "fallback task stop", tasks.stop_all,
                SHUTDOWN_FALLBACK_STOP_MS)

        leftovers = self._shutdown_workers_running()
        if leftovers:
            # The Qt loop is ending and a worker is still writing: both the
            # final flush and the resource teardown would race with it. Losing
            # one flush is safer than corrupting the state it is writing.
            log.error("[Shutdown] %d worker(s) still running at aboutToQuit; "
                      "skipping the final flush and resource teardown to "
                      "avoid corrupting state: %s",
                      len(leftovers),
                      ", ".join(type(w).__name__ for w in leftovers))
            self._shutdown_stop_poll_timer()
            self._shutdown_finish()
            return
        self._shutdown_stage3()

    def _shutdown_stage1(self):
        # 1. Stop timers so no callback rearms work while tearing down.
        for name in ("_state_backup_timer", "savegames_timer", "_steam_build_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except RuntimeError:
                    pass
        achievements = getattr(self, "achievements_manager", None)
        achievement_timer = getattr(achievements, "_timer", None) if achievements else None
        if achievement_timer is not None:
            try:
                achievement_timer.stop()
            except RuntimeError:
                pass

        # 2. Request cancellation only: waiting on the GUI thread here would
        # freeze the window for as long as the workers take to unwind.
        dl_manager = getattr(self, "dl_manager", None)
        worker = getattr(dl_manager, "active_worker", None) if dl_manager is not None else None
        if worker is not None:
            _best_effort_shutdown("download worker cancel", worker.cancel)

        flow = getattr(self, "installer_flow", None)
        installer = getattr(flow, "installer_thread", None) if flow is not None else None
        if installer is not None:
            _best_effort_shutdown("installer cancel", installer.cancel)

        igdb_batch = getattr(self, "igdb_batch_manager", None)
        if igdb_batch is not None:
            _best_effort_shutdown("igdb batch stop", igdb_batch.stop)

        # C7 hook: flush deferred playtime BEFORE close_operations() refuses
        # savegame work. record_session already persisted the counter to
        # steamrip_meta.json, so anything dropped after this point is not lost.
        savegames = getattr(self, "savegames_manager", None)
        if savegames is not None and hasattr(savegames, "flush_pending_playtime"):
            _best_effort_shutdown(
                "deferred playtime flush", savegames.flush_pending_playtime)
        if savegames is not None and hasattr(savegames, "close_operations"):
            # Requests cooperative cancellation, refuses newcomers and returns
            # immediately (wait budget 0).
            _best_effort_shutdown(
                "savegames cancel", savegames.close_operations, 0)

        self._shutdown_deadline = time.monotonic() + SHUTDOWN_DEADLINE_MS / 1000.0
        self._shutdown_deadline_logged = False
        self._shutdown_cancelled_workers = set()

        timer = QTimer()
        timer.setInterval(SHUTDOWN_POLL_INTERVAL_MS)
        timer.timeout.connect(self._shutdown_poll)
        self._shutdown_poll_timer = timer
        timer.start()
        # Synchronous first check: no workers means teardown finishes now.
        self._shutdown_poll()

    def _shutdown_stop_poll_timer(self):
        timer = getattr(self, "_shutdown_poll_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except RuntimeError:
                pass

    def _shutdown_workers_running(self):
        running = []
        tasks = getattr(self, "tasks", None)
        if tasks is not None:
            running.extend(
                _best_effort_shutdown(
                    "task registry poll", tasks.running_workers) or [])
        dl_manager = getattr(self, "dl_manager", None)
        worker = getattr(dl_manager, "active_worker", None) if dl_manager is not None else None
        if worker is not None and _worker_is_running(worker) and worker not in running:
            running.append(worker)
        flow = getattr(self, "installer_flow", None)
        installer = getattr(flow, "installer_thread", None) if flow is not None else None
        if installer is not None and _worker_is_running(installer) and installer not in running:
            running.append(installer)
        return running

    @staticmethod
    def _shutdown_savegames_running(running):
        return any(isinstance(worker, SavegamesTaskWorker) for worker in running)

    def _shutdown_poll(self):
        if getattr(self, "_shutdown_finished", False):
            self._shutdown_stop_poll_timer()
            return
        running = self._shutdown_workers_running()
        if not running:
            self._shutdown_stage3()
            return

        # Ask every other worker to stop, but only once no savegames
        # operation is running: stop_all() must never touch a savegames
        # worker while its operation may still be writing. Workers that appear
        # later get their own pass instead of being silently ignored.
        tasks = getattr(self, "tasks", None)
        if tasks is not None and not self._shutdown_savegames_running(running):
            cancelled = getattr(self, "_shutdown_cancelled_workers", None)
            if cancelled is None:
                cancelled = self._shutdown_cancelled_workers = set()
            new_workers = [w for w in running if w not in cancelled]
            if new_workers:
                cancelled.update(running)
                # Non-blocking: cancel is requested, the wait budget is zero.
                _best_effort_shutdown("task cancellation", tasks.stop_all, 0)

        if not getattr(self, "_shutdown_deadline_logged", False):
            deadline = getattr(self, "_shutdown_deadline", None)
            if deadline is not None and time.monotonic() >= deadline:
                self._shutdown_deadline_logged = True
                # Residual limitation: a worker that ignores cancellation keeps
                # the process alive. We never terminate() a QThread or force a
                # kill; AETHICC_HARD_EXIT=1 remains the opt-in escape hatch.
                log.error("[Shutdown] %d worker(s) still running after %ss; "
                          "keeping the process alive: %s",
                          len(running), SHUTDOWN_DEADLINE_MS / 1000.0,
                          ", ".join(type(w).__name__ for w in running))

    def _shutdown_stage3(self):
        """Run the final stage exactly once and always finish the teardown.

        The wrapper is the safety net: whatever an individual step raises, the
        process must still reach a quit. Otherwise the poll timer is stopped,
        the window is hidden and quitOnLastWindowClosed is off, so the app
        could never terminate.
        """
        if getattr(self, "_shutdown_finished", False):
            return
        try:
            self._shutdown_stage3_impl()
            log.info("[App] Shutdown sequence completed.")
        except Exception as exc:
            log.exception(f"[Shutdown] final stage failed: {exc}")
        finally:
            self._shutdown_stop_poll_timer()
            self._shutdown_finish()

    def _shutdown_stage3_impl(self):
        """Final stage body: only reached when no tracked worker is running."""
        # Managers that own their worker sets and wait internally, with
        # bounded budgets. Safe now that no tracked worker is using them.
        discovery = getattr(self, "discovery_manager", None)
        if discovery is not None:
            _best_effort_shutdown("discovery", discovery.shutdown)

        # State flush now that workers cannot write anymore.
        dl_manager = getattr(self, "dl_manager", None)
        if dl_manager is not None:
            _best_effort_shutdown("download state", dl_manager.save_state)
        if hasattr(self, "master_catalog"):
            _best_effort_shutdown(
                "catalog flush", self.master_catalog.save_immediate)
        if hasattr(self, "playtime_manager"):
            _best_effort_shutdown(
                "playtime", self.playtime_manager.try_flush_pending)
        if hasattr(self, "backup_service"):
            _best_effort_shutdown("state backup", self._flush_state_backup)

        # Tear down the hidden WebEngine view (top-level widget).
        scraper = getattr(self, "scraper", None)
        if scraper is not None and hasattr(scraper, "shutdown"):
            _best_effort_shutdown("scraper", scraper.shutdown)

        # Cover views and their shared off-the-record profile. Pages go
        # first: QWebEngineProfile must not outlive a page that uses it.
        for name in ("tab_explorer", "tab_downloads"):
            tab = getattr(self, name, None)
            release = getattr(tab, "release_webengine", None)
            if release is not None:
                try:
                    release()
                except (RuntimeError, AttributeError) as exc:
                    log.exception(f"[Shutdown] {name} WebEngine: {exc}")
        media_profile = getattr(self, "media_profile", None)
        if media_profile is not None:
            try:
                media_profile.deleteLater()
            except RuntimeError:
                pass

        # 7. Join the thread pools and the Python background threads.
        try:
            QThreadPool.globalInstance().waitForDone(2000)
        except Exception:
            # best-effort: offline cache/thread wait are regenerable/best-effort
            pass
        tasks = getattr(self, "tasks", None)
        if tasks is not None:
            _best_effort_shutdown("thread join", tasks.join_threads, 2000)

    def _shutdown_finish(self):
        """Mark the teardown finished and quit exactly once."""
        if getattr(self, "_shutdown_finished", False):
            return
        self._shutdown_finished = True
        _best_effort_shutdown("quit", self._quit_application)

    def _quit_application(self):
        if getattr(self, "_quit_requested", False):
            return
        self._quit_requested = True
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event):
        log.info(f"[App] Spegnimento sicuro in corso... (spontaneous={getattr(event, 'spontaneous', lambda: '?')()})")
        
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

        if getattr(self, "_shutdown_finished", False):
            event.accept()
        else:
            if not getattr(self, "_shutdown_started", False):
                self.shutdown()
            if getattr(self, "_shutdown_finished", False):
                event.accept()
            else:
                # Teardown is still draining workers: keep the process alive
                # but take the window out of the way instead of blocking here.
                event.ignore()
                try:
                    self.hide()
                except RuntimeError:
                    pass

        # Opt-in escape hatch for diagnosing a hang on a specific machine.
        # Off by default: the ordered teardown above is the normal path.
        if os.environ.get("AETHICC_HARD_EXIT") == "1":
            log.info("[App] AETHICC_HARD_EXIT=1: forcing os._exit(0).")
            os._exit(0)


# Read-only compatibility proxies for the legacy ``self.<widget>`` access.
# New code should use ``self.widgets.<widget>``.
for _widget_name in WindowWidgets.__slots__:
    setattr(
        AppWindow,
        _widget_name,
        property(lambda self, _name=_widget_name: getattr(self.widgets, _name)),
    )
