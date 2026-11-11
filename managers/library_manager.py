import os
import json
import re
from PyQt6.QtCore import Qt, QSize
#from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QListWidgetItem
from ui.widgets.elided_label import ElidedLabel
from core.config import get_base_dir
from core.events import EVENT_GAME_INSTALLED, EVENT_GAME_REMOVED
from core.version_status import (
    compute_version_state,
    STATE_UPDATE_AVAILABLE,
    STATE_STEAMRIP_OK_STEAM_AHEAD,
    STATE_UPDATED,
    STATE_VERSION_ONLY,
)


def is_local_install(meta):
    """True se il gioco è installato localmente ma NON proviene da SteamRip
    (flag is_local esplicito, oppure nessuna source_url valida) e non è un orfano."""
    if not meta or meta.get('is_orphan'):
        return False
    if meta.get('is_local') is True:
        return True
    url = str(meta.get('source_url') or '').strip()
    return not url or url == 'N/A'


class LibraryManager:
    def __init__(self, app_window):
        self.app_window = app_window
        self.lib_list = app_window.lib_list
        self.installed_games = app_window.installed_games
        self.app_config = app_window.app_config
        # The first scan after startup is a baseline: it must not look like
        # the user installed the whole library right now.
        self._scanned_once = False

    def _emit_event(self, name, **payload):
        events = getattr(self.app_window, 'events', None)
        if events is not None:
            try:
                events.emit_event(name, **payload)
            except Exception as exc:
                print(f"[Library] event '{name}' failed: {exc}")

    def scan_library(self):
        previous_titles = set(self.installed_games.keys()) if self._scanned_once else set()
        self.lib_list.clear()
        self.installed_games.clear()
        seen_titles = set()
        watched = self.app_window.app_config.get("watched_folders", [])
        
        def _add_orphan(game_dir, sub):
            self.installed_games[sub] = {
                "is_orphan": True,
                "install_dir": game_dir,
                "folder_name": sub
            }
            if sub in seen_titles:
                return
            seen_titles.add(sub)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, sub)
            self.lib_list.addItem(item)
            self._update_library_item(item, f"❓ {sub}", "(Missing: JSON)", title_color=None, sub_color="#ffcc00", meta=self.installed_games[sub], game_title=sub)

        for folder in watched:
            if not os.path.exists(folder): continue
            try:
                for sub in os.listdir(folder):
                    if sub.lower() == "patches":
                        continue
                    game_dir = os.path.join(folder, sub)
                    if not os.path.isdir(game_dir): continue
                    
                    meta_path = os.path.join(game_dir, "steamrip_meta.json")
                    if os.path.exists(meta_path):
                        try:
                            with open(meta_path, 'r', encoding='utf-8') as f:
                                meta = json.load(f)
                                meta['install_dir'] = game_dir
                                title = meta.get('title', sub)

                                # Gestione titoli duplicati (es. meta scopiazzata da un'altra installazione):
                                # teniamo la meta più completa e ri-renderizziamo la riga dalla vincente,
                                # così la sidebar e installed_games non possono mai divergere.
                                if title in seen_titles:
                                    existing = self.installed_games.get(title, {})
                                    existing_dir = existing.get('install_dir', '')
                                    if self._config_score(meta) > self._config_score(existing):
                                        print(f"[Scan] Duplicate title '{title}' in '{sub}' — replacing meta from '{os.path.basename(existing_dir)}' (more complete)")
                                        self.installed_games[title] = meta
                                        dup_item = self._find_item_by_title(title)
                                        if dup_item:
                                            self._update_library_item(dup_item, title, "", title_color=None, sub_color="transparent", meta=meta, game_title=title)
                                    else:
                                        print(f"[Scan] Duplicate title '{title}' in '{sub}' — keeping meta from '{os.path.basename(existing_dir)}'")
                                    continue

                                seen_titles.add(title)
                                self.installed_games[title] = meta
                                
                                item = QListWidgetItem()
                                item.setData(Qt.ItemDataRole.UserRole, title)
                                self.lib_list.addItem(item)
                                # Inizialmente tutti i titoli sono bianchi, il check degli aggiornamenti li colorerà
                                self._update_library_item(item, title, "", title_color=None, sub_color="transparent", meta=meta, game_title=title)
                        except Exception as e:
                            print(f"Error reading meta in {game_dir}: {e}")
                            _add_orphan(game_dir, sub) # FIX: Se il JSON è rotto, diventa orfano
                    else:
                        _add_orphan(game_dir, sub)
            except Exception as e:
                print(f"Error scanning folder {folder}: {e}")

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
        """Costruisce o aggiorna un Custom Widget HTML per le righe della libreria con i pallini indicatori allineati alle colonne."""
        if not title_color:
            title_color = self.app_config['theme']['text_primary_white']
            
        widget = self.lib_list.itemWidget(item)
        if not widget:
            widget = QWidget()
            widget.setStyleSheet("background: transparent;")
            main_layout = QHBoxLayout(widget)
            # Margini bilanciati
            main_layout.setContentsMargins(8, 6, 8, 5) 
            main_layout.setSpacing(0)
            
            # Colonna 1: Titolo e sottotesto (expandable)
            left_layout = QVBoxLayout()
            left_layout.setSpacing(2)
            left_layout.setContentsMargins(0, 0, 0, 0)
            
            lbl_title = ElidedLabel()
            lbl_title.setObjectName("ItemTitle")
            lbl_title.setStyleSheet("background: transparent; padding-bottom: 1px;")
            lbl_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

            lbl_sub = QLabel()
            lbl_sub.setObjectName("ItemSub")
            lbl_sub.setStyleSheet("background: transparent;")
            lbl_sub.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

            left_layout.addWidget(lbl_title)
            left_layout.addWidget(lbl_sub)
            main_layout.addLayout(left_layout, 1)  # Expandable
            
            # Spacing tra nome e pallini
            main_layout.addSpacing(4)
            
            # Colonne compatte per i 4 pallini (più spazio ai titoli)
            for i in range(4):
                dot_lbl = QLabel()
                dot_lbl.setObjectName(f"Indicator{i}")
                dot_lbl.setFixedWidth(24)
                dot_lbl.setMinimumHeight(32)
                dot_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
                dot_lbl.setStyleSheet("background: transparent; font-size: 16px;")
                main_layout.addWidget(dot_lbl)
                if i < 3:  # Spacing tra pallini (non dopo l'ultimo)
                    main_layout.addSpacing(2)
            
            self.lib_list.setItemWidget(item, widget)
            
        lbl_title = widget.findChild(QLabel, "ItemTitle")
        lbl_sub = widget.findChild(QLabel, "ItemSub")
        
        # --- LOGICA INTELLIGENTE DI PERSISTENZA WARNING ---
        if meta is not None and game_title is not None:
            indicators = self._get_status_indicators(game_title, meta)
            
            # Calcoliamo i problemi (Issues) per popolare il sottotitolo se è vuoto o generico
            issues = []
            if meta.get('is_orphan'):
                issues.append("JSON")
            else:
                if not (meta.get('savegame_path_win') or meta.get('savegame_path_linux') or meta.get('savegame_path_custom')):
                    issues.append("SAVES")
                steam_id = meta.get('steam_shortcut_id')
                if not steam_id or not str(steam_id).strip():
                    issues.append("STEAM")

            # Controllo Update disponibile dal dizionario degli indicatori appena calcolato
            dot_upd, color_upd, tip_upd = indicators['update']
            update_available = "Update available" in tip_upd

            # Se il sottotitolo passato è vuoto (es. da scan_library), o è un warning stantio
            # (es. "(Missing: SAVES - STEAM)" prodotto dallo scanner con una meta vecchia),
            # ricalcoliamo TUTTO dalla meta corrente così la riga si auto-corregge.
            if not sub_text or sub_text == "Installed" or sub_text.startswith("(Missing:"):
                if update_available:
                    title_text = f"❗ {game_title}"
                    sub_text = "(Update Available!)"
                    sub_color = self.app_config['theme']['status_green']
                elif issues:
                    if meta.get('is_orphan'):
                        title_text = f"❓ {game_title}"
                    sub_text = f"(Missing: {' - '.join(issues)})"
                    sub_color = self.app_config['theme']['status_yellow']
                elif is_local_install(meta):
                    # Installazione locale (non SteamRip): badge dedicato invece di "Installed"
                    title_text = game_title
                    sub_text = "Local Game"
                    sub_color = self.app_config['theme']['text_secondary_pink']
                else:
                    title_text = game_title # Reset del prefisso se tutto è risolto
                    sub_text = "Installed"
                    sub_color = "#a0a0a0"

            # Aggiorna i pallini indicatori
            indicator_keys = ['update', 'json', 'save', 'steam']
            for idx, key in enumerate(indicator_keys):
                if key in indicators:
                    dot, color, tooltip = indicators[key]
                    dot_lbl = widget.findChild(QLabel, f"Indicator{idx}")
                    if dot_lbl:
                        dot_lbl.setText(f"<span style='color:{color}; font-size:16px;'>{dot}</span>")
                        dot_lbl.setToolTip(tooltip)
        
        # Applicazione finale dei testi (titolo con ellissi + tooltip, sottotitolo)
        if hasattr(lbl_title, "set_full_text"):
            lbl_title.set_style(title_color, 12, True)
            lbl_title.set_full_text(title_text)
        else:  # fallback difensivo
            lbl_title.setText(f"<span style='color:{title_color}; font-weight:bold; font-size:12px;'>{title_text}</span>")
        lbl_sub.setText(f"<span style='color:{sub_color}; font-size:10px;'>{sub_text}</span>")

        hint = widget.sizeHint()
        item.setSizeHint(QSize(hint.width(), max(hint.height(), 46)))

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
            
            # Save path: dipende da savegame_path_win o savegame_path_linux
            if meta.get('savegame_path_win') or meta.get('savegame_path_linux') or meta.get('savegame_path_custom'):
                indicators['save'] = ('●', color_ok, 'Savegame path configured')
            else:
                indicators['save'] = ('●', color_warn, 'Savegame path missing')
            
            # Steam: Verifica se steam_shortcut_id esiste e non è vuoto
            steam_id = meta.get('steam_shortcut_id')
            if steam_id and str(steam_id).strip():
                indicators['steam'] = ('●', color_ok, f'Steam shortcut: {steam_id}')
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
