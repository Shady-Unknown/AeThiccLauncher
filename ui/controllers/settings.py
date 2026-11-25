"""Theme application, settings dialog and savegame timer.

Extracted from the historical AppWindow god object. The class is a
cohesive mixin: methods keep using ``self`` (the AppWindow instance),
so behaviour stays identical while the file is compartmentalised.
"""
import logging

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
from core.naming import safe_game_folder_name
from managers.save_manager import SavegamesManager
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
from ui.library_manager import LibraryManager, is_local_install
from managers.igdb_details_cache import IgdbDetailsCache
from network.igdb_details_worker import IGDBDetailsWorker
from ui.controllers.game_launcher import GameLauncher
from ui.controllers.installer_flow import InstallerFlowCoordinator
from core import platform_info, steam_integration
from core import secrets
from core.events import (
    EVENT_BACKUP_CREATED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_LAUNCHER_START,
    EVENT_PREFS_CHANGED,
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

log = logging.getLogger(__name__)

# --- APP WINDOW PRINCIPALE ---

class SettingsController:
    def check_initial_setup(self):
        """First-run setup: open the onboarding wizard when needed."""
        if getattr(self, "_onboarding_prompted", False):
            return
        onboarding = self.app_config.get("onboarding", {}) or {}
        if onboarding.get("completed"):
            return
        watched = self.app_config.get("watched_folders", []) or []
        if any(os.path.exists(folder) for folder in watched):
            # Existing user with valid libraries: no wizard needed.
            return
        self._onboarding_prompted = True
        self.open_onboarding_wizard()

    def open_onboarding_wizard(self):
        from ui.dialogs.onboarding_wizard import OnboardingWizard

        had_igdb = secrets.has_igdb_credentials()
        wizard = OnboardingWizard(self.app_config, self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return False
        self._onboarding_prompted = True
        if getattr(wizard, "data_root_changed", False):
            QMessageBox.information(
                self,
                "Restart required",
                "The data folder has been moved.\n\n"
                "The launcher will restart now to use the new location.",
            )
            self._restart_application()
            return True
        self.update_entire_config(wizard.config)
        self._offer_restart_for_api_keys(had_igdb)
        return True

    def _offer_restart_for_api_keys(self, had_igdb: bool):
        """The IGDB rows are built at startup, so new keys need a restart."""
        if had_igdb or not secrets.has_igdb_credentials():
            return
        reply = QMessageBox.question(
            self,
            "Restart required",
            "IGDB is now configured. Trending and New Releases only appear "
            "after a restart.\n\nRestart the launcher now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._restart_application()

    def _restart_application(self):
        try:
            from core import platform_info

            if platform_info.is_flatpak():
                # Inside the sandbox, relaunch through the host Flatpak CLI.
                subprocess.Popen(
                    ["flatpak-spawn", "--host", "flatpak", "run", "com.MJK.AeThiccLauncher"]
                )
            else:
                from PyQt6.QtCore import QProcess

                args = [] if getattr(sys, "frozen", False) else list(sys.argv)
                QProcess.startDetached(sys.executable, args)
        except Exception as exc:
            log.exception(f"[Settings] restart failed: {exc}")
        app = QApplication.instance()
        if app:
            app.quit()

    def apply_current_theme(self, theme_data):
        # Applying the theme is expensive (app-wide re-polish + every card).
        # The settings dialog used to call this once per colour editor; the
        # dialog now coalesces, and this guard drops no-op re-applications.
        try:
            theme_key = tuple(sorted(theme_data.items()))
        except AttributeError:
            theme_key = None
        if theme_key is not None and theme_key == getattr(self, "_applied_theme_key", None):
            return
        self._applied_theme_key = theme_key

        extra_css = f"""
            /* La lista libreria dell'Explorer è gestita da ExplorerTab
               (vetro + selezione neon); qui resta solo lo stile degli store. */

            /* TASTI STORE DIVENTANO ICONE TRASPARENTI CIRCOLARI */
            QPushButton#BtnSteamStore, QPushButton#BtnGogStore {{
                background-color: transparent;
                border: none;
                border-radius: 25px; 
            }}
            QPushButton#BtnSteamStore:hover, QPushButton#BtnGogStore:hover {{
                background-color: rgba(255, 255, 255, 0.15);
            }}
            
            /* Nuovo Tasto Play Gigante */
            QPushButton#BtnPlayMain {{
                background-color: #00ff88;
                color: #1a0f2b;
                border: none;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
            }}
            QPushButton#BtnPlayMain:hover {{
                background-color: #00cc6a;
            }}
        """
        full_style = style_settings.get_stylesheet(theme_data) + extra_css
        app = QApplication.instance()
        if app:
            app.setStyleSheet(full_style)
        else:
            self.setStyleSheet(full_style)

        # La tab Explorer ricostruisce la skin vetro dal tema attivo.
        if hasattr(self, "tab_explorer") and hasattr(self.tab_explorer, "set_theme"):
            self.tab_explorer.set_theme(theme_data)
            self.tab_explorer.set_blur_percent(
                (self.app_config.get("ui", {}) or {}).get("explorer_blur", 0)
            )

        # ---> INIETTA QUESTO CONTROLLO QUI SOTTO <---
        if hasattr(self, 'dl_manager'):
            self.dl_manager.apply_theme(theme_data)
        if hasattr(self, 'savegames_tab'):
            self.savegames_tab.apply_theme(theme_data)
        if hasattr(self, 'tab_discovery'):
            self.tab_discovery.apply_theme()
        if hasattr(self, 'stats_tab'):
            self.stats_tab.apply_theme()
        if hasattr(self, 'achievements_tab'):
            self.achievements_tab.apply_theme()
            
        if hasattr(self, 'btn_pause_dl'):
            media_dir = os.path.join(get_base_dir(), ".media")
            self.icon_pause = self._colorize_icon(os.path.join(media_dir, "pause_round.svg"), theme_data)

        # ---> AGGIUNTA PER L'AGGIORNAMENTO LIVE DELLE ICONE SVG <---
        if hasattr(self, 'btn_pause_dl'):
            media_dir = os.path.join(get_base_dir(), ".media")
            
            # Rigenera le icone col nuovo tema live
            self.icon_pause = self._colorize_icon(os.path.join(media_dir, "pause_round.svg"), theme_data)
            self.icon_play = self._colorize_icon(os.path.join(media_dir, "play_round.svg"), theme_data)
            self.icon_stop = self._colorize_icon(os.path.join(media_dir, "stop_round.svg"), theme_data)
            
            # Riapplica l'icona corretta in base allo stato attuale del manager
            if hasattr(self, 'dl_manager') and self.dl_manager.is_paused:
                self.btn_pause_dl.setIcon(self.icon_play)
            else:
                self.btn_pause_dl.setIcon(self.icon_pause)
                
            self.btn_stop_dl.setIcon(self.icon_stop)

        if hasattr(self, 'current_game_data') and self.current_game_data:
            QTimer.singleShot(50, lambda: self.display_game_data(self.current_game_data))

    def open_settings_dialog(self):
        status = {}
        if hasattr(self, 'backup_service'):
            try:
                status = self.backup_service.state_status()
            except Exception as exc:
                log.exception(f"[State] status failed: {exc}")
        had_igdb = secrets.has_igdb_credentials()
        dialog = SettingsDialog(self.app_config, self, state_backup_status=status)
        dialog.config_saved.connect(self.update_entire_config)
        dialog.state_backup_requested.connect(self._backup_launcher_state_now)
        dialog.state_restore_requested.connect(self._restore_launcher_state)
        dialog.exec()
        self._offer_restart_for_api_keys(had_igdb)
        if getattr(dialog, "run_wizard_after_close", False):
            self._onboarding_prompted = False
            self.open_onboarding_wizard()

    def _backup_launcher_state_now(self):
        service = getattr(self, 'backup_service', None)
        if service is None:
            QMessageBox.warning(self, "Launcher Data", "Backup service is not available.")
            return
        result = service.backup_state(push=True)
        if not result.get('ok'):
            reason = result.get('reason') or result.get('detail') or "unknown error"
            QMessageBox.warning(
                self, "Launcher Data Backup",
                "The launcher data could not be backed up:\n{}".format(reason),
            )
            return
        mirror = result.get('mirror') or {}
        mirror_text = self._state_backup_mirror_text(mirror)
        QMessageBox.information(
            self, "Launcher Data Backup",
            "Launcher data saved to:\n{}{}".format(result.get('root', ''), mirror_text),
        )

    @staticmethod
    def _state_backup_mirror_text(mirror):
        """One-line mirror status for the manual launcher-state backup."""
        mirror = mirror or {}
        status = mirror.get('status')
        if status == 'busy':
            return ("\nMirror: skipped (a savegame operation is busy); "
                    "it will be updated by the next backup operation.")
        if status == 'cancelled':
            return "\nMirror: skipped during shutdown; the local backup is intact."
        if status == 'failed':
            return ("\nMirror: skipped after an error; the local backup is intact, "
                    "see the log for details.")
        if mirror.get('configured'):
            return "\nMirror: {} file(s) copied.".format(mirror.get('copied', 0))
        return ""

    def _restore_launcher_state(self):
        service = getattr(self, 'backup_service', None)
        if service is None:
            QMessageBox.warning(self, "Launcher Data", "Backup service is not available.")
            return

        reply = QMessageBox.question(
            self, "Restore Launcher Data",
            "Restore achievements, stats and preferences from the backup?\n\n"
            "The merge is additive: local data wins on conflicts and nothing is deleted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        result = service.restore_state()
        if result.get('reason') == 'not_configured' or not result.get('files'):
            QMessageBox.information(
                self, "Restore Launcher Data",
                "No launcher data backup found. Run a launcher data backup first.",
            )
            return

        if hasattr(self, 'stats_manager'):
            self.stats_manager.invalidate()
        if hasattr(self, 'achievements_manager'):
            self.achievements_manager.reload()
        if hasattr(self, 'stats_tab'):
            self.stats_tab.refresh()
        if hasattr(self, 'achievements_tab'):
            self.achievements_tab.refresh()

        QMessageBox.information(
            self, "Restore Launcher Data",
            "Restored {} file(s): {} new record(s), {} new achievement(s).".format(
                result.get('files', 0),
                result.get('jsonl_added', 0),
                result.get('achievements_added', 0),
            ),
        )

    def update_entire_config(self, new_config):
        from core.paths import PathManager
        from core import data_files
        self.app_config = new_config
        self.temp_folder = self.app_config.get("default_temp_folder") or str(PathManager.get_cache_dir())
        try:
            data_files.ensure_user_data()
        except Exception as exc:
            log.exception(f"[DataFiles] seed after config change failed: {exc}")
        self.apply_current_theme(self.app_config["theme"])
        if hasattr(self, 'savegames_manager'):
            self.savegames_manager.update_app_config(self.app_config)
        if hasattr(self, 'savegames_tab'):
            self.savegames_tab.app_config = self.app_config
            self.savegames_tab.refresh_view()

        def _after_library_scan():
            if hasattr(self, 'tab_discovery'):
                self.tab_discovery.refresh_library()
                self.tab_discovery.refresh_config_notice()
            if hasattr(self, 'stats_tab'):
                self.stats_tab.refresh()

        self.lib_manager.scan_library(on_done=_after_library_scan)
        self.start_savegames_timer()
        if hasattr(self, 'schedule_state_backup'):
            self.schedule_state_backup()
        if hasattr(self, 'events'):
            try:
                self.events.emit_event(EVENT_PREFS_CHANGED, {"source": "settings"})
            except Exception:
                # best-effort: the event bus is optional
                pass

    def get_savegames_interval_ms(self):
        cfg = self.app_config.get('savegames', {})
        interval = cfg.get('sync_interval', 30)
        unit = cfg.get('sync_unit', 'minutes')
        if unit == 'hours':
            return max(1, interval) * 60 * 60 * 1000
        return max(1, interval) * 60 * 1000

    def start_savegames_timer(self):
        self.savegames_timer.stop()
        interval_ms = self.get_savegames_interval_ms()
        self.savegames_timer.setInterval(interval_ms)
        self.savegames_timer.start()
        if hasattr(self, 'savegames_tab'):
            self.savegames_tab.message_label.setText(
                f"Auto-sync enabled: every {self.app_config['savegames']['sync_interval']} "
                f"{self.app_config['savegames']['sync_unit']}"
            )

    def stop_savegames_timer(self):
        if self.savegames_timer.isActive():
            self.savegames_timer.stop()

    def _perform_scheduled_savegame_sync(self):
        if not hasattr(self, 'savegames_manager'):
            return
        if hasattr(self, 'savegames_tab'):
            started = self.savegames_tab.sync_all(
                silent=True, on_done=self._on_scheduled_savegame_sync_done)
            if not started:
                # A manual operation is already running; the next timer tick retries.
                return
        else:
            summary = self.savegames_manager.sync_all_games()
            self._on_scheduled_savegame_sync_done(summary)

    def _on_scheduled_savegame_sync_done(self, summary):
        # Playtime merge stays on the GUI thread: it is small and only reads
        # local VDF files, and the savegames copy already happened in a worker.
        if hasattr(self, 'playtime_manager'):
            try:
                self.playtime_manager.sync_all()
                self.playtime_manager.try_flush_pending()
            except Exception as e:
                log.exception(f"[Playtime] Errore sync programmata: {e}")
        if hasattr(self, 'savegames_tab'):
            created = summary.get('synced_games', 0)
            unchanged = summary.get('unchanged_games', 0)
            self.savegames_tab.refresh_view()
            if summary.get('errors'):
                self.savegames_tab.message_label.setText(
                    f"Auto-sync completed with warnings: {len(summary['errors'])} issue(s)."
                )
            else:
                self.savegames_tab.message_label.setText(
                    f"Auto-sync completed: {created} new snapshot(s), {unchanged} unchanged."
                )
