# installer.py
import os
import json
import re
import sys
import subprocess
import time
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from core.config import get_base_dir
from core.download_queue_model import clear_extraction_state, write_extraction_state
from core.meta_utils import EXTRACTING_MARKER, save_steamrip_meta

# Chiavi di metadati da conservare quando aggiorniamo un'installazione esistente
# (aggiornamento in-place di una versione full re-download). Tutte le altre chiavi
# vengono sovrascritte con i valori freschi (title, version, steam_id, ecc.).
PERSISTENT_META_KEYS = (
    "savegame_path_win",
    "savegame_path_linux",
    "savegame_path_custom",
    "savegame_scan_state",
    "steam_shortcut_id",
    "steam_grid_id",
    "executable_path",
    "linux_wrapper",
)

class GameInstaller(QThread):
    progress_update = pyqtSignal(str)
    extraction_started = pyqtSignal(float) # unpacked_size_mb
    extraction_progress = pyqtSignal(int, float) # pct, mb_extracted
    installation_complete = pyqtSignal(str)
    error_occurred = pyqtSignal(str)
    password_required = pyqtSignal(str, str) # NUOVO: archive_path, destination_folder

    def __init__(self, archive_path, destination_folder, game_data, password="", preserve_meta=False):
        super().__init__()
        self.archive_path = archive_path
        self.destination_folder = destination_folder
        self.game_data = game_data
        self.password = password
        self.preserve_meta = preserve_meta
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            game_title = self.game_data.get('title', 'Unknown Game')
            safe_title = re.sub(r'[\\/*?:"<>|]', "", game_title)
            final_game_path = os.path.join(self.destination_folder, safe_title)

            if not os.path.exists(final_game_path):
                os.makedirs(final_game_path)

            self.progress_update.emit(f"Estrazione in corso di {safe_title}... (Potrebbe volerci molto tempo)")

            base_dir = get_base_dir()
            if sys.platform == "win32":
                seven_z_bin = os.path.join(base_dir, "bin", "Windows", "x64", "7z.exe")
            else:
                seven_z_bin = os.path.join(base_dir, "bin", "Linux", "7zz")

            if sys.platform != "win32" and os.path.exists(seven_z_bin):
                try:
                    os.chmod(seven_z_bin, 0o755)
                except OSError:
                    pass

            # Il flag -p- impedisce l'hang in console. Se self.password esiste, la usiamo.
            pass_arg = f"-p{self.password}" if self.password else "-p-"
            
            cmd_list = [seven_z_bin, "l", "-y", pass_arg, self.archive_path]
            list_proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, text=True, cwd=final_game_path)
            list_out, _ = list_proc.communicate()

            # Controllo immediato se gli header sono criptati e abbiamo fallito
            if list_proc.returncode != 0:
                out_lower = list_out.lower()
                if "wrong password" in out_lower or "cannot open encrypted archive" in out_lower:
                    self.password_required.emit(self.archive_path, self.destination_folder)
                    return

            unpacked_size = 0
            lines = list_out.split('\n')
            for i, line in enumerate(lines):
                if line.startswith('-------------------'):
                    if i + 1 < len(lines):
                        parts = lines[i+1].split()
                        if len(parts) >= 2:
                            for p in parts:
                                if p.isdigit():
                                    val = int(p)
                                    if val > unpacked_size:
                                        unpacked_size = val

            unpacked_mb = unpacked_size / (1024 * 1024)
            self.extraction_started.emit(unpacked_mb)

            # Marker di estrazione in corso: se l'app viene chiusa o
            # l'estrazione si interrompe, la cartella non verrà scambiata per
            # un'installazione completa al prossimo avvio.
            marker_path = os.path.join(final_game_path, EXTRACTING_MARKER)
            try:
                with open(marker_path, "w", encoding="utf-8") as marker:
                    marker.write(self.archive_path)
            except OSError:
                pass

            # Sidecar used by the downloads queue to show "extraction
            # interrupted at x%" after a crash or a cancel.
            write_extraction_state(self.archive_path, {
                "archive_path": self.archive_path,
                "install_dir": final_game_path,
                "title": (self.game_data or {}).get("title", ""),
                "page_url": (self.game_data or {}).get("page_url", ""),
                "started_at": time.time(),
                "pct": 0,
            })

            cmd = [seven_z_bin, "x", self.archive_path, f"-o{final_game_path}", "-y", "-bsp1", pass_arg]
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, cwd=final_game_path)

            last_emit = time.time()
            last_state_write = time.time()
            mb_extracted = 0.0

            buffer = ""
            while True:
                if self._is_cancelled:
                    process.terminate()
                    break

                char = process.stdout.read(1)
                if not char and process.poll() is not None:
                    break

                if char in ('\b', '\r', '\n'):
                    if buffer:
                        match = re.search(r'(\d+)%', buffer)
                        if match:
                            pct = int(match.group(1))
                            if unpacked_size > 0:
                                mb_extracted = (pct / 100.0) * (unpacked_size / (1024 * 1024))
                            else:
                                mb_extracted = 0.0
                            
                            now = time.time()
                            if now - last_emit > 0.1:
                                self.extraction_progress.emit(pct, mb_extracted)
                                last_emit = now
                            if now - last_state_write > 5:
                                write_extraction_state(self.archive_path, {
                                    "archive_path": self.archive_path,
                                    "install_dir": final_game_path,
                                    "title": (self.game_data or {}).get("title", ""),
                                    "page_url": (self.game_data or {}).get("page_url", ""),
                                    "updated_at": now,
                                    "pct": pct,
                                })
                                last_state_write = now
                        buffer = ""
                else:
                    buffer += char

            _, stderr = process.communicate()
            
            if process.returncode != 0 and not self._is_cancelled:
                out_lower = (buffer + stderr).lower()
                if "wrong password" in out_lower or "cannot open encrypted archive" in out_lower or "data error in encrypted file" in out_lower:
                    self.password_required.emit(self.archive_path, self.destination_folder)
                    return
                raise Exception(f"Errore 7-Zip (Codice {process.returncode}): {stderr}")

            if self._is_cancelled:
                return

            self.progress_update.emit("Estrazione completata! Generazione metadati...")

            self._generate_metadata(final_game_path)
            try:
                os.remove(marker_path)
            except OSError:
                pass
            clear_extraction_state(self.archive_path)
            os.remove(self.archive_path)
            self.installation_complete.emit(final_game_path)

        except Exception as e:
            self.error_occurred.emit(f"Errore critico durante l'installazione: {str(e)}")

    def _generate_metadata(self, install_path):
        multiplayer_crack = "Nessuna / Sconosciuta"
        game_info = self.game_data.get('game_info', '')
        mp_match = re.search(r'(?i)(multiplayer by|co-op by|coop by|online by|crack by)[\s:]*([^\n]+)', game_info)
        if mp_match:
            raw_mp = mp_match.group(2).strip()
            clean_mp = re.sub(r'<[^>]+>', '', raw_mp)
            clean_mp = re.split(r'(?i)(version|pre-installed|\||<li>)', clean_mp)[0].strip()
            multiplayer_crack = clean_mp

        version_status = self.game_data.get('version_status') or {}
        meta_dict = {
            "title": self.game_data.get('title'),
            "version": self.game_data.get('version'),
            "build": version_status.get('posted_build') or "",
            "build_date": version_status.get('posted_build_date', ''),
            "executable_path": "",
            "multiplayer_crack": multiplayer_crack,
            "has_dlc": "📦 MOSTRA DLC INCLUSI" in self.game_data.get('description', ''),
            "source_url": self.game_data.get('page_url', 'N/A'),
            "savegame_path_win": "",
            "savegame_path_linux": "",
            "savegame_scan_state": False,
            "steam_grid_id": "",
            "steam_id": str(self.game_data.get("steam_id", "") or "")
        }

        if self.preserve_meta:
            meta_file = os.path.join(install_path, "steamrip_meta.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    for key in PERSISTENT_META_KEYS:
                        if key in existing:
                            meta_dict[key] = existing[key]
                except Exception as e:
                    print(f"[Installer] Impossibile preservare i metadati esistenti: {e}")

        save_steamrip_meta(install_path, meta_dict)