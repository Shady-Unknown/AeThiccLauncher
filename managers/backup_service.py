"""Facade used by the UI to back up and restore savegames.

The Savegames tab talks to this small surface instead of touching snapshots,
manifests and path validation directly (see ``plans/backup.md``, phase 3).
"""
from __future__ import annotations

import os


class BackupService:
    def __init__(self, savegames_manager, state_manager=None):
        self.savegames = savegames_manager
        self.state = state_manager

    # -------------------------------------------------------------- backups
    def backup_savegames(self, titles=None):
        """Back up every installed game, or only the requested titles."""
        if titles is None:
            return self.savegames.sync_all_games()

        installed = self.savegames.installed_games or {}
        report = {'synced_games': 0, 'unchanged_games': 0, 'errors': []}
        for title in titles:
            meta = installed.get(title)
            if not meta:
                continue
            if self.savegames.backup_on_demand(meta):
                report['synced_games'] += 1
        return report

    def backup_state(self, push=True):
        """Snapshot launcher state (achievements, stats, preferences)."""
        if self.state is None:
            return {'ok': False, 'reason': 'not_configured'}
        try:
            path = self.state.snapshot()
        except Exception as exc:
            return {'ok': False, 'reason': 'error', 'detail': str(exc)}
        mirror = self.savegames.push_mirror() if push else {}
        return {
            'ok': path is not None,
            'snapshot': path.name if path is not None else '',
            'root': str(self.state.root),
            'mirror': mirror,
        }

    def restore_state(self):
        """Merge the backed-up launcher state into the local one (additive)."""
        if self.state is None:
            return {'files': 0, 'jsonl_added': 0, 'achievements_added': 0,
                    'reason': 'not_configured'}
        return self.state.restore_merge()

    def state_status(self):
        if self.state is None:
            return {'root': '', 'files': 0, 'updated_at': 0}
        return self.state.status()

    # ------------------------------------------------------------ snapshots
    def list_snapshots(self, title):
        return self.savegames.list_snapshots(title)

    def status(self, meta=None):
        meta = meta or {}
        title = str(meta.get('title') or meta.get('game_title') or '')
        snapshots = self.list_snapshots(title) if title else []
        destination = self.savegames.get_source_save_path(meta) if meta else ''
        return {
            'snapshots': len(snapshots),
            'latest': snapshots[0]['id'] if snapshots else '',
            'destination': destination,
            'destination_exists': bool(destination) and os.path.exists(destination),
        }

    # -------------------------------------------------------------- restore
    def restore_game(self, meta, snapshot_id, create_missing=False):
        return self.savegames.restore_snapshot(
            meta, snapshot_id, create_missing=create_missing
        )
