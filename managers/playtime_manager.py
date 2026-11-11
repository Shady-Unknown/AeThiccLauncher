import os
import json
import math
import shutil
import re
import time
import vdf
from datetime import datetime
from pathlib import Path

from core import steam_integration
from core.meta_utils import save_steamrip_meta
from core.paths import PathManager


_VDF_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[^\s]+')


def _ci_find(d, target):
    """Ricerca case-insensitive di una chiave in un dict vdf."""
    if not isinstance(d, dict):
        return None
    for k, v in d.items():
        if str(k).lower() == target.lower():
            return v
    return None


def _parse_vdf_tolerant(text):
    """Parser VDF tollerante: gestisce sia il formato multilinea che quello
    compatto di Steam (es. '"200010" { "Playtime" "35" }')."""
    tokens = _VDF_TOKEN.findall(text)
    root = {}
    stack = [root]
    i, n = 0, len(tokens)

    def _unescape(s):
        return s.replace('\\"', '"').replace('\\\\', '\\')

    while i < n:
        tok = tokens[i]
        if tok in ('{', '}'):
            if tok == '}' and len(stack) > 1:
                stack.pop()
            i += 1
            continue

        key = _unescape(tok.strip('"'))
        j = i + 1
        if j >= n:
            break
        nxt = tokens[j]
        if nxt == '{':
            new_dict = {}
            stack[-1][key] = new_dict
            stack.append(new_dict)
            i = j + 1
        elif nxt == '}':
            stack[-1][key] = ""
            i = j
        else:
            stack[-1][key] = _unescape(nxt.strip('"'))
            i = j + 1
    return root


def _load_vdf(path):
    """Carica un localconfig.vdf, con fallback sul parser tollerante
    per i file in formato compatto scritti dalle Steam recenti."""
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    try:
        import io
        return vdf.load(io.StringIO(text))
    except (SyntaxError, ValueError):
        return _parse_vdf_tolerant(text)


def _steam_apps(data):
    store = _ci_find(data, 'UserLocalConfigStore') or {}
    software = _ci_find(store, 'Software') or {}
    valve = _ci_find(software, 'Valve') or {}
    steam = _ci_find(valve, 'Steam') or {}
    return _ci_find(steam, 'Apps') or {}


def extract_app_stats(data):
    """Return ``{appid: {minutes, last_played, playtime_2wks}}`` from VDF data.

    ``LastPlayed`` is a UTC epoch written by Steam; ``Playtime2wks`` is minutes
    played in the last two weeks. Both are ignored by the old playtime code.
    """
    apps = _steam_apps(data)
    result = {}
    for appid, info in apps.items():
        if not isinstance(info, dict):
            continue

        def _int(key):
            try:
                return max(0, int(info.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        minutes = _int('Playtime')
        last_played = _int('LastPlayed')
        playtime_2wks = _int('Playtime2wks')
        if minutes <= 0 and last_played <= 0 and playtime_2wks <= 0:
            continue
        result[str(appid)] = {
            "minutes": minutes,
            "last_played": last_played,
            "playtime_2wks": playtime_2wks,
        }
    return result


class PlaytimeManager:
    """Gestisce l'hour counter sincronizzato con Steam.

    Il valore autoritativo del launcher è memorizzato in secondi dentro
    steamrip_meta.json (chiave 'playtime_seconds'). La sync con Steam
    avviene sempre prendendo il MASSIMO tra i due valori, così non si
    perde mai tempo giocato. Le scritture su localconfig.vdf di Steam
    vengono accodate e scritte solo quando Steam è chiuso (altrimenti
    Steam sovrascriverebbe il file).
    """

    def __init__(self, app_window):
        self.app_window = app_window
        self.installed_games = app_window.installed_games
        self.app_config = app_window.app_config

    def _cfg(self):
        """Config corrente (può essere sostituita da update_entire_config)."""
        return self.app_window.app_config

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def signed_appid_from_grid_id(grid_id):
        """Converte l'appid unsigned (steam_grid_id) nell'appid signed a 32 bit
        usato come chiave in localconfig.vdf."""
        try:
            uint = int(grid_id)
        except (TypeError, ValueError):
            return None
        return uint - 0x100000000 if uint >= 0x80000000 else uint

    @staticmethod
    def format_playtime(seconds, mode="hours"):
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError):
            seconds = 0
        if mode == "timestamp":
            h = seconds // 3600
            m = (seconds % 3600) // 60
            if h:
                return f"{h}h {m}m"
            return f"{m}m"
        return f"{seconds / 3600:.1f}h"

    def _save_meta(self, title, meta):
        if not meta or 'install_dir' not in meta:
            return
        try:
            save_steamrip_meta(meta['install_dir'], meta)
        except Exception as e:
            print(f"[Playtime] Errore salvataggio meta per '{title}': {e}")

    # ------------------------------------------------------------------ reading

    def read_steam_stats(self, account_id=None):
        """Legge playtime, LastPlayed e Playtime2wks da ``localconfig.vdf``.

        Ritorna ``{appid_signed: {"minutes", "last_played", "playtime_2wks"}}``.
        Se ``account_id`` è None aggrega il massimo su tutti gli account locali.
        """
        result = {}
        accounts = steam_integration.get_steam_accounts()
        for acc in accounts:
            if account_id and str(acc['id']) != str(account_id):
                continue
            lc_path = os.path.join(acc["path"], "config", "localconfig.vdf")
            if not os.path.exists(lc_path):
                continue
            try:
                data = _load_vdf(lc_path)
            except Exception as e:
                print(f"[Playtime] Lettura fallita {lc_path}: {e}")
                continue

            for key, entry in extract_app_stats(data).items():
                current = result.setdefault(key, {
                    "minutes": 0,
                    "last_played": 0,
                    "playtime_2wks": 0,
                })
                current["minutes"] = max(current["minutes"], entry["minutes"])
                current["last_played"] = max(current["last_played"], entry["last_played"])
                current["playtime_2wks"] = max(current["playtime_2wks"], entry["playtime_2wks"])
        return result

    def read_steam_playtime(self, account_id=None):
        """Legge il playtime da localconfig.vdf di Steam.

        Ritorna {appid_signed: minuti}. Se account_id è None aggrega il
        massimo su tutti gli account locali.
        """
        return {
            appid: entry["minutes"]
            for appid, entry in self.read_steam_stats(account_id).items()
            if entry["minutes"] > 0
        }

    # ------------------------------------------------------------------ cache

    @staticmethod
    def steam_stats_cache_path():
        return PathManager.get_stats_dir() / "steam_playtime_cache.json"

    def refresh_steam_stats_cache(self, path=None):
        """Snapshot the Steam stats into app data so Stats can read them fast."""
        stats = self.read_steam_stats()
        payload = {
            "v": 1,
            "updated_at": int(time.time()),
            "apps": stats,
        }
        cache_path = Path(path) if path is not None else self.steam_stats_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, cache_path)
        return payload

    def load_steam_stats_cache(self, path=None):
        cache_path = Path(path) if path is not None else self.steam_stats_cache_path()
        try:
            with cache_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ merging

    def sync_game(self, title, meta, steam_minutes=None):
        """Confronta il playtime launcher vs Steam e tiene il massimo.

        Se il launcher ha più tempo di Steam, accoda una scrittura su Steam.
        Se Steam ha più tempo (primo import o sessioni Steam-only), importa.
        """
        if not meta or meta.get('is_orphan'):
            return meta
        grid_id = meta.get('steam_grid_id')
        if not grid_id:
            return meta

        signed = self.signed_appid_from_grid_id(grid_id)
        if signed is None:
            return meta

        if steam_minutes is None:
            account_id = meta.get('steam_account_id') or None
            steam_map = self.read_steam_playtime(account_id)
            steam_minutes = steam_map.get(str(signed), steam_map.get(signed, 0))

        steam_seconds = int(steam_minutes or 0) * 60
        launcher_seconds = int(meta.get('playtime_seconds', 0) or 0)
        effective = max(steam_seconds, launcher_seconds)

        changed = False
        if launcher_seconds != effective:
            meta['playtime_seconds'] = effective
            changed = True

        if launcher_seconds > steam_seconds:
            if not meta.get('playtime_sync_pending'):
                meta['playtime_sync_pending'] = True
                changed = True
        else:
            if meta.get('playtime_sync_pending'):
                meta['playtime_sync_pending'] = False
                changed = True

        meta['playtime_last_synced'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if changed:
            self._save_meta(title, meta)
        return meta

    def sync_all(self):
        """Sincronizza tutti i giochi installati con Steam (lettura + merge)."""
        # Keep the Stats cache fresh even when the Steam write-back is disabled.
        try:
            self.refresh_steam_stats_cache()
        except Exception as e:
            print(f"[Playtime] Errore cache Steam: {e}")

        if not self._cfg().get('playtime', {}).get('auto_sync', True):
            print("[Playtime] Auto-sync disabilitato nelle impostazioni.")
            return

        steam_cache = {}

        def _steam_for(meta, signed):
            account_id = meta.get('steam_account_id')
            key = account_id or '*'
            if key not in steam_cache:
                steam_cache[key] = self.read_steam_playtime(account_id or None)
            m = steam_cache[key]
            return m.get(str(signed), m.get(signed, 0))

        for title, meta in list(self.installed_games.items()):
            if meta.get('is_orphan'):
                continue
            try:
                signed = self.signed_appid_from_grid_id(meta.get('steam_grid_id'))
                if signed is None:
                    continue
                self.sync_game(title, meta, steam_minutes=_steam_for(meta, signed))
            except Exception as e:
                print(f"[Playtime] Errore sync per '{title}': {e}")

        # Stub dei savegames con solo playtime (giochi senza save sincronizzate)
        try:
            if hasattr(self.app_window, 'savegames_manager'):
                self.app_window.savegames_manager.ensure_playtime_stubs()
        except Exception as e:
            print(f"[Playtime] Errore aggiornamento stub savegames: {e}")

    # ------------------------------------------------------------------ sessions

    def record_session(self, title, session_seconds):
        """Somma la durata di una sessione al contatore del launcher."""
        meta = self.installed_games.get(title) if title else None
        if not meta:
            return
        try:
            session_seconds = max(0, int(session_seconds))
        except (TypeError, ValueError):
            session_seconds = 0
        if not session_seconds:
            return
        meta['playtime_seconds'] = int(meta.get('playtime_seconds', 0) or 0) + session_seconds
        # Nuovo tempo da spingere su Steam (verrà valutato da sync_game: se Steam
        # ha già contato la sessione da solo, il flag verrà rimosso).
        meta['playtime_sync_pending'] = True
        self._save_meta(title, meta)
        try:
            if hasattr(self.app_window, 'savegames_manager'):
                self.app_window.savegames_manager.ensure_playtime_stub(title, meta)
        except Exception as e:
            print(f"[Playtime] Errore stub per '{title}': {e}")

    # ------------------------------------------------------------------ restore

    def restore_playtime(self, title, meta, seconds):
        """Ripristina il playtime da un backup.

        Scrive il valore sul launcher. Su Steam viene accodata una scrittura
        SOLO se il valore ripristinato è maggiore del valore Steam attuale
        (regola "non perdere mai tempo giocato").
        """
        if not meta:
            return
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError):
            seconds = 0
        meta['playtime_seconds'] = seconds
        self._save_meta(title, meta)

        grid_id = meta.get('steam_grid_id')
        if not grid_id:
            return
        signed = self.signed_appid_from_grid_id(grid_id)
        if signed is None:
            return
        account_id = meta.get('steam_account_id') or None
        steam_map = self.read_steam_playtime(account_id)
        steam_minutes = int(steam_map.get(str(signed), steam_map.get(signed, 0)) or 0)
        meta['playtime_sync_pending'] = seconds > steam_minutes * 60
        self._save_meta(title, meta)

    # ------------------------------------------------------------------ writing

    def _resolve_write_targets(self, meta):
        account_id = meta.get('steam_account_id') or ''
        if account_id:
            return [account_id]
        return [str(acc['id']) for acc in steam_integration.get_steam_accounts()]

    @staticmethod
    def _find_app_block(text, appid_str):
        """Ritorna (start, end) del blocco '"<appid>" { ... }' oppure (None, None)."""
        token = '"%s"' % appid_str
        m = re.search(re.escape(token) + r'\s*\{', text)
        if not m:
            return None, None
        start = m.end()
        depth = 1
        pos = start
        n = len(text)
        while pos < n and depth:
            if text[pos] == '{':
                depth += 1
            elif text[pos] == '}':
                depth -= 1
            pos += 1
        if depth:
            return None, None
        return start, pos - 1

    @classmethod
    def _patch_app_block(cls, text, appid_str, minutes):
        """Aggiorna Playtime/Playtime2wks nel blocco dell'appid, se esiste."""
        start, end = cls._find_app_block(text, appid_str)
        if start is None:
            return text, False
        block = text[start:end]
        if '"Playtime"' in block:
            block = re.sub(r'"Playtime"\s*"[^"]*"', '"Playtime" "%d"' % minutes, block)
        else:
            block = block.rstrip('\n\t ') + ' "Playtime" "%d"' % minutes
        if '"Playtime2wks"' in block:
            block = re.sub(r'"Playtime2wks"\s*"[^"]*"', '"Playtime2wks" "%d"' % minutes, block)
        else:
            block = block.rstrip('\n\t ') + ' "Playtime2wks" "%d"' % minutes
        return text[:start] + block + text[end:], True

    @classmethod
    def _insert_app_block(cls, text, appid_str, minutes):
        """Inserisce un nuovo blocco appid dentro la sezione Apps di Steam,
        in formato multilinea per restare compatibile con qualsiasi parser VDF."""
        m = re.search(r'"Steam"\s*\{.*?"apps"\s*\{', text, re.DOTALL | re.IGNORECASE)
        if not m:
            return text, False
        insert_at = m.end()
        line_start = text.rfind('\n', 0, m.start()) + 1
        line_indent = text[line_start:m.start()]
        inner_indent = line_indent + "\t"
        block = ('\n%s"%s"\n%s{\n%s"Playtime" "%d"\n%s"Playtime2wks" "%d"\n%s}\n'
                 % (line_indent, appid_str, line_indent,
                    inner_indent, minutes, inner_indent, minutes, line_indent))
        return text[:insert_at] + block + text[insert_at:], True

    @staticmethod
    def _patch_playtime_text(text, appid_minutes):
        """Patch chirurgica: aggiorna/crea i soli blocchi '"<appid>" {...}'.
        Il resto del file resta identico byte per byte."""
        for appid_str, minutes in appid_minutes.items():
            text, ok = PlaytimeManager._patch_app_block(text, str(appid_str), minutes)
            if not ok:
                text, ok = PlaytimeManager._insert_app_block(text, str(appid_str), minutes)
            if not ok:
                print(f"[Playtime] Impossibile trovare/creare il blocco appid {appid_str} in localconfig.vdf")
        return text

    def write_playtime_to_steam(self):
        """Scrive su localconfig.vdf i playtime accodati (Steam deve essere chiuso).

        La modifica è chirurgica (solo i blocchi degli appid): il resto del
        localconfig.vdf resta intatto, per non rischiare di corrompere la
        configurazione di Steam.
        """
        if steam_integration.is_steam_running():
            print("[Playtime] Steam in esecuzione: scrittura rimandata.")
            return False

        grouped = {}
        for title, meta in self.installed_games.items():
            if meta.get('is_orphan') or not meta.get('playtime_sync_pending'):
                continue
            grid_id = meta.get('steam_grid_id')
            if not grid_id:
                continue
            signed = self.signed_appid_from_grid_id(grid_id)
            if signed is None:
                continue
            launcher_seconds = int(meta.get('playtime_seconds', 0) or 0)
            # ceil: non scriviamo mai meno del reale, mai "si perde tempo"
            minutes = max(1, math.ceil(launcher_seconds / 60)) if launcher_seconds > 0 else 0
            if minutes <= 0:
                continue
            for account_id in self._resolve_write_targets(meta):
                lc_path = steam_integration.get_steam_localconfig_path(account_id)
                if not lc_path:
                    continue
                bucket = grouped.setdefault(lc_path, {})
                bucket[signed] = max(bucket.get(signed, 0), minutes)

        if not grouped:
            return False

        written_any = False
        for lc_path, appids in grouped.items():
            try:
                text = ""
                if os.path.exists(lc_path):
                    with open(lc_path, 'r', encoding='utf-8') as f:
                        text = f.read()
                new_text = self._patch_playtime_text(text, appids)
                if new_text == text:
                    print(f"[Playtime] Nessuna modifica per {lc_path}")
                    continue
                if os.path.exists(lc_path):
                    shutil.copy(lc_path, lc_path + ".bak")
                with open(lc_path, 'w', encoding='utf-8') as f:
                    f.write(new_text)
                written_any = True
                print(f"[Playtime] Scritto su {lc_path}: {appids}")
            except Exception as e:
                print(f"[Playtime] Errore scrittura {lc_path}: {e}")

        if written_any:
            for title, meta in self.installed_games.items():
                if meta.get('playtime_sync_pending'):
                    meta['playtime_sync_pending'] = False
                    self._save_meta(title, meta)
        return written_any

    def has_pending(self):
        return any(meta.get('playtime_sync_pending') for meta in self.installed_games.values())

    def pending_games(self):
        return [
            (title, int(meta.get('playtime_seconds', 0) or 0))
            for title, meta in self.installed_games.items()
            if meta.get('playtime_sync_pending')
        ]

    def try_flush_pending(self):
        """Scrive gli aggiornamenti accodati solo se Steam è chiuso.

        Non uccide mai Steam da solo: è l'utente a scegliere quando
        sincronizzare subito (button manuale) o al prossimo riavvio.
        """
        if not self.has_pending():
            return False
        if steam_integration.is_steam_running():
            print("[Playtime] Steam attivo: write rimandata a chiusura/riavvio di Steam.")
            return False
        return self.write_playtime_to_steam()
