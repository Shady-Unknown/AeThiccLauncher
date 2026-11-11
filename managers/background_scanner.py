import re
from PyQt6.QtCore import QObject, pyqtSignal, QTimer
from core.version_status import compute_version_state, STATE_UPDATE_AVAILABLE

class BackgroundScanner(QObject):
    update_found = pyqtSignal(str, dict, str)

    def __init__(self, parent):
        super().__init__()
        self.parent = parent

    def start_background_update_checker(self):
        print("[App] Starting Background Update Scanner...")
        self.parent.bg_update_queue = list(self.parent.installed_games.keys())
        if not self.parent.is_bg_checking:
            self._process_next_bg_check()

    def _process_next_bg_check(self):
        if not self.parent.bg_update_queue:
            print("[App] Background Update Scanner finished.")
            self.parent.is_bg_checking = False
            return
            
        self.parent.is_bg_checking = True
        game_title = self.parent.bg_update_queue.pop(0)
        meta = self.parent.installed_games.get(game_title)
        
        url = meta.get('source_url') if meta else None
        
        item = self.parent.lib_manager._find_item_by_title(game_title)
        if item:
            # Lasciamo il sottotitolo vuoto durante il caricamento, mostriamo solo l'icona 🔄
            self.parent.lib_manager._update_library_item(item, f"🔄 {game_title}", "", title_color=None, sub_color="transparent")
            
        QTimer.singleShot(200, lambda: self._evaluate_bg_check(game_title, url, meta))

    def _evaluate_bg_check(self, game_title, url, meta):
        # Re-leggiamo la meta VIVA da installed_games: la snapshot può essere stantia
        # (es. l'utente ha nel frattempo configurato save path o integrazione Steam).
        live_meta = self.parent.installed_games.get(game_title)
        if live_meta is not None:
            meta = live_meta
            url = meta.get('source_url', url)

        actual_url = url.rstrip('/') if url and url != 'N/A' else ''
        web_version = ""
        found = False
        db_ready = hasattr(self.parent, 'master_catalog') and bool(self.parent.master_catalog)
        
        if actual_url and db_ready:
            catalog_entry = self.parent.master_catalog.get_entry(game_title)
            if isinstance(catalog_entry, dict) and catalog_entry.get("url", "").rstrip('/') == actual_url:
                found = True
                web_version = catalog_entry.get("version", "Release Base")
                
        # Ricerchiamo l'elemento in tempo reale (evita crash se l'utente ha disinstallato il gioco)
        item = self.parent.lib_manager._find_item_by_title(game_title)
        if item:
            is_orphan = meta.get('is_orphan', False) if meta else True
            installed_version = meta.get('version', '') if meta else ''
            
            # --- RACCOLTA PROBLEMI (SEMAFORI GIALLI/ROSSI) ---
            issues = []
            if is_orphan:
                issues.append("JSON")
            else:
                has_savegame_path = (meta.get('savegame_path_win')
                                     or meta.get('savegame_path_linux')
                                     or meta.get('savegame_path_custom'))
                if not has_savegame_path:
                    issues.append("SAVES")
                    
                steam_id = meta.get('steam_shortcut_id')
                if not steam_id or not str(steam_id).strip():
                    issues.append("STEAM")

            if issues:
                print(f"[BGCheck] '{game_title}' issues={issues} | win={meta.get('savegame_path_win')!r} linux={meta.get('savegame_path_linux')!r} custom={meta.get('savegame_path_custom')!r} steam={meta.get('steam_shortcut_id')!r}")

            update_available = False
            if found and meta:
                status = self.parent.master_catalog.get_version_status(game_title)
                update_available = compute_version_state(
                    installed_build=meta.get('build'),
                    posted_build=status.get('posted_build'),
                    steam_build=status.get('steam_build'),
                    installed_version=installed_version,
                    posted_version_text=web_version,
                    posted_version_only=status.get('posted_version', ''),
                    is_installed=True,
                    has_status=bool(status),
                ).get('state') == STATE_UPDATE_AVAILABLE

            # --- LOGICA DEL SOTTOTITOLO ---
            title_prefix = game_title
            sub_text = ""
            sub_color = "transparent"

            if update_available:
                title_prefix = f"❗ {game_title}"
                sub_text = "(Update Available!)"
                sub_color = "#00ff88" # Verde acceso
            elif not found and not is_orphan and actual_url and db_ready:
                sub_text = "(not in database)"
                sub_color = "#ff5555" # Rosso
            elif issues:
                if is_orphan:
                    title_prefix = f"❓ {game_title}"
                # Formatta la lista di ciò che manca: es. "(Missing: SAVES - STEAM)"
                sub_text = f"(Missing: {' - '.join(issues)})"
                sub_color = "#ffcc00" # Giallo
            else:
                # TUTTO OK! Semafori verdi. Non aggiungiamo nessun testo inquinante.
                pass 

            self.parent.lib_manager._update_library_item(item, title_prefix, sub_text, title_color=None, sub_color=sub_color, meta=meta, game_title=game_title)

        QTimer.singleShot(200, self._process_next_bg_check)
