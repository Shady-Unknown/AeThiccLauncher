"""Compatibility facade.

The window implementation now lives in ``ui.shell.app_window``. Keeping this
module as a re-export preserves the historical import path used by ``main.py``
and any external script.
"""
from ui.shell.app_window import AppWindow

__all__ = ["AppWindow"]
