"""Secure storage and retrieval for user-provided API credentials.

Priority (first non-empty value wins):

1. environment variables (dev / CI / personal builds)
2. OS keychain (Windows Credential Manager, macOS Keychain, Secret Service)
3. encrypted fallback file ``secrets.json`` in the data root
   - Windows: DPAPI (per-user encryption)
   - Linux/macOS: plain values in a ``0600`` file (keyring is the normal path)

No credential is ever written to ``config.json`` or logged.
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import requests

from core.paths import PathManager

SERVICE_NAME = "AeThiccLauncher"
SECRETS_FILENAME = "secrets.json"

SGDB_API_KEY = "sgdb_api_key"
IGDB_CLIENT_ID = "igdb_client_id"
IGDB_CLIENT_SECRET = "igdb_client_secret"

ENV_VARS = {
    SGDB_API_KEY: "AETHICC_SGDB_KEY",
    IGDB_CLIENT_ID: "AETHICC_IGDB_CLIENT_ID",
    IGDB_CLIENT_SECRET: "AETHICC_IGDB_CLIENT_SECRET",
}

SGDB_VALIDATE_URL = "https://www.steamgriddb.com/api/v2/search/autocomplete/portal"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

_keyring_module = None
_keyring_checked = False
_value_cache: dict[str, str] = {}


def clear_cache() -> None:
    """Drop the in-memory secret cache (tests / external env changes)."""
    _value_cache.clear()


# ── keyring ───────────────────────────────────────────────────────────

def _keyring():
    global _keyring_module, _keyring_checked
    if not _keyring_checked:
        _keyring_checked = True
        try:
            import keyring as _kr
            _keyring_module = _kr
        except Exception:
            _keyring_module = None
    return _keyring_module


def _keyring_get(name: str) -> str:
    kr = _keyring()
    if kr is None:
        return ""
    try:
        return kr.get_password(SERVICE_NAME, name) or ""
    except Exception:
        return ""


def _keyring_set(name: str, value: str) -> bool:
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE_NAME, name, value)
        return True
    except Exception:
        return False


def _keyring_delete(name: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE_NAME, name)
    except Exception:
        pass


# ── encrypted fallback file ──────────────────────────────────────────

def _secrets_path() -> Path:
    return PathManager.get_data_root() / SECRETS_FILENAME


def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI encrypt/decrypt using ctypes (no extra dependency)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
    if not ok:
        raise OSError("DPAPI operation failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _encode_file_value(value: str) -> str:
    if sys.platform == "win32":
        try:
            return "dpapi:" + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")
        except Exception as exc:
            print(f"[Secrets] DPAPI encrypt failed: {exc}")
    return value


def _decode_file_value(value: str) -> str:
    if value.startswith("dpapi:"):
        if sys.platform != "win32":
            return ""
        try:
            raw = base64.b64decode(value[6:])
            return _dpapi(raw, False).decode("utf-8", errors="replace")
        except Exception as exc:
            print(f"[Secrets] DPAPI decrypt failed: {exc}")
            return ""
    return value


def _read_file_store() -> dict[str, str]:
    path = _secrets_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    store = {}
    for name, value in raw.items():
        if isinstance(value, str) and value:
            decoded = _decode_file_value(value)
            if decoded:
                store[str(name)] = decoded
    return store


def _write_file_store(store: dict[str, str]) -> bool:
    path = _secrets_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {name: _encode_file_value(value) for name, value in store.items() if value}
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
        return True
    except OSError as exc:
        print(f"[Secrets] Cannot write {path}: {exc}")
        return False


def _file_get(name: str) -> str:
    return _read_file_store().get(name, "")


def _file_set(name: str, value: str) -> bool:
    store = _read_file_store()
    store[name] = value
    return _write_file_store(store)


def _file_delete(name: str) -> None:
    store = _read_file_store()
    if name in store:
        store.pop(name, None)
        _write_file_store(store)


# ── public API ───────────────────────────────────────────────────────

def get_secret(name: str) -> str:
    env_name = ENV_VARS.get(name)
    if env_name:
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    if name in _value_cache:
        return _value_cache[name]
    value = _keyring_get(name)
    if value:
        _value_cache[name] = value
        return value
    value = _file_get(name)
    if value:
        _value_cache[name] = value
        return value
    _value_cache[name] = ""
    return ""


def secret_source(name: str) -> str:
    env_name = ENV_VARS.get(name)
    if env_name and os.environ.get(env_name, "").strip():
        return "env"
    if _keyring_get(name):
        return "keyring"
    if _file_get(name):
        return "file"
    return ""


def set_secret(name: str, value: str) -> bool:
    value = (value or "").strip()
    _value_cache.pop(name, None)
    if not value:
        delete_secret(name)
        return True
    if _keyring_set(name, value):
        # Avoid a stale duplicate in the fallback file.
        _file_delete(name)
        _value_cache[name] = value
        return True
    ok = _file_set(name, value)
    if ok:
        _value_cache[name] = value
    return ok


def delete_secret(name: str) -> None:
    _keyring_delete(name)
    _file_delete(name)
    _value_cache.pop(name, None)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))


def get_igdb_credentials() -> tuple[str, str]:
    return get_secret(IGDB_CLIENT_ID), get_secret(IGDB_CLIENT_SECRET)


def has_igdb_credentials() -> bool:
    client_id, secret = get_igdb_credentials()
    return bool(client_id and secret)


def get_sgdb_key() -> str:
    return get_secret(SGDB_API_KEY)


def has_sgdb_key() -> bool:
    return bool(get_sgdb_key())


def sgdb_headers() -> dict[str, str]:
    key = get_sgdb_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


# ── validation (network, call from a worker/UI thread) ───────────────

def validate_sgdb_key(key: str | None = None) -> tuple[bool, str]:
    value = (key if key is not None else get_sgdb_key()).strip()
    if not value:
        return False, "SteamGridDB key not configured."
    try:
        response = requests.get(
            SGDB_VALIDATE_URL,
            headers={"Authorization": f"Bearer {value}"},
            timeout=8,
        )
    except Exception as exc:
        return False, f"Network error: {exc}"
    if response.status_code == 200:
        return True, "SteamGridDB key is valid."
    if response.status_code in (401, 403):
        return False, "SteamGridDB rejected the key (401/403)."
    return False, f"SteamGridDB returned HTTP {response.status_code}."


def validate_igdb_credentials(
    client_id: str | None = None,
    client_secret: str | None = None,
) -> tuple[bool, str]:
    cid = (client_id if client_id is not None else get_secret(IGDB_CLIENT_ID)).strip()
    secret = (
        client_secret
        if client_secret is not None
        else get_secret(IGDB_CLIENT_SECRET)
    ).strip()
    if not cid or not secret:
        return False, "IGDB client ID and secret are required."
    try:
        response = requests.post(
            TWITCH_TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
    except Exception as exc:
        return False, f"Network error: {exc}"
    if response.status_code == 200 and response.json().get("access_token"):
        return True, "IGDB credentials are valid."
    if response.status_code in (400, 401, 403):
        return False, "IGDB/Twitch rejected the credentials."
    return False, f"Twitch returned HTTP {response.status_code}."
