"""Explicit ``DiscoveryServices`` fakes for headless tests.

DiscoveryManager requires its dependencies; these helpers keep the tests
honest without a window and make "no catalog / no installed games" explicit.
"""
from managers.discovery_manager import DiscoveryServices


class FakeCatalog(dict):
    """Minimal catalog: mapping access plus the accessors Discovery uses."""

    def __init__(self, entries=None, db_path=""):
        super().__init__(entries or {})
        self._entries = dict(entries or {})
        self._db_path = db_path

    def __len__(self):
        return len(self._entries)

    def keys(self):
        return self._entries.keys()

    def get_entry(self, title):
        entry = self._entries.get(title)
        return entry if isinstance(entry, dict) else {}

    def set_game_field(self, title, key, value):
        entry = self._entries.setdefault(title, {})
        entry[key] = value

    def get_game_field(self, title, key, default=None):
        entry = self._entries.get(title) or {}
        return entry.get(key, default)


def empty_services():
    """No catalog, no installed games, empty covers."""
    return DiscoveryServices(
        installed_games=lambda: {},
        catalog=lambda: None,
        sync_folder=lambda: "",
        cover_path=lambda _title: "",
    )


def fake_services(catalog=None, installed=None, sync_folder="", cover=None):
    installed = installed if installed is not None else {}
    return DiscoveryServices(
        installed_games=lambda: installed,
        catalog=lambda: catalog,
        sync_folder=lambda: sync_folder,
        cover_path=cover or (lambda _title: ""),
    )
