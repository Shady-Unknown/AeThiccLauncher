"""User-state store: sessions, install history, usage and price history.

The store is a plain subscriber on the :class:`core.events.EventBus`; it knows
nothing about the UI. It owns the JSONL files described in ``plan.md`` §6 and
keeps a small crash-recovery marker for game sessions.

All writes go through :class:`core.jsonl_store.JsonlStore` (append + atomic
rewrite). The store never raises into the event bus: a broken disk must not
break the launcher.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from core import events as ev
from core.events import EventBus
from core.jsonl_store import JsonlStore
from core.paths import PathManager
from core.price_utils import detect_currency, parse_price_to_cents

SESSION_FILE = "play_sessions.jsonl"
INSTALL_FILE = "install_history.jsonl"
USAGE_FILE = "usage_events.jsonl"
PRICE_FILE = "price_history.jsonl"
PREFS_FILE = "stats_prefs.json"
OPEN_SESSION_FILE = "play_sessions.open.json"

OPEN_SESSION_MAX_AGE = 7 * 24 * 3600

USAGE_EVENTS = (
    ev.EVENT_LAUNCHER_START,
    ev.EVENT_GAME_LAUNCHED,
    ev.EVENT_BACKUP_CREATED,
    ev.EVENT_UPDATE_APPLIED,
    ev.EVENT_DOWNLOAD_RESUMED,
    ev.EVENT_HOSTER_USED,
    ev.EVENT_TRAILER_WATCHED,
    ev.EVENT_STORE_OPENED,
    ev.EVENT_ORPHAN_RECOVERED,
)

DEFAULT_PREFS = {
    "region": "US",
    "range": "90d",
    "cinema_rate": 6.0,
}


def _utc_day(timestamp: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(int(timestamp)))


def _write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, ValueError, TypeError):
        return default
    return value if value is not None else default


class StateStore:
    """Persist the user state that powers Stats and Achievements."""

    def __init__(self, events: EventBus | None = None, base_dir=None, clock=None):
        self.base_dir = Path(base_dir) if base_dir is not None else PathManager.get_stats_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time

        self.sessions = JsonlStore(self.base_dir / SESSION_FILE, schema_version=1)
        self.installs = JsonlStore(self.base_dir / INSTALL_FILE, schema_version=1)
        self.usage = JsonlStore(self.base_dir / USAGE_FILE, schema_version=1)
        self.prices = JsonlStore(self.base_dir / PRICE_FILE, schema_version=1)

        self._marker_path = self.base_dir / OPEN_SESSION_FILE
        self._prefs_path = self.base_dir / PREFS_FILE

        self._session_ids = self.sessions.ids()
        self._install_ids = self.installs.ids()
        self._usage_ids = self.usage.ids()
        self._price_ids = self.prices.ids()

        self._open_sessions: dict[str, dict] = {}
        self._first_seen: dict[str, int] = {}
        self._price_last: dict[tuple, dict] = {}

        self._load_indexes()
        self._recover_open_sessions()

        if events is not None:
            self.bind(events)

    # ------------------------------------------------------------------ setup

    def bind(self, events: EventBus) -> None:
        events.subscribe(ev.EVENT_SESSION_END, self.on_session_end)
        events.subscribe(ev.EVENT_GAME_INSTALLED, self.on_game_installed)
        events.subscribe(ev.EVENT_GAME_REMOVED, self.on_game_removed)
        events.subscribe(ev.EVENT_PRICE_REFRESHED, self.on_price_refreshed)
        for name in USAGE_EVENTS:
            events.subscribe(name, lambda payload, _name=name: self.record_usage(_name, payload))

    def _load_indexes(self) -> None:
        for record in self.installs.load():
            title = record.get("title")
            first_seen = record.get("first_seen_at") or record.get("at")
            if not title or not first_seen:
                continue
            try:
                first_seen = int(first_seen)
            except (TypeError, ValueError):
                continue
            current = self._first_seen.get(title)
            if current is None or first_seen < current:
                self._first_seen[title] = first_seen

        for record in self.prices.load():
            key = self._price_key(record)
            day = _utc_day(record.get("at") or 0)
            current = self._price_last.get(key)
            # Keep the first record of each day: the store writes at most one
            # price per title/store/region/day.
            if current is None or current.get("day") != day:
                self._price_last[key] = {
                    "day": day,
                    "final_cents": record.get("final_cents"),
                    "discount_percent": record.get("discount_percent"),
                }

    @staticmethod
    def _price_key(record: dict) -> tuple:
        return (
            str(record.get("title") or ""),
            str(record.get("store") or ""),
            str(record.get("region") or "").upper(),
        )

    # ------------------------------------------------------------------ helpers

    def _now(self) -> int:
        return int(self._clock())

    def _append_unique(self, store: JsonlStore, known: set, record: dict) -> dict | None:
        record_id = record.get("id")
        if record_id and record_id in known:
            return None
        store.append(record)
        if record_id:
            known.add(record_id)
        return record

    # ------------------------------------------------------------ sessions

    def on_session_end(self, payload: dict) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            return
        try:
            seconds = max(0, int(payload.get("seconds") or 0))
        except (TypeError, ValueError):
            seconds = 0
        if seconds <= 0:
            self._close_marker(title)
            return

        ended_at = int(payload.get("ended_at") or self._now())
        started_at = int(payload.get("started_at") or 0) or max(0, ended_at - seconds)
        record = {
            "id": payload.get("id") or uuid.uuid4().hex,
            "title": title,
            "start": started_at,
            "end": ended_at,
            "seconds": seconds,
            "source": "launcher",
            "install_dir": str(payload.get("install_dir") or ""),
        }
        self._append_unique(self.sessions, self._session_ids, record)
        self._close_marker(title)

    def _close_marker(self, title: str) -> None:
        if title in self._open_sessions:
            self._open_sessions.pop(title, None)
            self._write_marker()

    def _write_marker(self) -> None:
        _write_json_atomic(self._marker_path, {"v": 1, "open": self._open_sessions})

    def _recover_open_sessions(self) -> None:
        data = _read_json(self._marker_path, {"open": {}})
        opened = data.get("open") if isinstance(data, dict) else None
        if not isinstance(opened, dict):
            opened = {}

        now = self._now()
        recovered = []
        for title, entry in opened.items():
            if not isinstance(entry, dict):
                continue
            try:
                start = int(entry.get("start") or 0)
            except (TypeError, ValueError):
                continue
            age = now - start
            if start <= 0 or age < 0 or age > OPEN_SESSION_MAX_AGE:
                continue
            seconds = max(1, age)
            recovered.append({
                "id": entry.get("id") or (uuid.uuid4().hex + ":recovered"),
                "title": str(title),
                "start": start,
                "end": now,
                "seconds": seconds,
                "source": "launcher",
                "install_dir": str(entry.get("install_dir") or ""),
                "recovered": True,
            })

        if recovered:
            self.sessions.append_many(recovered)
            for record in recovered:
                self._session_ids.add(record["id"])
        if opened or self._marker_path.exists():
            self._open_sessions = {}
            self._write_marker()

    # ------------------------------------------------------- install history

    def on_game_installed(self, payload: dict) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            return
        at = int(payload.get("at") or self._now())
        first_seen = self._first_seen.get(title)
        if first_seen is None:
            first_seen = at
            self._first_seen[title] = at
        record = {
            "id": payload.get("id") or uuid.uuid4().hex,
            "title": title,
            "action": "add",
            "install_dir": str(payload.get("install_dir") or ""),
            "first_seen_at": first_seen,
            "at": at,
        }
        self._append_unique(self.installs, self._install_ids, record)

    def on_game_removed(self, payload: dict) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            return
        at = int(payload.get("at") or self._now())
        record = {
            "id": payload.get("id") or uuid.uuid4().hex,
            "title": title,
            "action": "remove",
            "first_seen_at": self._first_seen.get(title, at),
            "at": at,
        }
        self._append_unique(self.installs, self._install_ids, record)

    # ---------------------------------------------------------- usage events

    def record_usage(self, name: str, payload: dict) -> None:
        at = int(payload.get("at") or self._now())
        record = {
            "id": payload.get("id") or uuid.uuid4().hex,
            "event": name,
            "at": at,
        }
        for key, value in payload.items():
            if key in ("id", "at", "install_dir"):
                continue
            record[key] = value

        if name == ev.EVENT_GAME_LAUNCHED:
            title = str(payload.get("title") or "").strip()
            if title:
                self._open_sessions[title] = {
                    "id": uuid.uuid4().hex,
                    "start": int(payload.get("started_at") or at),
                    "pid": payload.get("pid"),
                    "install_dir": str(payload.get("install_dir") or ""),
                }
                self._write_marker()

        self._append_unique(self.usage, self._usage_ids, record)

    # ---------------------------------------------------------- price history

    def on_price_refreshed(self, payload: dict) -> None:
        title = str(payload.get("title") or "").strip()
        if not title:
            return
        region = str(payload.get("region") or "US").upper()
        at = int(payload.get("at") or self._now())
        day = _utc_day(at)
        event_id = payload.get("id") or uuid.uuid4().hex

        for store in ("steam", "gog"):
            data = payload.get(store)
            if not isinstance(data, dict):
                continue
            final_cents = data.get("final_cents")
            if final_cents is None:
                final_cents = parse_price_to_cents(data.get("final_price"))
            if final_cents is None:
                continue
            try:
                final_cents = int(final_cents)
            except (TypeError, ValueError):
                continue

            key = (title, store, region)
            last = self._price_last.get(key)
            if last is not None and last.get("day") == day:
                continue

            initial_cents = data.get("initial_cents")
            if initial_cents is None:
                initial_cents = parse_price_to_cents(data.get("initial_price"))
            currency = data.get("currency") or detect_currency(data.get("final_price", ""))
            discount = data.get("discount_percent") or 0
            record = {
                "id": f"{event_id}:{store}",
                "title": title,
                "store": store,
                "region": region,
                "currency": currency,
                "initial_cents": int(initial_cents) if initial_cents is not None else None,
                "final_cents": final_cents,
                "discount_percent": int(discount or 0),
                "on_sale": bool(data.get("on_sale")),
                "at": at,
            }
            if self._append_unique(self.prices, self._price_ids, record) is not None:
                self._price_last[key] = {
                    "day": day,
                    "final_cents": final_cents,
                    "discount_percent": int(discount or 0),
                }

    # --------------------------------------------------------------- prefs

    def load_prefs(self) -> dict:
        data = _read_json(self._prefs_path, {})
        prefs = dict(DEFAULT_PREFS)
        if isinstance(data, dict):
            prefs.update({k: v for k, v in data.items() if v is not None})
        return prefs

    def save_prefs(self, prefs: dict) -> dict:
        merged = self.load_prefs()
        merged.update(prefs or {})
        _write_json_atomic(self._prefs_path, merged)
        return merged
