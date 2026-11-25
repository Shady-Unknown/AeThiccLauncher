"""Shared filename helpers used by the launcher, downloads and savegames."""
from __future__ import annotations

import re


def safe_game_folder_name(game_title):
    """Turn a display title into a portable folder name."""
    name = re.sub(r'[\\/*?:"<>|]', '', game_title)
    name = re.sub(r"\s+", "_", name.strip())
    return name or "unknown_game"


def safe_filename(filename):
    """Strip characters that Windows does not allow in file names."""
    return re.sub(r'[\\/*?:"<>|]', "_", filename)
