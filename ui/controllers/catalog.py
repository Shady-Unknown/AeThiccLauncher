"""Remote catalog, offline cache and staleness checks.

Extracted from the historical AppWindow god object. The class is a
cohesive mixin: methods keep using ``self`` (the AppWindow instance),
so behaviour stays identical while the file is compartmentalised.
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
from ui.tabs.savegames_tab import SavegamesTab
from core import config as style_settings
from core.config import get_base_dir
from core.mini_cover_cache import MiniCoverManager

from ui.dialogs.orphan_dialog import OrphanDialog
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

class CatalogController:
    def _schedule_background_scan(self, delay_ms=60000):
        """Avvia il controllo aggiornamenti in background dopo l'avvio."""
        print(f"[App] Background Update Scanner tra {delay_ms // 1000}s")
        QTimer.singleShot(delay_ms, self.bg_scanner.start_background_update_checker)

    def _should_update_catalog(self):
        return self.master_catalog.needs_refresh()

    def _save_offline_cache(self):
        """Salva la session_cache su disco per non perdere i dati delle schede gioco."""
        os.makedirs(os.path.dirname(self.offline_cache_file), exist_ok=True)
        try:
            with open(self.offline_cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.session_cache, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[App] Errore salvataggio offline_cache: {e}")

    def _is_cache_stale_for_installed_game(self, cached_data, title):
        """Check if cached game page data is stale vs master_catalog for installed games."""
        if title not in self.installed_games:
            return False
        if not self.master_catalog:
            return False
        cached_ver = cached_data.get('version', '')
        if not cached_ver:
            return False
        catalog_entry = self.master_catalog.get_entry(title)
        if not isinstance(catalog_entry, dict):
            return False
        catalog_ver = catalog_entry.get('version', '')
        if not catalog_ver or catalog_ver == 'Release Base':
            return False
        return cached_ver != catalog_ver

    def fetch_master_catalog(self):
        print("[App] Starting silent background database update...")
        # Show progress bar, disable button
        self.db_progress.show() 
        
        # Update text gently
        if not self.master_catalog:
            self.search_input.setPlaceholderText("Downloading database for the first time...")
        else:
            self.search_input.setPlaceholderText(f"Search in {len(self.master_catalog)} games (Updating catalog...)")

        # Ask the scraper to do the actual web work
        self.scraper.fetch_master_catalog()

    def _on_catalog_ready(self, catalog_dict):
        size = self.master_catalog.refresh_from_remote(catalog_dict)
        print(f"[App] Background update complete! {size} games in catalog.")
        self.completer_model.setStringList(list(self.master_catalog.keys()))
        self.db_progress.hide()
        self.search_input.setPlaceholderText(f"Database Ready! {size} games indexed. Search here...")

        # Invalidazione batch della session_cache per giochi installati con versione cambiata
        stale_count = 0
        stale_urls = []
        for url, cached in self.session_cache.items():
            title = cached.get('title', '')
            if title in self.installed_games and self._is_cache_stale_for_installed_game(cached, title):
                stale_urls.append(url)
        for url in stale_urls:
            del self.session_cache[url]
            stale_count += 1
        if stale_count > 0:
            print(f"[App] Invalidated {stale_count} stale session cache entries after catalog refresh")
            self._save_offline_cache()

        self._schedule_background_scan()
        if hasattr(self, 'savegames_manager'):
            self.savegames_manager.update_manifest_background()

    def _on_catalog_error(self, err):
        print(f"[App] Critical database error: {err}")
        self.db_progress.hide() 
        
        if self.master_catalog:
            self.search_input.setPlaceholderText(f"Search in {len(self.master_catalog)} games (Offline mode)")
        else:
            self.search_input.setPlaceholderText("Database Error. Paste link manually.")

    def _on_silent_update_ready(self, data, game_title):
        pass # Disabilitato per evitare richieste HTTP di massa in background
