"""Game version identity recorded with savegame backups.

The launcher learns the installed build at install time (the SteamRIP "Version
Status" widget exposes ``posted_build``), keeps the human label found in the
page title and may infer the build later. This module turns those meta fields
into one opaque, serializable record and compares two records without
interpreting version strings: forward compatibility matters more than smart
ordering, and an unknown version must never block a restore.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from core.meta_utils import is_local_install

SOURCE_STEAMRIP = "steamrip"
SOURCE_LOCAL = "local"
SOURCE_STEAM = "steam"
SOURCE_UNKNOWN = "unknown"

STATUS_SAME = "same"
STATUS_DIFFERENT = "different"
STATUS_UNKNOWN = "unknown"

# Prefixes that do not change the identity of a label ("Build 25922370" and
# "25922370" are the same thing).
_LABEL_PREFIX = re.compile(r"^(?:v(?:ersion)?\.?\s*|build\s+)", re.IGNORECASE)
# Labels that mean "no version information" rather than a real version.
_EMPTY_LABELS = {"release base", "release", "unknown", "n/a", "none", "-"}


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def to_build(value):
    """Return the numeric build id as ``int``, or ``None`` when absent."""
    if value is None or isinstance(value, bool):
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def normalize_label(label) -> str:
    """Comparable form of a human version label (``""`` when meaningless)."""
    text = clean_text(label)
    if not text or text.casefold() in _EMPTY_LABELS:
        return ""
    text = _LABEL_PREFIX.sub("", text)
    return text.strip().casefold()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def version_info_from_meta(meta, now=None) -> dict:
    """Build the version record stored with a savegame snapshot."""
    meta = meta or {}
    build = to_build(meta.get("build"))
    label = clean_text(meta.get("version"))
    web_version = clean_text(meta.get("web_version"))
    inferred = bool(meta.get("build_inferred"))

    if build or normalize_label(label):
        # A real build id always identifies a SteamRIP build, even when the
        # older meta has no source_url (is_local_install would say "local").
        source = SOURCE_LOCAL if meta.get("is_local") is True else SOURCE_STEAMRIP
    elif is_local_install(meta):
        source = SOURCE_LOCAL
    else:
        source = SOURCE_UNKNOWN

    return {
        "build": build,
        "build_date": clean_text(meta.get("build_date")),
        "label": label,
        "web_version": web_version,
        "source": source,
        "inferred": inferred,
        "recorded_at": now or utc_now_iso(),
    }


def version_identity(info) -> tuple[str, str]:
    """Return ``(kind, value)`` used for equality comparisons."""
    info = info or {}
    build = to_build(info.get("build"))
    if build:
        return ("build", str(build))
    label = normalize_label(info.get("label"))
    if label:
        return ("label", label)
    return ("", "")


def compare_versions(backup, installed) -> dict:
    """Compare the version recorded with a backup against the installed one.

    Returns ``{status, reason, backup, installed}`` where ``status`` is one of
    ``same``, ``different`` or ``unknown``. ``unknown`` is a valid answer: a
    restore is never blocked, the UI only warns.
    """
    backup = backup if isinstance(backup, dict) else {}
    installed = installed if isinstance(installed, dict) else {}
    kind_backup, value_backup = version_identity(backup)
    kind_installed, value_installed = version_identity(installed)

    result = {
        "status": STATUS_UNKNOWN,
        "reason": "",
        "backup": backup,
        "installed": installed,
    }

    if not value_backup and not value_installed:
        result["reason"] = "no_version_info"
        return result
    if not value_backup:
        result["reason"] = "backup_missing"
        return result
    if not value_installed:
        result["reason"] = "installed_missing"
        return result

    if kind_backup == kind_installed:
        result["status"] = STATUS_SAME if value_backup == value_installed else STATUS_DIFFERENT
        result["reason"] = kind_backup
        return result

    # One side only has a human label ("v1.2.3") while the other has a build
    # id. Equal strings are still a match; anything else stays unknown instead
    # of raising a false warning.
    if value_backup == value_installed:
        result["status"] = STATUS_SAME
        result["reason"] = "mixed_match"
    else:
        result["reason"] = "mixed_incomparable"
    return result


def describe_version(info) -> str:
    """Human readable version, for tooltips and dialogs."""
    info = info or {}
    build = to_build(info.get("build"))
    if build:
        text = "build {}".format(build)
        date = clean_text(info.get("build_date"))
        if date:
            text += " ({})".format(date)
        if info.get("inferred"):
            text += " · inferred"
        return text
    label = clean_text(info.get("label"))
    if normalize_label(label):
        return label
    if str(info.get("source") or "") == SOURCE_LOCAL:
        return "unknown (local install)"
    return "unknown"
