import logging
import os
import json
import re
from PyQt6.QtCore import Qt, QSize
#from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidgetItem
from ui.widgets.elided_label import ElidedLabel
from core.config import get_base_dir
from core.events import EVENT_GAME_INSTALLED, EVENT_GAME_REMOVED
from core.meta_status import has_save_path, has_steam_shortcut
from core.meta_utils import is_local_install  # re-exported for existing imports
from core.task_registry import TaskRegistry
from ui.library_scan_worker import (
    LibraryScanWorker,
    config_score,
    scan_library_data,
)
from core.version_status import (
    compute_version_state,
    STATE_UPDATE_AVAILABLE,
    STATE_STEAMRIP_OK_STEAM_AHEAD,
    STATE_UPDATED,
    STATE_VERSION_ONLY,
)

log = logging.getLogger(__name__)


class LibraryManager:
    def __init__(self, app_window):
        self.app_window = app_window
        self.lib_list = app_window.lib_list
        self.installed_games = app_window.installed_games
        self.app_config = app_window.app_config
        # The first scan after startup is a baseline: it must not look like
        # the user installed the whole library right now.
        self._scanned_once = False
        self._scan_worker = None
        self._scan_callback = None
        self._scan_pending_callback = None

    def _emit_event(self, name, **payload):
        events = getattr(self.app_window, 'events', None)
        if events is not None:
            try:
                events.emit_event(name, **payload)
            except Exception as exc:
                log.exception(f"[Library] event '{name}' failed: {exc}")

    def scan_library(self, on_done=None):
        """Scan the watched folders in a worker, then rebuild the sidebar.

        ``on_done`` runs on the GUI thread after the rows are applied; callers
        that need the new ``installed_games`` (startup stats, playtime sync)
        must use it instead of assuming the scan completed synchronously.
        """
        if self._scan_worker is not None and self._scan_worker.isRunning():
            self._scan_pending_callback = on_done
            return
        watched = list(self.app_window.app_config.get("watched_folders", []) or [])
        self._scan_callback = on_done
        worker = LibraryScanWorker(watched, self.app_window)
        worker.finished_ok.connect(self._on_library_scanned)
        worker.failed.connect(self._on_library_scan_failed)
        registry = TaskRegistry.instance()
        if registry is not None:
            registry.track(worker)
        self._scan_worker = worker
        worker.start()

    def scan_library_sync(self):
        """Synchronous variant for tests and headless callers."""
        watched = list(self.app_window.app_config.get("watched_folders", []) or [])
        self._apply_scan_entries(scan_library_data(watched))

    def _on_library_scanned(self, entries):
        self._scan_worker = None
        self._apply_scan_entries(entries)
        callback = self._scan_callback
        self._scan_callback = None
        pending = self._scan_pending_callback
        self._scan_pending_callback = None
        if callback is not None:
            callback()
        if pending is not None:
            self.scan_library(on_done=pending)

    def _on_library_scan_failed(self, message):
        self._scan_worker = None
        log.warning(f"[Scan] library scan failed: {message}")
        callback = self._scan_callback
        self._scan_callback = None
        if callback is not None:
            callback()

    def _apply_scan_entries(self, entries):
        previous_titles = set(self.installed_games.keys()) if self._scanned_once else set()
        self.lib_list.clear()
        self.installed_games.clear()
        seen_titles = set()

        for title, meta in entries or []:
            if title in seen_titles:
                continue
            seen_titles.add(title)
            self.installed_games[title] = meta
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, title)
            self.lib_list.addItem(item)
            if meta.get('is_orphan'):
                self._update_library_item(
                    item, f"❓ {title}", "(Missing: JSON)",
                    title_color=None, sub_color="#ffcc00",
                    meta=meta, game_title=title)
            else:
                self._update_library_item(
                    item, title, "", title_color=None,
                    sub_color="transparent", meta=meta, game_title=title)

        if self._scanned_once:
            current_titles = set(self.installed_games.keys())
            for title in current_titles - previous_titles:
                self._emit_event(
                    EVENT_GAME_INSTALLED,
                    title=title,
                    install_dir=self.installed_games.get(title, {}).get('install_dir', ''),
                )
            for title in previous_titles - current_titles:
                self._emit_event(EVENT_GAME_REMOVED, title=title)
        self._scanned_once = True

        if hasattr(self.app_window, 'savegames_tab'):
            self.app_window.savegames_tab.refresh_view()

    def _config_score(self, meta):
        return config_score(meta)

    def filter_library(self, text):
        for i in range(self.lib_list.count()):
            item = self.lib_list.item(i)
            title = item.data(Qt.ItemDataRole.UserRole)
            if title:
                item.setHidden(text.lower() not in title.lower())

    def _find_item_by_title(self, game_title):
        """Finds a library list item using its hidden UserRole title data."""
        for i in range(self.lib_list.count()):
            item = self.lib_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == game_title:
                return item
        return None

    def _config_score(self, meta):
        """Quanti campi di configurazione sono valorizzati nella meta (più alto = più completa)."""
        score = 0
        if meta.get('savegame_path_win') or meta.get('savegame_path_linux') or meta.get('savegame_path_custom'):
            score += 1
        if meta.get('steam_shortcut_id'):
            score += 1
        if meta.get('executable_path'):
            score += 1
        return score

    def _update_library_item(self, item, title_text, sub_text, title_color=None, sub_color="#a0a0a0", meta=None, game_title=None):
        """Title-only library row; the four status dots live in the Explorer."""
        if not title_color:
            title_color = self.app_config['theme']['text_primary_white']

        widget = self.lib_list.itemWidget(item)
        if not widget:
            widget = QWidget()
            widget.setStyleSheet("background: transparent;")
            main_layout = QHBoxLayout(widget)
            main_layout.setContentsMargins(8, 4, 8, 4)
            main_layout.setSpacing(0)

            lbl_title = ElidedLabel()
            lbl_title.setObjectName("ItemTitle")
            lbl_title.setStyleSheet("background: transparent; padding-bottom: 1px;")
            lbl_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            main_layout.addWidget(lbl_title, 1)
            self.lib_list.setItemWidget(item, widget)

        lbl_title = widget.findChild(QLabel, "ItemTitle")

        # The full status (update/json/saves/steam) moved to the Explorer strip.
        # The row keeps the title and the at-a-glance prefixes only.
        if meta is not None and game_title is not None:
            indicators = self._get_status_indicators(game_title, meta)
            update_available = "Update available" in indicators['update'][2]
            if update_available:
                title_text = f"❗ {game_title}"
            elif meta.get('is_orphan'):
                title_text = f"❓ {game_title}"

        if hasattr(lbl_title, "set_full_text"):
            lbl_title.set_style(title_color, 12, True)
            lbl_title.set_full_text(title_text)
        else:  # fallback difensivo
            lbl_title.setText(f"<span style='color:{title_color}; font-weight:bold; font-size:12px;'>{title_text}</span>")

        hint = widget.sizeHint()
        item.setSizeHint(QSize(hint.width(), max(hint.height(), 30)))

    def _get_status_indicators(self, game_title, meta):
        """Ritorna i 4 pallini indicatori (Update, JSON, Save, Steam) per un gioco."""
        # Colori dal tema
        color_ok = self.app_config['theme']['status_green']
        color_warn = self.app_config['theme']['status_yellow']
        color_error = self.app_config['theme']['status_red']
        
        # Inizialmente tutti gialli (stato non controllato)
        indicators = {
            'update': ('●', color_warn, 'Update not checked'),
            'json': ('●', color_warn, 'JSON not checked'),
            'save': ('●', color_warn, 'Save path not checked'),
            'steam': ('●', color_warn, 'Steam integration not found')
        }
        
        if meta and not meta.get('is_orphan'):
            # JSON: OK se esiste e non è orfano
            indicators['json'] = ('●', color_ok, 'JSON metadata present')
            
            if has_save_path(meta):
                indicators['save'] = ('●', color_ok, 'Savegame path configured')
            else:
                indicators['save'] = ('●', color_warn, 'Savegame path missing')

            if has_steam_shortcut(meta):
                indicators['steam'] = (
                    '●', color_ok, f"Steam shortcut: {meta.get('steam_shortcut_id')}"
                )
            else:
                indicators['steam'] = ('●', color_warn, 'No Steam shortcut')
            
            # Update: build-aware (installed vs SteamRIP vs Steam)
            installed_version = meta.get('version', '')
            source_url = meta.get('source_url', 'N/A')

            if is_local_install(meta):
                # Giochi locali (non SteamRip): nessun update check, il pallino passa sempre verde
                indicators['update'] = ('●', color_ok, 'Local game - updates disabled')
            elif source_url and source_url != 'N/A' and self.app_window.master_catalog:
                meta_title = meta.get('title', game_title)
                catalog_entry = self.app_window.master_catalog.get_entry(meta_title)
                if isinstance(catalog_entry, dict) and catalog_entry.get("url", "").rstrip('/') == source_url.rstrip('/'):
                    web_version = catalog_entry.get("version", "Release Base")
                    vs = self.app_window.master_catalog.get_version_status(meta_title)
                    state = compute_version_state(
                        installed_build=meta.get('build'),
                        posted_build=vs.get('posted_build'),
                        steam_build=vs.get('steam_build'),
                        installed_version=installed_version,
                        posted_version_text=web_version,
                        posted_version_only=vs.get('posted_version', ''),
                        is_installed=True,
                        has_status=bool(vs),
                    ).get('state')

                    if state == STATE_UPDATE_AVAILABLE:
                        posted = vs.get('posted_build')
                        detail = f'build {posted}' if posted else str(web_version)
                        indicators['update'] = ('●', color_warn, f'Update available: {detail}')
                    elif state == STATE_STEAMRIP_OK_STEAM_AHEAD:
                        indicators['update'] = ('●', color_ok, 'Up to date on SteamRIP (Steam has a newer build)')
                    elif state == STATE_UPDATED:
                        indicators['update'] = ('●', color_ok, 'Up to date')
                    elif state == STATE_VERSION_ONLY:
                        indicators['update'] = ('●', color_ok, f'Up to date: {web_version}')
                    else:
                        indicators['update'] = ('●', color_warn, 'Version check pending')
                else:
                    indicators['update'] = ('●', color_error, 'Not in database')
            else:
                indicators['update'] = ('●', color_warn, 'Version check pending')
        else:
            # Orfano: tutto rosso tranne Steam che non sappiamo
            indicators['json'] = ('●', color_error, 'JSON metadata missing')
            indicators['save'] = ('●', color_warn, 'Orphaned - no metadata')
            indicators['update'] = ('●', color_error, 'Cannot check - orphaned')
        
        return indicators

    def _find_installed_meta_by_folder(self, folder_path):
        for meta in self.installed_games.values():
            if meta.get('install_dir') == folder_path:
                return meta
        return None
