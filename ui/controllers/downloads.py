"""Download lifecycle, extraction, cookies and Cloudflare gate.

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
from core.download_utils import normalize_dest_path
from core.meta_utils import EXTRACTING_MARKER, STEAMRIP_META_FILENAME
from core.archive_utils import (
    clear_incomplete,
    is_archive_complete,
    is_archive_path,
    is_marked_incomplete,
    mark_incomplete,
)

from ui.dialogs.orphan_dialog import OrphanDialog
from ui.dialogs.incomplete_download_dialog import IncompleteDownloadDialog
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
    EVENT_DOWNLOAD_RESUMED,
    EVENT_HOSTER_USED,
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

class DownloadsController:
    def _trigger_cf_bypass(self, url):
        """Riceve l'allarme dal downloader e lancia lo scraper in modalità scudo."""
        self.task_label.setText("🛡️ Bypass Cloudflare in corso (Invisibile)...")
        # Coloriamo la label di azzurro cyberpunk per indicare l'azione stealth
        self.task_label.setStyleSheet("color: #00d2ff; font-weight: bold; border: none; background: transparent;")
        self.scraper.solve_cloudflare(url)

    def _trigger_gate_fallback(self, url):
        """Un hoster ha negato l'accesso programmatico: apri il browser di fallback per download manuale."""
        self.task_label.setText("🔓 Hoster ha richiesto accesso manuale. Apro il browser di fallback...")
        self.task_label.setStyleSheet("color: #ffcc00; font-weight: bold; border: none; background: transparent;")
        self.scraper.open_download_link(url)

    @staticmethod
    def _explorer_search_text(entry):
        """Turn a download title into a clean Explorer search query."""
        title = str(entry.get("title") or "").strip()
        title = re.sub(r"(?i)\s*[-_ ]*steamrip(?:\.com)?.*$", "", title)
        title = re.sub(r"(?i)\s*(?:free\s*download|download)\b.*$", "", title)
        title = re.sub(
            r"(?i)\s*(?:v\d+(?:\.\d+)*|build\s*\d+).*$", "", title
        )
        return re.sub(r"\s+", " ", title.replace("_", " ")).strip()

    def _open_incomplete_in_explorer(self, entry):
        """Open the game page (or a clean title search) in the Explorer tab."""
        url = str(entry.get("page_url") or "").strip()
        if not url or url == "N/A":
            url = self._explorer_search_text(entry)
        if not url:
            return

        tabs = getattr(self, "tabs", None)
        explorer = getattr(self, "tab_explorer", None)
        if tabs is not None and explorer is not None:
            tabs.setCurrentWidget(explorer)

        search = getattr(self, "search_input", None)
        start_search = getattr(self, "start_search", None)
        if search is not None:
            search.setText(url)
            if callable(start_search):
                start_search()

    def _show_incomplete_downloads(self, entries):
        if not entries:
            return
        dialog = IncompleteDownloadDialog(
            entries,
            self.app_config.get("theme", {}),
            self._open_incomplete_in_explorer,
            self,
        )
        dialog.exec()

    def _on_cf_bypassed(self, html, _):
        """Scudo terminato! Riassegna il colore verde del download e invia i cookie freschi al worker dormiente."""
        if self.dl_manager.active_worker:
            self.task_label.setText(f"⬇️ Downloading: {self.dl_manager.current_task['filename']}")
            self.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
            # Mandando global_cookies.copy(), passiamo il token cf_clearance salvato dinamicamente da Steam!
            self.dl_manager.active_worker.cf_bypass_finished(html, self.global_cookies.copy())

    def on_extraction_started(self, url):
        self.db_progress.show()
        if self._pending_auto_populate_folder:
            self.lbl_game_title.setText("⚙️ Auto-Populating Metadata...")
            self.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Scaricamento metadati in corso...</h3><p>{url}</p>")
        else:
            self.lbl_game_title.setText("Game selected!")
            self.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Downloading game card from...</h3><p>{url}</p>")
        self.search_input.setText(url)

    def open_download_gate(self, link_data):
        if isinstance(link_data, str):
            url = link_data
            is_update = False
        else:
            url = link_data.get('url')
            is_update = link_data.get('is_update', False)

        if url.startswith("magnet:"):
            QDesktopServices.openUrl(QUrl(url))
            return

        from network.resolver import HOSTER_RESOLVERS
        from urllib.parse import urlparse
        import re

        domain = urlparse(url).netloc.lower().replace('www.', '')
        is_supported = any(pattern in domain for pattern in HOSTER_RESOLVERS.keys())
        provider_name = ''
        if isinstance(link_data, dict):
            provider_name = str(link_data.get('name') or '').strip()
        if not provider_name or provider_name.lower() in ('download', 'torrent (magnet)'):
            provider_name = domain or 'direct'

        events = getattr(self, 'events', None)
        if events is not None:
            events.emit_event(
                EVENT_HOSTER_USED,
                hoster=domain or 'direct',
                title=self.current_game_data.get('title', ''),
                update=bool(is_update),
                fallback=not is_supported,
            )

        if is_supported:
            print(f"[App] Hoster supportato ({domain}). Bypass del browser in corso!")

            # --- ESTRAZIONE DATI RICCHI PER LA UI AL NEON ---
            game_title = self.current_game_data.get('title', 'Unknown Game')
            web_ver = self.current_game_data.get('version', 'Unknown')
            meta = self.installed_games.get(game_title, {})
            installed_ver = meta.get('version', 'Not Installed')
            cover_urls = self.current_game_data.get('cover_urls', [])
            cover_url = cover_urls[0] if cover_urls else ""
            cover_url = CoverCache.resolve_single(cover_url, title=game_title, installed_games=self.installed_games)

            size_match = re.search(r'Size:.*?([\d\.]+\s*(?:GB|MB|TB|gb|mb|tb|[MGT]B))', self.current_game_data.get('game_info', ''), re.IGNORECASE | re.DOTALL)
            space_needed = size_match.group(1) if size_match else "Unknown"

            dest_folder_for_dl = self.temp_folder
            final_patch_dir = ""

            if is_update:
                watched = self.app_config.get("watched_folders", [])
                if not watched:
                    QMessageBox.warning(self, "Missing Library", 
                                        "You must configure at least one 'Game Library' in Settings to download patches into your library.")
                    return
                else:
                    dialog = QDialog(self)
                    dialog.setWindowTitle("Patch Destination")
                    dialog_layout = QVBoxLayout(dialog)
                    lbl = QLabel("Choose a folder to save the patch:")
                    dialog_layout.addWidget(lbl)

                    combo = QComboBox()
                    combo.addItems(watched)
                    dialog_layout.addWidget(combo)

                    btn_box = QPushButton("Confirm Destination")
                    dialog_layout.addWidget(btn_box)

                    self._selected_dest = ""
                    def on_confirm():
                        self._selected_dest = combo.currentText()
                        dialog.accept()

                    btn_box.clicked.connect(on_confirm)
                    dialog.exec()
                    chosen = self._selected_dest

                if not chosen:
                    return # User cancelled

                final_patch_dir = os.path.join(chosen, "patches", game_title)
                if not os.path.exists(final_patch_dir):
                    os.makedirs(final_patch_dir)

                dest_folder_for_dl = final_patch_dir

            safe_title = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', game_title)
            if is_update:
                safe_ver = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', str(web_ver))
                safe_filename = f"{safe_title}_V_{safe_ver}.patch.zip"
            else:
                safe_filename = f"{safe_title}_download.zip"
            full_dest_path = os.path.join(dest_folder_for_dl, safe_filename)

            # Inviamo tutto in coda senza passare dal popup!
            self.dl_manager.add_to_queue({
                "url": url,
                "filename": safe_filename,
                "dest_path": full_dest_path,
                "cookies": self.global_cookies.copy(),
                "user_agent": self.main_profile.httpUserAgent(),
                "referer": "https://steamrip.com/",  # <--- FIX: Firma Falsa Anti-Hotlink
                "title": game_title + (" (Patch)" if is_update else ""),
                "web_version": web_ver,
                "installed_version": installed_ver,
                "cover_url": cover_url,
                "space_needed": space_needed,
                "is_update": is_update,
                "game_info": self.current_game_data.get('game_info', ''),
                "description": self.current_game_data.get('description', ''),
                "page_url": self.current_game_data.get('source_url', self.current_game_data.get('page_url', 'N/A')),
                "provider": provider_name,
                "provider_domain": domain,
            })
        else:
            print(f"[App] Hoster sconosciuto. Apertura browser di fallback...")
            self.scraper.open_download_link(url)

    def _on_download_resumed(self, downloaded_bytes, total_bytes):
        events = getattr(self, 'events', None)
        if events is None:
            return
        title = ''
        if getattr(self.dl_manager, 'current_task', None):
            title = self.dl_manager.current_task.get('title', '')
        events.emit_event(
            EVENT_DOWNLOAD_RESUMED,
            title=title,
            bytes=int(downloaded_bytes or 0),
            total_bytes=int(total_bytes or 0),
        )

    def handle_webengine_download(self, download: QWebEngineDownloadRequest):
        from PyQt6.QtWebEngineCore import QWebEnginePage # Assicurati che sia importato!
        
        temp_dl_url = download.url().toString()
        temp_dl_filename = download.suggestedFileName()
        temp_dl_dest = os.path.join(self.temp_folder, temp_dl_filename)
        from urllib.parse import urlparse
        web_domain = urlparse(temp_dl_url).netloc.lower().replace('www.', '')
        web_provider = web_domain.split('.')[0].capitalize() if web_domain else ''
        
        download.cancel() 
        
        referer = ""
        if hasattr(self, 'scraper') and self.scraper.browser:
            referer = self.scraper.browser.url().toString()
            
            # GESTIONE FINESTRE SICURA PER LINUX
            # 1. Stoppa la navigazione per non sprecare CPU/RAM
            self.scraper.browser.page().triggerAction(QWebEnginePage.WebAction.Stop)
            # 2. Nasconde dolcemente (niente teletrasporto a -10000)
            self.scraper.browser.hide()                  
            self.scraper.browser.load(QUrl("about:blank")) 
            
            # 3. Richiama l'app principale solo se minimizzata
            if self.windowState() & Qt.WindowState.WindowMinimized:
                # Togli lo stato "Minimizzato" senza toccare il "Maximized" (se presente)
                self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
            self.activateWindow()
            
        print(f"[App] Intercettato link! Cookie passati al Manager.")
        
        # --- ESTRAZIONE DATI RICCHI PER LA NUOVA UI ---
        game_title = self.current_game_data.get('title', temp_dl_filename)
        web_ver = self.current_game_data.get('version', 'Unknown')
        
        # Controllo se è già installato per la versione locale
        meta = self.installed_games.get(game_title, {})
        installed_ver = meta.get('version', 'Not Installed')
        
        cover_urls = self.current_game_data.get('cover_urls', [])
        cover_url = cover_urls[0] if cover_urls else ""
        cover_url = CoverCache.resolve_single(cover_url, title=game_title, installed_games=self.installed_games)
        
        # Estrazione peso approssimativo per UI (Se disponibile nella descrizione)
        import re
        size_match = re.search(r'Size:.*?([\d\.]+\s*(?:GB|MB|TB|gb|mb|tb|[MGT]B))', self.current_game_data.get('game_info', ''), re.IGNORECASE | re.DOTALL)
        space_needed = size_match.group(1) if size_match else "Unknown"
        # -----------------------------------------------

        self.dl_manager.add_to_queue({
            "url": temp_dl_url,
            "filename": temp_dl_filename,
            "dest_path": temp_dl_dest,
            "cookies": self.global_cookies.copy(),
            "user_agent": self.main_profile.httpUserAgent(),
            "referer": referer,
            # Passiamo i nuovi dati alla Super-UI!
            "title": game_title,
            "web_version": web_ver,
            "installed_version": installed_ver,
            "cover_url": cover_url,
            "space_needed": space_needed,
            "game_info": self.current_game_data.get('game_info', ''),
            "description": self.current_game_data.get('description', ''),
            "page_url": self.current_game_data.get('source_url', self.current_game_data.get('page_url', 'N/A')),
            "provider": web_provider,
            "provider_domain": web_domain,
        })

    def _sync_dl_started(self, filename):
        self.task_progress.show()
        self.btn_pause_dl.show()
        self.btn_pause_dl.setIcon(self.icon_pause) # Resetta l'icona su Pausa all'avvio
        self.btn_stop_dl.show()
        self.task_label.setText(f"⬇️ Downloading: {filename}")
        self.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
        
        current_task = self.dl_manager.current_task
        if current_task:
            # A new attempt may resume a file we previously flagged as
            # incomplete, so the marker must go away immediately.
            clear_incomplete(current_task.get('dest_path', ''))
        if current_task and current_task.get('is_update'):
            QMessageBox.information(self, "Patch Application", f"Since patches aren't standardized, patch application has to be manually done by the User (you). Check the web page or content of the zip file for instructions.\n\nPatch is located here:\n{current_task.get('dest_path')}")

    def _sync_dl_progress(self, pct, mb_dl, mb_tot):
        self.task_progress.setValue(pct)
        self.task_progress.setFormat(f"{pct}%") # Testo minimalista per la barra piccola

    def _sync_dl_completed(self, final_path):
        current_task = self.dl_manager.current_task
        if current_task and current_task.get('is_update'):
            self.task_label.setText("✅ Patch Download Complete!")
            self.dl_manager.finish_extraction()
        else:
            if is_archive_path(final_path) and not is_archive_complete(final_path):
                # Last line of defence: never offer a truncated archive for
                # extraction. Keep the file so the download can resume.
                mark_incomplete(final_path, "truncated")
                self.task_label.setText("❌ Incomplete download")
                self.task_label.setStyleSheet("color: #ff00a0; font-weight: bold; border: none; background: transparent;")
                entry = dict(current_task or {})
                entry["archive_path"] = final_path
                self._show_incomplete_downloads([entry])
                return
            clear_incomplete(final_path)
            self.task_label.setText("✅ Download Complete! Preparing extraction...")
            self.installer_flow.prompt_installation(final_path, task_data=current_task)

    def _sync_dl_error(self, err_msg):
        self.task_label.setText("❌ Download Error!")
        self.task_label.setStyleSheet("color: #ff00a0; font-weight: bold; border: none; background: transparent;")
        QMessageBox.warning(self, "Download Error", f"A problem occurred:\n{err_msg}")

    def _sync_dl_empty(self):
        self.task_label.setText("Idle: Ready for action")
        self.task_label.setStyleSheet(f"color: {self.app_config['theme']['text_primary_white']}; font-weight: bold; border: none; background: transparent;")
        self.task_progress.hide()
        self.btn_pause_dl.hide()
        self.btn_stop_dl.hide()

    @staticmethod
    def _normalize_extract_name(text):
        """Normalize an archive/folder/title for the already-installed check."""
        name = os.path.splitext(os.path.basename(str(text or "").replace("\\", "/")))[0]
        name = re.sub(
            r"(?i)(\s*free\s*download.*|\s*download.*|"
            r"\s*v\d+(?:\.\d+)*.*|\s*build\s*\d+.*)",
            "",
            name,
        ).strip()
        return re.sub(r"[^a-z0-9]", "", name.lower())

    def _archive_matches_installed(self, archive_path, title_guess=""):
        """True when the archive belongs to a genuinely installed game.

        A game counts as installed only when the folder matches the archive
        name, contains ``steamrip_meta.json`` and is not flagged by the
        in-progress extraction marker. Partial/interrupted extractions must
        stay recoverable, so they keep prompting.
        """
        archive_names = {
            self._normalize_extract_name(archive_path),
            self._normalize_extract_name(title_guess),
        }
        archive_names.discard("")
        if not archive_names:
            return False
        for title, meta in (getattr(self, "installed_games", {}) or {}).items():
            if not isinstance(meta, dict) or meta.get("is_orphan"):
                continue
            install_dir = str(meta.get("install_dir") or "").strip()
            if not install_dir or not os.path.isdir(install_dir):
                continue
            if not os.path.isfile(
                os.path.join(install_dir, STEAMRIP_META_FILENAME)
            ):
                continue
            if os.path.isfile(os.path.join(install_dir, EXTRACTING_MARKER)):
                continue
            installed_names = {
                self._normalize_extract_name(title),
                self._normalize_extract_name(install_dir),
            }
            installed_names.discard("")
            if archive_names & installed_names:
                return True
        return False

    def _recover_pending_extractions(self):
        """All'avvio: cerca archivi completati ma non estratti e propone estrazione."""
        if self.is_task_running:
            return

        # 1. Recovery delle entry esplicite in pending_extractions
        valid = self.dl_manager.recover_pending_extractions()

        active_tasks = []
        current_task = getattr(self.dl_manager, "current_task", None)
        if isinstance(current_task, dict):
            active_tasks.append(current_task)
        active_tasks.extend(
            task for task in (getattr(self.dl_manager, "queue", []) or [])
            if isinstance(task, dict)
        )
        active_paths = {
            normalize_dest_path(task.get("dest_path"))
            for task in active_tasks if task.get("dest_path")
        }

        installed_entries = []
        incomplete_entries = []
        remaining = []
        for entry in valid:
            archive_path = entry.get("archive_path", "")
            title = entry.get("title", "")
            if normalize_dest_path(archive_path) in active_paths:
                print(
                    f"[App] Skip pending extraction: '{title}' is still in "
                    "the download queue"
                )
                self.dl_manager.remove_pending_extraction(archive_path)
                continue
            if self._archive_matches_installed(archive_path, title):
                installed_entries.append(entry)
                continue
            if is_marked_incomplete(archive_path):
                self.dl_manager.remove_pending_extraction(archive_path)
                continue
            if not is_archive_complete(archive_path):
                print(
                    f"[App] Incomplete download: '{title}' ({archive_path}) "
                    "cannot be extracted"
                )
                mark_incomplete(archive_path, "truncated")
                incomplete_entries.append(entry)
                self.dl_manager.remove_pending_extraction(archive_path)
                continue
            remaining.append(entry)

        for entry in installed_entries:
            archive_path = entry.get("archive_path", "")
            print(
                f"[App] Skip pending extraction: '{entry.get('title', '')}' "
                "is already installed"
            )
            self.dl_manager.remove_pending_extraction(archive_path)
        valid = remaining

        # 2. Scan della temp folder per archivi orfani (>100 MB)
        # Never treat the active download or queued downloads as orphans: they
        # are partial files that the downloader will resume.
        known = {
            normalize_dest_path(e.get("archive_path"))
            for e in valid if e.get("archive_path")
        }
        if os.path.isdir(self.temp_folder):
            for fname in os.listdir(self.temp_folder):
                if not fname.lower().endswith(('.zip', '.7z', '.rar')):
                    continue
                fpath = os.path.join(self.temp_folder, fname)
                key = normalize_dest_path(fpath)
                if key in known or key in active_paths:
                    continue
                try:
                    if os.path.getsize(fpath) < 100 * 1024 * 1024:
                        continue
                except OSError:
                    continue
                # Archivio orfano trovato — aggiungilo alla lista
                title_guess = fname.rsplit('.', 1)[0].replace('_', ' ')
                if self._archive_matches_installed(fpath, title_guess):
                    print(
                        f"[App] Skip pending extraction: '{fname}' is already "
                        "extracted in an installed game folder"
                    )
                    continue
                if is_marked_incomplete(fpath):
                    continue
                if not is_archive_complete(fpath):
                    print(
                        f"[App] Incomplete download: '{fname}' is truncated "
                        "and will not be offered for extraction"
                    )
                    mark_incomplete(fpath, "truncated")
                    incomplete_entries.append({
                        "archive_path": fpath,
                        "title": title_guess,
                    })
                    continue
                valid.append({
                    "archive_path": fpath,
                    "title": title_guess,
                    "web_version": "",
                    "game_info": "",
                    "description": "",
                    "page_url": "",
                    "cover_url": "",
                    "provider": "",
                    "provider_domain": "",
                })

        if incomplete_entries:
            names = ", ".join(
                entry.get('title') or os.path.basename(entry.get('archive_path', ''))
                for entry in incomplete_entries
            )
            print(f"[App] Incomplete downloads skipped: {names}")
            self._show_incomplete_downloads(incomplete_entries)

        if not valid:
            return

        print(f"[App] Trovati {len(valid)} download da estrarre")
        entry = valid[0]
        archive_path = entry.get("archive_path", "")
        if not os.path.exists(archive_path):
            self.dl_manager.remove_pending_extraction(archive_path)
            return
        title = entry.get("title", "Unknown")
        provider = entry.get("provider") or entry.get("provider_domain") or ""
        provider_line = f"Provider used: {provider}\n\n" if provider else ""
        task_data = {
            "title": title,
            "web_version": entry.get("web_version", ""),
            "game_info": entry.get("game_info", ""),
            "description": entry.get("description", ""),
            "page_url": entry.get("page_url", ""),
            "cover_url": entry.get("cover_url", ""),
            "provider": entry.get("provider", ""),
            "provider_domain": entry.get("provider_domain", ""),
        }
        reply = QMessageBox.question(
            self, "Pending Extraction",
            f"'{title}' has been downloaded but not yet extracted.\n\n"
            f"{provider_line}"
            f"Archive: {archive_path}\n\n"
            "Would you like to extract it now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.is_task_running = True
            self.task_label.setText("📦 Extracting pending download...")
            self.task_label.setStyleSheet("color: #ffcc00; font-weight: bold; border: none; background: transparent;")
            self.installer_flow.prompt_installation(archive_path, task_data=task_data)
        else:
            self.dl_manager.remove_pending_extraction(archive_path)

    def toggle_dl_pause(self):
        # Questo bottone ora agisce solo come un telecomando verso il vero motore in dl_manager
        if self.dl_manager.current_task:
            self.dl_manager.toggle_pause()

    def _cancel_extraction(self):
        if hasattr(self.installer_flow, 'installer_thread') and self.installer_flow.installer_thread.isRunning():
            self.installer_flow.installer_thread.cancel()
            self.installer_flow.installer_thread.wait(1000)
            self.task_label.setText("Idle: Ready for action")
            self.task_label.setStyleSheet(f"color: {self.app_config['theme']['text_primary_white']}; font-weight: bold; border: none; background: transparent;")
            self.task_progress.hide()
            self.is_task_running = False
            self.dl_manager.finish_extraction()

    def _sync_global_pause_state(self, is_paused):
        # Quando dl_manager cambia stato (sia internamente che via telecomando), aggiorna l'UI Globale
        if is_paused:
            self.btn_pause_dl.setIcon(self.icon_play)
            self.task_label.setText(f"⏸️ Paused: {self.dl_manager.current_task['filename']}")
            self.task_label.setStyleSheet("color: #ffcc00; font-weight: bold; border: none; background: transparent;")
        else:
            self.btn_pause_dl.setIcon(self.icon_pause)
            self.task_label.setText(f"⬇️ Downloading: {self.dl_manager.current_task['filename']}")
            self.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
        self.btn_pause_dl.clearFocus()

    def _on_cookie_extracted(self, cookie: QNetworkCookie):
        # Convertiamo il cookie in un formato dizionario leggibile da Python "requests"
        name = str(cookie.name(), 'utf-8')
        value = str(cookie.value(), 'utf-8')
        self.temp_extracted_cookies[name] = value

    def handle_error(self, msg):
        # Background failures (catalog fetch, discovery, cover download) are
        # not task failures: show a friendly notice instead of "Task failed".
        if not getattr(self, "is_task_running", False):
            if hasattr(self, "_on_discovery_error"):
                self._on_discovery_error(msg)
            return
        self.db_progress.hide()
        self.is_task_running = False
        self.task_label.setText("❌ Error during task.")
        self.task_label.setStyleSheet("color: #ff00a0; font-weight: bold; border: none; background: transparent;")
        self.task_progress.hide()
        
        if hasattr(self, 'dl_manager') and hasattr(self.dl_manager, 'is_extracting') and self.dl_manager.is_extracting:
            self.dl_manager.finish_extraction()
            
        self.lbl_game_title.setText("Error")
        self.desc_display.setHtml(f"<h3>Task failed.</h3><p>Trace: {msg}</p>")
