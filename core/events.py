"""Application event bus.

The bus is deliberately small: one Qt signal for cross-thread delivery plus a
plain callback list for in-process subscribers. It knows nothing about Stats
or Achievements; those are subscribers.

Threading contract
------------------
Emit from the GUI thread. Worker threads continue to use their existing Qt
signals and a controller re-emits on the bus. This avoids running subscriber
code on a worker thread by accident.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal

# --- Event names (keep in sync with plan.md §14) -------------------------
EVENT_LAUNCHER_START = "launcher_start"
EVENT_GAME_LAUNCHED = "game_launched"
EVENT_SESSION_END = "session_end"
EVENT_GAME_INSTALLED = "game_installed"
EVENT_GAME_REMOVED = "game_removed"
EVENT_BACKUP_CREATED = "backup_created"
EVENT_UPDATE_APPLIED = "update_applied"
EVENT_DOWNLOAD_RESUMED = "download_resumed"
EVENT_HOSTER_USED = "hoster_used"
EVENT_TRAILER_WATCHED = "trailer_watched"
EVENT_STORE_OPENED = "store_opened"
EVENT_ORPHAN_RECOVERED = "orphan_recovered"
EVENT_PRICE_REFRESHED = "price_refreshed"
EVENT_IGDB_DETAILS_READY = "igdb_details_ready"
EVENT_REGION_CHANGED = "region_changed"
EVENT_PREFS_CHANGED = "prefs_changed"
EVENT_ACHIEVEMENT_UNLOCKED = "achievement_unlocked"
EVENT_STATS_INVALIDATED = "stats_invalidated"

ALL_EVENTS = (
    EVENT_LAUNCHER_START,
    EVENT_GAME_LAUNCHED,
    EVENT_SESSION_END,
    EVENT_GAME_INSTALLED,
    EVENT_GAME_REMOVED,
    EVENT_BACKUP_CREATED,
    EVENT_UPDATE_APPLIED,
    EVENT_DOWNLOAD_RESUMED,
    EVENT_HOSTER_USED,
    EVENT_TRAILER_WATCHED,
    EVENT_STORE_OPENED,
    EVENT_ORPHAN_RECOVERED,
    EVENT_PRICE_REFRESHED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_REGION_CHANGED,
    EVENT_PREFS_CHANGED,
    EVENT_ACHIEVEMENT_UNLOCKED,
    EVENT_STATS_INVALIDATED,
)


class EventBus(QObject):
    """Tiny typed pub/sub used by controllers, managers and future features."""

    event_emitted = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._subscribers: dict[str, list[Callable[[dict], Any]]] = {}

    def subscribe(self, name: str, callback: Callable[[dict], Any]) -> Callable[[dict], Any]:
        self._subscribers.setdefault(name, []).append(callback)
        return callback

    def unsubscribe(self, name: str, callback: Callable[[dict], Any]) -> None:
        callbacks = self._subscribers.get(name)
        if not callbacks:
            return
        try:
            callbacks.remove(callback)
        except ValueError:
            pass

    def emit_event(self, name: str, payload: dict | None = None, **kwargs) -> dict:
        data = dict(payload or {})
        data.update(kwargs)
        data.setdefault("id", uuid.uuid4().hex)
        data.setdefault("at", int(time.time()))
        self.event_emitted.emit(name, data)
        for callback in tuple(self._subscribers.get(name, ())):
            try:
                callback(data)
            except Exception as exc:
                print(f"[Events] subscriber error for '{name}': {exc}")
        return data
