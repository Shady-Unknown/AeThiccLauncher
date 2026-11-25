"""Ownership of the derived catalog match index.

``MatchIndexCache`` is the only owner of the index, its token and the shared
fuzzy-match cache. The token is the manager's catalogue identity
``(id(catalog), len, mtime, match_revision)``; when it changes the object
rebuilds the index and drops the fuzzy entries, so the fields can never drift
apart. Catalog-like providers without ``match_revision`` fall back to 0.
"""
from __future__ import annotations

from managers import discovery_matching


class MatchIndexCache:
    def __init__(self):
        self._entries = None
        self._token = None
        self._cache = {}
        self._catalog_ref = None

    @property
    def cache(self):
        """Fuzzy results shared with ``discovery_matching.match_game``."""
        return self._cache

    @property
    def catalog_ref(self):
        """Strong reference that keeps the token's ``id`` meaningful."""
        return self._catalog_ref

    def get(self, catalog, token):
        if self._entries is not None and self._token == token:
            return self._entries
        self._cache = {}
        self._entries = discovery_matching.build_index(catalog)
        self._token = token
        self._catalog_ref = catalog
        return self._entries

    def invalidate(self):
        self._entries = None
        self._token = None
        self._cache = {}
        self._catalog_ref = None
