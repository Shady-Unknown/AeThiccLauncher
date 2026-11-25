import logging
import os
import json
import re
import shutil
import time
import uuid
import hashlib
import threading
from pathlib import Path
from datetime import datetime, timezone

from core import game_version, save_paths
from core.atomic_io import atomic_write_json
from core.naming import safe_game_folder_name
from managers.save_coordinator import (
    OperationBusy, OperationCancelled, SavegamesOperationCoordinator,
)
from managers.save_locations import SavePathResolver
from managers.save_manifest import LudusaviManifest
#from urllib.parse import quote

log = logging.getLogger(__name__)

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
# Restore transaction artifacts, always siblings of the live destination.
RESTORE_TMP_SUFFIX = '.restore_tmp'
RESTORE_OLD_SUFFIX = '.restore_old'
RESTORE_JOURNAL_SUFFIX = '.restore_journal.json'
RESTORE_JOURNAL_SCHEMA = 1
# Content hashing is opt-in and only for trees up to this size; above it the
# metadata fingerprint stays in charge so a big save folder never stalls sync.
SNAPSHOT_HASH_MAX_BYTES = 64 * 1024 * 1024


def _snapshot_content_signature(source_path, content_hash=False,
                                max_bytes=SNAPSHOT_HASH_MAX_BYTES):
    """Fingerprint of the source, used to skip identical snapshots.

    Metadata mode (default) hashes relative path, kind, size and mtime without
    reading any file. Content mode additionally hashes the bytes, but only when
    the tree fits ``max_bytes``; otherwise it falls back to metadata.
    """
    if not source_path:
        return ""

    entries = []
    if os.path.isfile(source_path):
        try:
            info = os.lstat(source_path)
        except OSError:
            return ""
        entries.append((os.path.basename(source_path), 'f',
                        info.st_size, info.st_mtime_ns, source_path))
    elif os.path.isdir(source_path):
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
                entries.append((rel, kind, info.st_size, info.st_mtime_ns, full))
            # Symlinked folders are part of the snapshot too (as links), so
            # their own metadata must influence the signature.
            for name in sorted(dirs, key=str.lower):
                full = os.path.join(root, name)
                if not os.path.islink(full):
                    continue
                try:
                    info = os.lstat(full)
                except OSError:
                    continue
                rel = os.path.normpath(os.path.join(rel_root, name)).replace(os.sep, '/')
                entries.append((rel, 'd', info.st_size, info.st_mtime_ns, full))
    else:
        return ""

    total = sum(size for _, kind, size, _, _ in entries if kind == 'f')
    use_content = bool(content_hash) and total <= max_bytes

    digest = hashlib.sha256()
    digest.update(b"content|" if use_content else b"meta|")
    for rel, kind, size, mtime, full in entries:
        digest.update(f"{rel}|{kind}|{size}|{mtime}\n".encode('utf-8'))
        if use_content and kind == 'f':
            try:
                with open(full, 'rb') as fh:
                    while True:
                        chunk = fh.read(1024 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
            except OSError:
                continue
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


class SavegamesManager:
    def __init__(self, app_config, installed_games, parent=None,
                 prompt_save_path=None, notify=None):
        self.app_config = app_config
        self.installed_games = installed_games
        self.parent = parent
        self._prompt_save_path = prompt_save_path
        self._notify_callback = notify
        self.manifest = None
        self.manifest_text = None
        from core.paths import PathManager
        self.local_dbs_dir = str(PathManager.get_dbs_dir())
        os.makedirs(self.local_dbs_dir, exist_ok=True)

        self.manifest_store = LudusaviManifest(
            os.path.join(self.local_dbs_dir, "ludusavi_manifest.yaml"),
            os.path.join(self.local_dbs_dir, "ludusavi_etag.txt"),
        )

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

    def _get_paths(self):
        """Lazily build the resolver from live providers (no state copies)."""
        resolver = getattr(self, "_path_resolver", None)
        if resolver is None:
            resolver = SavePathResolver(
                installed_games=lambda: getattr(self, "installed_games", {}) or {},
                find_manifest_entry=lambda title: self.find_manifest_entry(title),
                prompt_save_path=lambda title: self.prompt_manual_save_path(title),
                notify=lambda level, title, message: self._notify(level, title, message),
                has_parent=lambda: bool(getattr(self, "parent", None)),
                save_meta=lambda meta: self.save_meta(meta),
                get_compatdata_base=lambda grid_id: self._get_compatdata_base(grid_id),
            )
            self._path_resolver = resolver
        return resolver

    def _coordinator(self):
        coordinator = getattr(self, "_operations", None)
        if coordinator is None:
            coordinator = SavegamesOperationCoordinator()
            self._operations = coordinator
        return coordinator
    def close_operations(self, wait_ms=2000):
        """Cancel running operations, refuse new ones and wait outside the lock."""
        coordinator = self._coordinator()
        coordinator.request_cancel()
        coordinator.close()
        return coordinator.wait_idle(wait_ms)

    def request_cancel(self):
        """Ask the running savegame operation to stop at its next safe point."""
        self._coordinator().request_cancel()

    def _is_cancel_requested(self):
        return self._coordinator().cancel_requested()

    # Snapshot/restore/backup transactions are serialized globally. The lock is
    # reentrant so restore can create its safety snapshot and manifest inside
    # the same transaction.
    def create_snapshot(self, game_root, title, meta, source_path, reason='sync',
                        protected_snapshots=()):
        result = self._coordinator().run(
            lambda: self._create_snapshot_impl(
                game_root, title, meta, source_path, reason,
                protected_snapshots=protected_snapshots))
        self._after_transaction()
        return result

    def restore_snapshot(self, meta, snapshot_id, create_missing=False):
        result = self._coordinator().run(
            lambda: self._restore_snapshot_impl(
                meta, snapshot_id, create_missing))
        self._after_transaction()
        return result

    def backup_before_update(self, meta):
        result = self._coordinator().run(
            lambda: self._backup_before_update_impl(meta))
        self._after_transaction()
        return result

    def backup_on_demand(self, meta):
        result = self._coordinator().run(
            lambda: self._backup_on_demand_impl(meta))
        self._after_transaction()
        return result

    def sync_all_games(self):
        result = self._coordinator().run(self._sync_all_games_impl)
        self._after_transaction()
        return result

    def list_snapshots(self, title):
        return self._coordinator().run(
            lambda: self._list_snapshots_impl(title))

    def try_push_mirror(self, timeout_ms=0):
        """Non-blocking mirror push for GUI/timer callers.

        Never blocks the GUI thread: when another operation owns the lock the
        push is skipped and the next savegames operation performs it. The
        mirror is a copy of the local truth, so a skipped push loses nothing.
        """
        try:
            report = self._coordinator().run(
                self._push_mirror_impl, timeout_ms=timeout_ms)
        except OperationBusy:
            return {'status': 'busy', 'report': {}}
        except OperationCancelled:
            return {'status': 'cancelled', 'report': {}}
        except Exception as exc:
            return {'status': 'failed', 'detail': str(exc), 'report': {}}
        self._after_transaction()
        return {'status': 'completed', 'report': report}

    def try_list_snapshots(self, title, timeout_ms=0):
        """Non-blocking read: never reports an empty list for a busy lock."""
        try:
            snapshots = self._coordinator().run(
                lambda: self._list_snapshots_impl(title),
                timeout_ms=timeout_ms,
            )
        except OperationBusy:
            return {'status': 'busy', 'snapshots': []}
        except OperationCancelled:
            return {'status': 'cancelled', 'snapshots': []}
        except Exception as exc:
            return {'status': 'failed', 'detail': str(exc), 'snapshots': []}
        return {'status': 'completed', 'snapshots': snapshots}

    def ensure_playtime_stub(self, title, meta):
        """Defer instead of blocking when another operation holds the lock."""
        title = str(title or '').strip()
        if not title:
            return {'status': 'skipped'}
        try:
            self._coordinator().run(
                lambda: self._ensure_playtime_stub_impl(title, meta),
                timeout_ms=0)
        except OperationBusy:
            self._pending_playtime_titles_set().add(title)
            return {'status': 'deferred'}
        except OperationCancelled:
            return {'status': 'cancelled'}
        self._flush_pending_playtime()
        return {'status': 'completed'}

    def ensure_playtime_stubs(self):
        """Defer the bulk stub refresh instead of blocking the caller."""
        try:
            self._coordinator().run(
                self._ensure_playtime_stubs_impl, timeout_ms=0)
        except OperationBusy:
            self._pending_playtime_bulk = True
            return {'status': 'deferred'}
        except OperationCancelled:
            return {'status': 'cancelled'}
        self._flush_pending_playtime()
        return {'status': 'completed'}

    def _pending_playtime_titles_set(self):
        pending = getattr(self, "_pending_playtime_titles", None)
        if pending is None:
            pending = self._pending_playtime_titles = set()
        return pending

    def flush_pending_playtime(self):
        """Flush deferred playtime stubs; returns how many entries remain."""
        self._flush_pending_playtime()
        remaining = len(getattr(self, "_pending_playtime_titles", ()) or ())
        if getattr(self, "_pending_playtime_bulk", False):
            remaining += 1
        return remaining

    def _flush_pending_playtime(self):
        """Write deferred stubs with the CURRENT absolute playtime value.

        Entries are keyed by title and the meta is re-resolved from
        ``installed_games`` at execution: carrying a captured dict could write
        a stale counter over a newer or restored value.
        """
        if getattr(self, "_playtime_flush_active", False):
            return
        if self._coordinator().is_nested():
            # Only the outermost transaction completion flushes, outside the lock.
            return
        pending = getattr(self, "_pending_playtime_titles", None)
        bulk = bool(getattr(self, "_pending_playtime_bulk", False))
        if not pending and not bulk:
            return

        self._playtime_flush_active = True
        try:
            if bulk:
                try:
                    self._coordinator().run(
                        self._ensure_playtime_stubs_impl, timeout_ms=0)
                except OperationBusy:
                    pass
                except OperationCancelled:
                    self._pending_playtime_bulk = False
                    log.info("[Savegames] Dropped deferred playtime stubs at "
                             "shutdown; the counters are already persisted in "
                             "steamrip_meta.json")
                else:
                    self._pending_playtime_bulk = False

            installed = getattr(self, "installed_games", None) or {}
            for title in list(pending or ()):
                meta = installed.get(title)
                if not meta or meta.get('is_orphan') or not meta.get('playtime_seconds'):
                    pending.discard(title)
                    continue
                try:
                    self._coordinator().run(
                        lambda: self._ensure_playtime_stub_impl(title, meta),
                        timeout_ms=0)
                except OperationBusy:
                    continue
                except OperationCancelled:
                    pending.discard(title)
                    log.info("[Savegames] Dropped deferred playtime for '%s' at "
                             "shutdown; the counter is already persisted in "
                             "steamrip_meta.json", title)
                    continue
                pending.discard(title)
        finally:
            self._playtime_flush_active = False

    def _after_transaction(self):
        """Flush deferred work once the outermost transaction released the lock."""
        if self._coordinator().is_nested():
            return
        self._flush_notifications()
        self._flush_pending_playtime()

    def persist_mini_cover_on_uninstall(self, title, meta):
        return self._coordinator().run(
            lambda: self._persist_mini_cover_on_uninstall_impl(title, meta))

    # Public save-path API (the rules live in SavePathResolver).
    def save_meta(self, meta):
        if not meta or not meta.get('install_dir'):
            return False
        from core.meta_utils import save_steamrip_meta
        return bool(save_steamrip_meta(meta['install_dir'], meta))

    def set_custom_save_path(self, meta, path):
        return self._get_paths().set_custom_save_path(meta, path)

    def clear_custom_save_path(self, meta):
        return self._get_paths().clear_custom_save_path(meta)

    def prompt_manual_save_path(self, game_title):
        prompt = getattr(self, "_prompt_save_path", None)
        if prompt is None:
            return ""
        try:
            return prompt(game_title) or ""
        except Exception as exc:
            log.exception(
                f"[Savegames] save path prompt failed for '{game_title}': {exc}")
            return ""

    def prepare_save_path_for_game(self, meta):
        """Detection phase: runs under the lock and never prompts."""
        paths = self._get_paths()
        paths._shortcut_inventory(force=False)
        result = self._coordinator().run(
            lambda: paths.prepare_save_path_for_game(meta))
        self._after_transaction()
        return result

    def commit_manual_save_path(self, meta, manual_path, expected_state, title,
                                tracked=True):
        """Commit a prompted save path with revalidation under the lock."""
        paths = self._get_paths()
        result = self._coordinator().run(
            lambda: paths.commit_manual_save_path(
                meta, manual_path, expected_state, title, tracked=tracked))
        self._after_transaction()
        return result

    def ensure_save_path_for_game(self, meta):
        """Synchronous wrapper: only for headless callers and tests.

        The GUI orchestrates the two phases so the modal prompt runs outside
        the coordinator lock (see ``AppWindow.ensure_save_path_async``).
        """
        prepared = self.prepare_save_path_for_game(meta)
        if not prepared.get('needs_prompt'):
            return prepared.get('meta', meta)
        manual_path = self.prompt_manual_save_path(prepared['title'])
        return self.commit_manual_save_path(
            meta, manual_path, prepared['state'], prepared['title'],
            tracked=prepared.get('tracked', False))

    def rescan_save_folder(self, meta):
        paths = self._get_paths()
        # VDF discovery happens before the transaction lock; matching and
        # metadata/path commit happen under it.
        paths._shortcut_inventory(force=True)
        result = self._coordinator().run(
            lambda: paths.rescan_save_folder(
                meta, refresh_identity=True, force_identity=False))
        self._after_transaction()
        return result

    def refresh_steam_identities(self, games=None):
        paths = self._get_paths()
        paths._shortcut_inventory(force=True)
        result = self._coordinator().run(
            lambda: paths.refresh_steam_identities(
                games, force=False, inventory_ready=True))
        self._after_transaction()
        return result

    def get_source_save_path(self, meta):
        return self._get_paths().get_source_save_path(meta)

    def emulator_save_path(self, meta):
        return self._get_paths().emulator_save_path(meta)

    def extract_win_save_path(self, manifest_entry, meta=None):
        return self._get_paths().extract_win_save_path(manifest_entry, meta)

    def choose_manifest_save_path(self, manifest_entry, meta=None):
        return self._get_paths().choose_manifest_save_path(manifest_entry, meta)

    @staticmethod
    def expand_manifest_template(template):
        return SavePathResolver.expand_manifest_template(template)

    def translate_win_path_to_linux(self, win_path, steam_grid_id, account_id=""):
        return self._get_paths().translate_win_path_to_linux(
            win_path, steam_grid_id, account_id)

    def preview_linux_path(self, win_path, title=None):
        return self._get_paths().preview_linux_path(win_path, title)

    def resolve_portable_tokens(self, path, meta=None):
        return self._get_paths().resolve_portable_tokens(path, meta)

    @staticmethod
    def is_linux_path(path):
        return SavePathResolver.is_linux_path(path)

    def normalize_windows_path(self, path):
        return self._get_paths().normalize_windows_path(path)

    def _get_compatdata_base(self, steam_grid_id):
        """Compatdata folder of the game, found on any Steam library."""
        found = save_paths.compatdata_base(steam_grid_id)
        if found:
            return found
        return save_paths.default_compatdata_base(steam_grid_id)

    def _persisted_linux_path(self, meta):
        return self._get_paths()._persisted_linux_path(meta)

    def _linux_path_for_meta(self, meta):
        return self._get_paths()._linux_path_for_meta(meta)

    def _expand_path(self, path):
        return self._get_paths()._expand_path(path)

    def update_manifest_background(self):
        """Lancia il controllo e l'eventuale download in un thread separato (fire-and-forget)."""
        log.info("[Savegames] Avvio controllo Manifest in background...")
        thread = threading.Thread(
            target=lambda: self.manifest_store.ensure(force=True), daemon=True)
        from core.task_registry import TaskRegistry
        registry = TaskRegistry.instance()
        if registry is not None:
            registry.track_thread(thread)
        thread.start()


    def find_manifest_entry(self, game_title):
        return self.manifest_store.find_entry(game_title)


    # Thin aliases kept for existing callers and tests.


    def _notify(self, level, title, message):
        """Queue notices while a transaction lock is held."""
        if self._coordinator().is_active():
            pending = getattr(self, "_pending_notifications", None)
            if pending is None:
                pending = self._pending_notifications = []
            pending.append((level, title, message))
            return
        self._notify_now(level, title, message)

    def _notify_now(self, level, title, message):
        callback = getattr(self, "_notify_callback", None)
        if callback is not None:
            try:
                callback(level, title, message)
                return
            except Exception as exc:
                log.exception(f"[Savegames] notify failed: {exc}")
        log.log(logging.WARNING if level == "warning" else logging.INFO,
                "%s: %s", title, message)

    def _flush_notifications(self):
        pending = getattr(self, "_pending_notifications", None) or []
        self._pending_notifications = []
        for level, title, message in pending:
            self._notify_now(level, title, message)


    
    def get_latest_save_time(self, path):
        """Newest mtime under ``path``, tolerant to concurrent pruning.

        Snapshot directories can be rotated away while a card refresh walks
        them, so a disappearing entry is expected: keep the best value seen so
        far instead of raising. Symlinked directories are not followed, so a
        link loop cannot stall the refresh.
        """
        p = Path(path)
        try:
            if not p.exists():
                return None
            if p.is_file():
                return p.stat().st_mtime
        except OSError as exc:
            log.warning("[Savegames] Could not stat %s: %s", path, exc)
            return None

        latest = 0
        for root, _dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    mtime = os.stat(os.path.join(root, name),
                                    follow_symlinks=False).st_mtime
                except OSError:
                    # A file pruned between listing and stat is not an error.
                    continue
                if mtime > latest:
                    latest = mtime
        return latest if latest > 0 else None

    def _ensure_folder_path(self, path):
        try:
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
        except Exception as exc:
            log.exception(f"[Savegames] Could not create path {path}: {exc}")

    def _snapshot_hash_enabled(self):
        savegames = (self.app_config or {}).get('savegames', {}) or {}
        return bool(savegames.get('snapshot_hash', False))

    def keep_snapshots(self):
        """How many snapshots per game to keep (config, default 5)."""
        savegames = (self.app_config or {}).get('savegames', {}) or {}
        raw = savegames.get('keep_snapshots', DEFAULT_KEEP_SNAPSHOTS)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_KEEP_SNAPSHOTS
        return max(1, value)

    def _trim_backup_folders(self, save_root, keep=None, protected=()):
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
            protected_keys = {
                os.path.normcase(os.path.abspath(str(item)))
                for item in protected or () if item
            }
            for folder in candidates[keep:]:
                if os.path.normcase(os.path.abspath(folder)) in protected_keys:
                    continue
                shutil.rmtree(folder, ignore_errors=True)
        except OSError as exc:
            log.exception(f"[Savegames] Rotation failed in {save_root}: {exc}")

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

    def _copy_source_tolerant(self, source, destination, errors, should_cancel=None):
        """Copy files one by one, collecting failures instead of aborting.

        Returns ``(copied, cancelled)``. Cancellation is only checked at file
        granularity (and once before starting) so a file is never left half
        written by a cancellation. The caller owns the cleanup, because a
        cancelled snapshot build and a cancelled restore need different
        rollbacks.
        """
        def cancelled():
            return bool(should_cancel and should_cancel())

        if cancelled():
            return 0, True
        if os.path.isfile(source) and not os.path.isdir(source):
            try:
                shutil.copy2(source, destination)
                return 1, False
            except Exception as exc:
                errors.append(f"{os.path.basename(source)}: {exc}")
                return 0, False
        if not os.path.isdir(source):
            errors.append(f"Unsupported source: {source}")
            return 0, False

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
                if cancelled():
                    return copied, True
                src_dir = os.path.join(root, name)
                if not os.path.islink(src_dir):
                    continue
                try:
                    self._copy_symlink(src_dir, os.path.join(target_root, name))
                except Exception as exc:
                    errors.append(f"{os.path.relpath(src_dir, source)}: {exc}")
                dirs.remove(name)

            for name in sorted(files, key=str.lower):
                if cancelled():
                    return copied, True
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
        return copied, False

    def _create_snapshot_impl(self, game_root, title, meta, source_path, reason='sync', protected_snapshots=()):
        """Build an atomic snapshot of ``source_path`` under ``game_root/saves``.

        Returns ``{snapshot, created, unchanged, errors}``. Identical content is
        not duplicated; a snapshot that could not be copied at all is discarded.
        """
        result = {'snapshot': '', 'created': False, 'unchanged': False,
                  'cancelled': False, 'errors': []}
        if self._is_cancel_requested():
            # Cooperative shutdown: do not build (and never publish) anything.
            result['cancelled'] = True
            return result
        if not source_path or not os.path.exists(source_path):
            result['errors'].append(f"source not found: {source_path}")
            return result

        save_root = os.path.join(game_root, 'saves')
        self._ensure_folder_path(save_root)
        self._cleanup_stale_tmp(save_root)

        signature = _snapshot_content_signature(
            source_path, content_hash=self._snapshot_hash_enabled())
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
        copied, cancelled = self._copy_source_tolerant(
            source_path, tmp_dir, errors, should_cancel=self._is_cancel_requested)
        if cancelled:
            # No incomplete snapshot is published: remove the temp tree and
            # skip retention and the manifest entirely.
            shutil.rmtree(tmp_dir, ignore_errors=True)
            result['errors'] = errors
            result['cancelled'] = True
            return result
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

        self._trim_backup_folders(
            save_root,
            protected=tuple(protected_snapshots) + (final_dir,),
        )
        result.update({'snapshot': final_dir, 'created': True, 'errors': errors})
        return result

    def ensure_game_assets(self, game_root, title, meta):
        """Best effort: a game root always gets a cover and a manifest."""
        if not game_root:
            return
        try:
            os.makedirs(game_root, exist_ok=True)
        except OSError as exc:
            log.exception(f"[Savegames] Could not create {game_root}: {exc}")
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
                log.exception(f"[Savegames] Mini cover not available for {title}: {exc}")

        if not os.path.isfile(os.path.join(game_root, 'savegames.json')):
            self._touch_manifest(game_root, title, meta)

    def _backup_before_update_impl(self, meta):
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

    def _validate_restore_destination(self, destination, meta):
        """Validate lexical and real paths, including symlinked parents.

        A legitimate symlink is accepted when both its lexical path and its
        resolved target are inside an authorised root. Returns
        ``(ok, detail, effective_path)``; the effective path is the real path
        used for the transaction. Filesystem changes after the last validation
        are a residual TOCTOU window (documented in the audit).
        """
        allowed, exact, protected = self._restore_allowed_targets(meta)
        real_allowed = list(allowed) + [os.path.realpath(p) for p in allowed]
        real_exact = list(exact) + [os.path.realpath(p) for p in exact]
        real_protected = list(protected) + [os.path.realpath(p) for p in protected]
        real = os.path.realpath(destination)

        # Both views are checked against the union: the lexical path keeps a
        # symlinked authorised root valid, the real path rejects a symlink that
        # escapes it.
        for candidate in (destination, real):
            ok, why = save_paths.check_restore_target(
                candidate, real_allowed, real_exact, real_protected)
            if not ok:
                return False, why, destination
        return True, '', real

    @staticmethod
    def _restore_journal_path(root):
        return str(root) + RESTORE_JOURNAL_SUFFIX

    @staticmethod
    def _read_restore_journal(root):
        path = SavegamesManager._restore_journal_path(root)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _write_restore_journal(root, target, tmp, old, phase, snapshot_id=''):
        atomic_write_json(
            SavegamesManager._restore_journal_path(root),
            {
                'schema': RESTORE_JOURNAL_SCHEMA,
                'destination': str(root),
                'target': str(target),
                'tmp': str(tmp),
                'old': str(old),
                'phase': str(phase),
                'snapshot_id': str(snapshot_id or ''),
            },
            separators=(',', ':'),
            fsync=True,
        )

    @staticmethod
    def _clear_restore_journal(root):
        path = SavegamesManager._restore_journal_path(root)
        try:
            os.remove(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            log.warning("[Savegames] Could not remove restore journal %s: %s",
                        path, exc)

    @staticmethod
    def _remove_restore_path(path):
        """Remove a file/link/dir, never following a link to its target."""
        try:
            if os.path.islink(path):
                os.remove(path)
            elif hasattr(os.path, 'isjunction') and os.path.isjunction(path):
                # Remove the junction itself, never the directory it points to.
                os.rmdir(path)
            elif os.path.isfile(path):
                os.remove(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.lexists(path):
                os.remove(path)
        except OSError as exc:
            log.warning("[Savegames] Could not remove restore artifact %s: %s",
                        path, exc)
            return False
        return True

    def _recover_interrupted_restore(self, target, journal_root=None):
        """Recover a restore that died between its renames.

        Returns ``('', '')`` when the target is safe to use, or a stable reason
        and detail. Ambiguous states keep both copies and never delete a path
        that may be the only recoverable one.
        """
        root = journal_root or target
        journal = self._read_restore_journal(root)
        if journal.get('target'):
            recorded = save_paths.normalize_for_compare(journal.get('target'))
            current = save_paths.normalize_for_compare(target)
            if recorded != current:
                return ('recovery_ambiguous',
                        'restore journal belongs to another target')
        tmp = str(target) + RESTORE_TMP_SUFFIX
        old = str(target) + RESTORE_OLD_SUFFIX
        has_journal = os.path.lexists(self._restore_journal_path(root))
        target_exists = os.path.lexists(target)
        old_exists = os.path.lexists(old)
        tmp_exists = os.path.lexists(tmp)

        if not (target_exists or old_exists or tmp_exists or has_journal):
            return '', ''

        # The live path is gone and the original was moved aside: put it back.
        if not target_exists and old_exists:
            try:
                os.replace(old, target)
            except OSError as exc:
                return 'recovery_failed', f'cannot restore the original: {exc}'
            target_exists = True
            old_exists = False

        # Both copies exist. Only a journal that reached "swapped" proves the
        # live path is the new content; otherwise keep both and stop.
        if target_exists and old_exists:
            phase = str(journal.get('phase') or '') if journal else ''
            if phase != 'swapped':
                return ('recovery_ambiguous',
                        'destination and restore_old both exist')
            if not self._remove_restore_path(old):
                return 'recovery_failed', 'cannot remove restore_old'
            old_exists = False

        # No live path: a tmp is the only remaining content; never delete it.
        if not target_exists:
            if tmp_exists:
                return ('recovery_ambiguous',
                        'restore_tmp is the only remaining copy')
            self._clear_restore_journal(root)
            return '', ''

        # The live path exists; a tmp is an incomplete build only when the
        # journal says which phase produced it.
        if tmp_exists:
            phase = str(journal.get('phase') or '') if journal else ''
            if phase not in ('prepared', 'moving_old', 'moved_old', 'swapped'):
                return ('recovery_ambiguous',
                        'restore_tmp exists without a usable journal')
            if not self._remove_restore_path(tmp):
                return 'recovery_failed', 'cannot remove restore_tmp'

        self._clear_restore_journal(root)
        return '', ''

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

    def _list_snapshots_impl(self, title):
        """Snapshots of one game with their metadata, newest first."""
        game_root = os.path.join(self.get_sync_folder(), safe_game_folder_name(title))
        save_root = os.path.join(game_root, 'saves')
        entries = self._snapshot_entries(game_root)
        for entry in entries:
            entry['path'] = os.path.join(save_root, entry['id'])
        entries.reverse()
        return entries

    def _refresh_manifest_after_failed_restore(self, game_root, title, meta, result):
        """Rebuild ``savegames.json`` when a restore fails after a safety snapshot.

        Creating the safety snapshot can prune older folders, so the manifest
        must be rebuilt from disk. ``_freshen_json`` never records a restore and
        preserves ``last_restore_at``; a successful record would be a lie.
        """
        if not result.get('safety_snapshot'):
            return
        self._freshen_json(game_root, title, meta)

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
            log.exception(f"[Savegames] Could not record restore for {title}: {exc}")

    def _restore_snapshot_impl(self, meta, snapshot_id, create_missing=False):
        """Restore a snapshot into the live save folder, safely.

        Recovery of an interrupted previous restore runs before any destructive
        cleanup and before the new safety snapshot. The current state is then
        snapshotted (``pre_restore``), the destination is validated against
        known roots, and the new content is built next to it so a failure can be
        rolled back. A small journal records the transaction phases; when the
        state is ambiguous both copies are kept and the restore refuses.
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
        if self._is_cancel_requested():
            result['reason'] = 'cancelled'
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

        ok, why, effective = self._validate_restore_destination(destination, meta)
        if not ok:
            result['reason'] = 'unsafe_target'
            result['detail'] = why
            return result
        destination = effective
        result['destination'] = destination

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

            ok, why, target = self._validate_restore_destination(target, meta)
            if not ok:
                result['reason'] = 'unsafe_target'
                result['detail'] = why
                return result

            recovery, detail = self._recover_interrupted_restore(
                target, journal_root=destination)
            if recovery:
                result['reason'] = recovery
                result['detail'] = detail
                return result

            # Safe point: nothing has been written yet.
            if self._is_cancel_requested():
                result['reason'] = 'cancelled'
                return result

            tmp_file = target + RESTORE_TMP_SUFFIX
            old_file = target + RESTORE_OLD_SUFFIX
            try:
                self._write_restore_journal(
                    destination, target, tmp_file, old_file, 'prepared', snapshot_id)
            except OSError as exc:
                result['reason'] = 'restore_journal_failed'
                result['errors'] = [str(exc)]
                return result

            # Safe point: the journal exists but the destination is untouched.
            if self._is_cancel_requested():
                self._clear_restore_journal(destination)
                result['reason'] = 'cancelled'
                return result

            if os.path.exists(target):
                # The snapshot being restored must survive retention trimming.
                safety = self.create_snapshot(
                    game_root, title, meta, target, reason='pre_restore',
                    protected_snapshots=(snapshot_dir,))
                result['safety_snapshot'] = (
                    os.path.basename(safety['snapshot']) if safety['snapshot'] else ''
                )
                if safety.get('cancelled'):
                    # The snapshot was skipped for shutdown: the destination is
                    # untouched, so the journal can go too.
                    self._clear_restore_journal(destination)
                    result['reason'] = 'cancelled'
                    return result
                if not (safety['created'] or safety['unchanged']):
                    self._remove_restore_path(tmp_file)
                    self._clear_restore_journal(destination)
                    result['reason'] = 'safety_snapshot_failed'
                    result['errors'] = safety['errors']
                    return result

            ok, why, target = self._validate_restore_destination(target, meta)
            if not ok:
                self._remove_restore_path(tmp_file)
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'unsafe_target'
                result['detail'] = why
                return result

            # Safe point: only the safety snapshot exists, the target is intact.
            if self._is_cancel_requested():
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'cancelled'
                return result

            try:
                shutil.copy2(source_file, tmp_file)
                self._write_restore_journal(
                    destination, target, tmp_file, old_file, 'swapped', snapshot_id)
                ok, why, target = self._validate_restore_destination(target, meta)
                if not ok:
                    raise OSError(f"unsafe restore target: {why}")
                os.replace(tmp_file, target)
            except (OSError, ValueError) as exc:
                self._remove_restore_path(tmp_file)
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'copy_failed'
                result['errors'] = [str(exc)]
                return result

            self._clear_restore_journal(destination)
            self._record_restore(game_root, title, meta, result['safety_snapshot'])
            result.update({'ok': True, 'files': 1})
            return result

        # --- directory snapshot -------------------------------------------
        tmp_dir = destination + RESTORE_TMP_SUFFIX
        old_dir = destination + RESTORE_OLD_SUFFIX

        recovery, detail = self._recover_interrupted_restore(destination)
        if recovery:
            result['reason'] = recovery
            result['detail'] = detail
            return result

        destination_exists = os.path.exists(destination)
        if not destination_exists and not create_missing:
            result['reason'] = 'destination_missing'
            return result
        if destination_exists and os.path.isfile(destination):
            result['reason'] = 'destination_kind_mismatch'
            return result

        if not self._remove_restore_path(tmp_dir):
            result['reason'] = 'copy_failed'
            result['errors'] = ['cannot clear restore_tmp']
            return result
        # Safe point: nothing has been written to the destination yet.
        if self._is_cancel_requested():
            result['reason'] = 'cancelled'
            return result
        self._ensure_folder_path(tmp_dir)
        try:
            self._write_restore_journal(
                destination, destination, tmp_dir, old_dir, 'prepared', snapshot_id)
        except OSError as exc:
            self._remove_restore_path(tmp_dir)
            result['reason'] = 'restore_journal_failed'
            result['errors'] = [str(exc)]
            return result

        # Safe point: the journal exists but the destination is untouched.
        if self._is_cancel_requested():
            self._remove_restore_path(tmp_dir)
            self._clear_restore_journal(destination)
            result['reason'] = 'cancelled'
            return result

        errors = []
        files, cancelled = self._copy_source_tolerant(
            snapshot_dir, tmp_dir, errors,
            should_cancel=self._is_cancel_requested)
        if cancelled:
            # The destination was never moved: drop tmp and the journal.
            self._remove_restore_path(tmp_dir)
            self._clear_restore_journal(destination)
            result['reason'] = 'cancelled'
            result['errors'] = errors
            return result
        try:
            os.remove(os.path.join(tmp_dir, 'snapshot.json'))
            files = max(0, files - 1)
        except OSError:
            pass
        if not files and errors:
            self._remove_restore_path(tmp_dir)
            self._clear_restore_journal(destination)
            result['reason'] = 'copy_failed'
            result['errors'] = errors
            return result

        # Safe point: the new content is staged, the destination is intact.
        if self._is_cancel_requested():
            self._remove_restore_path(tmp_dir)
            self._clear_restore_journal(destination)
            result['reason'] = 'cancelled'
            return result

        # Snapshot the current state before overwriting it.
        if destination_exists:
            # The snapshot being restored must survive retention trimming.
            safety = self.create_snapshot(
                game_root, title, meta, destination, reason='pre_restore',
                protected_snapshots=(snapshot_dir,))
            result['safety_snapshot'] = (
                os.path.basename(safety['snapshot']) if safety['snapshot'] else ''
            )
            if safety.get('cancelled'):
                self._remove_restore_path(tmp_dir)
                self._clear_restore_journal(destination)
                result['reason'] = 'cancelled'
                return result
            if not (safety['created'] or safety['unchanged']):
                self._remove_restore_path(tmp_dir)
                self._clear_restore_journal(destination)
                result['reason'] = 'safety_snapshot_failed'
                result['errors'] = safety['errors']
                return result

            ok, why, effective = self._validate_restore_destination(destination, meta)
            if not ok:
                self._remove_restore_path(tmp_dir)
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'unsafe_target'
                result['detail'] = why
                return result
            destination = effective
            result['destination'] = destination

            # Last safe point: after this the destination starts moving and
            # only rollback may clear the journal.
            if self._is_cancel_requested():
                self._remove_restore_path(tmp_dir)
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'cancelled'
                return result

            try:
                self._write_restore_journal(
                    destination, destination, tmp_dir, old_dir, 'moving_old',
                    snapshot_id)
                os.replace(destination, old_dir)
            except OSError as exc:
                self._remove_restore_path(tmp_dir)
                self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'swap_failed'
                result['errors'] = [str(exc)]
                return result

            try:
                self._write_restore_journal(
                    destination, destination, tmp_dir, old_dir, 'moved_old',
                    snapshot_id)
            except OSError as exc:
                rolled_back = False
                try:
                    os.replace(old_dir, destination)
                    rolled_back = True
                except OSError:
                    pass
                self._remove_restore_path(tmp_dir)
                if rolled_back:
                    self._clear_restore_journal(destination)
                self._refresh_manifest_after_failed_restore(
                    game_root, title, meta, result)
                result['reason'] = 'restore_journal_failed'
                result['errors'] = [str(exc)]
                return result

        try:
            self._write_restore_journal(
                destination, destination, tmp_dir, old_dir, 'swapped', snapshot_id)
        except OSError as exc:
            rolled_back = False
            if destination_exists:
                try:
                    os.replace(old_dir, destination)
                    rolled_back = True
                except OSError:
                    pass
            self._remove_restore_path(tmp_dir)
            if rolled_back or not destination_exists:
                self._clear_restore_journal(destination)
            self._refresh_manifest_after_failed_restore(
                game_root, title, meta, result)
            result['reason'] = 'restore_journal_failed'
            result['errors'] = [str(exc)]
            return result

        ok, why, effective = self._validate_restore_destination(destination, meta)
        if not ok:
            rolled_back = False
            if destination_exists:
                try:
                    os.replace(old_dir, destination)
                    rolled_back = True
                except OSError:
                    pass
            self._remove_restore_path(tmp_dir)
            if rolled_back or not destination_exists:
                self._clear_restore_journal(destination)
            self._refresh_manifest_after_failed_restore(
                game_root, title, meta, result)
            result['reason'] = 'unsafe_target'
            result['detail'] = why
            return result
        destination = effective

        try:
            os.replace(tmp_dir, destination)
        except OSError as exc:
            rolled_back = False
            if destination_exists:
                try:
                    os.replace(old_dir, destination)
                    rolled_back = True
                except OSError:
                    pass
            self._remove_restore_path(tmp_dir)
            if rolled_back or not destination_exists:
                self._clear_restore_journal(destination)
            self._refresh_manifest_after_failed_restore(
                game_root, title, meta, result)
            result['reason'] = 'swap_failed'
            result['errors'] = [str(exc)]
            return result

        if destination_exists:
            if self._remove_restore_path(old_dir):
                self._clear_restore_journal(destination)
        else:
            self._clear_restore_journal(destination)
        self._record_restore(game_root, title, meta, result['safety_snapshot'])
        result.update({'ok': True, 'files': files, 'errors': errors})
        return result

    # -------------------------------------------------------------- mirror

    def ensure_mirror_import(self):
        result = self._coordinator().run(self._ensure_mirror_import_impl)
        self._after_transaction()
        return result

    def push_mirror(self):
        result = self._coordinator().run(self._push_mirror_impl)
        self._after_transaction()
        return result

    def compare_mirror(self):
        result = self._coordinator().run(self._compare_mirror_impl)
        self._after_transaction()
        return result

    def test_mirror(self, folder=None):
        return self._coordinator().run(
            lambda: self._test_mirror_impl(folder))

    def try_ensure_mirror_import(self, timeout_ms=0):
        try:
            return {'status': 'completed',
                    'report': self._coordinator().run(
                        self._ensure_mirror_import_impl,
                        timeout_ms=timeout_ms)}
        except OperationBusy:
            return {'status': 'busy', 'report': {}}
        except OperationCancelled:
            return {'status': 'cancelled', 'report': {}}
        except Exception as exc:
            return {'status': 'failed', 'detail': str(exc), 'report': {}}

    def try_compare_mirror(self, timeout_ms=0):
        try:
            return {'status': 'completed',
                    'entries': self._coordinator().run(
                        self._compare_mirror_impl,
                        timeout_ms=timeout_ms)}
        except OperationBusy:
            return {'status': 'busy', 'entries': []}
        except OperationCancelled:
            return {'status': 'cancelled', 'entries': []}
        except Exception as exc:
            return {'status': 'failed', 'detail': str(exc), 'entries': []}

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

    def _ensure_mirror_import_impl(self):
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

    def _push_mirror_impl(self):
        """Copy the local store to the configured mirror (incremental)."""
        mirror = self.get_mirror_folder()
        report = {'configured': bool(mirror), 'copied': 0, 'skipped': 0,
                  'cancelled': False, 'errors': []}
        if not mirror:
            return report
        if self._is_cancel_requested():
            # Shutdown: do not start a new mirror pass (and never write the
            # manifest for a partial one).
            report['cancelled'] = True
            return report

        self.ensure_mirror_import()

        from managers.mirror_backend import FolderMirrorBackend
        backend = FolderMirrorBackend(self.get_sync_folder(), mirror)
        report = backend.push(
            exclude_names=(DEVICE_ID_FILENAME, MIRROR_IMPORT_MARKER),
            should_cancel=self._is_cancel_requested,
        )
        if not report.get('errors') and not report.get('cancelled'):
            # The manifest describes what is actually available on the mirror;
            # after a partial/failed transfer the previous valid manifest stays.
            try:
                backend.write_manifest(self._device_id(self.get_sync_folder()))
            except Exception as exc:
                report['errors'].append(f"manifest: {exc}")
        return report

    def _compare_mirror_impl(self):
        """Per-game local/mirror states (empty list without a mirror)."""
        mirror = self.get_mirror_folder()
        if not mirror:
            return []
        from managers.mirror_backend import FolderMirrorBackend
        return FolderMirrorBackend(self.get_sync_folder(), mirror).compare()

    def _test_mirror_impl(self, folder=None):
        from managers.mirror_backend import FolderMirrorBackend
        return FolderMirrorBackend(
            self.get_sync_folder(), folder or self.get_mirror_folder()
        ).test()

    def _sync_all_games_impl(self):
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            return {'synced_games': 0, 'unchanged_games': 0,
                    'cancelled': False,
                    'errors': ['No local backup folder configured.']}

        self._ensure_folder_path(sync_folder)
        # Bring in backups that only exist in the mirror (one-time, non-destructive).
        self.ensure_mirror_import()
        created_games = 0
        unchanged_games = 0
        cancelled = False
        errors = []

        # Copy the mapping: a background library scan may update it while the
        # worker runs.
        for title, meta in list(self.installed_games.items()):
            # Cancellation is only honoured BETWEEN games, and only after the
            # previous game's manifest/assets update completed: stopping after
            # the snapshot but before the manifest would hide a real snapshot.
            if self._is_cancel_requested():
                cancelled = True
                break
            if meta.get('is_orphan'):
                continue

            source_path = self.get_source_save_path(meta)
            if not source_path:
                continue

            source_path = self._expand_path(source_path)

            if source_path.startswith('%') and os.name != 'nt':
                source_path = self.translate_win_path_to_linux(
                    source_path, meta.get('steam_grid_id', ''),
                    account_id=self._local_steam_account_id(meta),
                )

            safe_title = safe_game_folder_name(title)
            game_root = os.path.join(sync_folder, safe_title)

            if not os.path.exists(source_path):
                log.info(f"[Savegames] Salto {title}: La cartella di salvataggio non esiste ancora in -> {source_path}")
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

        if cancelled:
            log.info("[Savegames] Sync cancelled between games: %d game(s) processed",
                     created_games + unchanged_games)
            return {'synced_games': created_games,
                    'unchanged_games': unchanged_games,
                    'cancelled': True,
                    'errors': errors,
                    'mirror': {}}

        mirror_report = self.push_mirror()
        for error in mirror_report.get('errors') or []:
            errors.append(f"mirror: {error}")

        return {'synced_games': created_games,
                'unchanged_games': unchanged_games,
                'cancelled': False,
                'errors': errors,
                'mirror': mirror_report}


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

        # ``snapshots`` comes from the filesystem. ``latest_snapshot`` must
        # point to a folder that still exists, not to a pruned/removed one.
        valid_snapshots = {'saves/' + entry['id'] for entry in entries}
        latest = ''
        if snapshot_folder:
            candidate = 'saves/' + os.path.basename(os.path.normpath(snapshot_folder))
            if candidate in valid_snapshots:
                latest = candidate
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
            log.exception(f"[Savegames] Could not freshen JSON for {title}: {exc}")

    def _touch_manifest(self, game_root, title, meta):
        """Rewrite the manifest without touching ``last_synced``/``source_path``."""
        try:
            old = self.read_game_manifest(game_root)
            metadata = self._build_game_manifest(game_root, title, meta, old=old)
            self._write_game_manifest_file(game_root, metadata)
        except Exception as exc:
            log.exception(f"[Savegames] Could not update manifest for {title}: {exc}")

    def _backup_on_demand_impl(self, meta):
        """Esegue un backup istantaneo per un singolo gioco (chiamato alla chiusura)."""
        sync_folder = self.get_sync_folder()
        if not sync_folder:
            log.info("[Savegames] Backup on-demand saltato: cartella di sync non configurata.")
            return False

        title = meta.get('title', 'Unknown')
        source_path = self._expand_path(self.get_source_save_path(meta))

        if not source_path or not os.path.exists(source_path):
            log.info(f"[Savegames] Backup on-demand fallito: sorgente non trovata per {title}")
            return False

        log.info(f"[Savegames] Esecuzione backup on-demand per {title}...")

        game_root = os.path.join(sync_folder, safe_game_folder_name(title))
        result = self.create_snapshot(game_root, title, meta, source_path, reason='on_demand')

        for error in result['errors']:
            log.warning(f"[Savegames] Backup on-demand {title}: {error}")

        if not (result['created'] or result['unchanged']):
            return False

        self._freshen_json(game_root, title, meta, source_path, result['snapshot'] or None)
        self.ensure_game_assets(game_root, title, meta)
        mirror_report = self.push_mirror()
        for error in mirror_report.get('errors') or []:
            log.warning(f"[Savegames] Mirror {title}: {error}")
        state = "unchanged" if result['unchanged'] and not result['created'] else "created"
        log.info(f"[Savegames] Backup completato per {title} ({state}).")
        return True

    def _persist_mini_cover_on_uninstall_impl(self, title, meta):
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

    def _ensure_playtime_stub_impl(self, title, meta):
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

    def _ensure_playtime_stubs_impl(self):
        for title, meta in self.installed_games.items():
            try:
                self.ensure_playtime_stub(title, meta)
            except Exception as exc:
                log.exception(f"[Savegames] Errore stub playtime per '{title}': {exc}")
