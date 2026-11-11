"""Backup and merge for launcher-owned state (``plan.md`` §7).

Only user state is backed up: sessions, install/usage/price history,
achievements and Stats preferences. Caches, the Master Catalog, config.json and
credentials never enter the backup.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from core.jsonl_store import JsonlStore
from core.paths import PathManager

JSONL_FILES = (
    "play_sessions.jsonl",
    "install_history.jsonl",
    "usage_events.jsonl",
    "price_history.jsonl",
)
JSON_FILES = (
    "achievements.json",
    "stats_prefs.json",
)
STATE_FILES = JSONL_FILES + JSON_FILES

BACKUP_ROOT = ".launcher_state"
CURRENT_DIR = "current"
SNAPSHOTS_DIR = "snapshots"
MANIFEST_FILE = "manifest.json"


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError, TypeError):
        return default
    return value if value is not None else default


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class StateBackupManager:
    def __init__(self, state_dir=None, app_data_dir=None, sync_folder=None,
                 keep_snapshots: int = 5):
        self.state_dir = Path(state_dir) if state_dir is not None else PathManager.get_stats_dir()
        self.app_data_dir = (Path(app_data_dir) if app_data_dir is not None
                             else PathManager.get_app_data_dir())
        self.sync_folder = Path(sync_folder) if sync_folder else None
        self.keep_snapshots = max(1, int(keep_snapshots))

    # ------------------------------------------------------------------ paths

    @property
    def root(self) -> Path:
        if self.sync_folder is None:
            raise RuntimeError("State backups need a sync folder")
        return self.sync_folder / BACKUP_ROOT

    @property
    def current_dir(self) -> Path:
        return self.root / CURRENT_DIR

    @property
    def snapshots_dir(self) -> Path:
        return self.root / SNAPSHOTS_DIR

    def _source_path(self, name: str) -> Path:
        if name in JSONL_FILES:
            return self.state_dir / name
        if name == "achievements.json":
            return self.app_data_dir / name
        return self.state_dir / name

    # --------------------------------------------------------------- enabled

    @classmethod
    def enabled_for(cls, config: dict) -> bool:
        savegames = (config or {}).get("savegames", {}) or {}
        sync_folder = str(savegames.get("sync_folder") or "").strip()
        toggle = savegames.get("include_launcher_state", True)
        return bool(sync_folder) and bool(toggle)

    @classmethod
    def from_config(cls, config: dict, **kwargs):
        savegames = (config or {}).get("savegames", {}) or {}
        return cls(sync_folder=str(savegames.get("sync_folder") or "").strip() or None, **kwargs)

    # --------------------------------------------------------------- manifest

    def _manifest(self, reset_at: int = 0) -> dict:
        previous = _read_json(self.root / MANIFEST_FILE, {})
        device_id = previous.get("device_id") if isinstance(previous, dict) else None
        if not device_id:
            device_id = uuid.uuid4().hex
        return {
            "schema": 1,
            "device_id": device_id,
            "updated_at": int(time.time()),
            "reset_at": int(reset_at or (previous.get("reset_at", 0) if isinstance(previous, dict) else 0)),
        }

    # ---------------------------------------------------------------- write

    def write_current(self) -> int:
        """Mirror the source state into ``current/`` atomically."""
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        tmp = root / (CURRENT_DIR + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)

        copied = 0
        for name in STATE_FILES:
            source = self._source_path(name)
            if not source.exists():
                continue
            shutil.copy2(source, tmp / name)
            copied += 1

        _write_json_atomic(tmp / MANIFEST_FILE, self._manifest())

        previous = root / CURRENT_DIR
        shutil.rmtree(previous, ignore_errors=True)
        os.replace(tmp, previous)
        return copied

    def snapshot(self, now=None, reset_at: int = 0) -> Path | None:
        """Copy ``current/`` into a timestamped snapshot and rotate old ones."""
        self.write_current()
        if self.sync_folder is None:
            return None
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.gmtime(int(now or time.time())))
        target = self.snapshots_dir / stamp
        suffix = 1
        while target.exists():
            target = self.snapshots_dir / f"{stamp}_{suffix}"
            suffix += 1
        shutil.copytree(self.current_dir, target)

        snapshots = sorted(
            (p for p in self.snapshots_dir.iterdir() if p.is_dir()),
            key=lambda p: p.name,
        )
        for old in snapshots[:-self.keep_snapshots]:
            shutil.rmtree(old, ignore_errors=True)
        return target

    def backup_now(self, now=None) -> Path | None:
        if self.sync_folder is None:
            return None
        return self.snapshot(now=now)

    # ---------------------------------------------------------------- restore

    def restore_merge(self) -> dict:
        """Merge ``current/`` into local state. Local data wins on conflicts."""
        source = self.current_dir
        if not source.is_dir():
            return {"files": 0, "jsonl_added": 0, "achievements_added": 0}

        merged_files = 0
        added = 0
        for name in JSONL_FILES:
            remote = source / name
            if not remote.exists():
                continue
            added += self._merge_jsonl(self._source_path(name), remote)
            merged_files += 1

        achievements_added = self._merge_achievements(
            self._source_path("achievements.json"),
            source / "achievements.json",
        )
        if (source / "achievements.json").exists():
            merged_files += 1

        self._merge_prefs(self._source_path("stats_prefs.json"), source / "stats_prefs.json")
        if (source / "stats_prefs.json").exists():
            merged_files += 1

        return {
            "files": merged_files,
            "jsonl_added": added,
            "achievements_added": achievements_added,
        }

    @staticmethod
    def _merge_jsonl(local_path: Path, remote_path: Path) -> int:
        remote_records = JsonlStore(remote_path).load()
        if not remote_records:
            return 0

        local_store = JsonlStore(local_path)
        local_records = local_store.load()
        by_id = {r.get("id"): r for r in local_records if r.get("id")}

        added = 0
        changed = False
        for remote in remote_records:
            record_id = remote.get("id")
            if not record_id:
                local_records.append(remote)
                added += 1
                changed = True
                continue
            local = by_id.get(record_id)
            if local is None:
                local_records.append(remote)
                by_id[record_id] = remote
                added += 1
                changed = True
                continue
            # A session interrupted and restarted can carry the same id with a
            # newer end: keep the longest one.
            if ("end" in local and "end" in remote
                    and int(remote.get("end") or 0) > int(local.get("end") or 0)):
                local.clear()
                local.update(remote)
                changed = True

        if changed:
            local_store.rewrite(local_records)
        return added

    @staticmethod
    def _merge_achievements(local_path: Path, remote_path: Path) -> int:
        remote = _read_json(remote_path, None)
        if not isinstance(remote, dict):
            return 0
        local = _read_json(local_path, {})
        if not isinstance(local, dict):
            local = {}

        local["schema"] = max(int(local.get("schema", 1) or 1), int(remote.get("schema", 1) or 1))
        local["reset_at"] = max(int(local.get("reset_at", 0) or 0), int(remote.get("reset_at", 0) or 0))

        unlocked = local.get("unlocked") if isinstance(local.get("unlocked"), dict) else {}
        added = 0
        for achievement_id, entry in (remote.get("unlocked") or {}).items():
            if achievement_id not in unlocked:
                unlocked[achievement_id] = entry
                added += 1
            elif isinstance(entry, dict) and isinstance(unlocked[achievement_id], dict):
                local_at = int(unlocked[achievement_id].get("at", 0) or 0)
                remote_at = int(entry.get("at", 0) or 0)
                if remote_at and (not local_at or remote_at < local_at):
                    unlocked[achievement_id] = entry
        local["unlocked"] = unlocked

        progress = local.get("progress") if isinstance(local.get("progress"), dict) else {}
        for achievement_id, entry in (remote.get("progress") or {}).items():
            if not isinstance(entry, dict):
                continue
            current = progress.get(achievement_id) if isinstance(progress.get(achievement_id), dict) else {}
            current_value = int(current.get("value", 0) or 0)
            remote_value = int(entry.get("value", 0) or 0)
            if remote_value > current_value:
                progress[achievement_id] = entry
        local["progress"] = progress

        _write_json_atomic(local_path, local)
        return added

    @staticmethod
    def _merge_prefs(local_path: Path, remote_path: Path) -> None:
        remote = _read_json(remote_path, None)
        if not isinstance(remote, dict):
            return
        local = _read_json(local_path, {})
        if not isinstance(local, dict):
            local = {}
        changed = False
        # Local preferences win; only a missing region is imported.
        if not local.get("region") and remote.get("region"):
            local["region"] = remote["region"]
            changed = True
        for key, value in remote.items():
            if key not in local:
                local[key] = value
                changed = True
        if changed:
            _write_json_atomic(local_path, local)
