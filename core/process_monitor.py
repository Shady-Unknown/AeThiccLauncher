import psutil
import time
import os
from PyQt6.QtCore import QThread, pyqtSignal

from core import platform_info

class GameMonitorThread(QThread):
    game_exited_signal = pyqtSignal(dict)

    def __init__(self, pid, meta, exe_path=None, create_time=None,
                 session_start=None):
        super().__init__()
        self.pid = pid
        self.meta = meta
        self.exe_path = exe_path
        if create_time is None and pid:
            try:
                create_time = psutil.Process(pid).create_time()
            except Exception:
                create_time = None
        self.pid_create_time = create_time
        self._is_running = True
        self.session_start = (session_start if session_start is not None
                              else time.monotonic())
        self.target_exe_lower = ""
        self.target_filename_lower = ""
        if exe_path:
            self.target_exe_lower = os.path.normpath(exe_path).lower()
            self.target_filename_lower = os.path.basename(self.target_exe_lower)
        self.handoffs_used = 0

    def _emit_exit(self):
        payload = dict(self.meta)
        try:
            payload['session_seconds'] = int(time.monotonic() - self.session_start)
        except Exception:
            payload['session_seconds'] = 0
        # Wall-clock bounds for the session log. The monotonic clock is still
        # the source of truth for the duration; these fields are only used to
        # place the session on a timeline.
        payload['ended_at'] = int(time.time())
        payload['started_at'] = payload['ended_at'] - max(0, payload['session_seconds'])
        self.game_exited_signal.emit(payload)

    def _signature_matches(self, info):
        try:
            name = (info.get('name') or '').lower()
            if self.target_filename_lower and name == self.target_filename_lower:
                return True
            exe = info.get('exe') or ''
            if exe:
                exe_lower = os.path.normpath(exe).lower()
                if self.target_exe_lower and exe_lower == self.target_exe_lower:
                    return True
        except Exception:
            pass
        return False

    def _tracked_process_alive(self):
        if not self.pid:
            return False
        try:
            proc = psutil.Process(self.pid)
            if not proc.is_running():
                return False
            if self.pid_create_time is not None:
                try:
                    if abs(proc.create_time() - self.pid_create_time) > 1.0:
                        return False
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    return False
            try:
                if proc.status() == psutil.STATUS_ZOMBIE:
                    return False
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _find_matching_process(self):
        try:
            for proc in psutil.process_iter(['name', 'exe', 'create_time']):
                try:
                    if self._signature_matches(proc.info):
                        self.pid = proc.pid
                        self.pid_create_time = proc.info.get('create_time')
                        print(f"[Monitor] Riancorato al processo {proc.pid} ({proc.info.get('name')})")
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            print(f"[Monitor] Errore nella scansione processi: {e}")
        return False

    def _try_process_handoff(self):
        if self.handoffs_used >= 2:
            return False
        if self.pid_create_time is not None and (time.time() - self.pid_create_time) > 15:
            return False
        try:
            for proc in psutil.process_iter(['ppid', 'name', 'create_time']):
                try:
                    if proc.info.get('ppid') != self.pid:
                        continue
                    child_ct = proc.info.get('create_time')
                    if self.pid_create_time is not None and child_ct < self.pid_create_time - 1.0:
                        continue
                    self.pid = proc.pid
                    self.pid_create_time = child_ct
                    self.handoffs_used += 1
                    print(f"[Monitor] Handoff a processo figlio (PID {self.pid})")
                    return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass
        return False

    def run(self):
        is_flatpak = platform_info.is_flatpak()
        
        # Se non abbiamo il PID (lancio via Steam), dobbiamo cercarlo
        if self.pid is None and self.exe_path:
            target_exe = os.path.normpath(self.exe_path).lower()
            target_filename = os.path.basename(target_exe)
            
            # Regex trick per evitare che pgrep matchi il proprio wrapper DBus sull'host
            if len(target_filename) > 0:
                regex_target = f"[{target_filename[0]}]{target_filename[1:]}"
            else:
                regex_target = target_filename
            
            print(f"[Monitor] In attesa che il processo appaia (Fuzzy Match): {target_filename}")
            timeout = 60
            start_wait = time.time()
            found_host = False
            
            while self._is_running and time.time() - start_wait < timeout:
                if is_flatpak:
                    import subprocess
                    try:
                        res = subprocess.run(["flatpak-spawn", "--host", "pgrep", "-i", "-f", regex_target], capture_output=True, text=True)
                        if res.returncode == 0 and res.stdout.strip():
                            found_host = True
                            break
                    except Exception:
                        pass
                else:
                    parent = None
                    for proc in psutil.process_iter(['exe', 'cmdline']):
                        try:
                            p_exe = proc.info.get('exe')
                            if p_exe and os.path.normpath(p_exe).lower() == target_exe:
                                parent = proc
                            p_cmd = proc.info.get('cmdline')
                            if not parent and p_cmd:
                                cmd_string = " ".join(p_cmd).lower()
                                if target_filename in cmd_string:
                                    parent = proc
                            if parent:
                                self.pid = parent.pid
                                try:
                                    self.pid_create_time = parent.create_time()
                                except Exception:
                                    self.pid_create_time = None
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                            continue
                    if parent: break
                time.sleep(1)
            
            if not found_host and not self.pid:
                print("[Monitor] Timeout: Impossibile agganciare il processo dopo il lancio via Steam.")
                return
                
            if found_host:
                print(f"[Monitor] Monitoraggio attivo via host-pgrep per {target_filename} ({self.meta.get('title')})")
                while self._is_running:
                    try:
                        import subprocess
                        res = subprocess.run(["flatpak-spawn", "--host", "pgrep", "-i", "-f", regex_target], capture_output=True, text=True)
                        if res.returncode != 0 or not res.stdout.strip():
                            break
                    except Exception as e:
                        print(f"[Monitor] Errore critico nel monitoraggio host: {e}")
                        break
                    time.sleep(2)
                print(f"[Monitor] Gioco '{self.meta.get('title')}' chiuso (Host). Segnalazione backup...")
                self._emit_exit()
                return

        # MONITORAGGIO ROBUSTO (Windows/native): firma del gioco + anti PID-reuse
        if not self._tracked_process_alive():
            if not self._find_matching_process() and not self._try_process_handoff():
                print(f"[Monitor] Processo '{self.meta.get('title')}' già terminato all'avvio del monitoraggio.")
                self._emit_exit()
                return

        print(f"[Monitor] Monitoraggio attivo per {self.meta.get('title')} (PID {self.pid})")

        clean_scans = 0
        while self._is_running:
            if self._tracked_process_alive():
                clean_scans = 0
            elif self._find_matching_process() or self._try_process_handoff():
                clean_scans = 0
            else:
                clean_scans += 1
                if clean_scans >= 3:
                    print(f"[Monitor] Gioco '{self.meta.get('title')}' chiuso. Segnalazione backup...")
                    self._emit_exit()
                    return
            time.sleep(2)

        if self._is_running:
            self._emit_exit()
        self._is_running = False

    def stop(self):
        self._is_running = False