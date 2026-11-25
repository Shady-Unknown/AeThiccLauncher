"""Isolated Steam userdata/VDF fixtures for identity tests."""
from __future__ import annotations

import os
from pathlib import Path

import vdf

from core.steam_integration import get_steam_ids


def write_shortcuts(root: Path, account_id: str, entries):
    """Write a binary shortcuts.vdf and return its account userdata path."""
    user_dir = root / "userdata" / str(account_id)
    config = user_dir / "config"
    config.mkdir(parents=True, exist_ok=True)
    payload = {"shortcuts": {}}
    for index, entry in enumerate(entries):
        title = entry["title"]
        exe = entry["exe"]
        appid, grid_id, shortcut_id = get_steam_ids(exe, title)
        payload["shortcuts"][str(index)] = {
            "appid": appid,
            "AppName": title,
            "Exe": f'"{exe}"',
            "StartDir": f'"{entry.get("start_dir") or os.path.dirname(exe)}"',
        }
        entry["appid"] = appid
        entry["grid_id"] = grid_id
        entry["shortcut_id"] = shortcut_id
    (config / "shortcuts.vdf").write_bytes(vdf.binary_dumps(payload))
    return user_dir


def make_prefix(root: Path, grid_id: str, relative=("Documents", "inZOI", "SaveGames")):
    prefix = root / "steamapps" / "compatdata" / str(grid_id) / "pfx" / "drive_c" / "users" / "steamuser"
    for part in relative:
        prefix = prefix / part
    prefix.mkdir(parents=True, exist_ok=True)
    return prefix
