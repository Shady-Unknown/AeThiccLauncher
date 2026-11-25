"""Launcher achievements manager: state, evaluation, signals and badges.

The engine is data-driven (see ``achievement_engine``); this Qt wrapper owns
the state file, debounces event evaluation and emits signals for toasts/UI.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from core import events as ev
from core.atomic_io import atomic_write_json
from core.data_files import asset_path
from core.paths import PathManager
from managers.achievement_engine import (
    compute_stats, evaluate_trigger, load_definitions, load_event_history,
    load_title_lists, progress_for, trigger_event_names, trigger_has_event,
    trigger_stat_keys,
)

STATE_FILENAME = "achievements.json"
STATE_SCHEMA = 1
# Diagnostics: force a full pass that ignores the selective filter.
FULL_EVAL_ENV = "AETHICC_ACHIEVEMENTS_FULL"

log = logging.getLogger(__name__)


def _resolve(value):
    return value() if callable(value) else value


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _snapshot_signature(snapshot):
    """Content signature of the snapshot fields ``compute_stats`` reads.

    Only used when the caller cannot provide a snapshot revision (tests, simple
    integrations); the app passes ``StatsManager.snapshot_revision`` instead.
    """
    games = snapshot.get("games") or []
    hero = snapshot.get("hero") or {}
    timeline = snapshot.get("timeline") or {}
    weekday = timeline.get("by_weekday") or []
    return (
        _float(hero.get("hours_total")),
        _int(hero.get("best_streak")),
        tuple(_float(value) for value in weekday),
        tuple(
            (
                str(game.get("title") or ""),
                bool(game.get("installed")),
                _float(game.get("hours_total")),
                _float(game.get("hours_tracked")),
                _int(game.get("sessions")),
                bool(game.get("gold_tier")),
                tuple(str(name) for name in (game.get("developers") or [])),
                tuple(str(name) for name in (game.get("publishers") or [])),
                tuple(str(name) for name in (game.get("genres") or [])),
                game.get("cost_per_hour"),
            )
            for game in games
        ),
    )


def _read_state(path: Path):
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, TypeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        schema = int(data.get("schema") or STATE_SCHEMA)
    except (TypeError, ValueError):
        schema = STATE_SCHEMA
    if schema > STATE_SCHEMA:
        log.warning(
            "[Achievements] state file schema %s is newer than supported (%s); "
            "known fields are kept and the marker is not downgraded",
            schema, STATE_SCHEMA,
        )
    return {
        "schema": schema,
        "reset_at": int(data.get("reset_at") or 0),
        "seeded": bool(data.get("seeded")),
        "seeded_at": int(data.get("seeded_at") or 0),
        "unlocked": data.get("unlocked") if isinstance(data.get("unlocked"), dict) else {},
        "progress": data.get("progress") if isinstance(data.get("progress"), dict) else {},
    }


class AchievementsManager(QObject):
    achievement_unlocked = pyqtSignal(dict)
    retroactive_unlocked = pyqtSignal(list)
    state_changed = pyqtSignal()

    def __init__(self, state_store=None, installed_games=None, stats_provider=None,
                 config=None, events=None, clock=None, definitions=None,
                 base_dir=None, state_path=None, parent=None,
                 stats_revision=None):
        super().__init__(parent)
        self._state_store = state_store
        self._installed_games = installed_games
        self._stats_provider = stats_provider
        self._stats_revision = stats_revision
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
        self._stats_previous = {}
        self._stats_cache_key = None
        self._snapshot = {}
        self._installed_revision = 0
        self._stat_keys = {}
        self._event_names = {}
        self._compile_definitions()
        self._pending_events = []
        # Events are buffered until start(): the first evaluation must see the
        # whole session instead of emitting one toast per early event.
        self._started = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self.evaluate)

        if events is not None:
            self.bind(events)

    # ------------------------------------------------------------------ setup

    def bind(self, events):
        self._events = events
        ignored = {ev.EVENT_ACHIEVEMENT_UNLOCKED, ev.EVENT_STATS_INVALIDATED}
        for name in ev.ALL_EVENTS:
            if name in ignored:
                continue
            events.subscribe(
                name, lambda payload, _name=name: self._on_event(_name, payload)
            )

    def start(self):
        """Single retroactive pass; a brand-new state is seeded silently.

        On a first run every already-true condition would otherwise be
        announced as "just unlocked", which looks like a bug (badges popping up
        over the Discovery tab). History is imported without notifications.
        """
        self._started = True
        self._timer.stop()
        self._pending_events = []
        first_seed = not self._state.get("seeded") and not self._state.get("unlocked")
        self.evaluate(retroactive=True, silent=first_seed)
        if not self._state.get("seeded"):
            self._state["seeded"] = True
            self._state["seeded_at"] = int(self._clock())
            atomic_write_json(self._state_path, self._state,
                          separators=(",", ":"))

    def reload(self):
        """Reload state from disk, e.g. after restoring a launcher backup."""
        self._state = _read_state(self._state_path)
        self._pending_events = []
        self._stats = {}
        self._stats_previous = {}
        self._stats_cache_key = None
        self.state_changed.emit()
        return self.evaluate()

    def _on_event(self, name, payload):
        if name in (ev.EVENT_GAME_INSTALLED, ev.EVENT_GAME_REMOVED):
            self._installed_revision += 1
        self._pending_events.append((name, dict(payload or {})))
        if self._started:
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

    def _compile_definitions(self):
        """Cache the stats and events each definition depends on."""
        self._stat_keys = {}
        self._event_names = {}
        for definition in self._definitions:
            achievement_id = str(definition.get("id") or "")
            if not achievement_id:
                continue
            trigger = definition.get("trigger") or {}
            self._stat_keys[achievement_id] = trigger_stat_keys(trigger)
            self._event_names[achievement_id] = trigger_event_names(trigger)

    def _stats_key(self):
        """Cheap token for everything ``compute_stats`` reads.

        Store fingerprints cover the JSONL history; the config tuple covers the
        only config/prefs fields the stats dictionary exposes; the snapshot
        revision covers the Stats worker output (catalog/IGDB derived fields).
        """
        store = self._state_store
        fingerprints = []
        for name in ("sessions", "installs", "usage", "prices"):
            child = getattr(store, name, None) if store is not None else None
            fingerprints.append(
                child.fingerprint() if hasattr(child, "fingerprint") else None
            )

        prefs = {}
        if store is not None and hasattr(store, "load_prefs"):
            try:
                prefs = store.load_prefs() or {}
            except Exception:
                prefs = {}

        config = _resolve(self._config) or {}
        stats_cfg = {}
        watched = temp_custom = False
        theme = ""
        if isinstance(config, dict):
            stats_cfg = config.get("stats") or {}
            watched = bool(config.get("watched_folders"))
            temp_custom = bool(config.get("default_temp_folder"))
            theme = str(config.get("current_theme") or "")
        if not isinstance(stats_cfg, dict):
            stats_cfg = {}

        installed = _resolve(self._installed_games) or {}
        if self._stats_revision is not None:
            snapshot_key = self._stats_revision()
        else:
            snapshot_key = _snapshot_signature(self._snapshot)

        return (
            tuple(fingerprints),
            (
                str(prefs.get("region") or ""),
                watched,
                temp_custom,
                theme,
                str(stats_cfg.get("region") or ""),
                _float(stats_cfg.get("cinema_rate")),
            ),
            self._installed_revision,
            len(installed),
            snapshot_key,
        )

    def _current_stats(self):
        """Return ``(stats, rebuilt)``, reusing the last pass when nothing changed."""
        snapshot = _resolve(self._stats_provider) or {}
        self._snapshot = snapshot
        key = self._stats_key()
        if self._stats_cache_key == key:
            return self._stats, False
        stats = compute_stats(
            state_store=self._state_store,
            installed_games=self._installed_games,
            config=self._config,
            stats_snapshot=snapshot,
            title_lists=self._title_lists,
            now=int(self._clock()),
        )
        self._stats = stats
        self._stats_cache_key = key
        return stats, True

    def _open_definitions(self, unlocked):
        return [
            definition for definition in self._definitions
            if str(definition.get("id") or "") not in unlocked
        ]

    def _due_definitions(self, stats, events, unlocked, full=False):
        """Definitions whose inputs changed since the previous pass.

        A definition is due when one of its stat keys changed value or one of
        the pending events is named in its trigger. Event-only definitions keep
        working even when the stats dictionary is identical; definitions whose
        inputs did not change cannot unlock.
        """
        if full or not self._stats_previous:
            return self._open_definitions(unlocked)
        if os.environ.get(FULL_EVAL_ENV) == "1":
            return self._open_definitions(unlocked)

        changed = {
            key for key, value in stats.items()
            if self._stats_previous.get(key) != value
        }
        pending = {name for name, _payload in events}
        due = []
        for definition in self._open_definitions(unlocked):
            achievement_id = str(definition.get("id") or "")
            if self._stat_keys.get(achievement_id, set()) & changed:
                due.append(definition)
            elif self._event_names.get(achievement_id, set()) & pending:
                due.append(definition)
        return due

    def evaluate(self, retroactive=False, silent=False):
        stats, rebuilt = self._current_stats()
        events = (
            load_event_history(self._state_store)
            if retroactive else list(self._pending_events)
        )
        if not retroactive:
            self._pending_events = []

        unlocked = self._state.get("unlocked") or {}
        now = int(self._clock())
        due = self._due_definitions(stats, events, unlocked, full=retroactive)
        newly = []
        for definition in due:
            achievement_id = str(definition.get("id") or "")
            if achievement_id in unlocked:
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

        self._stats_previous = stats
        log.debug(
            "[Achievements] evaluate rebuilt=%s due=%d/%d events=%d",
            rebuilt, len(due), len(self._definitions), len(events),
        )

        if not newly:
            return []
        self._state["unlocked"] = unlocked
        atomic_write_json(self._state_path, self._state,
                          separators=(",", ":"))
        badges = [self.badge_for(definition, stats) for definition in newly]
        if not silent:
            if retroactive and len(badges) > 1:
                self.retroactive_unlocked.emit(badges)
            else:
                for badge in badges:
                    self.achievement_unlocked.emit(badge)
        events = getattr(self, "_events", None)
        if events is not None:
            try:
                events.emit_event(ev.EVENT_ACHIEVEMENT_UNLOCKED, {
                    "ids": [str(d.get("id") or "") for d in newly],
                    "count": len(newly),
                })
            except Exception:
                # best-effort: the event bus is optional; the unlock must not fail
                pass
        self.state_changed.emit()
        return badges
