"""Steam id conversions shared by the launcher.

Steam stores playtime under the signed 32-bit appid in ``localconfig.vdf`` and
uses the unsigned form as the library/shortcut grid id; account folders are the
32-bit account id, while most APIs expose the 64-bit SteamID. Keeping the
arithmetic in one place avoids the divergent copies that used to live in the
playtime, stats and integration modules.
"""
from __future__ import annotations

STEAMID64_BASE = 76561197960265728
_APPID_32 = 1 << 32
_APPID_SIGN = 1 << 31


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def signed_appid(value):
    """Signed 32-bit appid used as the ``localconfig.vdf`` key."""
    appid = _int(value)
    if appid is None:
        return None
    appid %= _APPID_32
    return appid - _APPID_32 if appid >= _APPID_SIGN else appid


def unsigned_appid(value):
    """Unsigned 32-bit appid used by grid ids and Steam shortcuts."""
    appid = _int(value)
    return None if appid is None else appid % _APPID_32


def account_id_from_steamid64(steamid64):
    """``userdata/<id>`` account id for a 64-bit SteamID."""
    value = _int(steamid64)
    return None if value is None else value - STEAMID64_BASE


def steamid64_from_account_id(account_id):
    """64-bit SteamID for a ``userdata/<id>`` account id."""
    value = _int(account_id)
    return None if value is None else value + STEAMID64_BASE
