"""Setup flags shared by the library subtitle and the Explorer status strip.

The background scanner and the library indicators used to test the same three
conditions (metadata present, save path configured, Steam shortcut created) with
two copies of the same expressions.
"""
from __future__ import annotations


def has_save_path(meta) -> bool:
    """True when the game has a portable or custom save path configured."""
    if not isinstance(meta, dict):
        return False
    return bool(
        meta.get("savegame_path_win")
        or meta.get("savegame_path_linux")
        or meta.get("savegame_path_custom")
    )


def has_steam_shortcut(meta) -> bool:
    """True when the game has a non-empty Steam shortcut id."""
    if not isinstance(meta, dict):
        return False
    return bool(str(meta.get("steam_shortcut_id") or "").strip())


def missing_setup(meta) -> list[str]:
    """Missing badges for a library row: ``JSON``, ``SAVES`` and/or ``STEAM``.

    Orphan metadata only reports ``JSON``; the other checks would all fail for a
    metadata file that could not be read.
    """
    if not isinstance(meta, dict) or meta.get("is_orphan"):
        return ["JSON"]
    missing = []
    if not has_save_path(meta):
        missing.append("SAVES")
    if not has_steam_shortcut(meta):
        missing.append("STEAM")
    return missing
