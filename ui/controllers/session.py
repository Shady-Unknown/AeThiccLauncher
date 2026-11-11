"""Game exit: playtime accounting, session event and backup hook.

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

class SessionController:
    def on_game_exited_backup(self, meta):
        """Invocato quando un monitor rileva la chiusura del gioco. Registra la
        sessione di gioco, esegue il backup e notifica l'utente."""
        # Il payload del monitor potrebbe non contenere 'title': risolviamo
        # la chiave della libreria tramite install_dir.
        title = meta.get('title') or next(
            (t for t, m in self.installed_games.items()
             if m.get('install_dir') == meta.get('install_dir')),
            None
        )
        # 1. Registra la durata della sessione nell'hour counter
        session_seconds = meta.get('session_seconds')
        if session_seconds and title and hasattr(self, 'playtime_manager'):
            try:
                self.playtime_manager.record_session(title, session_seconds)
            except Exception as e:
                print(f"[Playtime] Errore registrazione sessione: {e}")
            self.events.emit_event(
                EVENT_SESSION_END,
                title=title,
                seconds=int(session_seconds),
                started_at=int(meta.get('started_at') or 0),
                ended_at=int(meta.get('ended_at') or 0),
                install_dir=meta.get('install_dir') or '',
            )
            # Legge playtime Steam con leggero ritardo (Steam può aggiornare
            # il valore dopo la chiusura del processo di gioco)
            QTimer.singleShot(5000, self._sync_playtime_after_session)
        # 2. Backup on demand dei salvataggi
        if self.savegames_manager.backup_on_demand(meta):
            self.events.emit_event(
                EVENT_BACKUP_CREATED,
                title=title or meta.get('title') or '',
                install_dir=meta.get('install_dir') or '',
            )
            QMessageBox.information(
                self, 
                "Backup Successful", 
                f"Automatic save backup for <b>{meta.get('title')}</b> completed successfully after closing the game."
            )
            if hasattr(self, 'savegames_tab'):
                self.savegames_tab.refresh_view()

    def _sync_playtime_after_session(self):
        """Merge post-sessione: prende il massimo tra launcher e Steam."""
        if not hasattr(self, 'playtime_manager'):
            return
        try:
            self.playtime_manager.sync_all()
            self.playtime_manager.try_flush_pending()
        except Exception as e:
            print(f"[Playtime] Errore sync post-sessione: {e}")
