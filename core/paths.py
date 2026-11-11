"""Filesystem layout for the launcher.

Historically every runtime artefact (config, caches, stats, logs, staging)
lived under the fixed platform directory ``%LOCALAPPDATA%/AeThiccLauncher``
(or the XDG equivalent on Linux). The first-run wizard and the installer now
allow choosing a *data root* on any drive, so that cache and data can live
next to the games instead of the system disk.

The root is resolved lazily (as late as possible, so callers can point the
app at a new root before the first path is used) in this order:

1. ``AETHICC_DATA_DIR`` environment variable (dev / tests).
2. Bootstrap file ``aethicc.paths.json`` next to the executable, then in the
   legacy directory. It contains ``{"data_root": "<absolute or relative>"}``;
   a relative path is resolved against the bootstrap file's own directory, so
   the whole folder stays movable.
3. Legacy ``%LOCALAPPDATA%/AeThiccLauncher`` (unchanged behaviour).

Use :meth:`PathManager.set_data_root` to point the app at a new root (writes
the bootstrap and creates the directory tree) and :meth:`PathManager.initialize`
to (re)create every subdirectory.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BOOTSTRAP_FILENAME = "aethicc.paths.json"
LEGACY_APP_DIRNAME = "AeThiccLauncher"
ENV_DATA_DIR = "AETHICC_DATA_DIR"


def _executable_dir() -> Path:
    """Folder that holds the executable (frozen) or the repo root (dev)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _legacy_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / LEGACY_APP_DIRNAME
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / LEGACY_APP_DIRNAME


def _bootstrap_candidates() -> list[Path]:
    return [
        _executable_dir() / BOOTSTRAP_FILENAME,
        _legacy_root() / BOOTSTRAP_FILENAME,
    ]


def _expand(path: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(path.strip())))


def _read_bootstrap() -> tuple[Path | None, Path | None]:
    """Return ``(bootstrap_path, resolved_root)`` or ``(None, None)``."""
    for candidate in _bootstrap_candidates():
        try:
            if not candidate.is_file():
                continue
            data = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        raw = data.get("data_root")
        if not raw:
            continue
        root = _expand(str(raw))
        if not root.is_absolute():
            root = candidate.parent / root
        return candidate, root.resolve()
    return None, None


def _resolve_data_root() -> Path:
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return _expand(env).resolve()
    _, root = _read_bootstrap()
    if root is not None:
        return root
    return _legacy_root()


class PathManager:
    _root: Path | None = None
    _bootstrap_path: Path | None = None
    _bootstrap_checked: bool = False

    # ── root resolution ───────────────────────────────────────────────

    @classmethod
    def get_data_root(cls) -> Path:
        if cls._root is None:
            cls._root = _resolve_data_root()
        return cls._root

    @classmethod
    def get_bootstrap_path(cls) -> Path | None:
        """Bootstrap file currently in use, if any."""
        if not cls._bootstrap_checked:
            cls._bootstrap_path, _ = _read_bootstrap()
            cls._bootstrap_checked = True
        return cls._bootstrap_path

    @classmethod
    def _writable_bootstrap_target(cls) -> Path:
        """First bootstrap location we can write to (exe dir, else legacy)."""
        exe_file = _executable_dir() / BOOTSTRAP_FILENAME
        try:
            exe_file.parent.mkdir(parents=True, exist_ok=True)
            probe = exe_file.parent / ".aethicc_write_probe"
            probe.touch()
            probe.unlink()
            return exe_file
        except OSError:
            legacy = _legacy_root()
            legacy.mkdir(parents=True, exist_ok=True)
            return legacy / BOOTSTRAP_FILENAME

    @classmethod
    def set_data_root(cls, path) -> Path:
        """Persist ``path`` as the data root and recreate the tree.

        Returns the bootstrap file that was written.
        """
        root = _expand(str(path)).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = cls._writable_bootstrap_target()
        payload = {"data_root": str(root)}
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, target)

        cls._root = root
        cls._bootstrap_path = target
        cls._bootstrap_checked = True
        cls.initialize()
        return target

    @classmethod
    def reset_cache(cls) -> None:
        """Forget the resolved root (used by tests and by the first-run wizard)."""
        cls._root = None
        cls._bootstrap_path = None
        cls._bootstrap_checked = False

    @staticmethod
    def get_legacy_root() -> Path:
        """Historical platform directory (used for first-run migration)."""
        return _legacy_root()

    @staticmethod
    def get_legacy_cache_dir() -> Path:
        """Cache location before the data-root feature (for migration)."""
        if sys.platform == "win32":
            return _legacy_root() / "Cache"
        base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
        return Path(base) / LEGACY_APP_DIRNAME / "downloads"

    # ── per-purpose directories ───────────────────────────────────────

    @staticmethod
    def get_app_data_dir() -> Path:
        return PathManager.get_data_root()

    @classmethod
    def has_custom_root(cls) -> bool:
        """True when a data root was chosen explicitly (env or bootstrap)."""
        if os.environ.get(ENV_DATA_DIR):
            return True
        return cls.get_bootstrap_path() is not None

    @staticmethod
    def get_cache_dir() -> Path:
        # Preserve the historical Linux cache location (~/.cache/...) for users
        # who never opted into a custom data root, so downloads/resume survive.
        if sys.platform != "win32" and not PathManager.has_custom_root():
            base = os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
            return Path(base) / LEGACY_APP_DIRNAME / "downloads"
        root = PathManager.get_data_root()
        return root / "Cache" if sys.platform == "win32" else root / "downloads"

    @staticmethod
    def get_staging_dir() -> Path:
        root = PathManager.get_data_root()
        return root / "Staging" if sys.platform == "win32" else root / "staging"

    @staticmethod
    def get_dbs_dir() -> Path:
        return PathManager.get_data_root() / "local_dbs"

    @staticmethod
    def get_master_catalog_path() -> str:
        return str(PathManager.get_dbs_dir() / "master_catalog.json")

    @staticmethod
    def get_config_dir() -> Path:
        return PathManager.get_data_root() / "config"

    @staticmethod
    def get_stats_dir() -> Path:
        return PathManager.get_data_root() / "stats"

    @staticmethod
    def get_state_dir() -> Path:
        return PathManager.get_data_root() / "state"

    @staticmethod
    def get_logs_dir() -> Path:
        return PathManager.get_data_root() / "logs"

    # ── creation ──────────────────────────────────────────────────────

    @classmethod
    def initialize(cls) -> None:
        dirs = [
            cls.get_cache_dir(),
            cls.get_staging_dir(),
            cls.get_dbs_dir(),
            cls.get_config_dir(),
            cls.get_stats_dir(),
            cls.get_state_dir(),
            cls.get_logs_dir(),
        ]
        for directory in dirs:
            try:
                directory.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                print(f"[Paths] Cannot create {directory}: {exc}")


def initialize() -> None:
    """Module-level convenience matching the historical ``PathManager.initialize()``."""
    PathManager.initialize()


# Historical behaviour: create the directory tree on import. Kept so callers
# that never call initialize() explicitly still find their folders ready.
PathManager.initialize()
