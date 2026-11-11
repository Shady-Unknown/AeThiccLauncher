"""Append-only JSONL store used by sessions, history and usage events.

Properties:
- append is O(1) and never rewrites the file;
- a truncated/corrupt last line (crash) is skipped on load;
- every record carries ``v`` (schema), ``id`` (uuid) and ``at`` (epoch);
- ``merge`` deduplicates by ``id`` so backup restores are idempotent;
- ``rewrite`` is atomic (``tmp`` + ``os.replace``).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path


class JsonlStore:
    def __init__(self, path, schema_version: int = 1):
        self.path = Path(path)
        self.schema_version = int(schema_version)
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # --- write ---------------------------------------------------------

    def _normalize(self, record: dict) -> dict:
        if not isinstance(record, dict):
            raise TypeError("JsonlStore records must be dicts")
        data = dict(record)
        data.setdefault("v", self.schema_version)
        data.setdefault("id", uuid.uuid4().hex)
        data.setdefault("at", int(time.time()))
        return data

    def append(self, record: dict, fsync: bool = False) -> dict:
        data = self._normalize(record)
        line = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
                fh.flush()
                if fsync:
                    os.fsync(fh.fileno())
        return data

    def append_many(self, records, fsync: bool = False) -> list[dict]:
        data = [self._normalize(r) for r in records]
        if not data:
            return []
        payload = "".join(
            json.dumps(r, separators=(",", ":"), ensure_ascii=False) + "\n"
            for r in data
        )
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(payload)
                fh.flush()
                if fsync:
                    os.fsync(fh.fileno())
        return data

    def rewrite(self, records) -> int:
        data = [self._normalize(r) for r in records]
        tmp = self.path.with_name(self.path.name + ".tmp")
        with self._lock:
            with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                for record in data:
                    fh.write(json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        return len(data)

    # --- read ----------------------------------------------------------

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        records: list[dict] = []
        with self.path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # A crash can leave a partial last line. Ignore it: the
                    # rest of the file is still valid.
                    continue
                if isinstance(record, dict):
                    records.append(record)
        return records

    def ids(self) -> set:
        return {record.get("id") for record in self.load() if record.get("id")}

    def count(self) -> int:
        return len(self.load())

    # --- maintenance ---------------------------------------------------

    def merge(self, records) -> int:
        """Append records whose ``id`` is not already present. Returns added count."""
        known = self.ids()
        fresh = []
        for record in records:
            if not isinstance(record, dict):
                continue
            record_id = record.get("id")
            if record_id and record_id in known:
                continue
            fresh.append(record)
            if record_id:
                known.add(record_id)
        if fresh:
            self.append_many(fresh)
        return len(fresh)

    def compact(self, keep: int | None = None, dedupe: bool = True) -> tuple[int, int]:
        """Rewrite the file removing duplicates and (optionally) old records.

        Returns ``(kept, original)`` so callers can log when compaction fires.
        """
        records = self.load()
        original = len(records)
        if dedupe:
            seen = set()
            unique = []
            for record in records:
                record_id = record.get("id")
                if record_id:
                    if record_id in seen:
                        continue
                    seen.add(record_id)
                unique.append(record)
            records = unique
        if keep is not None and keep >= 0 and len(records) > keep:
            records = records[-keep:]
        if len(records) != original:
            self.rewrite(records)
        return len(records), original
