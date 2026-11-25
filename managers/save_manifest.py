"""Ludusavi manifest acquisition, parsing and lookup.

The store owns the lock, the per-session fetch lifecycle, the ETag sidecar and
the two caches (normalized title -> YAML block, block -> parsed entry). The
SavegamesManager keeps the background-thread orchestration and exposes the
public ``find_manifest_entry`` facade.
"""
from __future__ import annotations

import logging
import os
import re
import threading

import requests
import yaml

from core import titles
from core.atomic_io import atomic_write_text

log = logging.getLogger(__name__)

LUDUSAVI_MANIFEST_URL = (
    "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/"
    "master/data/manifest.yaml"
)


class LudusaviManifest:
    def __init__(self, manifest_path, etag_path):
        self.manifest_path = str(manifest_path)
        self.etag_path = str(etag_path)
        self._lock = threading.Lock()
        self._checked = False
        self._index_cache = None
        self._entry_cache = {}

    def invalidate(self):
        self._index_cache = None
        self._entry_cache = {}

    def ensure(self, force=False):
        """Check/download the manifest at most once per session unless forced."""
        with self._lock:
            if not force and self._checked:
                return os.path.exists(self.manifest_path)
            self._checked = True

            headers = {}
            if os.path.exists(self.manifest_path) and os.path.exists(self.etag_path):
                try:
                    with open(self.etag_path, 'r') as f:
                        cached_etag = f.read().strip()
                    if cached_etag:
                        headers['If-None-Match'] = cached_etag
                except Exception:
                    # best-effort: the cached ETag is optional; refetch without it
                    pass

            try:
                r = requests.get(
                    LUDUSAVI_MANIFEST_URL, headers=headers, stream=True, timeout=5)

                if r.status_code == 304:
                    log.info("[Savegames] Manifest locale già aggiornato (HTTP 304). Nessuna azione.")
                    return True

                if r.status_code == 200:
                    log.info("[Savegames] Download nuovo manifest da GitHub in corso...")
                    # Scrittura Atomica: scriviamo su un file .tmp per non corrompere la lettura in tempo reale
                    tmp_path = self.manifest_path + ".tmp"
                    with open(tmp_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    if not self._valid_manifest_file(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except OSError:
                            pass
                        log.warning(
                            "[Savegames] Downloaded manifest is invalid; "
                            "keeping the previous copy")
                        return os.path.exists(self.manifest_path)

                    # Sostituzione istantanea del file vecchio col nuovo
                    os.replace(tmp_path, self.manifest_path)

                    new_etag = r.headers.get('ETag')
                    if new_etag:
                        try:
                            # The ETag is a cache hint, not backup data: keep
                            # it atomic without failing a valid manifest when
                            # the small sidecar cannot be written.
                            atomic_write_text(self.etag_path, new_etag, fsync=True)
                        except OSError as exc:
                            log.warning(
                                "[Savegames] Could not write manifest ETag: %s",
                                exc)

                    self.invalidate()
                    log.info("[Savegames] Download completato con successo! (Aggiornato in local_dbs)")
                    return True

            except Exception as e:
                log.exception(f"[Savegames] Nessuna connessione o GitHub irraggiungibile: {e}")

            return os.path.exists(self.manifest_path)

    @staticmethod
    def _valid_manifest_file(path):
        """Validate the downloaded YAML without building the whole object tree."""
        try:
            if os.path.getsize(path) <= 0:
                return False
            first = None
            depth = 0
            root_pairs = 0
            nested_value = False
            documents = 0
            with open(path, 'rb') as f:
                for event in yaml.parse(f):
                    if isinstance(event, yaml.events.StreamStartEvent):
                        continue
                    if isinstance(event, yaml.events.DocumentStartEvent):
                        documents += 1
                        continue
                    if isinstance(event, yaml.events.DocumentEndEvent):
                        continue
                    if first is None:
                        first = event
                        if not isinstance(event, yaml.events.MappingStartEvent):
                            continue
                    if isinstance(event, yaml.events.MappingStartEvent):
                        depth += 1
                        if depth == 2:
                            nested_value = True
                    elif isinstance(event, yaml.events.MappingEndEvent):
                        depth = max(0, depth - 1)
                    elif isinstance(event, yaml.events.ScalarEvent) and depth == 1:
                        root_pairs += 1
            # A syntactically valid YAML document can still be an empty list or
            # a flat mapping. Ludusavi needs a non-empty root mapping whose
            # entries are blocks (the nested mapping).
            return (isinstance(first, yaml.events.MappingStartEvent)
                    and documents == 1
                    and depth == 0
                    and root_pairs > 0
                    and nested_value)
        except (OSError, yaml.YAMLError):
            return False

    @staticmethod
    def _extract_manifest_key(line):
        match = re.match(r'^\s*["\']?(.*?)["\']?\s*:\s*$', line)
        if match:
            return match.group(1).strip()
        return None

    def blocks(self):
        """Read the local manifest block by block without loading it all."""
        if not self.ensure():
            log.warning("[Savegames] Errore: Nessun manifest locale disponibile e rete assente.")
            return

        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                current_key = None
                current_block = []
                for raw_line in f:
                    line = raw_line.rstrip('\r\n')
                    if re.match(r'^[^ \t].*:\s*$', line):
                        if current_key is not None and current_block:
                            yield current_key, current_block
                        current_key = self._extract_manifest_key(line)
                        current_block = [line]
                    elif current_key is not None:
                        current_block.append(line)

                if current_key is not None and current_block:
                    yield current_key, current_block

        except Exception as exc:
            log.exception(f"[Savegames] Fallita la lettura del manifest locale: {exc}")
            return

    @staticmethod
    def normalize_title(title):
        return titles.normalize_spaced(title)

    def _normalize_key(self, key):
        if not key:
            return ""
        return self.normalize_title(key)

    def index(self):
        """Normalized title -> (raw key, block) built once per manifest file."""
        if self._index_cache is not None:
            return self._index_cache
        index = {}
        for key, block_lines in self.blocks():
            key_norm = self._normalize_key(key)
            if key_norm and key_norm not in index:
                index[key_norm] = (key, "\n".join(block_lines))
        self._index_cache = index
        return index

    def parse_entry(self, key, block_text):
        if key in self._entry_cache:
            return self._entry_cache[key]
        try:
            parsed = yaml.safe_load(block_text)
            entry = parsed.get(key) if isinstance(parsed, dict) else None
        except Exception as exc:
            log.exception(f"[Savegames] Failed to parse block for match '{key}': {exc}")
            entry = None
        self._entry_cache[key] = entry
        return entry

    def find_entry(self, game_title):
        if not game_title:
            return None

        normalized_title = self.normalize_title(game_title)
        index = self.index()
        exact = index.get(normalized_title)
        if exact is not None:
            return self.parse_entry(*exact)

        # The substring fallback is for edition/collection suffixes. A generic
        # one-word title must not match an unrelated game ("Menace" used to
        # match "Asdivine Menace"), because a wrong save path is worse than no
        # path: the user can still set one manually.
        if len(normalized_title.split()) < 2:
            return None
        for key_norm, (key, block_text) in index.items():
            if len(key_norm) > 2 and (
                    normalized_title in key_norm or key_norm in normalized_title):
                return self.parse_entry(key, block_text)
        return None
