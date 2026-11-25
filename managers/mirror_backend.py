"""Folder mirror for the savegame backup store (plans/backup.md, phase 4).

The local store (``Staging``) is always the source of truth. The mirror is a
second copy in another folder (external drive, a Google Drive Desktop folder,
...) that is only ever written *from* the local store and never deletes
anything: an interrupted or misconfigured mirror cannot damage the backups.

A snapshot is PUBLISHED on the mirror only when its directory exists there,
``snapshot.json`` is present and not marked incomplete, and (for an id that
also exists locally) the relative file set, sizes and symlink targets match.
Known limits: the same snapshot id with different content on two devices
overwrites the mirror copy, and verification compares sizes, not content
hashes, so a same-size change on the mirror is not detected.
"""
from __future__ import annotations

import json
import os
import shutil
import time

from core.atomic_io import atomic_write_json

MIRROR_SCHEMA = 1
MIRROR_MANIFEST = "backup_manifest.json"
MIRROR_EXCLUDED_ROOT_FILES = ("backup_manifest.json",)
# Hidden directories that are part of the backup (launcher state).
MIRROR_ALLOWED_HIDDEN_DIRS = (".launcher_state",)

STATE_LOCAL_ONLY = "local_only"
STATE_MIRROR_ONLY = "mirror_only"
STATE_SYNCED = "synced"
STATE_LOCAL_NEWER = "local_newer"
STATE_MIRROR_NEWER = "mirror_newer"
STATE_CONFLICT = "conflict"


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _game_roots(root):
    try:
        names = os.listdir(root)
    except OSError:
        return []
    result = []
    for name in sorted(names, key=str.lower):
        if name.startswith(".") or name.endswith(".tmp"):
            continue
        if os.path.isdir(os.path.join(root, name)):
            result.append(name)
    return result


def _manifest(root, safe_title):
    return _read_json(os.path.join(root, safe_title, "savegames.json"))


def _snapshot_dirs(root, safe_title):
    """Real snapshot directories (legacy snapshots without metadata included)."""
    saves = os.path.join(root, safe_title, "saves")
    try:
        names = os.listdir(saves)
    except OSError:
        return []
    return sorted(
        name for name in names
        if not name.startswith(".") and not name.endswith(".tmp")
        and os.path.isdir(os.path.join(saves, name))
    )


def _snapshot_info(snapshot_dir):
    return _read_json(os.path.join(snapshot_dir, "snapshot.json"))


def _snapshot_complete(snapshot_dir):
    """A snapshot is publishable only with metadata saying it is complete.

    Legacy local snapshots without ``snapshot.json`` stay visible in the local
    store but are never published: the mirror only advertises verified
    snapshots, and new snapshots always carry the metadata.
    """
    info = _snapshot_info(snapshot_dir)
    return bool(info) and info.get("complete", True) is not False


def _file_manifest(root):
    """Relative path -> ('link', target) | ('file', size); None on OSError.

    Verification compares the file set and sizes, not content hashes (see the
    known limits in the module docstring).
    """
    entries = {}
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            rel_root = os.path.relpath(dirpath, root)
            for name in list(dirnames):
                full = os.path.join(dirpath, name)
                if not os.path.islink(full):
                    continue
                rel = name if rel_root == "." else os.path.join(rel_root, name)
                entries[rel] = ("link", os.readlink(full))
                dirnames.remove(name)
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = name if rel_root == "." else os.path.join(rel_root, name)
                if os.path.islink(full):
                    entries[rel] = ("link", os.readlink(full))
                else:
                    entries[rel] = ("file", os.path.getsize(full))
    except OSError:
        return None
    return entries


def _same_snapshot(local_dir, mirror_dir):
    local = _file_manifest(local_dir)
    mirror = _file_manifest(mirror_dir)
    return local is not None and local == mirror


def _published_ids(local_root, mirror_root, safe_title):
    """Mirror snapshot ids that are actually published.

    A snapshot is published only if its directory exists on the mirror,
    ``snapshot.json`` is present and complete, and — when the same id exists
    locally — the relative file set, sizes and symlink targets match. Ids are
    never taken from a manifest: a partial push must not be advertised.

    Known limitation: a legacy local snapshot without ``snapshot.json`` is
    never published (the mirror cannot verify it), so old games may stay
    local-only until a new snapshot is written.
    """
    ids = []
    for snapshot_id in _snapshot_dirs(mirror_root, safe_title):
        mirror_dir = os.path.join(mirror_root, safe_title, "saves", snapshot_id)
        if not _snapshot_complete(mirror_dir):
            continue
        local_dir = os.path.join(local_root, safe_title, "saves", snapshot_id)
        if os.path.isdir(local_dir) and not _same_snapshot(local_dir, mirror_dir):
            continue
        ids.append(snapshot_id)
    return sorted(ids)


def _latest_snapshot(ids):
    return ids[-1] if ids else ""


def _snapshot_version(root, safe_title, latest):
    if not latest:
        return {}
    data = _read_json(os.path.join(
        root, safe_title, "saves", latest, "snapshot.json"))
    version = data.get("version")
    return version if isinstance(version, dict) else {}


def _game_version(root, safe_title, manifest, latest):
    version = _snapshot_version(root, safe_title, latest)
    if version:
        return version
    info = manifest.get("game_version")
    return info if isinstance(info, dict) else {}


class FolderMirrorBackend:
    def __init__(self, local_root, mirror_root):
        self.local_root = str(local_root or "").strip()
        self.mirror_root = str(mirror_root or "").strip()

    # ------------------------------------------------------------- status

    def is_configured(self) -> bool:
        return bool(self.local_root and self.mirror_root)

    def _overlaps(self) -> bool:
        """True when the mirror and the local store are the same tree."""
        if not self.is_configured():
            return False
        local = os.path.normcase(os.path.abspath(self.local_root))
        mirror = os.path.normcase(os.path.abspath(self.mirror_root))
        if local == mirror:
            return True
        return (mirror.startswith(local + os.sep)
                or local.startswith(mirror + os.sep))

    def test(self):
        """Check that the mirror can be created and written."""
        if not self.is_configured():
            return False, "Mirror folder is not configured."
        if self._overlaps():
            return False, "The mirror folder must be outside the local backup folder."
        try:
            os.makedirs(self.mirror_root, exist_ok=True)
            probe = os.path.join(self.mirror_root, ".aethicc_write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as exc:
            return False, str(exc)
        return True, ""

    # --------------------------------------------------------------- push

    def push(self, exclude_names=(), should_cancel=None):
        """Copy new/changed files from local to mirror. Never deletes.

        ``should_cancel`` is polled between files: every file is copied
        atomically, so stopping between two files leaves the mirror consistent.
        Nothing is ever deleted, and the caller must not write the mirror
        manifest when ``report['cancelled']`` is true.
        """
        report = {"configured": True, "copied": 0, "skipped": 0,
                  "cancelled": False, "errors": []}
        if not self.is_configured():
            report["configured"] = False
            return report
        if self._overlaps():
            report["errors"].append("Mirror folder overlaps the local backup folder.")
            return report
        if not os.path.isdir(self.local_root):
            report["errors"].append("Local backup folder does not exist.")
            return report
        if should_cancel is not None and should_cancel():
            report["cancelled"] = True
            return report

        exclude = set(exclude_names or ()) | set(MIRROR_EXCLUDED_ROOT_FILES)
        incomplete_cache = {}

        def is_incomplete(rel_root):
            parts = rel_root.split(os.sep) if rel_root != "." else []
            if len(parts) < 3 or parts[1] != "saves":
                return False
            game = parts[0]
            snapshot_id = parts[2]
            if game not in incomplete_cache:
                incomplete_cache[game] = {
                    name for name in _snapshot_dirs(self.local_root, game)
                    if not _snapshot_complete(os.path.join(
                        self.local_root, game, "saves", name))
                }
            return snapshot_id in incomplete_cache[game]

        for root, dirs, files in os.walk(self.local_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.endswith(".tmp")
                and (not name.startswith(".") or name in MIRROR_ALLOWED_HIDDEN_DIRS)
            )
            rel_root = os.path.relpath(root, self.local_root)
            if is_incomplete(rel_root):
                # Never publish an incomplete snapshot.
                dirs[:] = []
                continue
            target_root = (self.mirror_root if rel_root == "."
                           else os.path.join(self.mirror_root, rel_root))
            try:
                os.makedirs(target_root, exist_ok=True)
            except OSError as exc:
                report["errors"].append(f"{rel_root}: {exc}")
                continue

            for name in sorted(files, key=str.lower):
                if should_cancel is not None and should_cancel():
                    report["cancelled"] = True
                    return report
                if name.endswith(".tmp"):
                    continue
                if rel_root == "." and (name.startswith(".") or name in exclude):
                    continue
                source = os.path.join(root, name)
                target = os.path.join(target_root, name)
                try:
                    if self._needs_copy(source, target):
                        self._copy_atomic(source, target)
                        report["copied"] += 1
                    else:
                        report["skipped"] += 1
                except Exception as exc:
                    report["errors"].append(f"{os.path.join(rel_root, name)}: {exc}")
        return report

    # --------------------------------------------------------------- pull

    def pull_missing(self, exclude_names=()):
        """Copy files that exist only in the mirror. Never overwrites."""
        report = {"configured": True, "copied": 0, "skipped": 0, "errors": []}
        if not self.is_configured():
            report["configured"] = False
            return report
        if self._overlaps():
            report["errors"].append("Mirror folder overlaps the local backup folder.")
            return report
        if not os.path.isdir(self.mirror_root):
            report["errors"].append("Mirror folder does not exist.")
            return report

        exclude = set(exclude_names or ()) | set(MIRROR_EXCLUDED_ROOT_FILES)
        published_cache = {}

        def is_unpublished(rel_root):
            parts = rel_root.split(os.sep) if rel_root != "." else []
            if len(parts) < 3 or parts[1] != "saves":
                return False
            game = parts[0]
            snapshot_id = parts[2]
            if game not in published_cache:
                published_cache[game] = set(
                    _published_ids(self.local_root, self.mirror_root, game))
            return snapshot_id not in published_cache[game]

        for root, dirs, files in os.walk(self.mirror_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.endswith(".tmp")
                and (not name.startswith(".") or name in MIRROR_ALLOWED_HIDDEN_DIRS)
            )
            rel_root = os.path.relpath(root, self.mirror_root)
            if is_unpublished(rel_root):
                # An incomplete or partial mirror snapshot is never imported.
                dirs[:] = []
                continue
            target_root = (self.local_root if rel_root == "."
                           else os.path.join(self.local_root, rel_root))
            try:
                os.makedirs(target_root, exist_ok=True)
            except OSError as exc:
                report["errors"].append(f"{rel_root}: {exc}")
                continue

            for name in sorted(files, key=str.lower):
                if name.endswith(".tmp"):
                    continue
                if rel_root == "." and (name.startswith(".") or name in exclude):
                    continue
                source = os.path.join(root, name)
                target = os.path.join(target_root, name)
                if os.path.lexists(target):
                    report["skipped"] += 1
                    continue
                try:
                    self._copy_atomic(source, target)
                    report["copied"] += 1
                except Exception as exc:
                    report["errors"].append(f"{os.path.join(rel_root, name)}: {exc}")
        return report

    # ------------------------------------------------------------ compare

    def compare(self):
        """Per-game local/mirror state, based on manifests and UTC ids."""
        local_games = set(_game_roots(self.local_root))
        mirror_games = set(_game_roots(self.mirror_root))

        entries = []
        for safe_title in sorted(local_games | mirror_games, key=str.lower):
            local_manifest = _manifest(self.local_root, safe_title)
            mirror_manifest = _manifest(self.mirror_root, safe_title)
            local_ids = _snapshot_dirs(self.local_root, safe_title)
            mirror_ids = _published_ids(
                self.local_root, self.mirror_root, safe_title)
            local_latest = _latest_snapshot(local_ids)
            mirror_latest = _latest_snapshot(mirror_ids)

            if safe_title not in local_games:
                state = STATE_MIRROR_ONLY
            elif safe_title not in mirror_games:
                state = STATE_LOCAL_ONLY
            elif local_latest and mirror_latest:
                if local_latest == mirror_latest:
                    state = STATE_SYNCED
                elif mirror_latest in local_ids:
                    state = STATE_LOCAL_NEWER
                elif local_latest in mirror_ids:
                    state = STATE_MIRROR_NEWER
                else:
                    state = STATE_CONFLICT
            elif local_latest:
                state = STATE_LOCAL_NEWER
            elif mirror_latest:
                state = STATE_MIRROR_NEWER
            else:
                state = STATE_SYNCED

            entries.append({
                "safe_title": safe_title,
                "title": (local_manifest.get("game_title")
                          or mirror_manifest.get("game_title")
                          or safe_title),
                "state": state,
                "local_latest": local_latest,
                "mirror_latest": mirror_latest,
                "local_version": _game_version(
                    self.local_root, safe_title, local_manifest, local_latest),
                "mirror_version": _game_version(
                    self.mirror_root, safe_title, mirror_manifest, mirror_latest),
                "local_updated_at": str(local_manifest.get("last_synced") or ""),
                "mirror_updated_at": str(mirror_manifest.get("last_synced") or ""),
            })
        return entries

    # ----------------------------------------------------------- manifest

    def write_manifest(self, device_id=""):
        """Describe what the mirror actually contains (informational).

        Built from the mirror tree: only published snapshots are listed, and a
        game with no published snapshot is omitted rather than advertised from
        a stale local manifest.
        """
        games = {}
        for safe_title in _game_roots(self.mirror_root):
            ids = _published_ids(self.local_root, self.mirror_root, safe_title)
            if not ids:
                continue
            manifest = _manifest(self.mirror_root, safe_title)
            latest = _latest_snapshot(ids)
            games[safe_title] = {
                "game_title": manifest.get("game_title") or safe_title,
                "latest_snapshot": latest,
                "last_synced": str(manifest.get("last_synced") or ""),
                "game_version": _game_version(
                    self.mirror_root, safe_title, manifest, latest),
            }

        payload = {
            "schema": MIRROR_SCHEMA,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "device_id": str(device_id or ""),
            "games": games,
        }
        atomic_write_json(os.path.join(self.mirror_root, MIRROR_MANIFEST),
                          payload, indent=4)
        return payload

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _needs_copy(source, target):
        if os.path.islink(source):
            if not os.path.islink(target):
                return True
            return os.readlink(source) != os.readlink(target)
        if not os.path.isfile(target):
            return True
        try:
            src = os.stat(source)
            dst = os.stat(target)
        except OSError:
            return True
        return src.st_size != dst.st_size or abs(src.st_mtime - dst.st_mtime) > 0.001

    @staticmethod
    def _copy_atomic(source, target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.islink(source):
            if os.path.lexists(target):
                os.remove(target)
            os.symlink(os.readlink(source), target)
            return
        tmp = target + ".tmp"
        shutil.copy2(source, tmp)
        os.replace(tmp, target)
