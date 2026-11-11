# steam_integration.py
import os
import sys
import subprocess
import vdf
import psutil
import binascii
import requests
import re
import shutil
import time
from PIL import Image
import io
from urllib.parse import quote

from core import platform_info, secrets

STEAM_CHILD_PROCS = ["steamwebhelper", "wineserver", "steam-runtime-supervisor", "reaper", "wine64", "wine64-preloader", "wine", "wine-preloader"]

def _cleanup_steam_locks():
    """Remove stale Steam lock files that can block restart."""
    lock_paths = [
        os.path.expanduser("~/.steam/steam.pid"),
        os.path.expanduser("~/.local/share/Steam/ubuntu12_64/steam-runtime-sniper.lock"),
        os.path.expanduser("~/.steam/steam/steam-runtime-sniper.lock"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/ubuntu12_64/steam-runtime-sniper.lock"),
    ]
    for path in lock_paths:
        if os.path.exists(path):
            try:
                if path.endswith("steam.pid"):
                    with open(path) as f:
                        pid = int(f.read().strip())
                    os.kill(pid, 0)
                    continue
                os.remove(path)
            except Exception:
                try: os.remove(path)
                except: pass

_WIN_STEAM_PROCESSES = (
    "steam.exe",
    "steamwebhelper.exe",
    "gameoverlayui.exe",
)
_WIN_STEAM_CHILDREN = ("steamwebhelper.exe", "gameoverlayui.exe")


def _process_name(proc):
    try:
        return str(proc.info.get("name") or proc.name() or "").lower()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return ""


def _steam_executable():
    """Path to the Steam launcher, resolving steam.exe on Windows."""
    if sys.platform != "win32":
        return shutil.which("steam") or "steam"
    steam_dir = _get_steam_install_dir()
    if steam_dir:
        exe = os.path.join(steam_dir, "steam.exe")
        if os.path.isfile(exe):
            return exe
    return shutil.which("steam.exe") or shutil.which("steam") or "steam.exe"


def _terminate_windows_steam(names=_WIN_STEAM_PROCESSES, timeout=2.0):
    """Terminate the given Windows Steam processes, killing stragglers."""
    procs = [
        proc for proc in psutil.process_iter(["name"])
        if _process_name(proc) in names
    ]
    for proc in procs:
        try:
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if not procs:
        return
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    for proc in alive:
        try:
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def _kill_steam_children():
    """Kill orphaned Steam child processes that can block restart."""
    is_flatpak = platform_info.is_flatpak()

    if sys.platform == "win32" and not is_flatpak:
        _terminate_windows_steam(_WIN_STEAM_CHILDREN)
        return

    def _pkill(signal, proc):
        try:
            if is_flatpak:
                subprocess.run(["flatpak-spawn", "--host", "pkill", f"-{signal}", "-x", proc],
                               capture_output=True, timeout=5)
            else:
                subprocess.run(["pkill", f"-{signal}", "-x", proc],
                               capture_output=True, timeout=5)
        except Exception:
            pass

    # Phase 1: graceful SIGTERM for Wine/Proton processes
    for proc in ("wineserver", "wine", "wine64", "wine-preloader", "wine64-preloader"):
        _pkill(15, proc)
    time.sleep(1.5)

    # Phase 2: SIGKILL for everything still standing
    for proc in STEAM_CHILD_PROCS:
        _pkill(9, proc)

def _restart_steam():
    """Riavvia Steam in background e attende che il processo sia attivo.
    Returns True se Steam è partito entro 5s, False altrimenti."""
    is_flatpak = platform_info.is_flatpak()
    popen_kwargs = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    try:
        if is_flatpak:
            subprocess.Popen(["flatpak-spawn", "--host", "steam", "-silent"],
                             **popen_kwargs)
        else:
            subprocess.Popen([_steam_executable(), "-silent"], **popen_kwargs)
    except Exception as e:
        print(f"[Steam] Errore durante il riavvio di Steam: {e}")
        return False

    for i in range(10):
        time.sleep(0.5)
        if is_flatpak:
            res = subprocess.run(["flatpak-spawn", "--host", "pgrep", "-x", "steam"], capture_output=True)
            if res.returncode == 0:
                print(f"[Steam] Steam avviato dopo {i * 0.5 + 0.5:.1f}s.")
                return True
        else:
            for p in psutil.process_iter(['name']):
                if _process_name(p) in ('steam', 'steam.exe', 'steam.sh'):
                    print(f"[Steam] Steam avviato dopo {i * 0.5 + 0.5:.1f}s.")
                    return True
    print("[Steam] Steam non rilevato entro 5s dal riavvio.")
    return False


def is_steam_running():
    """True se Steam (o il processo flatpak) è attualmente in esecuzione."""
    is_flatpak = platform_info.is_flatpak()
    if is_flatpak:
        try:
            res = subprocess.run(["flatpak-spawn", "--host", "pgrep", "-x", "steam"], capture_output=True)
            if res.returncode == 0:
                return True
            res2 = subprocess.run(["flatpak-spawn", "--host", "flatpak", "ps"], capture_output=True, text=True)
            if "com.valvesoftware.Steam" in res2.stdout:
                return True
            return False
        except Exception:
            return False
    else:
        for p in psutil.process_iter(['name']):
            if _process_name(p) in ('steam', 'steam.exe', 'steam.sh'):
                return True
        return False


def get_steam_localconfig_path(account_id):
    """Percorso del localconfig.vdf per un determinato account Steam locale."""
    if account_id:
        candidates = [
            os.path.expanduser(f"~/.local/share/Steam/userdata/{account_id}/config/localconfig.vdf"),
            os.path.expanduser(f"~/.var/app/com.valvesoftware.Steam/data/Steam/userdata/{account_id}/config/localconfig.vdf"),
        ]
        if sys.platform == 'win32':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                    steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
                    candidates.insert(0, os.path.join(steam_path, "userdata", str(account_id), "config", "localconfig.vdf"))
            except Exception:
                pass
            candidates.insert(0, rf"C:\Program Files (x86)\Steam\userdata\{account_id}\config\localconfig.vdf")
        for p in candidates:
            if os.path.exists(p):
                return p
        return candidates[0]
    for acc in get_steam_accounts():
        p = os.path.join(acc["path"], "config", "localconfig.vdf")
        if os.path.exists(p):
            return p
    return ""


def restart_steam():
    """Riavvia Steam in background (wrapper pubblico di _restart_steam)."""
    return _restart_steam()

def kill_steam_safely():
    """Chiude Steam e i suoi processi figli in modo sicuro."""
    is_flatpak = platform_info.is_flatpak()
    
    if not is_steam_running():
        print("[Steam] Steam è già chiuso. Pulisco processi residui e lock.")
        _kill_steam_children()
        _cleanup_steam_locks()
        return

    try:
        if is_flatpak:
            subprocess.run(["flatpak-spawn", "--host", "steam", "-shutdown"], capture_output=True)
            subprocess.run(["flatpak-spawn", "--host", "flatpak", "run", "com.valvesoftware.Steam", "-shutdown"], capture_output=True)
        elif sys.platform == "win32":
            subprocess.run([_steam_executable(), "-shutdown"], capture_output=True, timeout=10)
        else:
            subprocess.run(["steam", "-shutdown"], capture_output=True)
            subprocess.run(["flatpak", "run", "com.valvesoftware.Steam", "-shutdown"], capture_output=True)
    except Exception as e:
        print(f"[Steam] Attenzione - Eseguibile non trovato durante lo spegnimento: {e}")
        
    for i in range(20):
        if not is_steam_running():
            print(f"[Steam] Processo morto dopo {i * 0.5:.1f}s.")
            _kill_steam_children()
            _cleanup_steam_locks()
            time.sleep(1.0)
            return
        time.sleep(0.5)
    
    try:
        if is_flatpak:
            subprocess.run(["flatpak-spawn", "--host", "pkill", "-9", "-x", "steam"], capture_output=True)
            subprocess.run(["flatpak-spawn", "--host", "flatpak", "kill", "com.valvesoftware.Steam"], capture_output=True)
        elif sys.platform == "win32":
            _terminate_windows_steam()
        else:
            subprocess.run(["killall", "-9", "steam"], capture_output=True)
            subprocess.run(["pkill", "-9", "-x", "steam"], capture_output=True)
            subprocess.run(["flatpak", "kill", "com.valvesoftware.Steam"], capture_output=True)
    except Exception as e:
        print(f"[Steam] Attenzione - Eseguibile non trovato durante il killall: {e}")
    
    _kill_steam_children()
    _cleanup_steam_locks()
    time.sleep(2.0) 

def _get_steam_install_dir():
    """Restituisce la cartella di installazione di Steam, o None se non trovata."""
    if sys.platform != 'win32':
        return None

    candidates = []
    try:
        import winreg
        # HKCU e' il caso normale; HKLM copre installazioni di altri utenti / versioni MSI.
        locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        )
        for hive, subkey, value_name in locations:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
                    if value:
                        candidates.append(value)
            except OSError:
                continue
    except Exception:
        pass

    # Fallback su percorsi standard se il registro non è leggibile (es. avvio elevato).
    candidates.extend((r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"))

    seen = set()
    for candidate in candidates:
        path = os.path.normpath(str(candidate).replace("/", "\\"))
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.isfile(os.path.join(path, "steam.exe")):
            return path
    return None

def _steam_account_id_from_steamid64(steamid64):
    """Converte uno SteamID64 (es. 76561197988281094) nell'ID cartella userdata."""
    try:
        return str(int(steamid64) - 76561197960265728)
    except (TypeError, ValueError):
        return None

def _get_linux_steam_accounts():
    """Scansione account su Linux/Flatpak (comportamento storico, invariato)."""
    accounts = []
    base_paths = [
        os.path.expanduser("~/.local/share/Steam/userdata"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/userdata")
    ]
    for bp in base_paths:
        if os.path.exists(bp):
            for d in os.listdir(bp):
                if d.isdigit() and d != "0":
                    user_dir = os.path.join(bp, d)
                    persona_name = f"Account ID: {d}" # Fallback
                    avatar_hash = None
                    
                    localconfig = os.path.join(user_dir, "config", "localconfig.vdf")
                    if os.path.exists(localconfig):
                        try:
                            with open(localconfig, 'r', encoding='utf-8') as f:
                                content = f.read()
                                # Ricerca velocissima con Regex per evitare il parsing pesante del VDF
                                match_name = re.search(r'"PersonaName"\s+"([^"]+)"', content, re.IGNORECASE)
                                if match_name:
                                    persona_name = match_name.group(1)
                                
                                match_avatar = re.search(r'"avatar"\s+"([^"]+)"', content, re.IGNORECASE)
                                if match_avatar:
                                    avatar_hash = match_avatar.group(1)
                        except:
                            pass
                            
                    accounts.append({
                        "id": d, 
                        "name": persona_name, 
                        "path": user_dir,
                        "avatar_hash": avatar_hash
                    })
    return accounts

def _get_windows_steam_accounts():
    """
    Scansione account su Windows.
    Usa la cartella Steam trovata nel registro (HKCU/HKLM) e arricchisce i dati con
    config/loginusers.vdf, che elenca gli account che hanno effettuato l'accesso.
    Deduplica i percorsi per evitare card doppie (filesystem case-insensitive).
    """
    accounts = []
    steam_dir = _get_steam_install_dir()
    if not steam_dir:
        return accounts

    # PersonaName autorevoli: SteamID64 -> nome account
    persona_overrides = {}
    loginusers_path = os.path.join(steam_dir, "config", "loginusers.vdf")
    if os.path.exists(loginusers_path):
        try:
            with open(loginusers_path, 'r', encoding='utf-8', errors='replace') as f:
                users = (vdf.load(f) or {}).get('users', {}) or {}
            for steamid64, info in users.items():
                acc_id = _steam_account_id_from_steamid64(steamid64)
                if not acc_id or not isinstance(info, dict):
                    continue
                name = info.get('PersonaName') or info.get('AccountName')
                if name:
                    persona_overrides[acc_id] = name
        except Exception:
            pass

    userdata_path = os.path.join(steam_dir, "userdata")
    if not os.path.isdir(userdata_path):
        return accounts

    try:
        entries = sorted(os.listdir(userdata_path))
    except OSError:
        return accounts

    seen = set()
    for d in entries:
        if not d.isdigit() or d == "0":
            continue
        user_dir = os.path.join(userdata_path, d)
        if not os.path.isdir(user_dir):
            continue
        key = os.path.normcase(os.path.normpath(user_dir))
        if key in seen:
            continue
        seen.add(key)

        persona_name = persona_overrides.get(d) or f"Account ID: {d}" # Fallback
        avatar_hash = None

        localconfig = os.path.join(user_dir, "config", "localconfig.vdf")
        if os.path.exists(localconfig):
            try:
                with open(localconfig, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                match_name = re.search(r'"PersonaName"\s+"([^"]+)"', content, re.IGNORECASE)
                if match_name:
                    persona_name = match_name.group(1)

                match_avatar = re.search(r'"avatar"\s+"([^"]+)"', content, re.IGNORECASE)
                if match_avatar:
                    avatar_hash = match_avatar.group(1)
            except Exception:
                pass

        accounts.append({
            "id": d,
            "name": persona_name,
            "path": user_dir,
            "avatar_hash": avatar_hash
        })
    return accounts

def get_steam_accounts():
    """Recupera tutti gli account locali e tenta di estrarne il Nickname (PersonaName) e l'avatar."""
    if sys.platform == 'win32':
        accounts = _get_windows_steam_accounts()
    else:
        accounts = _get_linux_steam_accounts()

    sync_steam_avatars(accounts)
    return accounts

def _get_avatars_dir():
    """
    Cartella scrivibile per gli avatar.
    Usa sempre il data root configurato (PathManager): su Windows segue
    l'installazione, su Linux/Flatpak evita la .media in sola lettura di /app.
    """
    from core.paths import PathManager
    return str(PathManager.get_data_root() / "avatars")

def sync_steam_avatars(accounts):
    """
    Sincronizza gli avatar di Steam nella cartella avatar.
    Cerca prima le immagini in locale nella cartella 'avatarcache' di Steam.
    Scarica quelli mancanti ed elimina quelli di account non più presenti.
    Popola anche una chiave 'local_avatar_path' nell'account.
    """
    media_dir = _get_avatars_dir()
    try:
        os.makedirs(media_dir, exist_ok=True)
    except Exception:
        pass
        
    current_account_ids = {str(acc['id']) for acc in accounts}
    
    # Rimuovi avatar di account non più presenti
    try:
        media_files = os.listdir(media_dir)
    except OSError:
        media_files = []
    for f in media_files:
        if f.startswith("avatar_") and (f.endswith(".jpg") or f.endswith(".png")):
            acc_id = f.replace("avatar_", "").replace(".jpg", "").replace(".png", "")
            if acc_id not in current_account_ids:
                try:
                    os.remove(os.path.join(media_dir, f))
                except Exception:
                    pass
                    
    # Determina i percorsi locali per l'avatarcache
    avatarcache_paths = []
    if sys.platform == 'win32':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
                avatarcache_paths.append(os.path.join(steam_path, "config", "avatarcache"))
        except Exception:
            pass
        avatarcache_paths.append(r"C:\Program Files (x86)\Steam\config\avatarcache")
    else:
        avatarcache_paths.extend([
            os.path.expanduser("~/.steam/steam/config/avatarcache"),
            os.path.expanduser("~/.local/share/Steam/config/avatarcache"),
            os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/config/avatarcache")
        ])
    # Deduplica (su Windows i percorsi possono differire solo per case)
    unique_cache_paths = []
    seen_cache = set()
    for p in avatarcache_paths:
        if not os.path.exists(p):
            continue
        key = os.path.normcase(os.path.normpath(p))
        if key in seen_cache:
            continue
        seen_cache.add(key)
        unique_cache_paths.append(p)
    avatarcache_paths = unique_cache_paths

    # Sincronizza i nuovi
    for acc in accounts:
        if not acc.get('avatar_hash'):
            acc['local_avatar_path'] = None
            continue
            
        avatar_hash = acc['avatar_hash']
        local_path = os.path.join(media_dir, f"avatar_{acc['id']}.jpg")
        local_path_png = os.path.join(media_dir, f"avatar_{acc['id']}.png")
        
        # Scegli il path esistente
        if os.path.exists(local_path_png):
            acc['local_avatar_path'] = local_path_png
        elif os.path.exists(local_path):
            acc['local_avatar_path'] = local_path
        else:
            acc['local_avatar_path'] = local_path
        
        if not os.path.exists(local_path) and not os.path.exists(local_path_png):
            steamid64 = int(acc['id']) + 76561197960265728
            copied_local = False
            
            for cache_dir in avatarcache_paths:
                png_path = os.path.join(cache_dir, f"{steamid64}.png")
                jpg_path = os.path.join(cache_dir, f"{steamid64}.jpg")
                
                if os.path.exists(png_path):
                    try:
                        shutil.copy2(png_path, local_path_png)
                        acc['local_avatar_path'] = local_path_png
                        copied_local = True
                        break
                    except Exception:
                        pass
                elif os.path.exists(jpg_path):
                    try:
                        shutil.copy2(jpg_path, local_path)
                        acc['local_avatar_path'] = local_path
                        copied_local = True
                        break
                    except Exception:
                        pass
                        
            # Se non l'abbiamo trovato in locale, scarichiamo (fallback)
            if not copied_local:
                url = f"https://avatars.steamstatic.com/{avatar_hash}_full.jpg"
                try:
                    r = requests.get(url, stream=True, timeout=10)
                    if r.status_code == 200:
                        with open(local_path, 'wb') as f:
                            for chunk in r.iter_content(1024):
                                f.write(chunk)
                        acc['local_avatar_path'] = local_path
                    else:
                        acc['local_avatar_path'] = None
                except Exception:
                    acc['local_avatar_path'] = None

def get_steam_ids(exe_path, app_name):
    exe_str = f'"{exe_path}"'
    unique_string = exe_str + app_name
    crc32_val = binascii.crc32(unique_string.encode('utf-8')) & 0xFFFFFFFF
    grid_id_uint = crc32_val | 0x80000000
    
    appid_signed = grid_id_uint
    if appid_signed > 2147483647:
        appid_signed -= 4294967296
        
    shortcut_id_uint = (grid_id_uint << 32) | 0x02000000
    return appid_signed, str(grid_id_uint), str(shortcut_id_uint)

def download_file(url, dest_path_no_ext, keep_alpha=False):
    if not url: return None
    try:
        r = requests.get(url, stream=False, timeout=10)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        
        if keep_alpha:
            img = img.convert("RGBA")
            final_path = dest_path_no_ext + '.png'
            img.save(final_path, 'PNG')
            return final_path
            
        if img.mode in ('RGBA', 'LA', 'P'):
            background = Image.new('RGB', img.size, (0, 0, 0))
            if img.mode == 'P': img = img.convert('RGBA')
            background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        final_path = dest_path_no_ext + '.jpg'
        img.save(final_path, 'JPEG', quality=95)
        return final_path
        
    except Exception as e:
        print(f"[SteamGridDB] Errore: {e}")
        return None

def get_steam_account_paths():
    """
    Elenco delle cartelle userdata/<id> degli account locali.
    Non legge localconfig.vdf né sincronizza gli avatar: è la variante veloce
    usata dai controlli frequenti (es. presenza di uno shortcut).
    """
    paths = []
    seen = set()

    base_paths = []
    if sys.platform == 'win32':
        steam_dir = _get_steam_install_dir()
        if steam_dir:
            base_paths.append(os.path.join(steam_dir, "userdata"))
    else:
        base_paths.extend([
            os.path.expanduser("~/.local/share/Steam/userdata"),
            os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/userdata"),
        ])

    for bp in base_paths:
        if not os.path.isdir(bp):
            continue
        try:
            entries = os.listdir(bp)
        except OSError:
            continue
        for d in entries:
            if not d.isdigit() or d == "0":
                continue
            user_dir = os.path.join(bp, d)
            key = os.path.normcase(os.path.normpath(user_dir))
            if key in seen:
                continue
            seen.add(key)
            if os.path.isdir(user_dir):
                paths.append(user_dir)
    return paths

def is_game_in_steam(game_title):
    """Controlla in modo infallibile se il titolo è presente nei collegamenti."""
    for user_dir in get_steam_account_paths():
        shortcuts_file = os.path.join(user_dir, "config", "shortcuts.vdf")
        if os.path.exists(shortcuts_file):
            try:
                with open(shortcuts_file, 'rb') as f:
                    shortcuts = vdf.binary_loads(f.read())
                shortcut_dict = shortcuts.get('shortcuts', {})
                # Cerchiamo il match perfetto sul nome visualizzato
                for k, v in shortcut_dict.items():
                    if v.get('AppName', '') == game_title:
                        return True
            except Exception:
                pass
    return False

def remove_from_steam(game_title, restart_steam=True):
    """
    Rimuove un gioco dai collegamenti Steam di tutti gli account locali.
    Pulisce anche le immagini grid e le entry di compatibilità Proton.

    Args:
        game_title: Il nome visualizzato del gioco in AppName
        restart_steam: Se True, kill di Steam prima della modifica e riavvio dopo

    Returns:
        bool: True se il gioco è stato rimosso da almeno un account
    """
    print(f"[Steam] Rimozione '{game_title}' dalle librerie Steam...")

    if restart_steam:
        kill_steam_safely()
    else:
        print("[Steam] Kill saltato (l'utente riavvierà Steam manualmente).")

    account_paths = get_steam_account_paths()
    found = False

    for user_dir in account_paths:
        config_dir = os.path.join(user_dir, "config")
        shortcuts_file = os.path.join(config_dir, "shortcuts.vdf")
        grid_folder = os.path.join(config_dir, "grid")
        config_path = os.path.join(config_dir, "config.vdf")

        if not os.path.exists(shortcuts_file):
            continue

        try:
            with open(shortcuts_file, 'rb') as f:
                shortcuts = vdf.binary_loads(f.read())

            shortcut_dict = shortcuts.get('shortcuts', {})

            keys_to_del = [k for k, v in shortcut_dict.items() if v.get('AppName') == game_title]

            if not keys_to_del:
                continue

            found = True

            # Estrai gli ID prima della cancellazione per pulizia immagini e Proton
            clean_ids = []
            proton_ids = []
            for k in keys_to_del:
                old_exe = shortcut_dict[k].get('Exe', '').strip('"')
                if old_exe:
                    _, grid_id_str, shortcut_id_str = get_steam_ids(old_exe, game_title)
                    clean_ids.append(grid_id_str)
                    proton_ids.append((grid_id_str, shortcut_id_str))
                del shortcut_dict[k]

            # Backup e salvataggio VDF modificato
            shutil.copy(shortcuts_file, shortcuts_file + ".bak")
            shortcuts['shortcuts'] = shortcut_dict
            with open(shortcuts_file, 'wb') as f:
                f.write(vdf.binary_dumps(shortcuts))

            # Pulizia immagini grid
            if os.path.exists(grid_folder):
                for clean_id in set(clean_ids):
                    for ext_del in ['.jpg', '.png']:
                        for suffix_del in ['p', '', '_hero', '_logo']:
                            old_file = os.path.join(grid_folder, f"{clean_id}{suffix_del}{ext_del}")
                            if os.path.exists(old_file):
                                try:
                                    os.remove(old_file)
                                except Exception:
                                    pass

            # Pulizia entry di compatibilità Proton
            if proton_ids and os.path.exists(config_path):
                try:
                    with open(config_path, 'r', encoding='utf-8') as f:
                        data = vdf.load(f)

                    store = data.get('InstallConfigStore', {})
                    software = store.get('Software', {})
                    valve = software.get('Valve', {})
                    steam = valve.get('Steam', {})
                    mapping = steam.get('CompatToolMapping', {})

                    modified = False
                    for gid, sid in set(proton_ids):
                        if gid in mapping:
                            del mapping[gid]
                            modified = True
                        if sid in mapping:
                            del mapping[sid]
                            modified = True

                    if modified:
                        with open(config_path, 'w', encoding='utf-8') as f:
                            vdf.dump(data, f, pretty=True)
                except Exception:
                    pass

        except Exception as e:
            print(f"[Steam] Errore durante la rimozione per account {os.path.basename(os.path.normpath(user_dir))}: {e}")
            continue

    if found:
        print("[Steam] VDF modificato. Attesa scrittura su disco...")
        time.sleep(1.0)

        if restart_steam:
            print("[Steam] Riavvio di Steam in background...")
            _restart_steam()
        else:
            print("[Steam] Riavvio automatico saltato. L'utente riavvierà Steam manualmente.")
    else:
        print(f"[Steam] '{game_title}' non trovato in nessun account Steam.")

    return found

def _find_proton():
    """Trova il Proton più adatto tra custom e built-in.
    Priorità:
      1. GE-Proton / Proton-GE (custom, compatibilitytools.d)
      2. proton-cachyos (custom, compatibilitytools.d)
      3. Proton - Experimental (built-in, steamapps/common)
      4. Proton 10 (built-in)
      5. Proton 9 (built-in)
    Returns il nome del tool di compatibilità.
    """
    steam_roots = [
        os.path.expanduser("~/.local/share/Steam"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam"),
    ]

    def get_version(name):
        match = re.search(r'\d+-\d+', name)
        if match:
            parts = match.group(0).split('-')
            return int(parts[0]) * 1000 + int(parts[1])
        return 0

    # TIER 1: GE-Proton / Proton-GE
    for root in steam_roots:
        compat_dir = os.path.join(root, "compatibilitytools.d")
        if os.path.isdir(compat_dir):
            candidates = [d for d in os.listdir(compat_dir) if "GE-Proton" in d or "Proton-GE" in d]
            if candidates:
                candidates.sort(key=get_version, reverse=True)
                return candidates[0]

    # TIER 2: proton-cachyos
    for root in steam_roots:
        compat_dir = os.path.join(root, "compatibilitytools.d")
        if os.path.isdir(compat_dir):
            candidates = [d for d in os.listdir(compat_dir) if "cachyos" in d.lower()]
            if candidates:
                candidates.sort(key=get_version, reverse=True)
                return candidates[0]

    # TIER 3-5: built-in Proton (steamapps/common)
    builtin_tiers = [
        ("proton_experimental", "Proton - Experimental"),
        ("proton_10", "Proton 10"),
        ("proton_9", "Proton 9"),
    ]
    for internal_name, dir_name in builtin_tiers:
        for root in steam_roots:
            if os.path.isdir(os.path.join(root, "steamapps", "common", dir_name)):
                return internal_name

    return "proton_experimental"

def set_proton_compatibility(grid_id_str, shortcut_id_str):
    if sys.platform == 'win32':
        return True, "Native (Windows)"

    proton_name = _find_proton()

    config_paths = [
        os.path.expanduser("~/.local/share/Steam/config/config.vdf"),
        os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam/config/config.vdf")
    ]

    success = False
    for config_path in config_paths:
        if os.path.exists(config_path):
            try:
                shutil.copy(config_path, config_path + ".bak")
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = vdf.load(f)

                store = data.setdefault('InstallConfigStore', {})
                software = store.setdefault('Software', {})
                valve = software.setdefault('Valve', {})
                steam = valve.setdefault('Steam', {})
                mapping = steam.setdefault('CompatToolMapping', {})

                proton_data = {"name": proton_name, "config": "", "priority": "250"}
                mapping[grid_id_str] = proton_data
                mapping[shortcut_id_str] = proton_data

                with open(config_path, 'w', encoding='utf-8') as f:
                    vdf.dump(data, f, pretty=True)
                success = True

            except Exception: pass

    return success, proton_name

# --- NOTA: ABBIAMO AGGIUNTO IL PARAMETRO selected_user_dir ---
def add_game_to_steam(game_title, exe_path, install_dir, selected_user_dir, restart_steam=True):
    print(f"[Steam] Inizio integrazione per '{game_title}'...")
    if restart_steam:
        kill_steam_safely()
    else:
        print("[Steam] Kill saltato (l'utente riavvierà Steam manualmente).")
    
    assets_dir = os.path.join(install_dir, "Steam_Assets")
    os.makedirs(assets_dir, exist_ok=True)
    
    clean_title = re.sub(r'(?i)(\s*free download.*|\s*v\d+\..*|\s*build\s\d+.*)', '', game_title).strip()
    search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{quote(clean_title)}"
    urls = {"cover": "", "wide": "", "hero": "", "logo": "", "icon": ""}
    sgdb_headers = secrets.sgdb_headers()

    try:
        if not sgdb_headers:
            raise RuntimeError("SteamGridDB key not configured")
        r_search = requests.get(search_url, headers=sgdb_headers, timeout=5)
        if r_search.status_code == 200 and r_search.json().get("data"):
            game_id = r_search.json()["data"][0]["id"]

            def get_asset_url(type_str, dim=""):
                url = f"https://www.steamgriddb.com/api/v2/{type_str}/game/{game_id}" + (f"?dimensions={dim}" if dim else "")
                req = requests.get(url, headers=sgdb_headers, timeout=3)
                if req.status_code == 200 and req.json().get("data"):
                    return req.json()["data"][0]["url"]
                return ""

            urls["cover"] = get_asset_url("grids", "600x900")
            urls["wide"]  = get_asset_url("grids", "460x215,920x430")
            urls["hero"]  = get_asset_url("heroes")
            urls["logo"]  = get_asset_url("logos")
            urls["icon"]  = get_asset_url("icons")
    except Exception: pass

    appid_signed, grid_id_str, shortcut_id_str = get_steam_ids(exe_path, game_title)
    
    # --- NON C'È PIÙ IL CICLO FOR, USIAMO SOLO L'ACCOUNT SELEZIONATO! ---
    config_dir = os.path.join(selected_user_dir, "config")
    shortcuts_file = os.path.join(config_dir, "shortcuts.vdf")
    grid_folder = os.path.join(config_dir, "grid")
    os.makedirs(grid_folder, exist_ok=True)

    # Pulizia vecchi file per non creare conflitti
    for ext_del in ['.jpg', '.png']:
        for suffix_del in ['p', '', '_hero', '_logo']:
            old_file = os.path.join(grid_folder, f"{grid_id_str}{suffix_del}{ext_del}")
            if os.path.exists(old_file):
                try: os.remove(old_file)
                except: pass

    shortcuts = {'shortcuts': {}}
    if os.path.exists(shortcuts_file):
        shutil.copy(shortcuts_file, shortcuts_file + ".bak") 
        with open(shortcuts_file, 'rb') as f:
            shortcuts = vdf.binary_loads(f.read())
            
    shortcut_dict = shortcuts.get('shortcuts', {})
    
    # Caccia ai vecchi ID per spazzare la cache
    keys_to_del = [k for k, v in shortcut_dict.items() if v.get('AppName') == game_title]
    ids_to_clean = [grid_id_str]
    for k in keys_to_del:
        old_exe = shortcut_dict[k].get('Exe', '').strip('"')
        if old_exe:
            _, old_grid_id_str, _ = get_steam_ids(old_exe, game_title)
            ids_to_clean.append(old_grid_id_str)
        del shortcut_dict[k] 
        
    for clean_id in set(ids_to_clean): 
        for ext_del in ['.jpg', '.png']:
            for suffix_del in ['p', '', '_hero', '_logo']:
                old_file = os.path.join(grid_folder, f"{clean_id}{suffix_del}{ext_del}")
                if os.path.exists(old_file):
                    try: os.remove(old_file)
                    except: pass
        
    next_idx = str(max([int(k) for k in shortcut_dict.keys()] + [-1]) + 1)
    
    shortcut_dict[next_idx] = {
        'appid': appid_signed, 
        'AppName': game_title,
        'Exe': f'"{exe_path}"',
        'StartDir': f'"{os.path.dirname(exe_path)}"',
        'icon': '',
        'ShortcutPath': '',
        'LaunchOptions': '',
        'IsHidden': 0,
        'AllowDesktopConfig': 1,
        'AllowOverlay': 1,
        'OpenVR': 0,
        'Devkit': 0,
        'DevkitGameID': '',
        'DevkitOverrideAppID': 0,
        'LastPlayTime': 0,
        'tags': {'0': 'SteamRIP'}
    }
    shortcuts['shortcuts'] = shortcut_dict
    
    with open(shortcuts_file, 'wb') as f:
        f.write(vdf.binary_dumps(shortcuts))
        
    if urls["cover"]:
        local = download_file(urls["cover"], os.path.join(assets_dir, "Cover"), keep_alpha=False)
        if local: shutil.copy(local, os.path.join(grid_folder, f"{grid_id_str}p.jpg"))

    if urls["wide"]:
        local = download_file(urls["wide"], os.path.join(assets_dir, "Wide_Cover"), keep_alpha=False)
        if local: shutil.copy(local, os.path.join(grid_folder, f"{grid_id_str}.jpg"))

    if urls["hero"]:
        local = download_file(urls["hero"], os.path.join(assets_dir, "Background_Hero"), keep_alpha=False)
        if local: shutil.copy(local, os.path.join(grid_folder, f"{grid_id_str}_hero.jpg"))

    if urls["logo"]:
        local = download_file(urls["logo"], os.path.join(assets_dir, "Logo"), keep_alpha=True)
        if local: shutil.copy(local, os.path.join(grid_folder, f"{grid_id_str}_logo.png"))

    if urls["icon"]:
        local = download_file(urls["icon"], os.path.join(assets_dir, "Icon"), keep_alpha=True)
        if local:
            shortcut_dict[next_idx]['icon'] = local
            with open(shortcuts_file, 'wb') as f:
                f.write(vdf.binary_dumps(shortcuts))

    proton_success, proton_used = set_proton_compatibility(grid_id_str, shortcut_id_str)

    print("[Steam] VDF write concluded. Waiting for disk flush.")
    time.sleep(1.0)  

    if restart_steam:
        print("[Steam] Riavvio di Steam in background per caricare il nuovo VDF...")
        _restart_steam()
    else:
        print("[Steam] Riavvio automatico saltato. L'utente riavvierà Steam manualmente.")
    
    # Restituiamo ENTRAMBI gli ID (64-bit per lanciare, 32-bit per la pagina libreria)
    # + l'ID dell'account Steam su cui è stato creato lo shortcut (per la sync del playtime)
    selected_account_id = os.path.basename(os.path.normpath(selected_user_dir))
    return shortcut_id_str, grid_id_str, proton_success, proton_used, selected_account_id