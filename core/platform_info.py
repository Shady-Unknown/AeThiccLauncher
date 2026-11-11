"""Platform detection helpers.

The Flatpak check used to be repeated in several modules with
``os.path.exists('/.flatpak-info')``. Centralising it keeps the behaviour
identical while making the check cheap and easy to replace in tests.
"""
from __future__ import annotations

import functools
import os
import sys


@functools.lru_cache(maxsize=1)
def is_flatpak() -> bool:
    """True when the process is running inside a Flatpak sandbox."""
    return os.path.exists("/.flatpak-info")


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def is_macos() -> bool:
    return sys.platform == "darwin"
