"""Folder mirror for the savegame backup store (plans/backup.md, phase 4).

The local store (``Staging``) is always the source of truth. The mirror is a
second copy in another folder (external drive, a Google Drive Desktop folder,
...) that is only ever written *from* the local store and never deletes
anything: an interrupted or misconfigured mirror cannot damage the backups.
"""
from __future__ import annotations

import json
import os
import shutil
import time

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


def _write_json_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


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


def _snapshot_ids(root, safe_title, manifest):
    ids = []
    for entry in manifest.get("snapshots") or []:
        if isinstance(entry, dict) and entry.get("id"):
            ids.append(str(entry["id"]))
    if ids:
        return sorted(ids)

    saves = os.path.join(root, safe_title, "saves")
    try:
        return sorted(
            name for name in os.listdir(saves)
            if not name.startswith(".") and not name.endswith(".tmp")
            and os.path.isdir(os.path.join(saves, name))
        )
    except OSError:
        return []


def _latest_snapshot(root, safe_title, manifest, ids):
    latest = str(manifest.get("latest_snapshot") or "").strip()
    if latest.startswith("saves/"):
        latest = latest[len("saves/"):]
    if latest:
        return latest
    return ids[-1] if ids else ""


def _game_version(root, safe_title, manifest, latest):
    info = manifest.get("game_version")
    if isinstance(info, dict) and (info.get("build") or info.get("label")):
        return info
    if not latest:
        return {}
    data = _read_json(os.path.join(root, safe_title, "saves", latest, "snapshot.json"))
    version = data.get("version")
    return version if isinstance(version, dict) else {}


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

    def push(self, exclude_names=()):
        """Copy new/changed files from local to mirror. Never deletes."""
        report = {"configured": True, "copied": 0, "skipped": 0, "errors": []}
        if not self.is_configured():
            report["configured"] = False
            return report
        if self._overlaps():
            report["errors"].append("Mirror folder overlaps the local backup folder.")
            return report
        if not os.path.isdir(self.local_root):
            report["errors"].append("Local backup folder does not exist.")
            return report

        exclude = set(exclude_names or ()) | set(MIRROR_EXCLUDED_ROOT_FILES)
        for root, dirs, files in os.walk(self.local_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.endswith(".tmp")
                and (not name.startswith(".") or name in MIRROR_ALLOWED_HIDDEN_DIRS)
            )
            rel_root = os.path.relpath(root, self.local_root)
            target_root = (self.mirror_root if rel_root == "."
                           else os.path.join(self.mirror_root, rel_root))
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
        for root, dirs, files in os.walk(self.mirror_root):
            dirs[:] = sorted(
                name for name in dirs
                if not name.endswith(".tmp")
                and (not name.startswith(".") or name in MIRROR_ALLOWED_HIDDEN_DIRS)
            )
            rel_root = os.path.relpath(root, self.mirror_root)
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
            local_ids = _snapshot_ids(self.local_root, safe_title, local_manifest)
            mirror_ids = _snapshot_ids(self.mirror_root, safe_title, mirror_manifest)
            local_latest = _latest_snapshot(
                self.local_root, safe_title, local_manifest, local_ids)
            mirror_latest = _latest_snapshot(
                self.mirror_root, safe_title, mirror_manifest, mirror_ids)

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
        """Describe what the mirror currently contains (informational)."""
        games = {}
        for safe_title in _game_roots(self.local_root):
            manifest = _manifest(self.local_root, safe_title)
            ids = _snapshot_ids(self.local_root, safe_title, manifest)
            latest = _latest_snapshot(self.local_root, safe_title, manifest, ids)
            games[safe_title] = {
                "game_title": manifest.get("game_title") or safe_title,
                "latest_snapshot": latest,
                "last_synced": str(manifest.get("last_synced") or ""),
                "game_version": _game_version(
                    self.local_root, safe_title, manifest, latest),
            }

        payload = {
            "schema": MIRROR_SCHEMA,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "device_id": str(device_id or ""),
            "games": games,
        }
        _write_json_atomic(os.path.join(self.mirror_root, MIRROR_MANIFEST), payload)
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
