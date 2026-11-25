"""Where the Steam emulators of SteamRIP releases keep their save data.

The launcher ships no game files, but the release's emulator decides where the
saves live. CODEX, RUNE and PLAZA use a folder under ``%public%``; Goldberg uses
``%appdata%``. The result is a portable Windows path so the existing resolver can
translate it into the Proton prefix (or read it on Windows).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Emulator -> Windows folders holding ``<appid>/``. Newer Goldberg builds use
# "GSE Saves"; the older name is kept as a second candidate because both are
# still shipped and only the prefix can tell which one has the saves.
EMULATOR_FOLDERS = {
    "codex": (r"%public%\Documents\Steam\CODEX",),
    "rune": (r"%public%\Documents\Steam\RUNE",),
    "plaza": (r"%public%\Documents\Steam\PLAZA",),
    "goldberg": (
        r"%appdata%\GSE Saves",
        r"%appdata%\Goldberg SteamEmu Saves",
    ),
}

# Emulator signature files next to the executable.
SIGNATURE_FILES = {
    "steam_api64.cdx": "codex",
    "steam_api.cdx": "codex",
    "steam_api64.rne": "rune",
    "steam_api.rne": "rune",
}

_APPID_INI_RE = re.compile(r"(?im)^\s*AppId\s*=\s*(\d+)\s*$")


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _appid_from_ini(path):
    match = _APPID_INI_RE.search(_read_text(path))
    return match.group(1) if match else ""


def _appid_from_txt(path):
    text = _read_text(path).strip().splitlines()
    if not text:
        return ""
    value = text[0].strip()
    return value if value.isdigit() else ""


def _search_dirs(exe_path, install_dir):
    """Bounded list of folders that may hold the emulator files."""
    dirs = []
    for value in (os.path.dirname(str(exe_path or "")), str(install_dir or "")):
        value = os.path.normpath(value) if value else ""
        if value and os.path.isdir(value) and value not in dirs:
            dirs.append(value)
    # Some releases keep the emulator one level below the executable.
    for parent in list(dirs):
        for name in ("steam_settings", "steamemu", "emu", "Binaries", "Win64", "x64"):
            candidate = os.path.join(parent, name)
            if os.path.isdir(candidate) and candidate not in dirs:
                dirs.append(candidate)
    return dirs


def detect_emulator(exe_path, install_dir="", appid_hint=""):
    """Detect the release's Steam emulator and its save folders.

    Returns ``{"emulator", "appid", "candidates", "windows_path"}`` or ``{}``.
    Detection is intentionally conservative: a folder is only used when both
    the emulator and an appid are identifiable, because the wrong appid
    silently points at an empty folder.

    ``appid_hint`` is the id the game runs under (the Steam shortcut id when the
    launcher starts it through Steam). Goldberg keys its folder by the id the
    process sees, which can be the shortcut id instead of the appid in
    ``steam_appid.txt``, so both are returned as candidates.
    """
    dirs = _search_dirs(exe_path, install_dir)
    if not dirs:
        return {}

    emulator = ""
    appid = ""

    for directory in dirs:
        if not emulator:
            for filename, name in SIGNATURE_FILES.items():
                if os.path.isfile(os.path.join(directory, filename)):
                    emulator = name
                    break
        if not emulator:
            text = _read_text(os.path.join(directory, "steam_emu.ini")).lower()
            for name in ("codex", "rune", "plaza"):
                if name in text:
                    emulator = name
                    break
        if not emulator and os.path.isdir(os.path.join(directory, "steam_settings")):
            emulator = "goldberg"

        ini = os.path.join(directory, "steam_emu.ini")
        if not appid and os.path.isfile(ini):
            appid = _appid_from_ini(ini)
        if not appid:
            appid = _appid_from_txt(os.path.join(directory, "steam_appid.txt"))
        if not appid:
            appid = _appid_from_txt(
                os.path.join(directory, "steam_settings", "steam_appid.txt")
            )

    folders = EMULATOR_FOLDERS.get(emulator)
    if not folders or not appid.isdigit():
        return {}

    ids = []
    for value in (str(appid_hint or "").strip(), appid):
        if value.isdigit() and value not in ids:
            ids.append(value)
    candidates = [
        folder + "\\" + value for folder in folders for value in ids
    ]
    return {
        "emulator": emulator,
        "appid": appid,
        "candidates": candidates,
        "windows_path": candidates[0],
    }
