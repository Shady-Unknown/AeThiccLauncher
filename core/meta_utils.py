import json
import os
import re
from datetime import datetime

GARBAGE_KEYS = {"description", "game_info", "requirements", "links"}

STEAMRIP_META_FILENAME = "steamrip_meta.json"
# Marker written while an extraction is running. It stays in place when the
# extraction is interrupted, so a partially extracted folder is not mistaken
# for a complete installation.
EXTRACTING_MARKER = ".steamrip_extracting"


def strip_title(raw_title):
    """Extract clean title, version, and multiplayer tag from a SteamRIP-style title.

    Input:  "High On Life 2 Free Download (Build 23508776)"
    Output: ("High On Life 2", "Build 23508776", "")
    """
    version = "Release Base"
    v_match = re.search(r'\((v[\d\.]+.*?|Build.*?|Update.*?|Patch.*?|All DLCs.*?)\)', raw_title, re.IGNORECASE)
    if v_match:
        version = v_match.group(1)

    title = re.sub(r'\s*Free Download.*', '', raw_title, flags=re.IGNORECASE)
    title = re.sub(r'\s*(?:v\d[\d.]*|build\s+\d+).*', '', title, flags=re.IGNORECASE).strip()

    mp_tag = ""
    match_mp = re.search(r'\s*\+\s*(Multiplayer|Co-op|Coop|Online.*)', version, re.IGNORECASE)
    if match_mp:
        mp_tag = match_mp.group(1).strip()
        version = re.sub(r'\s*\+\s*(Multiplayer|Co-op|Coop|Online.*)', '', version, flags=re.IGNORECASE).strip()

    return title, version, mp_tag


def extract_steam_id_from_store_url(url):
    """Extract numeric Steam App ID from a store URL like /app/2069250."""
    m = re.search(r'/app/(\d+)', url)
    return m.group(1) if m else ""


def save_steamrip_meta(install_dir, meta):
    """Shared utility to write steamrip_meta.json.

    All 4 write sites (installer, installer_flow, game_launcher, save_manager)
    converge here so the key set and format stay consistent.
    """
    meta_file = os.path.join(install_dir, "steamrip_meta.json")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    existing = {}
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            existing = {}

    clean = {}
    # Preserve everything that isn't garbage
    for k, v in meta.items():
        if k not in GARBAGE_KEYS:
            clean[k] = v

    # Maintain install_date across re-installs
    if "install_date" not in clean:
        clean["install_date"] = existing.get("install_date", now)
    clean["last_updated"] = now

    try:
        os.makedirs(install_dir, exist_ok=True)
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(clean, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[MetaUtils] Save error: {e}")
