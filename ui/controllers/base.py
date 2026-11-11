"""Shared helpers: icons, cookies and local path opening.

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

class BaseController:
    def _load_colored_svg(self, svg_name, color, size=12):
        """Carica un SVG dalla cartella .media e lo colora con il colore dato."""
        svg_path = os.path.join(get_base_dir(), ".media", svg_name)
        if not os.path.exists(svg_path):
            return "●"  # Fallback se il file non esiste
        try:
            with open(svg_path, 'r', encoding='utf-8') as f:
                svg_content = f.read()
            # Sostituisci i colori nel SVG
            svg_content = svg_content.replace('fill="#000000"', f'fill="{color}"')
            svg_content = svg_content.replace('fill="black"', f'fill="{color}"')
            svg_content = svg_content.replace('stroke="#000000"', f'stroke="{color}"')
            svg_content = svg_content.replace('stroke="black"', f'stroke="{color}"')
            # IMPORTANTE: Se l'SVG ha fill="none", sostituiscilo con il colore
            svg_content = svg_content.replace('fill="none"', f'fill="{color}"')
            # Encode per data URI
            import base64
            b64 = base64.b64encode(svg_content.encode()).decode()
            # Ritorna HTML img tag con data URI - usa style inline per mantenere size fisso
            return f'<img src="data:image/svg+xml;base64,{b64}" width="{size}" height="{size}" style="margin: 0px 1px; vertical-align: middle; display: inline-block;">'
        except Exception as e:
            print(f"[SVG Error] {e}")
            return "●"

    def _colorize_icon(self, path, theme_data=None):
        from PyQt6.QtGui import QPixmap, QPainter, QLinearGradient, QColor
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIcon # Aggiunto per sicurezza
        
        # Se non passiamo dati live, usa quelli salvati
        if theme_data is None:
            theme_data = self.app_config['theme']
            
        size = 64
        pixmap = QIcon(path).pixmap(size, size)
        
        result = QPixmap(pixmap.size())
        result.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        
        grad = QLinearGradient(0, 0, size, size)
        # USIAMO I COLORI LIVE DAL PARAMETRO theme_data
        grad.setColorAt(0.0, QColor(theme_data.get('neon_gradient_start', '#8d0bb1')))
        grad.setColorAt(1.0, QColor(theme_data.get('neon_gradient_end', '#00d2ff')))
        
        painter.fillRect(result.rect(), grad)
        painter.end()
        
        return QIcon(result)

    def _gold_icon(self, icon_path, size=50):
        from PyQt6.QtGui import QPixmap, QPainter
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QIcon

        pixmap = QIcon(icon_path).pixmap(size, size)
        result = QPixmap(pixmap.size())
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(result.rect(), QColor(GOLD_PRIMARY))
        painter.end()
        return QIcon(result)

    def _on_global_cookie_added(self, cookie):
        try:
            name = str(cookie.name(), 'utf-8')
            value = str(cookie.value(), 'utf-8')
            self.global_cookies[name] = value
        except:
            pass

    def open_local_path(self, path):
        if not path:
            return
        try:
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', path])
            else:
                if platform_info.is_flatpak():
                    subprocess.Popen(['flatpak-spawn', '--host', 'xdg-open', path])
                else:
                    subprocess.Popen(['xdg-open', path])
        except Exception as exc:
            QMessageBox.warning(self, "Open Path Failed", f"Unable to open path:\n{path}\n\n{exc}")
