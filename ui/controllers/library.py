"""Library item interactions, context menu and custom save paths.

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

class LibraryController:
    def _update_library_item_from_bg(self, game_title, meta, status):
        # Se lo scanner ha trovato un update, invalida la cache di sessione
        if meta and "Update available" in status:
            source_url = meta.get('source_url', '').rstrip('/')
            if source_url and source_url in self.session_cache:
                del self.session_cache[source_url]
                print(f"[App] Invalidated session cache for '{game_title}' (background update detected)")
        item = self.lib_manager._find_item_by_title(game_title)
        if item:
            self.lib_manager._update_library_item(item, game_title, status, meta=meta)

    def on_library_item_clicked(self, item):
        _click_t0 = time.perf_counter()
        # Elegant extraction via native Qt UserRole metadata
        raw_title = item.data(Qt.ItemDataRole.UserRole)
        meta = self.installed_games.get(raw_title)
        if not meta: return
        
        # --- FIX: ANTI-CONTAMINATION SHIELD ---
        # If the user abandons the auto-populate flow by clicking a normal game,
        # we immediately reset the pending folder state to protect local directories.
        if not meta.get("is_orphan"):
            self._pending_auto_populate_folder = None
            
        if meta.get("is_orphan"):
            dialog = OrphanDialog(meta["folder_name"], self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                if dialog.choice == "Download":
                    self.search_input.setText(meta["folder_name"])
                    self.start_search()
                elif dialog.choice == "Populate":
                    self._pending_auto_populate_folder = meta["install_dir"]
                    QMessageBox.information(self, "Auto-Populate", "Search for the correct game (if the folder name is not exact, modify it in the top bar and press Enter).\n\nMetadata will be generated automatically AS SOON AS the game tab is opened!")
                    
                    # --- FIX FEEDBACK: Aggiorniamo subito la riga nella sidebar! ---
                    self.lib_manager._update_library_item(item, f"⏳ {meta['folder_name']}", "(generating JSON...)", title_color="#00ff88", sub_color="#00cc6a")
                    
                    self.search_input.setText(meta["folder_name"])
                    self.start_search()
                elif dialog.choice == "Local":
                    # Gioco installato da fonte esterna (non SteamRip): titolo precompilato
                    # dal nome della cartella ESTERNA (root del gioco), modificabile per
                    # il matching Ludusavi (save scan) e SteamGridDB (cover / steam_grid_id).
                    title, ok = QInputDialog.getText(
                        self,
                        "Add as Local Only",
                        "Game title (pre-filled from the game folder name):\n"
                        "Use the EXACT title so Ludusavi can find the save path and "
                        "SteamGridDB can find the covers.",
                        text=meta["folder_name"],
                    )
                    if ok and title.strip():
                        self.installer_flow.create_local_only_metadata(meta["install_dir"], title.strip())
                        self.lib_manager.scan_library()
                        QMessageBox.information(
                            self,
                            "Local Game Added",
                            f"{title.strip()} was added as a Local Game.\n\n"
                            "Update checks are disabled (external source).\n"
                            "Click it to open its local card and configure save paths.",
                        )
            
            return

        if not meta.get('savegame_path_win') and not meta.get('savegame_path_linux'):
            self.savegames_manager.ensure_save_path_for_game(meta)
            self.lib_manager._update_library_item(item, raw_title, "Installed", meta=meta, game_title=raw_title)
            if hasattr(self, 'savegames_tab'):
                self.savegames_tab.refresh_view()

        self.clear_links()
        self.current_game_data = {} 
        
        url = meta.get('source_url')
        if not url or url == 'N/A':
            if is_local_install(meta):
                # Gioco installato localmente ma NON su SteamRip: mostriamo la scheda locale
                self._display_local_game(meta)
            else:
                QMessageBox.warning(self, "No URL", "Original URL not found for this game.\nYou need to re-populate the metadata.")
            return

        clean_url = url.rstrip('/') # <--- NORMALIZZIAMO QUI
        self.search_input.setText(clean_url)

        # LOGICA DI CACHING CON CONTROLLO DI FRESCHEZZA
        if clean_url in self.session_cache:
            cached = self.session_cache[clean_url]
            if self._is_cache_stale_for_installed_game(cached, raw_title):
                print(f"[App] Cache stale for '{raw_title}', forcing re-fetch")
                del self.session_cache[clean_url]
                self.start_search()
            else:
                self.lbl_game_title.setText(f"Loading {raw_title} from cache...")
                if os.environ.get("SRP_PROFILE"):
                    print(f"[Perf] on_library_item_clicked pre-display: {(time.perf_counter() - _click_t0) * 1000:.0f} ms")
                self.display_game_data(cached)
        else:
            self.start_search()

    def _display_local_game(self, meta):
        """Mostra la scheda di un gioco installato localmente ma NON disponibile su SteamRip."""
        self.clear_links()
        title = meta.get('title') or os.path.basename(str(meta.get('install_dir') or ''))

        sync_folder = self.app_config.get('savegames', {}).get('sync_folder', '')
        cover = MiniCoverManager.resolve_cover_path(title, self.installed_games, sync_folder)
        cover_urls = [cover] if cover else []

        folder = str(meta.get('install_dir') or '')
        exe = meta.get('executable_path') or 'Not set — use ▶ Play to select'
        save_custom = str(meta.get('savegame_path_custom') or '')
        save_win = str(meta.get('savegame_path_win') or '')
        save_linux = str(meta.get('savegame_path_linux') or '')
        save_display = next((p for p in (save_custom, save_win, save_linux) if p), 'Not configured')
        steam_id = str(meta.get('steam_shortcut_id') or '')
        steam_status = f"Integrated (shortcut {steam_id})" if steam_id.strip() else "Not integrated"

        esc = html.escape

        description = (
            "<h2>Local Game</h2>"
            f"<p style='color:#e0e0e0;'>This game is an <b>external/local install</b> and is not available on SteamRip, "
            "so update checks are disabled. Use your original source to check for updates.</p>"
            "<table cellspacing='4'>"
            f"<tr><td><b>&#128193; Install folder:</b></td><td>{esc(folder)}</td></tr>"
            f"<tr><td><b>&#128640; Executable:</b></td><td>{esc(exe)}</td></tr>"
            f"<tr><td><b>&#128190; Save path:</b></td><td>{esc(save_display)}</td></tr>"
            f"<tr><td><b>&#127918; Steam:</b></td><td>{esc(steam_status)}</td></tr>"
            "</table>"
        )

        info = (
            "<p><b>Source:</b> External / manual install (not via SteamRip).</p>"
            f"<p><b>Install directory:</b> {esc(folder)}</p>"
            f"<p><b>Multiplayer:</b> {esc(meta.get('multiplayer_crack') or 'None / Unknown')}</p>"
        )

        requirements = (
            "<p>Update checks are disabled for local games. "
            "Use the launcher's context menu to launch, browse files, manage savegames or integrate with Steam.</p>"
        )

        data = {
            "title": title,
            "version": "",
            "mp_tag": "",
            "description": description,
            "game_info": info,
            "requirements": requirements,
            "links": [],
            "page_url": "",
            "img_url": "",
            "cover_urls": cover_urls,
            "source_url": "",
            "steam_id": "",
            "local_only": True,
        }
        self.display_game_data(data)

    def on_library_item_double_clicked(self, item):
        raw_title = item.data(Qt.ItemDataRole.UserRole)
        meta = self.installed_games.get(raw_title)
        if meta and not meta.get("is_orphan"):
            self.game_launcher.launch_game(raw_title)

    def show_library_context_menu(self, pos):
        item = self.lib_list.itemAt(pos)
        if not item: return
        
        # Safe extraction directly from hidden anchor data
        raw_title = item.data(Qt.ItemDataRole.UserRole)
        meta = self.installed_games.get(raw_title)
        if not meta: return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2b1a42; border: 1px solid #733d91; border-radius: 6px; padding: 4px; }
            QMenu::item { color: #ffffff; padding: 8px 24px; border-radius: 4px; }
            QMenu::item:selected { background-color: #3b2a52; }
            QMenu::item:disabled { color: #8a7fa4; background: transparent; padding: 6px 12px; font-size: 11px; }
            QMenu::separator { height: 1px; background: #3d2a5a; margin: 4px 8px; }
        """)
        
        play_action = menu.addAction("▶ Play Game")

        h = menu.addAction("── Game Commands ──")
        h.setEnabled(False)
        browse_action = menu.addAction("📂 Browse Local Files")
        open_pfx_action = None
        if sys.platform == 'linux' and meta.get('steam_grid_id'):
            open_pfx_action = menu.addAction("🐧 Open PFX Folder")
        page_action = None
        update_action = None
        url_ok = bool(meta.get('source_url')) and meta.get('source_url') != 'N/A'
        if not meta.get("is_orphan") and url_ok:
            page_action = menu.addAction("🌐 Go to Game Page")
            update_action = menu.addAction("🔄 Refresh / Update Cache")

        menu.addSeparator()
        h2 = menu.addAction("── Savegames ──")
        h2.setEnabled(False)
        open_saves_action = menu.addAction("📁 Open Savegames Folder")
        open_backup_action = menu.addAction("🗄️ Open Backup Saves Folder")
        rescan_save_action = menu.addAction("🔄 Rescan for Save Folder")
        point_save_action = menu.addAction("📍 Point to Saves Folder")

        menu.addSeparator()
        h3 = menu.addAction("── Management ──")
        h3.setEnabled(False)
        rescan_action = menu.addAction("🔍 Rescan Executable")
        uninstall_action = menu.addAction("🗑️ Uninstall Game")

        action = menu.exec(self.lib_list.mapToGlobal(pos))
        
        # --- LOGICA AZIONI ---
        if action == play_action:
            self.game_launcher.launch_game(raw_title)
            
        elif action == browse_action:
            self.open_local_path(meta.get('install_dir'))

        elif open_pfx_action and action == open_pfx_action:
            grid_id = meta.get('steam_grid_id')
            pfx_path = self.savegames_manager._get_compatdata_base(grid_id)
            if pfx_path and os.path.exists(pfx_path):
                self.open_local_path(pfx_path)
            else:
                QMessageBox.warning(self, "PFX Not Found",
                    f"Proton prefix not found for <b>{raw_title}</b>.\n\n"
                    f"Expected at: {pfx_path}\n\n"
                    "Try launching the game once via Steam first.")

        elif action == open_saves_action:
            save_path = self.savegames_manager.get_source_save_path(meta)
            if save_path:
                self.open_local_path(save_path)
            else:
                self._prompt_save_path_fallback(raw_title, meta, item)

        elif action == open_backup_action:
            backup_path = self._get_game_backup_folder(raw_title)
            if backup_path:
                self.open_local_path(backup_path)
            else:
                QMessageBox.warning(self, "No Backup Folder", "Backup saves folder is not available for this game. Run a save sync first.")

        elif action == rescan_save_action:
            updated_meta = self.savegames_manager.rescan_save_folder(meta)
            
            if updated_meta:
                resolved_path = self.savegames_manager.get_source_save_path(updated_meta)
                if resolved_path and os.path.exists(resolved_path):
                    QMessageBox.information(self, "Save Folder Rescanned",
                        "Save path has been refreshed from the manifest and steamrip_meta.json has been updated.")
                    self.lib_manager._update_library_item(item, raw_title, "Installed", meta=updated_meta, game_title=raw_title)
                else:
                    self._prompt_save_path_fallback(raw_title, updated_meta, item)

        elif action == point_save_action:
            self._set_custom_save_path_for_game(raw_title, meta, item)

        elif page_action and action == page_action:
            url = meta.get('source_url')
            if url and url != 'N/A':
                # Open the official external URL inside the operating system's default native browser
                QDesktopServices.openUrl(QUrl(url))
            else:
                QMessageBox.warning(self, "No URL", "URL not found for this game.")

        elif update_action and action == update_action:
            url = meta.get('source_url')
            if url and url != 'N/A':
                if url in self.session_cache:
                    del self.session_cache[url]
                self._update_check_game = raw_title
                self.search_input.setText(url)
                self.start_search()
            else:
                QMessageBox.warning(self, "No URL", "Impossibile aggiornare la cache: URL della pagina gioco non disponibile.")
        
        elif action == rescan_action:
            self.game_launcher.rescan_executable(raw_title, meta)
                
        elif action == uninstall_action:
            reply = QMessageBox.question(self, 'Confirm Uninstall', f"Are you sure you want to permanently delete '{raw_title}'?\n\nThis operation will remove the folder:\n{meta['install_dir']}", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.desc_display.setHtml(f"<h3 style='color:#ff00a0;'>Removing {raw_title} in progress...</h3>")
                QApplication.processEvents()

                if steam_integration.is_game_in_steam(raw_title):
                    steam_reply = QMessageBox.question(self, 'Remove from Steam',
                        f"'{raw_title}' is in your Steam Library.\n\nDo you want to remove it from Steam too?",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                    if steam_reply == QMessageBox.StandardButton.Yes:
                        restart_dialog = SteamRestartDialog(self)
                        restart_dialog.exec()
                        try:
                            steam_integration.remove_from_steam(raw_title, restart_steam=restart_dialog.restart_now)
                        except Exception as e:
                            print(f"[Steam] Errore durante la rimozione da Steam: {e}")

                if hasattr(self, 'savegames_manager'):
                    self.savegames_manager.persist_mini_cover_on_uninstall(raw_title, meta)

                try:
                    shutil.rmtree(meta['install_dir'], ignore_errors=True)
                    self.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Game uninstalled successfully.</h3>")
                    self.lib_manager.scan_library()
                    if hasattr(self, 'tab_discovery'):
                        self.tab_discovery.refresh_library()
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Impossible to completely remove the folder:\n{str(e)}")

    def _set_custom_save_path_for_game(self, raw_title, meta, item):
        has_custom = bool(meta.get('savegame_path_custom'))
        if has_custom:
            msg = QMessageBox(self)
            msg.setWindowTitle("Custom Save Path")
            msg.setText(f"<b>{raw_title}</b> has a custom save path:<br><code>{meta['savegame_path_custom']}</code>")
            msg.setInformativeText("What would you like to do?")
            msg.setIcon(QMessageBox.Icon.Question)
            btn_browse = msg.addButton("Change Path", QMessageBox.ButtonRole.AcceptRole)
            btn_revert = msg.addButton("Revert to Auto-Detect", QMessageBox.ButtonRole.RejectRole)
            msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_revert:
                self.savegames_manager.clear_custom_save_path(meta)
                self.lib_manager._update_library_item(item, raw_title, "Installed", meta=meta, game_title=raw_title)
                self._update_desc_custom_save_path(meta)
                QMessageBox.information(self, "Reverted", f"Custom save path removed for <b>{raw_title}</b>. The app will auto-detect the save folder again.")
                if hasattr(self, 'savegames_tab'):
                    self.savegames_tab.refresh_view()
                return
            elif clicked != btn_browse:
                return

        start_dir = ""
        if sys.platform == 'linux' and meta.get('steam_grid_id'):
            pfx = self.savegames_manager._get_compatdata_base(meta['steam_grid_id'])
            if pfx and os.path.exists(pfx):
                start_dir = pfx

        folder = QFileDialog.getExistingDirectory(self, f"Select Save Folder for {raw_title}", start_dir)
        if not folder:
            return

        self.savegames_manager.set_custom_save_path(meta, folder)
        self.lib_manager._update_library_item(item, raw_title, "Installed", meta=meta, game_title=raw_title)
        QMessageBox.information(self, "Custom Save Path Set",
            f"Save path for <b>{raw_title}</b> set to:<br><code>{folder}</code><br><br>"
            "<i>Tip: Launch the game at least once so it can create the save folder structure if it doesn't exist yet.</i>")
        if hasattr(self, 'savegames_tab'):
            self.savegames_tab.refresh_view()
        self._update_desc_custom_save_path(meta)

    def _update_desc_custom_save_path(self, meta):
        current_html = self.desc_display.toHtml()

        badge_marker = "<hr style='border:none; border-top:1px solid #733d91; margin:12px 0;'>"
        if badge_marker in current_html:
            start = current_html.rfind("<br>" + badge_marker)
            if start == -1:
                start = current_html.rfind(badge_marker)
            end = current_html.find("</p>", start)
            if end != -1:
                current_html = current_html[:start] + current_html[end + 4:]

        custom = meta.get('savegame_path_custom', '')
        if custom:
            badge = (
                f"<br><hr style='border:none; border-top:1px solid #733d91; margin:12px 0;'>"
                f"<p style='color:#cba6d1; font-size:12px;'>"
                f"<b>📍 Custom Save Path:</b> <code style='color:#e0e0e0;'>{custom}</code><br>"
                f"<span style='color:#8a7fa4; font-size:11px;'>Tip: The game must be launched at least once to create the save folder structure.</span>"
                f"</p>"
            )
            if '</body>' in current_html:
                current_html = current_html.replace('</body>', badge + '</body>')
            else:
                current_html += badge

        self.desc_display.setHtml(current_html)

    def _prompt_save_path_fallback(self, raw_title, meta, item):
        from urllib.parse import quote

        pfx_start_dir = ""
        if sys.platform == 'linux' and meta.get('steam_grid_id'):
            pfx = self.savegames_manager._get_compatdata_base(meta['steam_grid_id'])
            if pfx and os.path.exists(pfx):
                pfx_start_dir = pfx

        msg = QMessageBox(self)
        msg.setWindowTitle("Save Path Not Found")
        msg.setText(f"Save path could not be resolved for <b>{raw_title}</b>.")
        msg.setInformativeText("What would you like to do?")
        msg.setIcon(QMessageBox.Icon.Warning)
        btn_scan = msg.addButton("🔍 Scan Manifest", QMessageBox.ButtonRole.AcceptRole)
        btn_web = msg.addButton("🌐 Web Search", QMessageBox.ButtonRole.ActionRole)
        btn_browse = msg.addButton("📁 Browse to Folder", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked == btn_scan:
            updated_meta = self.savegames_manager.rescan_save_folder(meta)
            if updated_meta:
                new_path = self.savegames_manager.get_source_save_path(updated_meta)
                if new_path and os.path.exists(new_path):
                    self.open_local_path(new_path)
                    self.lib_manager._update_library_item(item, raw_title, "Installed", meta=updated_meta, game_title=raw_title)
                    return
            QMessageBox.information(self, "Still Not Found",
                "Manifest scan completed but no save path was found.\n"
                "Try 'Web Search' or 'Browse to Folder'.")

        elif clicked == btn_web:
            query = quote(f"{raw_title} save files location")
            QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={query}"))
            pasted = self.savegames_manager.prompt_manual_save_path(raw_title)
            if pasted:
                self.savegames_manager.set_custom_save_path(meta, pasted)
                self.lib_manager._update_library_item(item, raw_title, "Installed", meta=meta, game_title=raw_title)
                expanded = os.path.expandvars(pasted)
                if os.path.exists(expanded):
                    self.open_local_path(expanded)
                QMessageBox.information(self, "Custom Save Path Set",
                    f"Save path for <b>{raw_title}</b> set to:<br><code>{pasted}</code><br><br>"
                    "<i>Tip: Launch the game at least once so it can create the save folder structure if it doesn't exist yet.</i>")
                if hasattr(self, 'savegames_tab'):
                    self.savegames_tab.refresh_view()
                self._update_desc_custom_save_path(meta)

        elif clicked == btn_browse:
            folder = QFileDialog.getExistingDirectory(self, f"Select Save Folder for {raw_title}", pfx_start_dir)
            if folder:
                self.savegames_manager.set_custom_save_path(meta, folder)
                self.lib_manager._update_library_item(item, raw_title, "Installed", meta=meta, game_title=raw_title)
                self.open_local_path(folder)
                QMessageBox.information(self, "Custom Save Path Set",
                    f"Save path for <b>{raw_title}</b> set to:\n{folder}\n\n"
                    "Tip: Launch the game at least once so it can create the save folder structure if it doesn't exist yet.")
                if hasattr(self, 'savegames_tab'):
                    self.savegames_tab.refresh_view()
                self._update_desc_custom_save_path(meta)

    def _get_game_backup_folder(self, raw_title):
        sync_folder = self.app_config.get('savegames', {}).get('sync_folder', '').strip()
        if not sync_folder:
            return ""
        safe_title = safe_game_folder_name(raw_title)
        backup_folder = os.path.join(sync_folder, safe_title, 'saves')
        if os.path.exists(backup_folder):
            return backup_folder
        return ""

    def start_search(self):
            target = self.search_input.text().strip()
            if not target: return
            
            # 1. È un URL diretto incollato?
            if target.startswith("http"):
                url = target
                
            # 2. È un titolo esatto pescato dall'auto-completamento?
            elif target in self.master_catalog:
                url = self.master_catalog[target]
                
            # 3. È una ricerca generica?
            else:
                url = f"https://steamrip.com/?s={target.replace(' ', '+')}"
                
            # --- CHECK CACHE BEFORE SCRAPING (CON CONTROLLO DI FRESCHEZZA) ---
            clean_url = url.rstrip('/')
            if clean_url in self.session_cache:
                cached = self.session_cache[clean_url]
                title = cached.get('title', '')
                # 1. Versione installata diversa → re-fetch obbligatorio
                if title and self._is_cache_stale_for_installed_game(cached, title):
                    print(f"[App] Cache stale per '{title}' (versione diversa), re-fetch forzato")
                    del self.session_cache[clean_url]
                else:
                    # Niente HEAD sincrono: decidiamo sul TTL e, se scaduto,
                    # mostriamo subito la cache e ri-estraiamo in background.
                    cached_at = cached.get('cached_at', 0)
                    time_fresh = (time.time() - cached_at) < _PAGE_TTL if cached_at else True
                    if time_fresh:
                        # Cache valida per TTL: mostra subito, verifica HTTP in background
                        self.db_progress.hide()
                        self._check_page_freshness_async(clean_url, cached)
                        self.display_game_data(cached)
                        return
                    print(f"[App] Cache potenzialmente stale per '{title}' "
                          f"(age={int(time.time()-cached_at)}s), re-extract in background")
                    self.db_progress.hide()
                    self.display_game_data(cached)
                    self.scraper.extract_game(clean_url)
                    return
            
            # Svuotiamo l'interfaccia
            self.clear_links()
            self._display_cover_html("<style>body{margin:0;display:flex;justify-content:center;align-items:center;overflow:hidden;height:100vh;background:transparent;}.card{width:240px;height:360px;border-radius:10px;border:2px dashed #733d91;display:flex;justify-content:center;align-items:center;color:#733d91;font-family:sans-serif;font-size:16px;font-weight:bold;}</style><body><div class='card'>Waiting for data...</div></body>")
            self.req_display.clear()
            
            # --- FIX FEEDBACK: Mostriamo subito la barra e differenziamo il testo ---
            self.db_progress.show()
            if self._pending_auto_populate_folder:
                # Siamo in modalità "Ripristino metadati orfani"
                self.lbl_game_title.setText("⚙️ Auto-Populating Metadata...")
                self.desc_display.setHtml("<h3 style='color:#00ff88;'>Search and JSON generation in progress...</h3>")
            else:
                # Siamo in modalità ricerca normale
                self.lbl_game_title.setText("Data extraction in progress...")
                self.desc_display.setHtml("<h3 style='color:#00ff88;'>Connecting...</h3>")
            
            # Avviamo lo scraper
            if "?s=" in url:
                self.scraper.search_game(target) 
            else:
                self.scraper.extract_game(url)
