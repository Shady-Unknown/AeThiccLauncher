"""Save-path resolution for SavegamesManager.

The resolver owns the portable-token, emulator, Steam/Proton and manual-path
rules; the manager owns orchestration and the persistent metadata write. All
metadata and installed-games reads go through providers, so replacing the
manager's ``installed_games``/``app_config`` is visible immediately and the
resolver never keeps a second copy of the state.
"""
from __future__ import annotations

import logging
import os
import re
import time

from core import platform_info, save_paths, steam_emu, titles
from core import steam_integration

log = logging.getLogger(__name__)

STORE_PRIORITY = {"steam": 0, "gog": 2, "epic": 3, "microsoft": 4}
STEAM_SHORTCUT_CACHE_TTL = 30.0


class SavePathResolver:
    def __init__(self, installed_games, find_manifest_entry,
                 prompt_save_path, notify, has_parent, save_meta,
                 get_compatdata_base):
        self._installed_games = installed_games
        self._find_manifest_entry = find_manifest_entry
        self._prompt_save_path = prompt_save_path
        self._notify_callback = notify
        self._has_parent = has_parent
        self._save_meta = save_meta
        self._compatdata_base = get_compatdata_base
        self._shortcut_records = None
        self._shortcut_errors = []
        self._shortcut_records_at = 0.0


    def extract_win_save_paths(self, manifest_entry):
        """Save candidates as ``(template, when)``, Windows ones first.

        Entries that only name a store (for example ``store: steam`` without
        ``os: windows``) are kept: Steam userdata is OS-independent and can be
        resolved on Linux too.
        """
        if not isinstance(manifest_entry, dict):
            return []

        windows = []
        store_only = []
        for section_name in ("files", "installDir", "registry"):
            section = manifest_entry.get(section_name, {})
            if not isinstance(section, dict):
                continue
            for path_template, details in section.items():
                if not isinstance(details, dict) or not path_template:
                    continue
                if "save" not in (details.get("tags") or []):
                    continue
                when = details.get("when") or []
                if not when or self._when_has(when, "os", "windows"):
                    windows.append((path_template, when))
                elif self._when_has(when, "store", "steam"):
                    store_only.append((path_template, when))

        if not windows and not store_only:
            files = manifest_entry.get("files")
            if isinstance(files, dict):
                for path_template, details in files.items():
                    if (isinstance(details, dict)
                            and "save" in (details.get("tags") or [])):
                        windows.append((path_template, details.get("when") or []))
        windows = sorted(windows, key=lambda item: self._store_rank(item[1]))
        return windows + store_only

    @classmethod
    def _store_rank(cls, when):
        conditions = when if isinstance(when, list) else [when]
        ranks = []
        for condition in conditions:
            if not isinstance(condition, dict):
                continue
            store = str(condition.get("store") or "").strip().lower()
            ranks.append(STORE_PRIORITY.get(store, 1 if not store else 5))
        return min(ranks) if ranks else 1

    @classmethod
    def _portable_save_template(cls, path_template, when):
        """``<root>`` is the Steam client root for ``store: steam`` entries.

        Ludusavi writes Steam Cloud saves as ``<root>/userdata/<storeUserId>/...``,
        where ``<root>`` is the Steam installation, not the drive root. Turning
        it into ``<steam-folder>`` keeps the path portable and lets both the
        Linux userdata resolver and the Windows Steam detection use it.
        """
        text = str(path_template or "")
        if cls._when_has(when, "store", "steam") and text.lower().startswith("<root>"):
            return "<steam-folder>" + text[len("<root>"):]
        return text

    def _expand_save_candidate(self, path_template, when):
        portable = self._portable_save_template(path_template, when)
        return self._normalize_manifest_save_path(
            self.expand_manifest_template(portable)
        )

    def choose_manifest_save_path(self, manifest_entry, meta=None):
        """Best manifest save path: an existing one, else the first Windows one."""
        candidates = self.extract_win_save_paths(manifest_entry)
        if not candidates:
            return None

        meta = meta or {}
        grid_id = str(meta.get("steam_grid_id") or "")
        base = ""
        if grid_id and platform_info.is_linux():
            base = self._get_compatdata_base(grid_id)
        account = self._local_steam_account_id(meta)

        expanded = []
        for path_template, when in candidates:
            path = self._expand_save_candidate(path_template, when)
            if path:
                expanded.append(path)
        if not expanded:
            return None

        for path in expanded:
            if platform_info.is_linux():
                probe = save_paths.translate_win_path(
                    path, base, account_id=account
                )
            elif os.name == "nt":
                probe = self.resolve_portable_tokens(path, meta)
            else:
                probe = ""
            if probe and os.path.isdir(probe):
                return path
        return expanded[0]

    def extract_win_save_path(self, manifest_entry, meta=None):
        return self.choose_manifest_save_path(manifest_entry, meta)

    @staticmethod
    def expand_manifest_template(template):
        template = str(template).strip()

        # Usiamo 'lambda m: r"..."' per forzare Python a trattare i backslash come testo semplice,
        # evitando che re.sub vada in crash interpretandoli come comandi (\D, \S, ecc.)
        replacements = {
            r'(?i)<winLocalAppData>': lambda m: r'%localappdata%',
            r'(?i)<winLocalAppDataLow>': lambda m: r'%userprofile%\AppData\LocalLow',
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

    def _shortcut_inventory(self, force=False):
        now = time.monotonic()
        stale = (self._shortcut_records is None
                 or force
                 or now - self._shortcut_records_at > STEAM_SHORTCUT_CACHE_TTL)
        if stale:
            errors = []
            self._shortcut_records = steam_integration.shortcut_records(
                errors=errors)
            self._shortcut_errors = list(errors)
            self._shortcut_records_at = now
        return self._shortcut_records, self._shortcut_errors

    @staticmethod
    def _normalized_path(path):
        text = str(path or '').strip().strip('"').replace('\\', '/')
        if not text:
            return ''
        return os.path.normcase(os.path.normpath(text))

    @classmethod
    def _same_path(cls, first, second):
        a = str(first or '').strip().strip('"')
        b = str(second or '').strip().strip('"')
        if not a or not b:
            return False
        try:
            if os.path.exists(a) and os.path.exists(b):
                return os.path.samefile(a, b)
        except OSError:
            pass
        return cls._normalized_path(a) == cls._normalized_path(b)

    @classmethod
    def _under(cls, path, root):
        p = cls._normalized_path(path)
        r = cls._normalized_path(root)
        if not p or not r:
            return False
        try:
            return os.path.commonpath([p, r]) == r
        except ValueError:
            return False

    def _record_matches_installation(self, record, meta):
        exe = str(meta.get('executable_path') or '').strip()
        install = str(meta.get('install_dir') or '').strip()
        record_exe = str(record.get('exe') or '').strip()
        start_dir = str(record.get('start_dir') or '').strip()
        if exe and self._same_path(exe, record_exe):
            return True
        if install and record_exe and self._under(record_exe, install):
            return True
        if install and start_dir and self._same_path(start_dir, install):
            return True
        if (install and exe and record_exe
                and os.path.basename(exe).lower() == os.path.basename(record_exe).lower()
                and (not start_dir or self._under(start_dir, install))):
            return True
        return False

    def match_shortcut_identity(self, meta, records):
        """Return one verified shortcut, or ``None``/an ambiguity marker."""
        title = titles.normalize_spaced(meta.get('title') or '')
        old_shortcut = str(meta.get('steam_shortcut_id') or '').strip()
        old_grid = str(meta.get('steam_grid_id') or '').strip()
        old_account = str(meta.get('steam_account_id') or '').strip()
        exact_exe = []
        title_matches = []
        id_matches = []
        for record in records:
            record_title = titles.normalize_spaced(record.get('title') or '')
            exe_match = self._record_matches_installation(record, meta)
            title_match = bool(title and record_title == title)
            id_match = ((old_shortcut and record.get('shortcut_id') == old_shortcut)
                        or (old_grid and record.get('grid_id') == old_grid))
            if exe_match:
                exact_exe.append(record)
            if title_match:
                title_matches.append(record)
            if id_match and title_match and exe_match:
                id_matches.append(record)
        if len(exact_exe) == 1:
            return exact_exe[0]
        if len(exact_exe) > 1:
            identities = {(r.get('grid_id'), r.get('shortcut_id'))
                          for r in exact_exe}
            if len(identities) == 1:
                accounts = {r.get('account_id') for r in exact_exe}
                if old_account in accounts:
                    for record in exact_exe:
                        if record.get('account_id') == old_account:
                            return record
                # Same installation in multiple accounts: keep the ID update
                # but do not invent an ownership switch.
                record = dict(exact_exe[0])
                record['account_id'] = ''
                return record
            return {'ambiguous': 'multiple_exe_candidates'}
        if id_matches:
            if len({(r.get('account_id'), r.get('shortcut_id')) for r in id_matches}) == 1:
                return id_matches[0]
            return {'ambiguous': 'multiple_id_candidates'}
        # Title alone is not enough unless it is unique and the shortcut is
        # verifiably tied to the current installation directory.
        if len(title_matches) == 1 and self._record_matches_installation(
                title_matches[0], meta):
            return title_matches[0]
        if len(title_matches) > 1:
            return {'ambiguous': 'multiple_title_candidates'}
        return None

    def refresh_steam_identity(self, meta, force=False):
        """Return an identity refresh result without writing metadata."""
        result = {'changed': False, 'status': 'not_found', 'updates': {},
                  'old': {}, 'candidate': None}
        if not meta or meta.get('is_orphan'):
            result['status'] = 'skipped'
            return result
        records, errors = self._shortcut_inventory(force=force)
        if errors:
            result['status'] = 'steam_unreadable'
            return result
        match = self.match_shortcut_identity(meta, records)
        if isinstance(match, dict) and match.get('ambiguous'):
            result['status'] = 'ambiguous'
            result['detail'] = match['ambiguous']
            return result
        if not match:
            return result
        updates = {}
        for field, value in (
                ('steam_grid_id', match.get('grid_id')),
                ('steam_shortcut_id', match.get('shortcut_id')),
                ('steam_account_id', match.get('account_id'))):
            value = str(value or '').strip()
            if value and str(meta.get(field) or '').strip() != value:
                updates[field] = value
        candidate_exe = str(match.get('exe') or '').strip()
        current_exe = str(meta.get('executable_path') or '').strip()
        if (candidate_exe and os.path.isfile(candidate_exe)
                and (not current_exe or not os.path.isfile(current_exe))
                and candidate_exe != current_exe):
            # The shortcut is the live installation; replace an obsolete
            # absolute path only when the old one no longer exists.
            updates['executable_path'] = candidate_exe
        if not updates:
            result['status'] = 'current'
            return result
        result.update({
            'changed': True,
            'status': 'changed',
            'updates': updates,
            'old': {key: meta.get(key) for key in updates},
            'candidate': match,
        })
        return result

    def _get_compatdata_base(self, steam_grid_id):
        return self._compatdata_base(steam_grid_id)

    def translate_win_path_to_linux(self, win_path, steam_grid_id, account_id=""):
        if not win_path:
            return ""
        # The compatdata prefix only exists on the machine that runs the game.
        # On Windows ``~`` expands to C:\Users\<name> and os.path.join emits
        # backslashes, which used to store garbage such as
        # "C:Users\me\.local\share\Steam\steamapps\compatdata\...".
        # Leave the field empty and translate again when running on Linux.
        if not platform_info.is_linux():
            return ""
        if not steam_grid_id:
            # Steam userdata and native paths carry their own location; only
            # prefix paths need the game's appid.
            text = str(win_path).strip().strip('"')
            if (not save_paths.is_posix_path(text)
                    and save_paths.steam_userdata_tail(text) is None):
                return ""
        base = self._get_compatdata_base(steam_grid_id) if steam_grid_id else ""
        return save_paths.translate_win_path(win_path, base, account_id=account_id)

    def preview_linux_path(self, win_path, title=None):
        """Explain how a Windows save path will be read on Linux (UI preview).

        Returns the ``translate_win_path_details`` dict plus ``ok``; unlike
        ``translate_win_path_to_linux`` it works without a Steam appid because
        Steam userdata paths carry their own appid.
        """
        if not platform_info.is_linux():
            return {"path": "", "kind": "", "reason": "not_linux",
                    "token": "", "ok": False}
        text = str(win_path or "").strip()
        if not text:
            return {"path": "", "kind": "", "reason": "empty",
                    "token": "", "ok": False}
        base = ""
        account = ""
        if title:
            meta = (self._installed_games() or {}).get(title) or {}
            grid_id = str(meta.get("steam_grid_id") or "")
            if grid_id:
                base = self._get_compatdata_base(grid_id)
            account = self._local_steam_account_id(meta)
        details = save_paths.translate_win_path_details(
            text, base or "", account_id=account
        )
        details["exists"] = bool(
            details.get("path") and os.path.exists(details["path"])
        )
        details["ok"] = bool(details.get("path"))
        return details

    @staticmethod
    def _windows_steam_dir():
        try:
            from core import steam_integration
            return steam_integration.steam_install_dir() or ""
        except Exception:
            return ""

    @staticmethod
    def _local_steam_account_id(meta=None):
        try:
            from core import steam_integration
            paths = steam_integration.get_steam_account_paths()
        except Exception:
            return ""
        ids = []
        for path in paths or []:
            name = os.path.basename(os.path.normpath(str(path)))
            if name.isdigit():
                ids.append(name)
        if not ids:
            return ""
        preferred = str((meta or {}).get("steam_account_id") or "")
        return preferred if preferred in ids else ids[0]

    def emulator_save_path(self, meta):
        """Portable Windows save folder used by the release's Steam emulator.

        SteamRIP releases carry their own emulator (CODEX, RUNE, PLAZA,
        Goldberg), so the manifest path can be wrong for the exact release.
        When an emulator has more than one convention (Goldberg's ``GSE Saves``
        vs the older folder, or the real appid vs the shortcut id), the folder
        that exists on this machine wins.
        """
        if not meta:
            return {}
        info = steam_emu.detect_emulator(
            meta.get("executable_path") or "",
            meta.get("install_dir") or "",
            appid_hint=str(meta.get("steam_grid_id") or ""),
        )
        if not info:
            return {}

        base = ""
        grid_id = str(meta.get("steam_grid_id") or "")
        if grid_id and platform_info.is_linux():
            base = self._get_compatdata_base(grid_id)

        for candidate in info.get("candidates") or []:
            if platform_info.is_linux():
                probe = save_paths.translate_win_path(candidate, base) if base else ""
            else:
                probe = os.path.expandvars(candidate)
            if probe and os.path.isdir(probe):
                info["windows_path"] = candidate
                break
        return info

    def resolve_portable_tokens(self, path, meta=None):
        """Replace machine-specific tokens in a stored Windows save path.

        ``<steam-folder>`` becomes the local Steam client folder and
        ``<storeUserId>`` the local userdata account. Returns ``""`` when a
        token is present but Steam itself cannot be found.
        """
        text = str(path or "")
        if not text:
            return text
        lower = text.lower()
        if any(token in lower for token in save_paths.STEAM_FOLDER_TOKENS):
            steam_dir = self._windows_steam_dir()
            if not steam_dir:
                return ""
            for token in save_paths.STEAM_FOLDER_TOKENS:
                text = re.sub(
                    re.escape(token), lambda _match: steam_dir, text,
                    flags=re.IGNORECASE,
                )
        if any(token in text.lower() for token in save_paths.STEAM_ACCOUNT_TOKENS):
            account = self._local_steam_account_id(meta)
            if not account:
                return ""
            text = save_paths.replace_steam_account_token(text, account)
        return text

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
            return False
        result = self._save_meta(meta)
        return False if result is False else True

    def _commit(self, meta, before):
        if not meta or not meta.get('install_dir'):
            # Headless/no-folder callers still use the mutation in memory.
            return True
        if self.save_meta(meta):
            return True
        meta.clear()
        meta.update(before)
        log.error("[Savegames] Metadata write failed; refresh rolled back")
        return False

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
        return self._prompt_save_path(game_title) or ""

    def _notify(self, level, title, message):
        self._notify_callback(level, title, message)

    @staticmethod
    def _path_state(meta):
        return (
            str(meta.get('install_dir') or ''),
            str(meta.get('savegame_path_win') or ''),
            str(meta.get('savegame_path_linux') or ''),
            str(meta.get('savegame_path_custom') or ''),
            str(meta.get('savegame_path_source') or ''),
            str(meta.get('steam_grid_id') or ''),
            str(meta.get('steam_shortcut_id') or ''),
        )

    def _prepared(self, meta, needs_prompt=False, title='', tracked=False):
        return {'meta': meta, 'needs_prompt': needs_prompt,
                'title': title, 'tracked': tracked,
                'state': self._path_state(meta)}

    def ensure_save_path_for_game(self, meta):
        """Synchronous detect/prompt/commit, for headless callers only.

        GUI callers use ``prepare_save_path_for_game`` and
        ``commit_manual_save_path`` separately so the modal prompt never runs
        while the coordinator lock is held.
        """
        prepared = self.prepare_save_path_for_game(meta)
        if prepared.get('needs_prompt'):
            manual = self.prompt_manual_save_path(prepared['title'])
            return self.commit_manual_save_path(
                meta, manual, prepared['state'], prepared['title'],
                tracked=prepared.get('tracked', False))
        return prepared['meta']

    def prepare_save_path_for_game(self, meta):
        """Detection phase: never prompts, safe to run under the lock."""
        if not meta or meta.get('is_orphan'):
            return self._prepared(meta)

        before = dict(meta)
        try:
            identity_changed = False
            identity = self.refresh_steam_identity(meta)
            if identity.get('changed'):
                meta.update(identity['updates'])
                identity_changed = True
                log.info("[Savegames] Steam identity refreshed for '%s': %s -> %s",
                         meta.get('title'), identity.get('old'), identity.get('updates'))
            automatic = (str(meta.get('savegame_path_source') or '').lower() != 'user'
                         and not meta.get('savegame_path_custom'))
            if identity_changed and automatic:
                # Force the automatic Linux path through the new identity instead
                # of silently returning the old compatdata value.
                meta['savegame_path_linux'] = ''

            has_paths = bool(meta.get('savegame_path_win')
                             or meta.get('savegame_path_linux')
                             or meta.get('savegame_path_custom'))
            if has_paths and not (identity_changed and automatic):
                if identity_changed and not self._commit(meta, before):
                    return self._prepared(meta)
                return self._prepared(meta)

            title = meta.get('title') or os.path.basename(meta.get('install_dir', ''))

            # The release's own emulator is the most reliable source: the manifest
            # describes the official store layout, which often differs from the
            # cracked release (e.g. CODEX writes under %public%\Documents\Steam).
            emulator = self.emulator_save_path(meta)
            if emulator:
                win_path = emulator["windows_path"]
                meta['savegame_path_win'] = win_path
                meta['savegame_path_source'] = 'emulator'
                if meta.get('steam_grid_id'):
                    linux_path = self.translate_win_path_to_linux(
                        win_path, meta['steam_grid_id'],
                        account_id=self._local_steam_account_id(meta),
                    )
                    if linux_path:
                        meta['savegame_path_linux'] = linux_path
                meta['savegame_scan_state'] = True
                if not self._commit(meta, before):
                    return self._prepared(meta)
                self._notify(
                    "info", "Savegames Manager",
                    f"Save path detected from the {emulator['emulator']} emulator for {title}.")
                return self._prepared(meta)

            manifest_entry = self._find_manifest_entry(title)
            if manifest_entry:
                win_path = self.extract_win_save_path(manifest_entry, meta)
                if win_path:
                    meta['savegame_path_win'] = win_path
                    meta['savegame_path_source'] = 'manifest'
                    if meta.get('steam_grid_id'):
                        linux_path = self.translate_win_path_to_linux(
                            win_path, meta['steam_grid_id'],
                            account_id=self._local_steam_account_id(meta),
                        )
                        if linux_path:
                            meta['savegame_path_linux'] = linux_path
                    meta['savegame_scan_state'] = True
                    if not self._commit(meta, before):
                        return self._prepared(meta)
                    self._notify(
                        "info", "Savegames Manager",
                        f"Save path inferred from Ludusavi manifest for {title}.")
                    return self._prepared(meta)

            if os.name == 'posix' and self._has_parent() and not meta.get('steam_grid_id'):
                self._notify(
                    "warning", "Steam Grid ID Required",
                    "On Linux, to map Windows save locations to compatdata, you should add this game to Steam as a Non-Steam game. "
                    "Without the Steam grid ID the app can still store a manual path, but full Linux sync may be incomplete."
                )

            if identity_changed and not self._commit(meta, before):
                return self._prepared(meta)
            # A manual path is needed: the caller prompts outside the lock.
            installed = self._installed_games() or {}
            return self._prepared(
                meta, needs_prompt=True, title=title,
                tracked=installed.get(title) is meta)

        except Exception as exc:
            meta.clear()
            meta.update(before)
            log.exception("[Savegames] Save-path recalculation failed; rolled back: %s", exc)
            return self._prepared(meta)

    def commit_manual_save_path(self, meta, manual_path, expected_state, title,
                                tracked=True):
        """Commit phase for a prompted path, with revalidation under the lock."""
        if not meta or meta.get('is_orphan') or not manual_path:
            return meta
        installed = (self._installed_games() or {}) if callable(self._installed_games) \
            else (self._installed_games or {})
        if tracked and installed.get(title) is not meta:
            log.info("[Savegames] Save-path prompt result discarded for '%s': "
                     "the game changed while the dialog was open", title)
            return meta
        if self._path_state(meta) != expected_state:
            log.info("[Savegames] Save-path prompt result discarded for '%s': "
                     "the save path changed while the dialog was open", title)
            return meta

        before = dict(meta)
        try:
            if re.search(r'[%\\]|C:|c:', manual_path):
                meta['savegame_path_win'] = manual_path
                meta['savegame_path_source'] = 'user'
                if meta.get('steam_grid_id'):
                    linux_path = self.translate_win_path_to_linux(
                        manual_path, meta['steam_grid_id'],
                        account_id=self._local_steam_account_id(meta),
                    )
                    if linux_path:
                        meta['savegame_path_linux'] = linux_path
            else:
                meta['savegame_path_linux'] = manual_path
                meta['savegame_path_source'] = 'user'

            meta['savegame_scan_state'] = True
            if not self._commit(meta, before):
                return meta
            return meta
        except (OSError, KeyError, TypeError, ValueError) as exc:
            meta.clear()
            meta.update(before)
            log.exception("[Savegames] Manual save-path commit failed; rolled back: %s", exc)
            return meta
    def rescan_save_folder(self, meta, refresh_identity=True, force_identity=True):
        if not meta or meta.get('is_orphan'):
            return meta

        before = dict(meta)
        try:
            changed = False
            identity = (self.refresh_steam_identity(meta, force=force_identity)
                        if refresh_identity else {})
            if identity.get('changed'):
                meta.update(identity['updates'])
                changed = True
                if (str(meta.get('savegame_path_source') or '').lower() != 'user'
                        and not meta.get('savegame_path_custom')):
                    meta['savegame_path_linux'] = ''
                log.info("[Savegames] Steam identity refreshed for '%s': %s -> %s",
                         meta.get('title'), identity.get('old'), identity.get('updates'))

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

            # --- 3. SORGENTE DEL PERCORSO ---
            title = meta.get('title') or os.path.basename(meta.get('install_dir', ''))
            manifest_entry = self._find_manifest_entry(title)
            path_source = str(meta.get('savegame_path_source') or '').strip().lower()

            if path_source != 'user':
                emulator = self.emulator_save_path(meta)
                if emulator:
                    if meta.get('savegame_path_win') != emulator["windows_path"]:
                        meta['savegame_path_win'] = emulator["windows_path"]
                        changed = True
                    if path_source != 'emulator':
                        meta['savegame_path_source'] = 'emulator'
                        changed = True
                elif manifest_entry:
                    win_path = self.extract_win_save_path(manifest_entry, meta)
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

            if meta.get('savegame_path_custom'):
                # A manual path is authoritative: identity refresh may change the
                # Steam ids, but it must not rewrite the user's destination.
                current_win_path = ''
            if current_win_path and grid_id:
                linux_path = self.translate_win_path_to_linux(
                    current_win_path, grid_id,
                    account_id=self._local_steam_account_id(meta),
                )
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
                if not self._commit(meta, before):
                    return meta

            return meta

        except Exception as exc:
            meta.clear()
            meta.update(before)
            log.exception("[Savegames] Save-path recalculation failed; rolled back: %s", exc)
            return meta

    def refresh_steam_identities(self, games=None, force=True, inventory_ready=False):
        """Refresh identities/paths for a batch with one VDF inventory read."""
        if games is None:
            games = self._installed_games() or {}
        if not inventory_ready:
            self._shortcut_inventory(force=force)
        changed = []
        for title, meta in list(games.items()):
            if not isinstance(meta, dict) or meta.get('is_orphan'):
                continue
            if not (meta.get('steam_shortcut_id') or meta.get('steam_grid_id')
                    or meta.get('executable_path')):
                continue
            identity = self.refresh_steam_identity(meta, force=False)
            if not identity.get('changed'):
                continue
            # Reuse the warm inventory index but let rescan own the single
            # identity + path transaction (no partially updated meta here).
            self.rescan_save_folder(meta, refresh_identity=True, force_identity=False)
            if any(str(meta.get(k) or '') == str(v)
                   for k, v in identity['updates'].items()):
                changed.append(title)
        if changed:
            log.info("[Savegames] Steam identity refreshed for %d game(s): %s",
                     len(changed), ", ".join(changed[:5]))
        return changed

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
            if os.name == 'nt':
                resolved = self.resolve_portable_tokens(expanded, meta)
                if resolved:
                    expanded = resolved
            elif platform_info.is_linux() and not save_paths.is_posix_path(expanded):
                # A custom path typed as a portable Windows/Steam location is
                # translated here as well; otherwise the dialog would accept it
                # and then silently ignore it on Linux.
                translated = self.translate_win_path_to_linux(
                    expanded, meta.get('steam_grid_id') or '',
                    account_id=self._local_steam_account_id(meta),
                )
                if translated:
                    expanded = translated
            if os.path.exists(expanded):
                return expanded

        if os.name == 'nt':
            if meta.get('savegame_path_win'):
                path = self.normalize_windows_path(meta['savegame_path_win'])
                path = self.resolve_portable_tokens(path, meta)
                if not path:
                    return ""
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
                derived = self.translate_win_path_to_linux(
                    win_path, grid_id,
                    account_id=self._local_steam_account_id(meta),
                )
                if derived:
                    return self._resolve_save_root(derived)

            if stored_ok:
                return self._resolve_save_root(self._resolve_linux_path_from_absolute(stored_linux))

            if win_path.startswith(('%', '<')):
                derived = self.translate_win_path_to_linux(
                    win_path, grid_id,
                    account_id=self._local_steam_account_id(meta),
                )
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
            return self.translate_win_path_to_linux(
                win_path, grid_id,
                account_id=self._local_steam_account_id(meta),
            )
        return ''

    @staticmethod
    def _when_has(when, key, value):
        conditions = when if isinstance(when, list) else [when]
        return any(
            isinstance(condition, dict)
            and str(condition.get(key) or "").strip().lower() == value
            for condition in conditions
        )
