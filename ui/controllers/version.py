"""SteamRIP vs Steam version status and Steam build checks.

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

class VersionController:
    def _check_page_freshness_async(self, url, cached):
        if not url:
            return
        worker = PageFreshnessWorker(
            url,
            cached.get("etag", "") if cached else "",
            cached.get("last_modified", "") if cached else "",
            parent=self,
        )
        worker.checked.connect(self._on_page_freshness_checked)
        worker.finished.connect(lambda w=worker: self._cleanup_page_worker(w))
        self._active_page_workers.append(worker)
        worker.start()

    def _on_page_freshness_checked(self, url, fresh):
        if fresh:
            return
        print(f"[App] HTTP stale per '{url}', re-extract in background")
        try:
            self.scraper.extract_game(url)
        except Exception as e:
            print(f"[App] background re-extract failed: {e}")

    def _fetch_page_headers_async(self, url):
        if not url:
            return
        worker = PageHeadersWorker(url, parent=self)
        worker.headers_ready.connect(self._on_page_headers_ready)
        worker.finished.connect(lambda w=worker: self._cleanup_page_worker(w))
        self._active_page_workers.append(worker)
        worker.start()

    def _on_page_headers_ready(self, url, etag, last_modified):
        entry = self.session_cache.get(url)
        if entry is None:
            return
        changed = False
        if etag and entry.get("etag") != etag:
            entry["etag"] = etag
            changed = True
        if last_modified and entry.get("last_modified") != last_modified:
            entry["last_modified"] = last_modified
            changed = True
        if changed:
            self._save_offline_cache()

    def _backfill_version_status_async(self, title, url):
        if not title or not url:
            return
        worker = VersionStatusWorker(title, url, parent=self)
        worker.status_ready.connect(self._on_version_status_backfilled)
        worker.finished.connect(lambda w=worker: self._cleanup_page_worker(w))
        self._active_page_workers.append(worker)
        worker.start()

    def _on_version_status_backfilled(self, title, url, status):
        if status:
            self.master_catalog.set_version_status(title, status)
            print(f"[VersionStatus] backfill OK per '{title}': "
                  f"posted={status.get('posted_build')} steam={status.get('steam_build')}")
            entry = self.session_cache.get(url)
            if entry is not None:
                entry["version_status"] = status
                self._save_offline_cache()
        else:
            # Marker: la pagina non ha il widget, non ritentare a ogni avvio
            self.master_catalog.set_version_status(title, {"variant": "none"})
            print(f"[VersionStatus] nessun widget Version Status per '{title}'")

        if self._version_status_current_title == title:
            self._recompute_version_status(title)

    def _cleanup_page_worker(self, worker):
        if worker in self._active_page_workers:
            self._active_page_workers.remove(worker)

    def _version_status_tooltip(self, state, is_installed):
        if state == STATE_UPDATE_AVAILABLE:
            return "A newer build is available on SteamRIP. Click for details."
        if state == STATE_STEAMRIP_OK_STEAM_AHEAD:
            if is_installed:
                return ("You're on the latest SteamRIP build. Steam has a newer build "
                        "not yet released on SteamRIP. Click for details.")
            return ("SteamRIP hasn't released the latest Steam build yet. Click for details.")
        if state == STATE_UPDATED:
            return "You're on the latest build available on Steam. Click for details."
        if state == STATE_VERSION_ONLY:
            return "This page tracks the version only (no build numbers). Click for details."
        return "Version status not available. Click for details."

    def _version_status_alert(self, state, status, is_installed, installed_build, posted_build, steam_build):
        theme = self.app_config.get('theme', {})
        green = theme.get('status_green', '#00ff88')
        amber = theme.get('status_yellow', '#ffcc00')
        purple = theme.get('main_accent_color', '#733d91')
        muted = theme.get('text_muted', '#8a7fa4')
        pink = theme.get('bright_neon_accent', '#ff00a0')

        if state == STATE_UPDATE_AVAILABLE:
            if installed_build and posted_build:
                return (f"SteamRIP has a newer build ({installed_build} → {posted_build}). Update to get it.", pink)
            return ("A newer version is available on SteamRIP.", pink)

        if state == STATE_STEAMRIP_OK_STEAM_AHEAD:
            if is_installed and posted_build:
                message = (f"You're on the latest SteamRIP build ({posted_build}). "
                           f"Steam has a newer build ({steam_build}) not yet available here.")
            elif posted_build and steam_build:
                message = (f"SteamRIP is on build {posted_build}; Steam is already on {steam_build}.")
            else:
                message = status.get('alert') or "Steam has a newer build than SteamRIP."
            return (message, amber)

        if state == STATE_UPDATED:
            if is_installed:
                return (status.get('alert') or "You're on the latest build available on Steam.", green)
            return (status.get('alert') or "SteamRIP is up to date with Steam.", green)

        if state == STATE_VERSION_ONLY:
            return (status.get('alert') or "This page tracks the version only (no build numbers).", purple)

        return ("No version data available yet.", muted)

    def _render_version_status(self, title, is_installed, is_local, web_version=""):
        pill = getattr(self, 'btn_version_status', None)
        panel = getattr(self, 'version_status_panel', None)
        if pill is None or panel is None:
            return

        if is_local:
            pill.hide()
            panel.hide()
            pill.set_expanded(False)
            self._version_panel_open = False
            return

        # Cambio gioco: il pannello riparte chiuso
        if self._version_status_rendered_title != title:
            self._version_status_rendered_title = title
            self._version_panel_open = False
        panel.setVisible(self._version_panel_open)
        pill.set_expanded(self._version_panel_open)

        status = self.master_catalog.get_version_status(title) or {}
        state = (self._version_status_state or {}).get('state', STATE_UNKNOWN)

        theme = self.app_config.get('theme', {})
        green = theme.get('status_green', '#00ff88')
        amber = theme.get('status_yellow', '#ffcc00')
        pink = theme.get('bright_neon_accent', '#ff00a0')
        purple = theme.get('main_accent_color', '#733d91')
        muted = theme.get('text_muted', '#8a7fa4')

        posted_build = status.get('posted_build')
        steam_build = status.get('steam_build')
        site_version = status.get('posted_version') or web_version

        if state == STATE_UPDATE_AVAILABLE:
            pill.set_solid("↻ Update available", pink)
        elif state == STATE_STEAMRIP_OK_STEAM_AHEAD:
            if is_installed:
                pill.set_split("✓ SteamRIP", "Steam ahead", green, purple)
            else:
                pill.set_solid("⚠ SteamRIP behind Steam", amber)
        elif state == STATE_UPDATED:
            pill.set_solid("✓ Updated", green)
        elif state == STATE_VERSION_ONLY:
            pill.set_solid(f"Version {site_version}" if site_version else "Version", purple, outline=True)
        else:
            pill.set_solid("Status unavailable", muted, outline=True)

        pill.setToolTip(self._version_status_tooltip(state, is_installed))
        pill.show()

        meta = self.installed_games.get(title, {})
        installed_build = meta.get('build')

        def _set_row(row_key, value, date):
            value_lbl, date_lbl = self.vs_rows.get(row_key, (None, None))
            if value_lbl is not None:
                value_lbl.setText(str(value) if value else "—")
            if date_lbl is not None:
                date_lbl.setText(date or "")

        installed_date = ""
        if is_installed:
            installed_date = meta.get('build_date', '') or ''
            if meta.get('build_inferred'):
                installed_date = (installed_date + " · inferred").strip(" ·")
        _set_row('installed', installed_build if is_installed else "—", installed_date)
        _set_row('posted', posted_build or status.get('posted_version') or site_version,
                 status.get('posted_build_date', ''))
        _set_row('steam', steam_build, status.get('steam_build_date', ''))

        alert_text, alert_color = self._version_status_alert(
            state, status, is_installed, installed_build, posted_build, steam_build
        )
        self.vs_alert.setText(alert_text)
        self.vs_alert.setStyleSheet(f"font-size: 11px; background: transparent; color: {alert_color};")

        subtext = status.get('subtext', '')
        self.vs_subtext.setText(subtext)
        self.vs_subtext.setVisible(bool(subtext))

        site_checked = status.get('site_last_checked') or "—"
        steam_checked = "—"
        try:
            steam_checked_ts = float(status.get('steam_checked_at') or 0)
        except (TypeError, ValueError):
            steam_checked_ts = 0
        if steam_checked_ts:
            steam_checked = time.strftime("%d %b %Y %H:%M", time.localtime(steam_checked_ts))
        elif status.get('steam_build_date'):
            steam_checked = status.get('steam_build_date')
        self.vs_meta.setText(
            f"SteamRIP last checked: {site_checked}   ·   Steam checked: {steam_checked}"
        )

        self._version_status_steamdb_url = status.get('steamdb_url', '') or ''
        self.btn_vs_steamdb.setEnabled(bool(self._version_status_steamdb_url))

        app_id = str(status.get('app_id', '') or '')
        self._version_status_app_id = app_id
        self.btn_vs_refresh.setEnabled(bool(app_id))
        self.btn_vs_refresh.setText("↻ Refresh")

        # Refresh all'apertura se il dato Steam è vecchio (>24h).
        # Vale anche per i giochi non installati: serve solo l'app id.
        if (app_id
                and self.master_catalog.is_steam_build_stale(title)
                and title not in self._steam_build_inflight
                and not self._steam_build_queue):
            QTimer.singleShot(1200, lambda t=title, a=app_id: self._start_steam_build_worker(t, a, silent=True))

    def toggle_version_status_panel(self):
        panel = getattr(self, 'version_status_panel', None)
        pill = getattr(self, 'btn_version_status', None)
        if panel is None or pill is None or pill.isHidden():
            return
        self._version_panel_open = not self._version_panel_open
        panel.setVisible(self._version_panel_open)
        pill.set_expanded(self._version_panel_open)

    def open_version_status_steamdb(self):
        url = self._version_status_steamdb_url
        if not url and self._version_status_app_id:
            url = f"https://steamdb.info/app/{self._version_status_app_id}/patchnotes/"
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def refresh_version_status(self):
        title = self._version_status_current_title
        status = self.master_catalog.get_version_status(title) if title else {}
        app_id = str(status.get('app_id', '') or '')
        if not title or not app_id or title in self._steam_build_inflight:
            return
        self.btn_vs_refresh.setEnabled(False)
        self.btn_vs_refresh.setText("Checking…")
        self._start_steam_build_worker(title, app_id, silent=False)

    def _start_steam_build_worker(self, title, app_id, silent=False):
        if not title or not app_id or title in self._steam_build_inflight:
            return
        self._steam_build_inflight.add(title)
        worker = SteamBuildWorker(title, app_id, parent=self)
        worker.result_ready.connect(self._on_steam_build_ready)
        worker.failed.connect(self._on_steam_build_failed)
        worker.finished.connect(lambda w=worker: self._cleanup_steam_build_worker(w))
        self._active_steam_build_workers.append(worker)
        worker.start()

    def _on_steam_build_ready(self, title, app_id, build, date_str, timestamp):
        self.master_catalog.update_version_status_steam(title, build, date_str, timestamp)
        if self._version_status_current_title == title:
            self.btn_vs_refresh.setText("↻ Refresh")
            self.btn_vs_refresh.setEnabled(True)
            self._recompute_version_status(title)

    def _on_steam_build_failed(self, title, app_id):
        if self._version_status_current_title == title:
            self.btn_vs_refresh.setText("↻ Refresh")
            self.btn_vs_refresh.setEnabled(bool(app_id))

    def _cleanup_steam_build_worker(self, worker):
        title = getattr(worker, 'title', None)
        if title:
            self._steam_build_inflight.discard(title)
        if worker in self._active_steam_build_workers:
            self._active_steam_build_workers.remove(worker)
        self._process_steam_build_queue()

    def _recompute_version_status(self, title):
        data = self.current_game_data or {}
        if data.get('title') != title:
            return
        meta = self.installed_games.get(title)
        status = self.master_catalog.get_version_status(title)
        self._version_status_state = compute_version_state(
            installed_build=meta.get('build') if meta else None,
            posted_build=status.get('posted_build'),
            steam_build=status.get('steam_build'),
            installed_version=meta.get('version', '') if meta else "",
            posted_version_text=self._current_web_version,
            posted_version_only=status.get('posted_version', ''),
            is_installed=bool(meta),
            has_status=bool(status),
        )
        self._render_version_status(
            title, bool(meta), bool(data.get('local_only')), self._current_web_version
        )

    def _auto_refresh_steam_builds(self):
        if self._steam_build_queue or self._active_steam_build_workers:
            return
        candidates = []
        for title, meta in self.installed_games.items():
            if not meta or meta.get('is_orphan') or is_local_install(meta):
                continue
            status = self.master_catalog.get_version_status(title)
            app_id = str(status.get('app_id', '') or meta.get('steam_id', '') or '')
            if not app_id:
                continue
            if self.master_catalog.is_steam_build_stale(title):
                candidates.append((title, app_id))
        self._steam_build_queue = candidates[:10]
        self._process_steam_build_queue()

    def _process_steam_build_queue(self):
        if not self._steam_build_queue or self._active_steam_build_workers:
            return
        title, app_id = self._steam_build_queue.pop(0)
        self._start_steam_build_worker(title, app_id, silent=True)
