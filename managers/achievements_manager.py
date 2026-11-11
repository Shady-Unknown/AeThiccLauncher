"""Launcher achievements manager: state, evaluation, signals and badges.

The engine is data-driven (see ``achievement_engine``); this Qt wrapper owns
the state file, debounces event evaluation and emits signals for toasts/UI.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core import events as ev
from core.data_files import asset_path
from core.paths import PathManager
from managers.achievement_engine import (
    compute_stats, evaluate_trigger, load_definitions, load_event_history,
    load_title_lists, progress_for, trigger_has_event,
)

STATE_FILENAME = "achievements.json"


def _read_state(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "schema": 1,
        "reset_at": int(data.get("reset_at") or 0),
        "unlocked": data.get("unlocked") if isinstance(data.get("unlocked"), dict) else {},
        "progress": data.get("progress") if isinstance(data.get("progress"), dict) else {},
    }


def _write_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(state, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class AchievementsManager(QObject):
    achievement_unlocked = pyqtSignal(dict)
    retroactive_unlocked = pyqtSignal(list)
    state_changed = pyqtSignal()

    def __init__(self, state_store=None, installed_games=None, stats_provider=None,
                 config=None, events=None, clock=None, definitions=None,
                 base_dir=None, state_path=None, parent=None):
        super().__init__(parent)
        self._state_store = state_store
        self._installed_games = installed_games
        self._stats_provider = stats_provider
        self._config = config
        self._clock = clock or time.time
        self._definitions = (
            list(definitions) if definitions is not None else load_definitions()
        )
        self._title_lists = load_title_lists()
        self._state_path = (
            Path(state_path) if state_path is not None
            else (Path(base_dir) / STATE_FILENAME if base_dir is not None
                  else PathManager.get_app_data_dir() / STATE_FILENAME)
        )
        self._state = _read_state(self._state_path)
        self._stats = {}
        self._pending_events = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.evaluate)

        if events is not None:
            self.bind(events)

    # ------------------------------------------------------------------ setup

    def bind(self, events):
        ignored = {ev.EVENT_ACHIEVEMENT_UNLOCKED, ev.EVENT_STATS_INVALIDATED}
        for name in ev.ALL_EVENTS:
            if name in ignored:
                continue
            events.subscribe(
                name, lambda payload, _name=name: self._on_event(_name, payload)
            )

    def start(self):
        self.evaluate(retroactive=True)

    def _on_event(self, name, payload):
        self._pending_events.append((name, dict(payload or {})))
        self._timer.start()

    # ------------------------------------------------------------------ public

    @property
    def state(self):
        return self._state

    def definitions(self):
        return list(self._definitions)

    def unlocked_count(self):
        return len(self._state.get("unlocked") or {})

    def icon_path(self, definition):
        icon = str(definition.get("icon") or definition.get("id") or "")
        return str(asset_path(f"achievements/{icon}.png"))

    def badge_for(self, definition, stats=None):
        stats = stats if isinstance(stats, dict) else self._stats
        badge = dict(definition)
        unlocked = (self._state.get("unlocked") or {}).get(definition.get("id"))
        badge["unlocked"] = isinstance(unlocked, dict)
        badge["unlocked_at"] = int(unlocked.get("at") or 0) if isinstance(unlocked, dict) else 0
        badge["hidden_locked"] = bool(definition.get("hidden")) and not badge["unlocked"]
        badge["icon_path"] = self.icon_path(definition)
        progress = progress_for(definition, stats) if not badge["unlocked"] else None
        badge["progress"] = (
            {"current": progress[0], "target": progress[1]}
            if progress else None
        )
        return badge

    def badges(self, stats=None):
        return [self.badge_for(definition, stats) for definition in self._definitions]

    def stats(self):
        return dict(self._stats)

    # ------------------------------------------------------------------ evaluate

    def _current_stats(self):
        return compute_stats(
            state_store=self._state_store,
            installed_games=self._installed_games,
            config=self._config,
            stats_snapshot=self._stats_provider,
            title_lists=self._title_lists,
            now=int(self._clock()),
        )

    def evaluate(self, retroactive=False):
        stats = self._current_stats()
        self._stats = stats
        events = (
            load_event_history(self._state_store)
            if retroactive else list(self._pending_events)
        )
        if not retroactive:
            self._pending_events = []

        unlocked = self._state.get("unlocked") or {}
        newly = []
        now = int(self._clock())
        for definition in self._definitions:
            achievement_id = str(definition.get("id") or "")
            if not achievement_id or achievement_id in unlocked:
                continue
            trigger = definition.get("trigger") or {}
            matched = evaluate_trigger(trigger, stats, now=now)
            if not matched and events and trigger_has_event(trigger):
                for event_name, payload in events:
                    if evaluate_trigger(
                        trigger, stats, event_name=event_name, payload=payload, now=now
                    ):
                        matched = True
                        break
            if matched:
                unlocked[achievement_id] = {"at": now}
                newly.append(definition)

        if not newly:
            return []
        self._state["unlocked"] = unlocked
        _write_state(self._state_path, self._state)
        badges = [self.badge_for(definition, stats) for definition in newly]
        if retroactive and len(badges) > 3:
            self.retroactive_unlocked.emit(badges)
        else:
            for badge in badges:
                self.achievement_unlocked.emit(badge)
        self.state_changed.emit()
        return badges
