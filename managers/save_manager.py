import os
import json
import re
import shutil
import time
import uuid
import hashlib
import requests
import yaml
import threading
from pathlib import Path
from datetime import datetime, timezone
#from urllib.parse import quote
from PyQt6.QtWidgets import (QMessageBox, QDialog) # QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
                             #QTreeWidget, QTreeWidgetItem, QFileDialog, 
                              #QLineEdit, QComboBox
#from PyQt6.QtCore import Qt, QUrl
#from PyQt6.QtGui import QDesktopServices

LUDUSAVI_MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"

# Schema of the per-game ``savegames.json`` written under the staging folder.
SAVEGAMES_SCHEMA = 2
# File (next to the game folders) holding this machine's stable id.
DEVICE_ID_FILENAME = ".launcher_device"
# Tracks which mirror folders were already imported into the local store.
MIRROR_IMPORT_MARKER = ".mirror_imports.json"
# How many snapshots per game are kept when the config has no preference.
DEFAULT_KEEP_SNAPSHOTS = 5
# Suffix of the temporary directory a snapshot is built in before the rename.
SNAPSHOT_TMP_SUFFIX = '.tmp'


def _snapshot_content_signature(source_path):
    """Cheap fingerprint of the source, used to skip identical snapshots.

    Hashes relative path, kind, size and mtime: no file is read, so it stays
    fast on big save folders while still reacting to any real change.
    """
    if not source_path:
        return ""

    digest = hashlib.sha256()

    if os.path.isfile(source_path):
        try:
            info = os.lstat(source_path)
        except OSError:
            return ""
        digest.update(
            f"F|{os.path.basename(source_path)}|{info.st_size}|{info.st_mtime_ns}".encode('utf-8')
        )
        return digest.hexdigest()

    if not os.path.isdir(source_path):
        return ""

    for root, dirs, files in os.walk(source_path, followlinks=False):
        dirs.sort(key=str.lower)
        rel_root = os.path.relpath(root, source_path)
        for name in sorted(files, key=str.lower):
            full = os.path.join(root, name)
            try:
                info = os.lstat(full)
            except OSError:
                continue
            rel = os.path.normpath(os.path.join(rel_root, name)).replace(os.sep, '/')
            kind = 'l' if os.path.islink(full) else 'f'
            digest.update(f"{rel}|{kind}|{info.st_size}|{info.st_mtime_ns}\n".encode('utf-8'))
        # Symlinked folders are part of the snapshot too (as links), so their
        # own metadata must influence the signature.
        for name in sorted(dirs, key=str.lower):
            full = os.path.join(root, name)
            if not os.path.islink(full):
                continue
            try:
                info = os.lstat(full)
            except OSError:
                continue
            rel = os.path.normpath(os.path.join(rel_root, name)).replace(os.sep, '/')
            digest.update(f"{rel}|d|{info.st_size}|{info.st_mtime_ns}\n".encode('utf-8'))
    return digest.hexdigest()


def _utc_from_snapshot_name(name):
    """UTC ISO timestamp inferred from a ``YYYYMMDD_HHMMSS`` snapshot folder."""
    match = re.match(r'^(\d{8})_(\d{6})', str(name or ''))
    if not match:
        return ''
    try:
        stamp = datetime.strptime(match.group(1) + match.group(2), '%Y%m%d%H%M%S')
    except ValueError:
        return ''
    return stamp.strftime('%Y-%m-%dT%H:%M:%SZ')


def safe_game_folder_name(game_title):
    name = re.sub(r'[\\/*?:"<>|]', '', game_title)
    name = re.sub(r'\s+', '_', name.strip())
    return name or "unknown_game"


def safe_filename(filename):
    return re.sub(r'[\\/*?:"<>|]', '_', filename)
from ui.dialogs.manual_save_path_dialog import ManualSavePathDialog
from core import game_version, platform_info, save_paths
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
        """Local backup folder (always the staging folder).

        The name is historical: before phase 4 the configured ``sync_folder``
        was the backup root. It is now an optional mirror target, while the
        local store never leaves the data root.
        """
        from core.paths import PathManager
        return str(PathManager.get_staging_dir())

    def get_backup_root(self):
        """Explicit alias for the local backup folder."""
        return self.get_sync_folder()

    def get_mirror_folder(self):
        """Optional mirror folder configured in Settings (may be empty)."""
        value = (self.app_config or {}).get('savegames', {}).get('sync_folder', '')
        return str(value or '').strip()

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

    @staticmethod
    def is_linux_path(path):
        """True when ``path`` can only be meaningful on a POSIX filesystem."""
        return save_paths.is_posix_path(path)

    def _get_compatdata_base(self, steam_grid_id):
        """Compatdata folder of the game, found on any Steam library."""
        found = save_paths.compatdata_base(steam_grid_id)
        if found:
            return found
        return save_paths.default_compatdata_base(steam_grid_id)

    def translate_win_path_to_linux(self, win_path, steam_grid_id):
        if not win_path or not steam_grid_id:
            return ""
        # The compatdata prefix only exists on the machine that runs the game.
        # On Windows ``~`` expands to C:\Users\<name> and os.path.join emits
        # backslashes, which used to store garbage such as
        # "C:Users\me\.local\share\Steam\steamapps\compatdata\...".
        # Leave the field empty and translate again when running on Linux.
        if not platform_info.is_linux():
            return ""
        base = self._get_compatdata_base(steam_grid_id)
        return save_paths.translate_win_path(win_path, base)

    # Thin aliases kept for existing callers and tests.
    @staticmethod
    def _resolve_case_insensitive_path(path):
        return save_paths.resolve_case_insensitive(path)

    @staticmethod
    def _resolve_save_root(path):
        return save_paths.resolve_save_root(path)

    @staticmethod
    def _resolve_linux_path_from_absolute(path):
        return save_paths.resolve_absolute(path)

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
                meta['savegame_path_source'] = 'manifest'
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
            meta['savegame_path_source'] = 'user'
            if meta.get('steam_grid_id'):
                linux_path = self.translate_win_path_to_linux(manual_path, meta['steam_grid_id'])
                if linux_path:
                    meta['savegame_path_linux'] = linux_path
        else:
            meta['savegame_path_linux'] = manual_path
            meta['savegame_path_source'] = 'user'

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
        if old_win_path and '<' in old_win_path:
            clean_path = self._normalize_manifest_save_path(self.expand_manifest_template(old_win_path))
            meta['savegame_path_win'] = clean_path
            changed = True
        elif old_win_path and str(meta.get('savegame_path_source') or '').strip().lower() != 'user':
            # Inferred paths get the canonical shell folder name; paths typed by
            # the user are left exactly as entered.
            canonical = save_paths.canonicalize_shell_folders(old_win_path.replace('/', '\\'))
            if canonical != old_win_path:
                meta['savegame_path_win'] = canonical
                changed = True

        # --- 3. CONTROLLO MANIFEST LUDUSAVI ---
        title = meta.get('title') or os.path.basename(meta.get('install_dir', ''))
        manifest_entry = self.find_manifest_entry(title)
        path_source = str(meta.get('savegame_path_source') or '').strip().lower()

        if manifest_entry and path_source != 'user':
            win_path = self.extract_win_save_path(manifest_entry)
            if win_path and meta.get('savegame_path_win') != win_path:
                meta['savegame_path_win'] = win_path
                changed = True
            if meta.get('savegame_path_win') and path_source != 'manifest':
                meta['savegame_path_source'] = 'manifest'
                changed = True
        elif (not manifest_entry and not path_source
              and (meta.get('savegame_path_win') or meta.get('savegame_path_linux'))):
            # A path the manifest cannot explain was chosen by the user: keep it
            # and mark it, instead of deleting it on every rescan.
            meta['savegame_path_source'] = 'user'
            changed = True

        # --- 4. TRADUZIONE LINUX ---
        current_win_path = meta.get('savegame_path_win', '')
        grid_id = meta.get('steam_grid_id')
        
        if current_win_path and grid_id:
            linux_path = self.translate_win_path_to_linux(current_win_path, grid_id)
            if linux_path and meta.get('savegame_path_linux') != linux_path:
                meta['savegame_path_linux'] = linux_path
                changed = True
        if meta.get('savegame_path_linux') and not self.is_linux_path(meta['savegame_path_linux']):
            # Drop values generated on another OS (e.g. a Windows-grown "~" path).
            meta['savegame_path_linux'] = ""
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
            if meta.get('savegame_path_linux') and self.is_linux_path(meta['savegame_path_linux']):
                return self._resolve_save_root(meta['savegame_path_linux'])
        else:
            stored_linux = meta.get('savegame_path_linux') or ''
            stored_ok = bool(stored_linux) and self.is_linux_path(stored_linux)
            path_source = str(meta.get('savegame_path_source') or '').strip().lower()
            win_path = meta.get('savegame_path_win') or ''
            grid_id = meta.get('steam_grid_id') or ''

            # A path typed by the user (and with no portable Windows path to
            # derive from) wins over the compatdata location.
            if stored_ok and path_source == 'user' and not (win_path and grid_id):
                return self._resolve_save_root(self._resolve_linux_path_from_absolute(stored_linux))

            # The compatdata path is machine-specific and depends on the Steam
            # shortcut id: rebuild it from the portable Windows path instead of
            # trusting the value saved on another install.
            if win_path and grid_id:
                derived = self.translate_win_path_to_linux(win_path, grid_id)
                if derived:
                    return self._resolve_save_root(derived)

            if stored_ok:
                return self._resolve_save_root(self._resolve_linux_path_from_absolute(stored_linux))

            if win_path.startswith('%'):
                derived = self.translate_win_path_to_linux(win_path, grid_id)
                if derived:
                    return self._resolve_save_root(derived)
            if win_path:
                resolved = self._resolve_base_path(win_path, meta)
                if resolved != win_path:
                    return self._resolve_save_root(self._resolve_case_insensitive_path(resolved))
            return ""

        return ""

    def normalize_windows_path(self, path):
        path = str(path).strip().strip('"')
        path = path.replace('/', '\\')
        return save_paths.canonicalize_shell_folders(path)
    
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

    def keep_snapshots(self):
        """How many snapshots per game to keep (config, default 5)."""
        savegames = (self.app_config or {}).get('savegames', {}) or {}
        raw = savegames.get('keep_snapshots', DEFAULT_KEEP_SNAPSHOTS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_KEEP_SNAPSHOTS
        return max(1, value)

    def _trim_backup_folders(self, save_root, keep=None):
        """Keep only the newest ``keep`` snapshots (UTC names sort correctly)."""
        keep = self.keep_snapshots() if keep is None else max(1, int(keep))
        try:
            candidates = [
                os.path.join(save_root, name)
                for name in os.listdir(save_root)
                if not name.startswith('.') and not name.endswith(SNAPSHOT_TMP_SUFFIX)
                and os.path.isdir(os.path.join(save_root, name))
            ]
            candidates.sort(key=lambda p: os.path.basename(p), reverse=True)
            for folder in candidates[keep:]:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError as exc:
            print(f"[Savegames] Rotation failed in {save_root}: {exc}")

    def _cleanup_stale_tmp(self, save_root, max_age_seconds=6 * 3600):
        """Remove leftovers of an interrupted snapshot build."""
        now = time.time()
        try:
            names = os.listdir(save_root)
        except OSError:
            return
        for name in names:
            if not name.endswith(SNAPSHOT_TMP_SUFFIX):
                continue
            path = os.path.join(save_root, name)
            try:
                if os.path.isdir(path) and now - os.path.getmtime(path) > max_age_seconds:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    @staticmethod
    def _read_snapshot_info(snapshot_dir):
        info_path = os.path.join(snapshot_dir, 'snapshot.json')
        if not os.path.isfile(info_path):
            return {}
        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _copy_symlink(source, destination):
        target = os.readlink(source)
        try:
            os.symlink(target, destination)
        except (OSError, NotImplementedError):
            # Windows without the privilege: keep the data instead of the link.
            if os.path.isfile(source):
                shutil.copy2(source, destination)
            else:
                raise

    def _copy_source_tolerant(self, source, destination, errors):
        """Copy files one by one, collecting failures instead of aborting."""
        if os.path.isfile(source) and not os.path.isdir(source):
            try:
                shutil.copy2(source, destination)
                return 1
            except Exception as exc:
                errors.append(f"{os.path.basename(source)}: {exc}")
                return 0
        if not os.path.isdir(source):
            errors.append(f"Unsupported source: {source}")
            return 0

        copied = 0
        for root, dirs, files in os.walk(source, followlinks=False):
            dirs.sort(key=str.lower)
            rel_root = os.path.relpath(root, source)
            target_root = destination if rel_root == '.' else os.path.join(destination, rel_root)
            try:
                os.makedirs(target_root, exist_ok=True)
            except OSError as exc:
                errors.append(f"{rel_root}: {exc}")
                continue

            for name in list(dirs):
                src_dir = os.path.join(root, name)
                if not os.path.islink(src_dir):
                    continue
                try:
                    self._copy_symlink(src_dir, os.path.join(target_root, name))
                except Exception as exc:
                    errors.append(f"{os.path.relpath(src_dir, source)}: {exc}")
                dirs.remove(name)

            for name in sorted(files, key=str.lower):
                src = os.path.join(root, name)
                dst = os.path.join(target_root, name)
                try:
                    if os.path.islink(src):
                        self._copy_symlink(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    copied += 1
                except Exception as exc:
                    errors.append(f"{os.path.relpath(src, source)}: {exc}")
        return copied

    def create_snapshot(self, game_root, title, meta, source_path, reason='sync'):
        """Build an atomic snapshot of ``source_path`` under ``game_root/saves``.

        Returns ``{snapshot, created, unchanged, errors}``. Identical content is
        not duplicated; a snapshot that could not be copied at all is discarded.
        """
        result = {'snapshot': '', 'created': False, 'unchanged': False, 'errors': []}
        if not source_path or not os.path.exists(source_path):
            result['errors'].append(f"source not found: {source_path}")
            return result

        save_root = os.path.join(game_root, 'saves')
        self._ensure_folder_path(save_root)
        self._cleanup_stale_tmp(save_root)

        signature = _snapshot_content_signature(source_path)
        entries = self._snapshot_entries(game_root)
        if signature and entries:
            latest_dir = os.path.join(save_root, entries[-1]['id'])
            latest_info = self._read_snapshot_info(latest_dir)
            if (latest_info.get('content_hash') == signature
                    and latest_info.get('complete', True)):
                result['snapshot'] = latest_dir
                result['unchanged'] = True
                return result

        stamp = time.strftime('%Y%m%d_%H%M%S', time.gmtime())
        final_dir = os.path.join(save_root, stamp)
        counter = 1
        while os.path.exists(final_dir) or os.path.exists(final_dir + SNAPSHOT_TMP_SUFFIX):
            final_dir = os.path.join(save_root, f"{stamp}_{counter}")
            counter += 1
        tmp_dir = final_dir + SNAPSHOT_TMP_SUFFIX

        try:
            os.makedirs(tmp_dir, exist_ok=False)
        except OSError as exc:
            result['errors'].append(str(exc))
            return result

        errors = []
        copied = self._copy_source_tolerant(source_path, tmp_dir, errors)
        if not copied and errors:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            result['errors'] = errors
            return result

        info = {
            'schema': 1,
            'id': os.path.basename(final_dir),
            'created_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'device_id': self._device_id(os.path.dirname(os.path.normpath(game_root))),
            'reason': reason,
            'version': game_version.version_info_from_meta(meta),
            'title': title,
            'kind': 'file' if os.path.isfile(source_path) and not os.path.isdir(source_path) else 'dir',
            'source_name': os.path.basename(os.path.normpath(source_path)),
            'content_hash': signature,
            'complete': not errors,
            'errors': len(errors),
        }
        try:
            with open(os.path.join(tmp_dir, 'snapshot.json'), 'w', encoding='utf-8') as f:
                json.dump(info, f, indent=4, ensure_ascii=False)
            os.replace(tmp_dir, final_dir)
        except OSError as exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            result['errors'] = errors + [f"snapshot rename failed: {exc}"]
            return result

        self._trim_backup_folders(save_root)
        result.update({'snapshot': final_dir, 'created': True, 'errors': errors})
        return result

    def ensure_game_assets(self, game_root, title, meta):
        """Best effort: a game root always gets a cover and a manifest."""
        if not game_root:
            return
        try:
            os.makedirs(game_root, exist_ok=True)
        except OSError as exc:
            print(f"[Savegames] Could not create {game_root}: {exc}")
            return

        cover_path = os.path.join(game_root, 'mini_cover.jpg')
        try:
            has_cover = os.path.isfile(cover_path) and os.path.getsize(cover_path) > 1024
        except OSError:
            has_cover = False
        if not has_cover:
            try:
                from core.mini_cover_cache import MiniCoverManager
                MiniCoverManager.persist_cover(title, self.installed_games, self.get_sync_folder())
            except Exception as exc:
                print(f"[Savegames] Mini cover not available for {title}: {exc}")

        if not os.path.isfile(os.path.join(game_root, 'savegames.json')):
            self._touch_manifest(game_root, title, meta)

    def backup_before_update(self, meta):
        """Snapshot the current saves before an in-place update. Never prompts."""
        if not meta:
            return False
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return False
        title = meta.get('title') or 'Unknown'
        source_path = self._expand_path(self.get_source_save_path(meta))
        if not source_path or not os.path.exists(source_path):
            return False

        game_root = os.path.join(sync_folder, safe_game_folder_name(title))
        result = self.create_snapshot(game_root, title, meta, source_path, reason='pre_update')
        if not (result['created'] or result['unchanged']):
            return False
        self._freshen_json(game_root, title, meta, source_path, result['snapshot'] or None)
        self.ensure_game_assets(game_root, title, meta)
        return True

    def _restore_allowed_targets(self, meta):
        """Roots a restore may write into, plus paths that must never be hit."""
        home = os.path.expanduser('~')
        userprofile = os.environ.get('USERPROFILE') or home
        allowed = []
        protected = {home, userprofile}

        for base in (home, userprofile):
            if not base:
                continue
            allowed.extend([
                os.path.join(base, 'Documents'),
                os.path.join(base, 'My Documents'),
                os.path.join(base, 'Saved Games'),
                os.path.join(base, 'AppData'),
            ])

        for env in ('LOCALAPPDATA', 'APPDATA', 'PROGRAMDATA', 'PUBLIC'):
            value = str(os.environ.get(env) or '').strip()
            if value:
                allowed.append(value)

        install_dir = str(meta.get('install_dir') or '').strip()
        if install_dir:
            allowed.append(install_dir)
            protected.add(install_dir)

        grid_id = meta.get('steam_grid_id')
        if grid_id:
            compatdata = self._get_compatdata_base(grid_id)
            if compatdata:
                allowed.append(compatdata)
                protected.add(compatdata)

        exact = []
        custom = str(meta.get('savegame_path_custom') or '').strip()
        if custom:
            expanded = self._expand_path(custom)
            if expanded:
                exact.append(expanded)
                allowed.append(expanded)

        # Never restore into a system folder or a drive root.
        protected.add('C:\\')
        protected.add('/')
        if os.name == 'nt':
            drive, _ = os.path.splitdrive(os.path.abspath(home))
            if drive:
                protected.add(drive + '\\')
            for env in ('ProgramFiles', 'ProgramFiles(x86)', 'WINDIR', 'SystemRoot'):
                value = str(os.environ.get(env) or '').strip()
                if value:
                    protected.add(value)

        return allowed, exact, protected

    def _restore_guard(self, meta):
        """Return a reason when the game cannot receive a restore."""
        if not meta or meta.get('is_orphan'):
            return 'not_installed'
        install_dir = str(meta.get('install_dir') or '').strip()
        if not install_dir or not os.path.isdir(install_dir):
            return 'not_installed'
        return ''

    def list_snapshots(self, title):
        """Snapshots of one game with their metadata, newest first."""
        game_root = os.path.join(self.get_sync_folder(), safe_game_folder_name(title))
        save_root = os.path.join(game_root, 'saves')
        entries = self._snapshot_entries(game_root)
        for entry in entries:
            entry['path'] = os.path.join(save_root, entry['id'])
        entries.reverse()
        return entries

    def _record_restore(self, game_root, title, meta, safety_snapshot=''):
        try:
            old = self.read_game_manifest(game_root)
            data = self._build_game_manifest(
                game_root, title, meta,
                snapshot_folder=safety_snapshot or None,
                old=old,
            )
            data['last_restore_at'] = game_version.utc_now_iso()
            self._write_game_manifest_file(game_root, data)
            self.ensure_game_assets(game_root, title, meta)
        except Exception as exc:
            print(f"[Savegames] Could not record restore for {title}: {exc}")

    def restore_snapshot(self, meta, snapshot_id, create_missing=False):
        """Restore a snapshot into the live save folder, safely.

        The current state is snapshotted (``pre_restore``) before the swap, the
        destination is validated against known roots, and the new content is
        built next to it so a failure can be rolled back.
        """
        result = {
            'ok': False,
            'reason': '',
            'detail': '',
            'destination': '',
            'snapshot': str(snapshot_id or ''),
            'safety_snapshot': '',
            'files': 0,
            'errors': [],
        }

        guard = self._restore_guard(meta)
        if guard:
            result['reason'] = guard
            return result

        title = meta.get('title') or os.path.basename(str(meta.get('install_dir') or ''))
        game_root = os.path.join(self.get_sync_folder(), safe_game_folder_name(title))
        save_root = os.path.join(game_root, 'saves')
        snapshot_id = str(snapshot_id or '').strip()
        snapshot_dir = os.path.join(save_root, snapshot_id)
        if not snapshot_id or not os.path.isdir(snapshot_dir):
            result['reason'] = 'snapshot_not_found'
            return result

        destination = self.get_source_save_path(meta)
        result['destination'] = destination
        if not destination:
            result['reason'] = 'destination_unknown'
            return result

        # Never write into the backup store itself.
        sync_folder = self.get_sync_folder()
        dest_norm = save_paths.normalize_for_compare(destination)
        if sync_folder and (dest_norm == save_paths.normalize_for_compare(sync_folder)
                            or save_paths.is_inside(destination, sync_folder)):
            result['reason'] = 'unsafe_target'
            result['detail'] = 'staging_folder'
            return result

        allowed, exact, protected = self._restore_allowed_targets(meta)
        ok, why = save_paths.check_restore_target(destination, allowed, exact, protected)
        if not ok:
            result['reason'] = 'unsafe_target'
            result['detail'] = why
            return result

        if os.path.islink(destination):
            resolved = os.path.realpath(destination)
            ok, why = save_paths.check_restore_target(resolved, allowed, exact, protected)
            if not ok:
                result['reason'] = 'unsafe_target'
                result['detail'] = why
                return result
            destination = resolved
            result['destination'] = destination

        destination_exists = os.path.exists(destination)
        if not destination_exists and not create_missing:
            result['reason'] = 'destination_missing'
            return result

        parent = os.path.dirname(os.path.normpath(destination))
        if not os.path.isdir(parent):
            result['reason'] = 'destination_parent_missing'
            return result

        info = self._read_snapshot_info(snapshot_dir)
        result['snapshot_complete'] = bool(info.get('complete', True))
        kind = str(info.get('kind') or 'dir')

        if kind != 'file' and os.path.isfile(destination):
            result['reason'] = 'destination_kind_mismatch'
            return result

        # --- single-file snapshot -----------------------------------------
        if kind == 'file':
            source_name = str(info.get('source_name') or '').strip()
            source_file = os.path.join(snapshot_dir, source_name) if source_name else ''
            if not source_name or not os.path.isfile(source_file):
                result['reason'] = 'snapshot_content_missing'
                return result

            target = destination
            if os.path.isdir(target):
                target = os.path.join(target, source_name)

            if os.path.exists(target):
                safety = self.create_snapshot(game_root, title, meta, target, reason='pre_restore')
                result['safety_snapshot'] = (
                    os.path.basename(safety['snapshot']) if safety['snapshot'] else ''
                )
                if not (safety['created'] or safety['unchanged']):
                    result['reason'] = 'safety_snapshot_failed'
                    result['errors'] = safety['errors']
                    return result

            tmp_file = target + '.restore_tmp'
            try:
                shutil.copy2(source_file, tmp_file)
                os.replace(tmp_file, target)
            except OSError as exc:
                try:
                    os.remove(tmp_file)
                except OSError:
                    pass
                result['reason'] = 'copy_failed'
                result['errors'] = [str(exc)]
                return result

            self._record_restore(game_root, title, meta, result['safety_snapshot'])
            result.update({'ok': True, 'files': 1})
            return result

        # --- directory snapshot -------------------------------------------
        tmp_dir = destination + '.restore_tmp'
        old_dir = destination + '.restore_old'
        shutil.rmtree(tmp_dir, ignore_errors=True)
        self._ensure_folder_path(tmp_dir)

        errors = []
        files = self._copy_source_tolerant(snapshot_dir, tmp_dir, errors)
        try:
            os.remove(os.path.join(tmp_dir, 'snapshot.json'))
            files = max(0, files - 1)
        except OSError:
            pass
        if not files and errors:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            result['reason'] = 'copy_failed'
            result['errors'] = errors
            return result

        # Snapshot the current state before overwriting it.
        if destination_exists:
            safety = self.create_snapshot(game_root, title, meta, destination, reason='pre_restore')
            result['safety_snapshot'] = (
                os.path.basename(safety['snapshot']) if safety['snapshot'] else ''
            )
            if not (safety['created'] or safety['unchanged']):
                shutil.rmtree(tmp_dir, ignore_errors=True)
                result['reason'] = 'safety_snapshot_failed'
                result['errors'] = safety['errors']
                return result

        # Swap the prepared content in; roll back if the second rename fails.
        shutil.rmtree(old_dir, ignore_errors=True)
        if destination_exists:
            try:
                os.replace(destination, old_dir)
            except OSError as exc:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                result['reason'] = 'swap_failed'
                result['errors'] = [str(exc)]
                return result

        try:
            os.replace(tmp_dir, destination)
        except OSError as exc:
            if destination_exists:
                try:
                    os.replace(old_dir, destination)
                except OSError:
                    pass
            shutil.rmtree(tmp_dir, ignore_errors=True)
            result['reason'] = 'swap_failed'
            result['errors'] = [str(exc)]
            return result

        shutil.rmtree(old_dir, ignore_errors=True)
        self._record_restore(game_root, title, meta, result['safety_snapshot'])
        result.update({'ok': True, 'files': files, 'errors': errors})
        return result

    # -------------------------------------------------------------- mirror

    def _mirror_import_marker(self):
        return os.path.join(self.get_sync_folder(), MIRROR_IMPORT_MARKER)

    def _imported_mirrors(self):
        path = self._mirror_import_marker()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict):
            return []
        return [str(item) for item in data.get('mirrors') or []]

    def ensure_mirror_import(self):
        """One-time, non-destructive import of backups already in the mirror.

        Before phase 4 the configured folder was the backup root. Importing it
        into Staging keeps those backups visible without moving or deleting
        anything; the mirror itself is only read here.
        """
        mirror = self.get_mirror_folder()
        report = {'configured': bool(mirror), 'copied': 0, 'skipped': 0, 'errors': []}
        if not mirror or not os.path.isdir(mirror):
            return report

        local = self.get_sync_folder()
        self._ensure_folder_path(local)
        imported = self._imported_mirrors()
        key = save_paths.normalize_for_compare(mirror)
        if key in imported:
            return report

        from managers.mirror_backend import FolderMirrorBackend
        backend = FolderMirrorBackend(local, mirror)
        report.update(backend.pull_missing(exclude_names=(DEVICE_ID_FILENAME,)))

        if key not in imported:
            imported.append(key)
        try:
            marker = self._mirror_import_marker()
            tmp = marker + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'mirrors': sorted(set(imported))}, f, indent=4)
            os.replace(tmp, marker)
        except OSError as exc:
            report['errors'].append(f"marker: {exc}")
        return report

    def push_mirror(self):
        """Copy the local store to the configured mirror (incremental)."""
        mirror = self.get_mirror_folder()
        report = {'configured': bool(mirror), 'copied': 0, 'skipped': 0, 'errors': []}
        if not mirror:
            return report

        self.ensure_mirror_import()

        from managers.mirror_backend import FolderMirrorBackend
        backend = FolderMirrorBackend(self.get_sync_folder(), mirror)
        report = backend.push(exclude_names=(DEVICE_ID_FILENAME, MIRROR_IMPORT_MARKER))
        try:
            backend.write_manifest(self._device_id(self.get_sync_folder()))
        except Exception as exc:
            report['errors'].append(f"manifest: {exc}")
        return report

    def compare_mirror(self):
        """Per-game local/mirror states (empty list without a mirror)."""
        mirror = self.get_mirror_folder()
        if not mirror:
            return []
        from managers.mirror_backend import FolderMirrorBackend
        return FolderMirrorBackend(self.get_sync_folder(), mirror).compare()

    def test_mirror(self, folder=None):
        from managers.mirror_backend import FolderMirrorBackend
        return FolderMirrorBackend(
            self.get_sync_folder(), folder or self.get_mirror_folder()
        ).test()

    def sync_all_games(self):
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return {'synced_games': 0, 'unchanged_games': 0,
                    'errors': ['No local backup folder configured.']}

        self._ensure_folder_path(sync_folder)
        # Bring in backups that only exist in the mirror (one-time, non-destructive).
        self.ensure_mirror_import()
        created_games = 0
        unchanged_games = 0
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
                    # No source: keep the recorded last_synced instead of lying.
                    self._freshen_json(game_root, title, meta)
                    self.ensure_game_assets(game_root, title, meta)
                continue

            result = self.create_snapshot(game_root, title, meta, source_path, reason='sync')
            if result['created']:
                created_games += 1
            elif result['unchanged']:
                unchanged_games += 1
            errors.extend(f"{title}: {err}" for err in result['errors'])

            if result['created'] or result['unchanged']:
                self._freshen_json(game_root, title, meta, source_path, result['snapshot'] or None)
            else:
                self._touch_manifest(game_root, title, meta)
            self.ensure_game_assets(game_root, title, meta)

        mirror_report = self.push_mirror()
        for error in mirror_report.get('errors') or []:
            errors.append(f"mirror: {error}")

        return {'synced_games': created_games,
                'unchanged_games': unchanged_games,
                'errors': errors,
                'mirror': mirror_report}

    def _persisted_linux_path(self, meta):
        """Return the stored Linux path only when it is valid on POSIX.

        Prevents a Windows-grown value (``C:Users\\me\\.local\\...``) from being
        written back into ``savegames.json``.
        """
        stored = meta.get('savegame_path_linux', '')
        return stored if self.is_linux_path(stored) else ''

    def _linux_path_for_meta(self, meta):
        """Linux path to persist: the stored one when valid, else a fresh translation."""
        stored = self._persisted_linux_path(meta)
        if stored:
            return stored
        win_path = meta.get('savegame_path_win', '')
        grid_id = meta.get('steam_grid_id', '')
        if win_path and grid_id:
            return self.translate_win_path_to_linux(win_path, grid_id)
        return ''

    # ------------------------------------------------------------- manifest

    @staticmethod
    def read_game_manifest(game_root):
        """Read ``savegames.json`` (any schema) without touching the files."""
        path = os.path.join(game_root, 'savegames.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_game_manifest_file(game_root, data):
        os.makedirs(game_root, exist_ok=True)
        path = os.path.join(game_root, 'savegames.json')
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _device_id(self, root):
        """Stable id of this machine, stored next to the game folders."""
        path = os.path.join(str(root), DEVICE_ID_FILENAME)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                value = f.read().strip()
            if value:
                return value
        except OSError:
            pass

        value = uuid.uuid4().hex
        try:
            os.makedirs(str(root), exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(value)
            os.replace(tmp, path)
        except OSError:
            # Fail transparently instead of inventing a new id on every write.
            return ''
        return value

    @staticmethod
    def _snapshot_entries(game_root):
        """Snapshot list rebuilt from ``saves/``: the filesystem is the truth."""
        saves_dir = os.path.join(game_root, 'saves')
        try:
            names = sorted(
                name for name in os.listdir(saves_dir)
                if not name.startswith('.')
                and not name.endswith(SNAPSHOT_TMP_SUFFIX)
                and os.path.isdir(os.path.join(saves_dir, name))
            )
        except OSError:
            return []

        entries = []
        for name in names:
            info = SavegamesManager._read_snapshot_info(os.path.join(saves_dir, name))
            version = info.get('version') if isinstance(info.get('version'), dict) else {}
            entries.append({
                'id': name,
                'created_at': str(info.get('created_at') or '').strip()
                              or _utc_from_snapshot_name(name),
                'device_id': str(info.get('device_id') or ''),
                'version': version,
                'reason': str(info.get('reason') or ''),
                'complete': bool(info.get('complete', True)),
                'errors': int(info.get('errors', 0) or 0),
            })
        return entries

    def _build_game_manifest(self, game_root, title, meta, source_path=None,
                             snapshot_folder=None, old=None):
        """Schema 2 manifest for one game: the only writer for every call site."""
        old = old if old is not None else self.read_game_manifest(game_root)
        sync_folder = os.path.dirname(os.path.normpath(game_root))
        entries = self._snapshot_entries(game_root)

        latest = ''
        if snapshot_folder:
            latest = 'saves/' + os.path.basename(os.path.normpath(snapshot_folder))
        if not latest:
            latest = str(old.get('latest_snapshot') or '')
        if not latest and entries:
            latest = 'saves/' + entries[-1]['id']

        last_synced = str(old.get('last_synced') or '')
        if source_path:
            last_synced = game_version.utc_now_iso()

        info = game_version.version_info_from_meta(meta)
        if not info.get('build') and not game_version.normalize_label(info.get('label')):
            info = old.get('game_version') or info

        playtime_seconds = int(meta.get('playtime_seconds', 0) or 0)
        win_path = meta.get('savegame_path_win', '') or ''
        path_source = str(meta.get('savegame_path_source') or '').strip()

        return {
            'schema': SAVEGAMES_SCHEMA,
            'game_title': title,
            'safe_title': os.path.basename(os.path.normpath(game_root)),
            'device_id': self._device_id(sync_folder),
            'last_synced': last_synced,
            'last_restore_at': str(old.get('last_restore_at') or ''),
            'latest_snapshot': latest,
            'snapshots': entries,
            'source_path': source_path or old.get('source_path', ''),
            'install_dir': meta.get('install_dir', ''),
            'steam_grid_id': meta.get('steam_grid_id', ''),
            'game_version': info,
            'savegame_path_win': win_path,
            'savegame_path_linux': self._linux_path_for_meta(meta),
            'savegame_path_custom': meta.get('savegame_path_custom', ''),
            'savegame_path_source': path_source,
            'playtime_seconds': playtime_seconds,
            'playtime_hours': round(playtime_seconds / 3600.0, 2),
            'playtime_only': not entries,
            # Legacy key: older readers expect the relative snapshot path here.
            'snapshot_folder': latest,
        }

    def _freshen_json(self, game_root, title, meta, source_path=None, snapshot_folder=None):
        try:
            old = self.read_game_manifest(game_root)
            metadata = self._build_game_manifest(
                game_root, title, meta,
                source_path=source_path,
                snapshot_folder=snapshot_folder,
                old=old,
            )
            self._write_game_manifest_file(game_root, metadata)
        except Exception as exc:
            print(f"[Savegames] Could not freshen JSON for {title}: {exc}")

    def _touch_manifest(self, game_root, title, meta):
        """Rewrite the manifest without touching ``last_synced``/``source_path``."""
        try:
            old = self.read_game_manifest(game_root)
            metadata = self._build_game_manifest(game_root, title, meta, old=old)
            self._write_game_manifest_file(game_root, metadata)
        except Exception as exc:
            print(f"[Savegames] Could not update manifest for {title}: {exc}")

    def backup_on_demand(self, meta):
        """Esegue un backup istantaneo per un singolo gioco (chiamato alla chiusura)."""
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            print("[Savegames] Backup on-demand saltato: cartella di sync non configurata.")
            return False

        title = meta.get('title', 'Unknown')
        source_path = self._expand_path(self.get_source_save_path(meta))

        if not source_path or not os.path.exists(source_path):
            print(f"[Savegames] Backup on-demand fallito: sorgente non trovata per {title}")
            return False

        print(f"[Savegames] Esecuzione backup on-demand per {title}...")

        game_root = os.path.join(sync_folder, safe_game_folder_name(title))
        result = self.create_snapshot(game_root, title, meta, source_path, reason='on_demand')

        for error in result['errors']:
            print(f"[Savegames] Backup on-demand {title}: {error}")

        if not (result['created'] or result['unchanged']):
            return False

        self._freshen_json(game_root, title, meta, source_path, result['snapshot'] or None)
        self.ensure_game_assets(game_root, title, meta)
        mirror_report = self.push_mirror()
        for error in mirror_report.get('errors') or []:
            print(f"[Savegames] Mirror {title}: {error}")
        state = "unchanged" if result['unchanged'] and not result['created'] else "created"
        print(f"[Savegames] Backup completato per {title} ({state}).")
        return True

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
        self._touch_manifest(game_root, title, meta)

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
        self.ensure_game_assets(game_root, title, meta)

    def ensure_playtime_stubs(self):
        for title, meta in self.installed_games.items():
            try:
                self.ensure_playtime_stub(title, meta)
            except Exception as exc:
                print(f"[Savegames] Errore stub playtime per '{title}': {exc}")