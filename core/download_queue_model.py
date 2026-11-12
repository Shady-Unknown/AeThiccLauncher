"""Pure view-model helpers for the download queue.

This module owns the rules that used to be implicit in ``downloads_tab``:
which row is the "active / next" slot, how the remaining rows are grouped and
how each state is labelled. Keeping it free of Qt makes the ordering and
priority rules unit-testable.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

STATE_DOWNLOADING = "downloading"
STATE_EXTRACTING = "extracting"
STATE_PARTIAL = "partial"
STATE_QUEUED = "queued"
STATE_READY = "ready"
STATE_INTERRUPTED = "interrupted"

KIND_DOWNLOAD = "download"
KIND_EXTRACT = "extract"

SECTION_NEEDS_EXTRACTION = "Needs Extraction"
SECTION_UP_NEXT = "Up Next"

# Theme keys are resolved in order, so the widgets never hard-code a color.
STATE_THEME_KEYS = {
    STATE_DOWNLOADING: ("neon_gradient_end",),
    STATE_EXTRACTING: ("neon_gradient_start",),
    STATE_READY: ("queue_state_ready", "status_green"),
    STATE_INTERRUPTED: ("queue_state_interrupted", "bright_neon_accent", "status_red"),
    STATE_PARTIAL: ("queue_state_partial", "status_yellow"),
    STATE_QUEUED: ("queue_state_queued", "text_muted"),
}

STATE_LABELS = {
    STATE_DOWNLOADING: "DOWNLOADING",
    STATE_EXTRACTING: "EXTRACTING",
    STATE_READY: "READY TO EXTRACT",
    STATE_INTERRUPTED: "EXTRACTION INTERRUPTED",
    STATE_PARTIAL: "PARTIAL",
    STATE_QUEUED: "QUEUED",
}


@dataclass
class WorkRow:
    """One rendered row: either a download task or a pending extraction."""

    kind: str
    state: str
    row_id: str
    item: dict
    title: str = ""
    provider: str = ""
    badge: str = ""
    pct: int | None = None
    downloaded: int = 0
    total: int = 0
    detail: str = ""

    @property
    def needs_attention(self):
        return self.state in (STATE_READY, STATE_INTERRUPTED, STATE_PARTIAL)


EXTRACTION_STATE_SUFFIX = ".extracting.json"


def extraction_state_path(archive_path):
    archive_path = str(archive_path or "").strip()
    if not archive_path:
        return ""
    return f"{archive_path}{EXTRACTION_STATE_SUFFIX}"


def write_extraction_state(archive_path, payload):
    """Persist progress for an extraction that may be interrupted."""
    path = extraction_state_path(archive_path)
    if not path:
        return
    data = dict(payload or {})
    data.setdefault("archive_path", str(archive_path))
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
    except OSError:
        pass


def read_extraction_state(path):
    """Read an extraction sidecar (accepts the sidecar path or the archive path)."""
    path = str(path or "").strip()
    if not path:
        return {}
    if not path.endswith(EXTRACTION_STATE_SUFFIX):
        path = extraction_state_path(path)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_extraction_state(archive_path):
    path = extraction_state_path(archive_path)
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _normalize_temp_key(path):
    try:
        return os.path.normcase(os.path.abspath(str(path)))
    except (OSError, ValueError):
        return str(path).lower()


def temp_cleanup_candidates(
    folder,
    protected_paths,
    state_names=("dl_state.json", ".dl_state.json"),
):
    """Files in the temp folder that are safe to delete.

    Active downloads, queued archives, pending extractions and the queue state
    file are protected; their ``.incomplete`` / ``.extracting.json`` sidecars
    are protected too.
    """
    if not folder or not os.path.isdir(folder):
        return []
    protected = {
        _normalize_temp_key(path) for path in (protected_paths or []) if path
    }
    state_names = set(state_names or ())
    result = []
    for name in os.listdir(folder):
        if name in state_names:
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        key = _normalize_temp_key(path)
        if key in protected:
            continue
        skip = False
        for suffix in (".incomplete", ".extracting.json"):
            if key.endswith(suffix) and key[: -len(suffix)] in protected:
                skip = True
                break
        if skip:
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        result.append((path, size))
    return result


def format_bytes(size):
    try:
        size = float(size or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024.0
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


def disk_size(path, size_provider=None):
    """Size of ``path`` on disk, 0 when it does not exist."""
    if not path:
        return 0
    provider = size_provider or os.path.getsize
    try:
        if size_provider is not None:
            return int(provider(path) or 0)
        if os.path.isfile(path):
            return int(provider(path) or 0)
    except OSError:
        return 0
    return 0


def task_progress(task, size_provider=None):
    """Return ``(downloaded, total, pct)`` for a download task.

    ``downloaded_bytes`` is persisted when possible; when it is missing (legacy
    state or a hard crash) the partial file on disk is the source of truth.
    ``pct`` is ``None`` when the total size was never recorded.
    """
    task = task or {}
    downloaded = int(task.get("downloaded_bytes") or 0)
    if downloaded <= 0:
        downloaded = disk_size(task.get("dest_path"), size_provider)
    total = int(task.get("total_bytes") or 0)
    pct = None
    if total > 0 and downloaded > 0:
        pct = min(100, max(0, int(round(downloaded * 100.0 / total))))
    return downloaded, total, pct


def _row_id(item, fallback):
    rid = str((item or {}).get("_id") or "").strip()
    if rid:
        return rid
    path = str((item or {}).get("dest_path") or (item or {}).get("archive_path") or "")
    if path:
        return path
    return fallback


def _provider(item):
    item = item or {}
    return str(
        item.get("provider") or item.get("provider_domain") or ""
    ).strip()


def classify_download(
    task,
    *,
    is_active=False,
    is_paused=False,
    size_provider=None,
    index=0,
):
    task = task or {}
    downloaded, total, pct = task_progress(task, size_provider)

    if is_active and not is_paused:
        state = STATE_DOWNLOADING
        badge = STATE_LABELS[state]
    elif is_active:
        state = STATE_PARTIAL
        badge = "PAUSED" + (f" \u00b7 {pct}%" if pct is not None else "")
    elif downloaded > 0:
        state = STATE_PARTIAL
        badge = "PARTIAL" + (f" \u00b7 {pct}%" if pct is not None else "")
    else:
        state = STATE_QUEUED
        badge = STATE_LABELS[state]

    title = str(task.get("title") or task.get("filename") or "Unknown download")
    detail = ""
    if downloaded > 0 and total > 0:
        detail = f"{format_bytes(downloaded)} / {format_bytes(total)}"
    elif downloaded > 0:
        detail = f"{format_bytes(downloaded)} downloaded"
    elif task.get("space_needed"):
        detail = f"Space needed: {task.get('space_needed')}"

    return WorkRow(
        kind=KIND_DOWNLOAD,
        state=state,
        row_id=_row_id(task, f"download-{index}"),
        item=task,
        title=title,
        provider=_provider(task),
        badge=badge,
        pct=pct,
        downloaded=downloaded,
        total=total,
        detail=detail,
    )


def classify_extraction(entry, *, running=False, size_provider=None, index=0):
    entry = entry or {}
    archive_path = str(entry.get("archive_path") or "")
    size = disk_size(archive_path, size_provider)
    pct = entry.get("pct")
    try:
        pct = int(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct = None

    if running:
        state = STATE_EXTRACTING
        badge = STATE_LABELS[state]
    elif entry.get("state") == STATE_INTERRUPTED:
        state = STATE_INTERRUPTED
        badge = STATE_LABELS[state] + (f" \u00b7 {pct}%" if pct is not None else "")
    else:
        state = STATE_READY
        badge = STATE_LABELS[state]
        pct = None

    title = str(
        entry.get("title")
        or os.path.basename(archive_path)
        or "Unknown archive"
    )
    return WorkRow(
        kind=KIND_EXTRACT,
        state=state,
        row_id=_row_id(entry, f"extract-{index}"),
        item=entry,
        title=title,
        provider=_provider(entry),
        badge=badge,
        pct=pct,
        downloaded=0,
        total=size,
        detail=format_bytes(size) if size else "Archive ready",
    )


def state_color(theme, state):
    """Resolve a semantic state color from the active theme."""
    theme = theme or {}
    for key in STATE_THEME_KEYS.get(state, ()):
        value = theme.get(key)
        if value:
            return value
    return theme.get("text_primary_white") or "#ffffff"


def badge_for(row):
    return row.badge or STATE_LABELS.get(row.state, "")


def pick_next_download(download_rows):
    """Idle rule: partial downloads before queued ones."""
    for row in download_rows:
        if row.state == STATE_PARTIAL:
            return row
    return download_rows[0] if download_rows else None


def build_layout(
    pending_extractions,
    queue,
    current_task=None,
    *,
    is_extracting=False,
    current_extraction_id=None,
    is_paused=False,
    size_provider=None,
):
    """Build the downloads list.

    The active (or, when idle, the next) row is returned separately as ``top``.
    The remaining rows are grouped into the two optional sections; empty
    sections are omitted so nothing is hard-coded in the UI.
    """
    pending_rows = [
        classify_extraction(
            entry,
            running=bool(
                is_extracting
                and current_extraction_id
                and _row_id(entry, "") == str(current_extraction_id)
            ),
            size_provider=size_provider,
            index=index,
        )
        for index, entry in enumerate(pending_extractions or [])
    ]

    download_rows = [
        classify_download(
            task,
            size_provider=size_provider,
            index=index,
        )
        for index, task in enumerate(queue or [])
    ]

    active_row = None
    if current_task:
        active_row = classify_download(
            current_task,
            is_active=True,
            is_paused=is_paused,
            size_provider=size_provider,
        )

    top = None
    if is_extracting:
        running = next((row for row in pending_rows if row.state == STATE_EXTRACTING), None)
        if running is not None:
            top = running
        else:
            top = active_row
    elif active_row is not None:
        top = active_row
    else:
        if pending_rows:
            top = pending_rows[0]
        else:
            top = pick_next_download(download_rows)

    top_id = top.row_id if top is not None else None
    remaining_pending = [
        row for row in pending_rows if top is None or row.row_id != top_id
    ]
    remaining_downloads = [
        row for row in download_rows if top is None or row.row_id != top_id
    ]

    sections = []
    if remaining_pending:
        sections.append(
            {"label": SECTION_NEEDS_EXTRACTION, "rows": remaining_pending}
        )
    if remaining_downloads:
        sections.append({"label": SECTION_UP_NEXT, "rows": remaining_downloads})

    return {"top": top, "sections": sections}
