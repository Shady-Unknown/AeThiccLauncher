import fnmatch
import os
import json
import re
import shutil
import requests
import yaml
import threading
from pathlib import Path
from datetime import datetime
#from urllib.parse import quote
from PyQt6.QtWidgets import (QMessageBox, QDialog) # QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             #QTreeWidget, QTreeWidgetItem, QFileDialog, 
                              #QLineEdit, QComboBox
#from PyQt6.QtCore import Qt, QUrl
#from PyQt6.QtGui import QDesktopServices

LUDUSAVI_MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"


def safe_game_folder_name(game_title):
    name = re.sub(r'[\\/*?:"<>|]', '', game_title)
    name = re.sub(r'\s+', '_', name.strip())
    return name or "unknown_game"


def safe_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', '_', filename)
from ui.dialogs.manual_save_path_dialog import ManualSavePathDialog
from core.meta_utils import save_steamrip_meta

class SavegamesManager:
    def __init__(self, app_config, installed_games, parent=None):
        self.app_config = app_config
        self.installed_games = installed_games
        self.parent = parent
        self.manifest = None
        self.manifest_text = None
        from core.paths import PathManager
        self.local_dbs_dir = str(PathManager.get_dbs_dir())
        os.makedirs(self.local_dbs_dir, exist_ok=True)
        
        self.manifest_path = os.path.join(self.local_dbs_dir, "ludusavi_manifest.yaml")
        self.etag_path = os.path.join(self.local_dbs_dir, "ludusavi_etag.txt")

    def get_sync_folder(self):
        folder = self.app_config.get('savegames', {}).get('sync_folder', '').strip()
        if not folder:
            from core.paths import PathManager
            folder = str(PathManager.get_staging_dir())
        return folder

    def update_app_config(self, app_config):
        self.app_config = app_config

    def _extract_manifest_key(self, line):
        match = re.match(r'^\s*["\']?(.*?)["\']?\s*:\s*$', line)
        if match:
            return match.group(1).strip()
        return None
    
    def update_manifest_background(self):
        """Lancia il controllo e l'eventuale download in un thread separato (fire-and-forget)."""
        print("[Savegames] Avvio controllo Manifest in background...")
        threading.Thread(target=self._ensure_local_manifest, daemon=True).start()

    def _ensure_local_manifest(self):
        headers = {}
        if os.path.exists(self.manifest_path) and os.path.exists(self.etag_path):
            try:
                with open(self.etag_path, 'r') as f:
                    cached_etag = f.read().strip()
                if cached_etag:
                    headers['If-None-Match'] = cached_etag
            except Exception: pass

        try:
            r = requests.get(LUDUSAVI_MANIFEST_URL, headers=headers, stream=True, timeout=5)
            
            if r.status_code == 304:
                print("[Savegames] Manifest locale già aggiornato (HTTP 304). Nessuna azione.")
                return True 
            
            if r.status_code == 200:
                print("[Savegames] Download nuovo manifest da GitHub in corso...")
                # Scrittura Atomica: scriviamo su un file .tmp per non corrompere la lettura in tempo reale
                tmp_path = self.manifest_path + ".tmp"
                with open(tmp_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Sostituzione istantanea del file vecchio col nuovo
                os.replace(tmp_path, self.manifest_path)
                
                new_etag = r.headers.get('ETag')
                if new_etag:
                    with open(self.etag_path, 'w') as f:
                        f.write(new_etag)
                        
                print(f"[Savegames] Download completato con successo! (Aggiornato in local_dbs)")
                return True
                
        except Exception as e:
            print(f"[Savegames] Nessuna connessione o GitHub irraggiungibile: {e}")
            
        return os.path.exists(self.manifest_path)

    def _iter_local_manifest_blocks(self):
        """Legge il database in locale senza sovraccaricare la RAM del PC."""
        if not self._ensure_local_manifest():
            print("[Savegames] Errore: Nessun manifest locale disponibile e rete assente.")
            return

        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                current_key = None
                current_block = []
                for raw_line in f:
                    line = raw_line.rstrip('\r\n')
                    if re.match(r'^[^ \t].*:\s*$', line):
                        if current_key is not None and current_block:
                            yield current_key, current_block
                        current_key = self._extract_manifest_key(line)
                        current_block = [line]
                    elif current_key is not None:
                        current_block.append(line)
                        
                if current_key is not None and current_block:
                    yield current_key, current_block
                    
        except Exception as exc:
            print(f"[Savegames] Fallita la lettura del manifest locale: {exc}")
            return

    def _normalize_manifest_key(self, key):
        if not key:
            return ""
        return self.normalize_title(key)

    def _title_matches_exact(self, title, key):
        title_norm = self.normalize_title(title)
        key_norm = self._normalize_manifest_key(key)
        if not title_norm or not key_norm:
            return False
        return title_norm == key_norm

    def find_manifest_entry(self, game_title):
        if not game_title:
            return None

        normalized_title = self.normalize_title(game_title)
        fallback_block = None
        fallback_key = None

        for key, block_lines in self._iter_local_manifest_blocks():
            if not key:
                continue

            if self._title_matches_exact(game_title, key):
                try:
                    parsed = yaml.safe_load("\n".join(block_lines))
                    return parsed.get(key)
                except Exception as exc:
                    print(f"[Savegames] Failed to parse block for match '{key}': {exc}")
                    return None

            key_norm = self._normalize_manifest_key(key)
            if not fallback_block and normalized_title and key_norm and len(normalized_title) > 2 and len(key_norm) > 2:
                if normalized_title in key_norm or key_norm in normalized_title:
                    fallback_block = block_lines
                    fallback_key = key

        if fallback_block is not None:
            try:
                parsed = yaml.safe_load("\n".join(fallback_block))
                return parsed.get(fallback_key)
            except Exception as exc:
                print(f"[Savegames] Failed to parse fallback block '{fallback_key}': {exc}")
                return None

        return None

    def normalize_title(self, title):
        title = re.sub(r'[^a-zA-Z0-9]+', ' ', str(title).lower()).strip()
        return re.sub(r'\s+', ' ', title)

    def extract_win_save_path(self, manifest_entry):
        if not isinstance(manifest_entry, dict):
            return None

        candidates = []
        for section_name in ('files', 'installDir', 'registry'):
            section = manifest_entry.get(section_name, {})
            if not isinstance(section, dict):
                continue
            for path_template, details in section.items():
                if not isinstance(details, dict):
                    continue
                tags = details.get('tags', [])
                when = details.get('when', [])
                if 'save' not in tags:
                    continue
                if when:
                    conditions = when if isinstance(when, list) else [when]
                    if not any(cond.get('os') == 'windows' for cond in conditions if isinstance(cond, dict)):
                        continue
                candidates.append(path_template)

        if not candidates and isinstance(manifest_entry.get('files'), dict):
            for path_template, details in manifest_entry['files'].items():
                if isinstance(details, dict) and 'save' in details.get('tags', []):
                    candidates.append(path_template)

        candidates = [c for c in candidates if '<storeUserId>' not in c]

        if not candidates:
            return None

        raw_path = self.expand_manifest_template(candidates[0])
        return self._normalize_manifest_save_path(raw_path)

    def expand_manifest_template(self, template):
        template = str(template).strip()
        
        # Usiamo 'lambda m: r"..."' per forzare Python a trattare i backslash come testo semplice,
        # evitando che re.sub vada in crash interpretandoli come comandi (\D, \S, ecc.)
        replacements = {
            r'(?i)<winLocalAppData>': lambda m: r'%localappdata%',
            r'(?i)<winAppData>': lambda m: r'%appdata%',
            r'(?i)<winProgramData>': lambda m: r'%programdata%',
            r'(?i)<winUserProfile>': lambda m: r'%userprofile%',
            r'(?i)<winCommonAppData>': lambda m: r'%programdata%',
            r'(?i)<winDocuments>': lambda m: r'%userprofile%\Documents',
            r'(?i)<winPublic>': lambda m: r'%public%',
            r'(?i)<winSavedGames>': lambda m: r'%userprofile%\Saved Games',
            r'(?i)<winVideo>': lambda m: r'%userprofile%\Videos',
            r'(?i)<winMusic>': lambda m: r'%userprofile%\Music',
            r'(?i)<winPictures>': lambda m: r'%userprofile%\Pictures',
            r'(?i)<home>': lambda m: r'%userprofile%',
            r'(?i)<osUserName>': lambda m: r'%username%',
            r'(?i)<root>': lambda m: 'C:\\',
            r'(?i)<winDir>': lambda m: r'%windir%',
            r'(?i)<xdgConfig>': lambda m: r'~/.config',
            r'(?i)<xdgData>': lambda m: r'~/.local/share',
            r'(?i)<base>': lambda m: r''
        }
        
        for pattern, repl_func in replacements.items():
            template = re.sub(pattern, repl_func, template)
            
        template = template.replace('/', '\\')
        return template

    def _normalize_manifest_save_path(self, path):
        if not path:
            return path
        candidate = str(path).strip().strip('"')
        candidate = candidate.replace('/', '\\')
        if '*' in candidate or '?' in candidate:
            parts = [p for p in re.split(r'[\\/]+', candidate) if p]
            static_parts = []
            for part in parts:
                if '*' in part or '?' in part:
                    break
                static_parts.append(part)
            if static_parts:
                return '\\'.join(static_parts)
        return candidate

    def _is_variable_folder_segment(self, segment):
        return bool(re.search(r'[<\[\(\{].+[>\]\)\}]', str(segment)))

    def _find_matching_child(self, current_dir, segment):
        if not os.path.isdir(current_dir):
            return None

        entries = [entry for entry in os.listdir(current_dir) if os.path.isdir(os.path.join(current_dir, entry))]
        if not entries:
            return None

        if self._is_variable_folder_segment(segment):
            return sorted(entries, key=str.lower)[0]

        if '*' in segment or '?' in segment:
            matches = [entry for entry in entries if fnmatch.fnmatchcase(entry, segment)]
            if matches:
                return sorted(matches, key=str.lower)[0]

        exact_path = os.path.join(current_dir, segment)
        if os.path.isdir(exact_path):
            return segment

        lower = segment.lower()
        for entry in entries:
            if entry.lower() == lower:
                return entry

        return None

    def _resolve_linux_path(self, base, relative_path):
        if not relative_path:
            return base
        raw_parts = [p for p in re.split(r'[\\/]+', relative_path) if p]
        current = base
        output_parts = []
        for part in raw_parts:
            match = self._find_matching_child(current, part)
            if match is None:
                output_parts.extend(raw_parts[raw_parts.index(part):])
                return os.path.join(current, *output_parts)

            if self._is_variable_folder_segment(part) or '*' in part or '?' in part:
                output_parts.append(match)
            else:
                output_parts.append(part)
            current = os.path.join(current, match)
        return os.path.join(base, *output_parts)

    def _resolve_linux_path_from_absolute(self, path):
        if not path:
            return path
        candidate = os.path.expanduser(str(path).strip().strip('"'))
        candidate = os.path.normpath(candidate)
        if os.path.exists(candidate):
            return candidate
        if not os.path.isabs(candidate):
            return candidate

        parts = [p for p in candidate.split(os.sep) if p]
        current = os.sep if candidate.startswith(os.sep) else ''
        output_parts = []

        for part in parts:
            if not part:
                continue
            match = self._find_matching_child(current, part)
            if match is None:
                output_parts.append(part)
                current = os.path.join(current, part)
                continue

            if self._is_variable_folder_segment(part) or '*' in part or '?' in part:
                output_parts.append(match)
            else:
                output_parts.append(part)
            current = os.path.join(current, match)

        return os.path.join(os.sep if candidate.startswith(os.sep) else '', *output_parts)

    def _resolve_case_insensitive_path(self, path):
        if not path:
            return path
            
        # 1. Normalizzazione Universale degli slash
        # Forziamo la conversione dei backslash di Windows prima di passarlo a pathlib
        clean_path = str(path).strip().strip('"').replace('\\', '/')
        candidate = Path(clean_path).expanduser()
        
        # Se il percorso esatto esiste già, vittoria immediata (risparmiamo cicli)
        if candidate.exists():
            return str(candidate)

        # 2. Risoluzione Segmento per Segmento
        parts = [p for p in clean_path.split('/') if p]
        
        # Rispettiamo la radice assoluta su Linux
        if clean_path.startswith('/'):
            current = Path('/')
        else:
            current = Path('')

        for part in parts:
            exact = current / part
            
            # Se il segmento esiste esattamente com'è, andiamo avanti veloci
            if exact.exists():
                current = exact
                continue
            
            # 3. Ricerca case-insensitive se il match esatto fallisce
            try:
                found = False
                if current.exists() and current.is_dir():
                    for entry in current.iterdir():
                        if entry.name.lower() == part.lower():
                            current = entry
                            found = True
                            break
                            
                if not found:
                    # Se non trova nulla, la cartella (o il file) non esiste ancora fisicamente sul disco.
                    # Aggiungiamo il resto del percorso mantenendo il case originale e usciamo.
                    current = exact
            except PermissionError:
                # Fallback di sicurezza in caso di cartelle di sistema inaccessibili
                current = exact
                
        return str(current)

    def _get_compatdata_base(self, steam_grid_id):
        paths = [
            os.path.expanduser(f"~/.local/share/Steam/steamapps/compatdata/{steam_grid_id}"),
            os.path.expanduser(f"~/.var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata/{steam_grid_id}")
        ]
        for p in paths:
            if os.path.exists(p):
                return p
        return paths[0]

    def _get_sanitized_linux_path(self, win_path, steam_grid_id, prefix, root_type):
        original = str(win_path).strip().strip('"')
        normalized = original.replace('/', '\\')
        rel = normalized[len(prefix):]
        base = os.path.join(self._get_compatdata_base(steam_grid_id), f'pfx/drive_c/users/steamuser/{root_type}')
        return self._resolve_linux_path(base, rel)

    def translate_win_path_to_linux(self, win_path, steam_grid_id):
        if not win_path or not steam_grid_id:
            return ""
        original = str(win_path).strip().strip('"')
        path = original.replace('/', '\\')
        lower_path = path.lower()

        # --- NUOVI BLOCCHI PER I TOKEN DI SISTEMA (Che ci eravamo persi!) ---
        if lower_path.startswith('%userprofile%\\'):
            rel = path[len('%userprofile%\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/users/steamuser')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))
            
        if lower_path.startswith('%programdata%\\'):
            rel = path[len('%programdata%\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/ProgramData')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))
            
        if lower_path.startswith('%public%\\'):
            rel = path[len('%public%\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/users/Public')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))

        if lower_path.startswith('%windir%\\'):
            rel = path[len('%windir%\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/windows')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))

        # --- VECCHI BLOCCHI ---
        if lower_path.startswith('%localappdata%\\'):
            return self._resolve_case_insensitive_path(self._get_sanitized_linux_path(path, steam_grid_id, '%localappdata%\\', 'AppData/Local'))
        
        if lower_path.startswith('%appdata%\\'):
            return self._resolve_case_insensitive_path(self._get_sanitized_linux_path(path, steam_grid_id, '%appdata%\\', 'AppData/Roaming'))
            
        if lower_path.startswith('c:\\users\\steamuser\\'):
            rel = path[len('c:\\users\\steamuser\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/users/steamuser')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))
            
        if lower_path.startswith('c:\\users\\'):
            parts = path.split('\\')
            if len(parts) > 2:
                rel = '\\'.join(parts[3:])
                base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c/users/steamuser')
                return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))
                
        if lower_path.startswith('c:\\'):
            rel = path[len('c:\\'):]
            base = os.path.join(self._get_compatdata_base(steam_grid_id), 'pfx/drive_c')
            return self._resolve_case_insensitive_path(self._resolve_linux_path(base, rel))
                
        return ""

    def save_meta(self, meta):
        if not meta or not meta.get('install_dir'):
            return
        save_steamrip_meta(meta['install_dir'], meta)

    def set_custom_save_path(self, meta, path):
        if not meta or not path:
            return
        meta['savegame_path_custom'] = path
        meta['savegame_scan_state'] = True
        self.save_meta(meta)

    def clear_custom_save_path(self, meta):
        if not meta:
            return
        meta.pop('savegame_path_custom', None)
        self.save_meta(meta)

    def prompt_manual_save_path(self, game_title):
        dialog = ManualSavePathDialog(game_title, self.parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.chosen_path
        return ""

    def ensure_save_path_for_game(self, meta):
        if not meta or meta.get('is_orphan'):
            return meta

        if meta.get('savegame_path_win') or meta.get('savegame_path_linux') or meta.get('savegame_path_custom'):
            return meta

        title = meta.get('title') or os.path.basename(meta.get('install_dir', ''))
        manifest_entry = self.find_manifest_entry(title)
        if manifest_entry:
            win_path = self.extract_win_save_path(manifest_entry)
            if win_path:
                meta['savegame_path_win'] = win_path
                if meta.get('steam_grid_id'):
                    linux_path = self.translate_win_path_to_linux(win_path, meta['steam_grid_id'])
                    if linux_path:
                        meta['savegame_path_linux'] = linux_path
                meta['savegame_scan_state'] = True
                self.save_meta(meta)
                if self.parent:
                    QMessageBox.information(self.parent, "Savegames Manager", f"Save path inferred from Ludusavi manifest for {title}.")
                return meta

        if os.name == 'posix' and self.parent and not meta.get('steam_grid_id'):
            QMessageBox.warning(
                self.parent,
                "Steam Grid ID Required",
                "On Linux, to map Windows save locations to compatdata, you should add this game to Steam as a Non-Steam game. "
                "Without the Steam grid ID the app can still store a manual path, but full Linux sync may be incomplete."
            )

        manual_path = self.prompt_manual_save_path(title)
        if not manual_path:
            return meta

        if re.search(r'[%\\]|C:|c:', manual_path):
            meta['savegame_path_win'] = manual_path
            if meta.get('steam_grid_id'):
                linux_path = self.translate_win_path_to_linux(manual_path, meta['steam_grid_id'])
                if linux_path:
                    meta['savegame_path_linux'] = linux_path
        else:
            meta['savegame_path_linux'] = manual_path

        meta['savegame_scan_state'] = True
        self.save_meta(meta)
        return meta

    def rescan_save_folder(self, meta):
        if not meta or meta.get('is_orphan'):
            return meta

        changed = False

        # --- 1. GARBAGE COLLECTOR IMMEDIATO ---
        keys_to_remove = ['description', 'game_info', 'requirements', 'links']
        for k in keys_to_remove:
            if k in meta:
                meta.pop(k)
                changed = True

        # --- 2. AUTO-FIX DEI VECCHI PATH CORROTTI ---
        old_win_path = meta.get('savegame_path_win', '')
        if '<' in old_win_path:
            clean_path = self._normalize_manifest_save_path(self.expand_manifest_template(old_win_path))
            meta['savegame_path_win'] = clean_path
            changed = True

        # --- 3. CONTROLLO MANIFEST LUDUSAVI ---
        title = meta.get('title') or os.path.basename(meta.get('install_dir', ''))
        manifest_entry = self.find_manifest_entry(title)
        
        if manifest_entry:
            win_path = self.extract_win_save_path(manifest_entry)
            if win_path and meta.get('savegame_path_win') != win_path:
                meta['savegame_path_win'] = win_path
                changed = True
        else:
            if meta.get('savegame_path_win'):
                meta['savegame_path_win'] = ""
                changed = True
            if meta.get('savegame_path_linux'):
                meta['savegame_path_linux'] = ""
                changed = True

        # --- 4. TRADUZIONE LINUX ---
        current_win_path = meta.get('savegame_path_win', '')
        grid_id = meta.get('steam_grid_id')
        
        if current_win_path and grid_id:
            linux_path = self.translate_win_path_to_linux(current_win_path, grid_id)
            if linux_path and meta.get('savegame_path_linux') != linux_path:
                meta['savegame_path_linux'] = linux_path
                changed = True
        elif not grid_id and 'savegame_path_linux' not in meta:
            meta['savegame_path_linux'] = ""
            changed = True

        meta['savegame_scan_state'] = True
        
        # --- 5. SALVATAGGIO DEFINITIVO ---
        if changed or meta.get('savegame_scan_state') is not True:
            self.save_meta(meta)
            
        return meta

    def _expand_path(self, path):
        """Expand environment variables on Windows (%VAR% -> value)."""
        path = str(path)
        if os.name == 'nt':
            path = os.path.expandvars(path)
        return path

    def _resolve_base_path(self, path, meta):
        """Resolve <base>-relative paths against install_dir."""
        if not path:
            return path
        if re.match(r'^[a-zA-Z]:\\', path) or path.startswith('%'):
            return path
        install_dir = meta.get('install_dir', '')
        if install_dir:
            return os.path.join(install_dir, path.lstrip('\\/'))
        return path

    def get_source_save_path(self, meta):
        if not meta or meta.get('is_orphan'):
            return ""

        custom = meta.get('savegame_path_custom', '')
        if custom:
            expanded = self._expand_path(custom)
            if os.path.exists(expanded):
                return expanded

        if os.name == 'nt':
            if meta.get('savegame_path_win'):
                path = self.normalize_windows_path(meta['savegame_path_win'])
                path = self._resolve_base_path(path, meta)
                path = self._expand_path(path)
                return self._resolve_save_root(path)
            if meta.get('savegame_path_linux'):
                return self._resolve_save_root(meta['savegame_path_linux'])
        else:
            if meta.get('savegame_path_linux'):
                path = self._resolve_linux_path_from_absolute(meta['savegame_path_linux'])
                path = self._resolve_case_insensitive_path(path)
                return self._resolve_save_root(path)
            if meta.get('savegame_path_win') and meta.get('steam_grid_id'):
                path = self.translate_win_path_to_linux(meta['savegame_path_win'], meta['steam_grid_id'])
                return self._resolve_save_root(path)
            if meta.get('savegame_path_win') and meta.get('savegame_path_win').startswith('%'):
                path = self.translate_win_path_to_linux(meta['savegame_path_win'], meta.get('steam_grid_id', ''))
                return self._resolve_save_root(path)
            if meta.get('savegame_path_win'):
                resolved = self._resolve_base_path(meta['savegame_path_win'], meta)
                if resolved != meta['savegame_path_win']:
                    return self._resolve_save_root(self._resolve_case_insensitive_path(resolved))
            return self._resolve_case_insensitive_path(meta.get('savegame_path_linux', ''))

        return ""

    def _resolve_save_root(self, path):
        if not path:
            return path
        candidate = str(path).strip().strip('"')
        candidate = candidate.replace('/', os.sep).replace('\\', os.sep)

        last_part = os.path.basename(candidate)
        if not last_part:
            return candidate

        if self._is_variable_folder_segment(last_part) or '*' in last_part or '?' in last_part:
            return os.path.dirname(candidate)

        if '.' in last_part and not self._is_variable_folder_segment(last_part) and not last_part.startswith('*'):
            parent = os.path.dirname(candidate)
            return parent if parent else candidate

        return candidate

    def normalize_windows_path(self, path):
        path = str(path).strip().strip('"')
        path = path.replace('/', '\\')
        return path
    
    def get_latest_save_time(self, path):
        """
        Trova il timestamp della modifica più recente all'interno di un percorso.
        Se è un file, restituisce il suo mtime. Se è una cartella, scansiona ricorsivamente.
        """
        p = Path(path)
        if not p.exists():
            return None
        if p.is_file():
            return p.stat().st_mtime
            
        latest = 0
        for file in p.rglob('*'):
            if file.is_file():
                mtime = file.stat().st_mtime
                if mtime > latest:
                    latest = mtime
        return latest if latest > 0 else None

    def _ensure_folder_path(self, path):
        try:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
        except Exception as exc:
            print(f"[Savegames] Could not create path {path}: {exc}")

    def _copy_source(self, source, destination):
        """Copia ricorsivamente una cartella o un singolo file alla destinazione."""
        try:
            if os.path.isdir(source):
                shutil.copytree(source, destination, dirs_exist_ok=True)
            elif os.path.isfile(source):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copy2(source, destination)
            else:
                return False
            return True
        except Exception as exc:
            print(f"[Savegames] Copy failed from {source} to {destination}: {exc}")
            return False

    def _trim_backup_folders(self, save_root, keep=3):
        """Mantiene solo gli ultimi N backup (rotazione)."""
        try:
            candidates = [
                os.path.join(save_root, nm)
                for nm in os.listdir(save_root)
                if os.path.isdir(os.path.join(save_root, nm))
            ]
            candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for folder in candidates[keep:]:
                shutil.rmtree(folder, ignore_errors=True)
        except Exception:
            pass

    def _create_snapshot_folder(self, save_root, title):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = f"{timestamp}"
        candidate = os.path.join(save_root, base_name)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(save_root, f"{base_name}_{counter}")
            counter += 1
        os.makedirs(candidate, exist_ok=True)
        return candidate

    def sync_all_games(self):
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return {'synced_games': 0, 'errors': ['No sync folder configured.']}

        self._ensure_folder_path(sync_folder)
        synced_games = 0
        errors = []

        for title, meta in self.installed_games.items():
            if meta.get('is_orphan'):
                continue
            
            source_path = self.get_source_save_path(meta)
            if not source_path:
                continue

            source_path = self._expand_path(source_path)

            if source_path.startswith('%') and os.name != 'nt':
                source_path = self.translate_win_path_to_linux(source_path, meta.get('steam_grid_id', ''))

            safe_title = safe_game_folder_name(title)
            game_root = os.path.join(sync_folder, safe_title)

            if not os.path.exists(source_path):
                print(f"[Savegames] Salto {title}: La cartella di salvataggio non esiste ancora in -> {source_path}")
                if os.path.isdir(game_root):
                    self._freshen_json(game_root, title, meta, source_path)
                continue

            save_root = os.path.join(game_root, 'saves')
            self._ensure_folder_path(save_root)

            snapshot = self._create_snapshot_folder(save_root, title)
            if self._copy_source(source_path, snapshot):
                self._trim_backup_folders(save_root, keep=3)
                synced_games += 1
            else:
                errors.append(f"Unable to copy saves for {title} from {source_path}")

            self._freshen_json(game_root, title, meta, source_path, snapshot)

        return {'synced_games': synced_games, 'errors': errors}

    def _freshen_json(self, game_root, title, meta, source_path=None, snapshot_folder=None):
        try:
            old = {}
            json_path = os.path.join(game_root, 'savegames.json')
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    old = json.load(f)

            resolved_linux = meta.get('savegame_path_linux', '')
            win_path = meta.get('savegame_path_win', '')
            grid_id = meta.get('steam_grid_id', '')
            if not resolved_linux and win_path and grid_id:
                resolved_linux = self.translate_win_path_to_linux(win_path, grid_id)

            playtime_seconds = int(meta.get('playtime_seconds', 0) or 0)

            metadata = {
                'game_title': title,
                'source_path': source_path or old.get('source_path', ''),
                'last_synced': datetime.now().strftime('%Y-%m-%d %H:%M:%S') if source_path else old.get('last_synced', ''),
                'snapshot_folder': snapshot_folder or old.get('snapshot_folder', ''),
                'steam_grid_id': grid_id,
                'install_dir': meta.get('install_dir', ''),
                'savegame_path_win': win_path,
                'savegame_path_linux': resolved_linux,
                'savegame_path_custom': meta.get('savegame_path_custom', ''),
                'playtime_seconds': playtime_seconds,
                'playtime_hours': round(playtime_seconds / 3600.0, 2),
                'playtime_only': False,
            }
            os.makedirs(game_root, exist_ok=True)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"[Savegames] Could not freshen JSON for {title}: {exc}")

    def backup_on_demand(self, meta):
        """Esegue un backup istantaneo per un singolo gioco (chiamato alla chiusura)."""
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            print("[Savegames] Backup on-demand saltato: cartella di sync non configurata.")
            return

        title = meta.get('title', 'Unknown')
        source_path = self.get_source_save_path(meta)
        source_path = self._expand_path(source_path)

        if not source_path or not os.path.exists(source_path):
            print(f"[Savegames] Backup on-demand fallito: sorgente non trovata per {title}")
            return False

        print(f"[Savegames] Esecuzione backup on-demand per {title}...")
        
        safe_title = safe_game_folder_name(title)
        game_root = os.path.join(sync_folder, safe_title)
        save_root = os.path.join(game_root, 'saves')
        self._ensure_folder_path(save_root)

        snapshot = self._create_snapshot_folder(save_root, title)
        if self._copy_source(source_path, snapshot):
            self._trim_backup_folders(save_root, keep=3)
            
            metadata = {
                'game_title': title,
                'source_path': source_path,
                'last_synced': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'snapshot_folder': os.path.relpath(snapshot, game_root).replace('\\', '/'),
                'steam_grid_id': meta.get('steam_grid_id', ''),
                'savegame_path_win': meta.get('savegame_path_win', ''),
                'savegame_path_linux': meta.get('savegame_path_linux', ''),
                'savegame_path_custom': meta.get('savegame_path_custom', ''),
                'playtime_seconds': int(meta.get('playtime_seconds', 0) or 0),
                'playtime_hours': round(int(meta.get('playtime_seconds', 0) or 0) / 3600.0, 2),
                'playtime_only': False,
                'on_demand_backup': True
            }
            try:
                with open(os.path.join(game_root, 'savegames.json'), 'w', encoding='utf-8') as f:
                    json.dump(metadata, f, indent=4, ensure_ascii=False)
            except Exception: pass
            print(f"[Savegames] Backup completato per {title}.")
            return True
        return False

    def persist_mini_cover_on_uninstall(self, title, meta):
        if not meta:
            return
        if not meta.get('savegame_path_win') and not meta.get('savegame_path_linux'):
            return
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return
        from core.mini_cover_cache import MiniCoverManager
        MiniCoverManager.persist_cover(title, self.installed_games, sync_folder)

    def _write_playtime_json(self, game_root, title, meta):
        """Scrive (o aggiorna) savegames.json con i dati playtime del launcher."""
        try:
            json_path = os.path.join(game_root, 'savegames.json')
            old = {}
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    old = json.load(f)

            playtime_seconds = int(meta.get('playtime_seconds', 0) or 0)

            metadata = {
                'game_title': title,
                'source_path': old.get('source_path', ''),
                'last_synced': old.get('last_synced', ''),
                'snapshot_folder': old.get('snapshot_folder', ''),
                'steam_grid_id': meta.get('steam_grid_id', ''),
                'install_dir': meta.get('install_dir', ''),
                'savegame_path_win': meta.get('savegame_path_win', ''),
                'savegame_path_linux': meta.get('savegame_path_linux', ''),
                'savegame_path_custom': meta.get('savegame_path_custom', ''),
                'playtime_seconds': playtime_seconds,
                'playtime_hours': round(playtime_seconds / 3600.0, 2),
                'playtime_only': not os.path.isdir(os.path.join(game_root, 'saves')),
            }
            os.makedirs(game_root, exist_ok=True)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=4, ensure_ascii=False)
        except Exception as exc:
            print(f"[Savegames] Could not write playtime JSON for {title}: {exc}")

    def ensure_playtime_stub(self, title, meta):
        """Aggiorna/crea la scheda playtime-only per i giochi senza save sincronizzate."""
        if not meta or meta.get('is_orphan'):
            return
        if not meta.get('playtime_seconds'):
            return
        if not title:
            title = meta.get('title') or os.path.basename(meta.get('install_dir', '') or '')
        if not title:
            return
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return
        safe_title = safe_game_folder_name(title)
        game_root = os.path.join(sync_folder, safe_title)
        self._write_playtime_json(game_root, title, meta)

    def ensure_playtime_stubs(self):
        for title, meta in self.installed_games.items():
            try:
                self.ensure_playtime_stub(title, meta)
            except Exception as exc:
                print(f"[Savegames] Errore stub playtime per '{title}': {exc}")