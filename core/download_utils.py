"""Pure helpers for the download queue (identity, provider and merging)."""
from __future__ import annotations

import os
from urllib.parse import urlparse

_IDENTITY_KEYS = {"_id", "dest_path", "created_at"}
_INTERNAL_KEYS = {"_restart_partial", "_provider_conflict"}
_DOWNLOAD_KEYS = {
    "url",
    "cookies",
    "user_agent",
    "referer",
    "provider",
    "provider_domain",
}


def normalize_dest_path(path):
    """Canonical key for an archive path (case-insensitive on Windows)."""
    path = str(path or "").strip()
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.expanduser(path)))
    except (OSError, ValueError):
        return path.lower()


def provider_key(task):
    """Identify the provider used by a download task.

    Older tasks do not have the provider fields, so fall back to the hostname
    of the download URL. This lets us distinguish two providers even for tasks
    created before provider tracking existed.
    """
    task = task or {}
    domain = str(task.get("provider_domain") or "").strip().lower()
    if domain:
        return domain
    url = str(task.get("url") or "").strip()
    if url:
        try:
            host = urlparse(url).netloc.lower()
            host = host.replace("www.", "")
            if host:
                return host
        except ValueError:
            pass
    return str(task.get("provider") or "").strip().lower()


def task_created_at(task):
    try:
        return float((task or {}).get("created_at") or 0)
    except (TypeError, ValueError):
        return 0.0


def choose_provider_task(first, second):
    """Pick which duplicate to keep when the providers differ.

    The most recently created task wins; for legacy entries without a
    timestamp, prefer the one that actually carries provider information.
    """
    first_time = task_created_at(first)
    second_time = task_created_at(second)
    if first_time or second_time:
        return (
            (second, first)
            if second_time >= first_time
            else (first, second)
        )
    first_has = bool(provider_key(first))
    second_has = bool(provider_key(second))
    if second_has and not first_has:
        return second, first
    if first_has and not second_has:
        return first, second
    return second, first


def merge_task_data(target, source, keep_download_credentials=False):
    """Copy useful metadata from ``source`` into ``target``.

    Identity fields are never copied. When ``keep_download_credentials`` is
    true, the URL, cookies and provider of ``target`` are preserved (used when
    the user keeps the existing provider and only refreshes display metadata).
    """
    for key, value in (source or {}).items():
        if key in _IDENTITY_KEYS:
            continue
        if key in _INTERNAL_KEYS:
            continue
        if keep_download_credentials and key in _DOWNLOAD_KEYS:
            continue
        if value not in (None, "", [], {}):
            target[key] = value
    return target


def task_has_partial_file(task):
    dest = (task or {}).get("dest_path", "")
    try:
        return bool(dest and os.path.isfile(dest) and os.path.getsize(dest) > 0)
    except OSError:
        return False


def dedupe_tasks(tasks, has_partial_file=None):
    """Collapse saved duplicate tasks by dest_path.

    When two duplicates use different providers and a partial file exists, the
    newest task wins and is flagged with ``_restart_partial``: resuming a file
    from another provider could corrupt the archive.
    """
    checker = has_partial_file or task_has_partial_file
    deduped = []
    positions = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        key = normalize_dest_path(task.get("dest_path"))
        if not key:
            deduped.append(task)
            continue
        if key not in positions:
            positions[key] = len(deduped)
            deduped.append(task)
            continue

        existing = deduped[positions[key]]
        existing_provider = provider_key(existing)
        incoming_provider = provider_key(task)
        conflict = bool(
            checker(existing)
            and existing_provider
            and incoming_provider
            and existing_provider != incoming_provider
        )
        if not conflict:
            merge_task_data(existing, task)
            existing["created_at"] = max(
                task_created_at(existing), task_created_at(task)
            )
            continue

        winner, loser = choose_provider_task(existing, task)
        merge_task_data(winner, loser, keep_download_credentials=True)
        winner["_restart_partial"] = True
        winner["_provider_conflict"] = (
            loser.get("provider")
            or loser.get("provider_domain")
            or "unknown provider"
        )
        deduped[positions[key]] = winner
    return deduped

