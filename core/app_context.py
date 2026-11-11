"""Composition root for the launcher.

Owns the infrastructure shared by the window, controllers and managers:
configuration, paths, master catalog, event bus and worker registry.

This module intentionally does not import ``ui.*``. Keeping the dependency
direction one-way (ui -> core) is what allows the window to be split into
controllers without circular imports.
"""
from __future__ import annotations

from PyQt6.QtCore import QObject

from core import config as style_settings
from core.events import EventBus
from core.master_catalog import MasterCatalog
from core.paths import PathManager
from core.state_store import StateStore
from core.task_registry import TaskRegistry


class AppContext(QObject):
    def __init__(self, app_config: dict | None = None, parent=None):
        super().__init__(parent)
        self.config = app_config if app_config is not None else style_settings.load_config()
        self.paths = PathManager
        self.events = EventBus(self)
        self.tasks = TaskRegistry(self)
        self.state_store = StateStore(self.events)
        self.master_catalog = MasterCatalog(PathManager.get_master_catalog_path(), self)
        self.installed_games: dict = {}
        # Temporary bridge while the UI is split into controllers (R3).
        # Removed in R4.
        self.window = None

    def bind_window(self, window):
        self.window = window
        return window

    def save_config(self) -> None:
        style_settings.save_config(self.config)
