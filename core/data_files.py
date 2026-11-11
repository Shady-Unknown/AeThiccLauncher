"""Access to shipped data files and their writable user copies.

Two locations exist:

- ``bundled``: ``get_base_dir()/data``. Shipped with the app. In Flatpak this
  is under ``/app/bin`` and is **read-only**. It is only a *seed*: the app never
  reads runtime data from here.
- ``user``: ``PathManager.get_app_data_dir()/data``. Writable, chosen by the
  first-run wizard. Every runtime read uses this copy.

``ensure_user_data()`` copies the seed into the user data root and keeps it
updated with a hash state: files the user has not modified are refreshed when
the app ships a new version; user-edited files are preserved. New achievement
packs are added, existing files are not clobbered without the check above.

``data/steamrip_posts_index.json`` is the exception: it is seeded into
``local_dbs`` by :mod:`network.posts_index`, not into ``data/``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from core.config import get_base_dir
from core.paths import PathManager

SEED_STATE_FILENAME = "data_seed.json"
POSTS_INDEX_FILENAME = "steamrip_posts_index.json"


def bundled_root() -> Path:
    return Path(get_base_dir()) / "data"


def user_root() -> Path:
    return PathManager.get_app_data_dir() / "data"


def bundled_path(relative: str) -> Path:
    return bundled_root() / relative


def user_path(relative: str) -> Path:
    return user_root() / relative


def _read_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def load_bundled_json(relative: str, default=None):
    """Read a bundled seed file (tests/seeding only; not for runtime)."""
    return _read_json(bundled_path(relative), default)


def load_user_json(relative: str, default=None):
    return _read_json(user_path(relative), default)


def load_json(
    relative: str,
    default=None,
    *,
    prefer_user: bool = True,
    schema_key: str = "schema",
    schema_version=None,
):
    """Load runtime data from the user copy only.

    ``prefer_user`` is kept for API compatibility; bundled files are seeds and
    are never used as a runtime fallback. Call :func:`ensure_user_data` first
    (the app does it at startup).
    """
    data = _read_json(user_path(relative), None)
    if data is None:
        return default
    if schema_version is not None and isinstance(data, dict):
        if data.get(schema_key) != schema_version:
            return default
    return data


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _state_path() -> Path:
    return PathManager.get_state_dir() / SEED_STATE_FILENAME


def _load_state() -> dict[str, str]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict[str, str]) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        print(f"[DataFiles] seed state save failed: {exc}")


def _iter_seed_files(source: Path):
    if not source.is_dir():
        return
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if relative.as_posix() == POSTS_INDEX_FILENAME:
            # Handled by network.posts_index -> local_dbs.
            continue
        yield relative, path


def ensure_user_data() -> tuple[int, int]:
    """Copy/refresh the shipped ``data`` seed into the user data root.

    Returns ``(copied, kept)``. Safe to call on every startup.
    """
    source = bundled_root()
    destination = user_root()
    if not source.is_dir():
        return 0, 0
    destination.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    copied = 0
    kept = 0
    for relative, source_file in _iter_seed_files(source):
        rel_key = relative.as_posix()
        target = destination / relative
        bundle_hash = _hash_file(source_file)
        if not bundle_hash:
            continue
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            state[rel_key] = bundle_hash
            copied += 1
            continue
        user_hash = _hash_file(target)
        previous = state.get(rel_key, "")
        if user_hash == bundle_hash:
            state[rel_key] = bundle_hash
            kept += 1
            continue
        if previous and user_hash == previous:
            # Unmodified shipped file: refresh it from the new bundle.
            shutil.copy2(source_file, target)
            state[rel_key] = bundle_hash
            copied += 1
            continue
        if not previous:
            # First run of the seeding feature: keep existing user data.
            state[rel_key] = user_hash
            kept += 1
            continue
        # User edited it: never clobber.
        kept += 1
    _save_state(state)
    return copied, kept


def seed_user_copy(relative: str) -> Path | None:
    """Copy one bundled file into the writable data dir if it is missing."""
    source = bundled_path(relative)
    destination = user_path(relative)
    if destination.exists() or not source.exists():
        return destination if destination.exists() else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
        return destination
    except OSError as exc:
        print(f"[DataFiles] seed failed for {relative}: {exc}")
        return None


def asset_path(relative: str) -> Path:
    """Resolve a runtime asset from the user data root only."""
    return user_path(relative)
