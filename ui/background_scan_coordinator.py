"""Background library check orchestrator.

Walks the installed games, updates the sidebar subtitle (update available,
missing setup, not in database) and refreshes the Explorer strip for the open
game. Everything here is UI orchestration: version comparison lives in
``core.version_status`` and the setup flags in ``core.meta_status``.
"""
import logging

from PyQt6.QtCore import QObject, pyqtSignal, QTimer

from core.meta_status import missing_setup
from core.version_status import compute_version_state, STATE_UPDATE_AVAILABLE

log = logging.getLogger(__name__)


class BackgroundScanCoordinator(QObject):
    update_found = pyqtSignal(str, dict, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self._queue = []
        self._running = False

    # ---------------------------------------------------------------- queue

    def start_background_update_checker(self):
        log.info("[App] Starting Background Update Scanner...")
        self._queue = list(self.parent.installed_games.keys())
        self._start_if_idle()

    def request_check(self, title):
        """Queue one title and start the walk if it is idle."""
        title = str(title or "").strip()
        if not title:
            return
        if title not in self._queue:
            self._queue.append(title)
        self._start_if_idle()

    def _start_if_idle(self):
        if not self._running:
            self._process_next_bg_check()

    # ---------------------------------------------------------------- walk

    def _process_next_bg_check(self):
        if not self._queue:
            log.info("[App] Background Update Scanner finished.")
            self._running = False
            return

        self._running = True
        game_title = self._queue.pop(0)
        meta = self.parent.installed_games.get(game_title)
        url = meta.get('source_url') if meta else None

        item = self.parent.lib_manager._find_item_by_title(game_title)
        if item:
            # Empty subtitle while checking: only the spinning icon is shown.
            self.parent.lib_manager._update_library_item(
                item, f"🔄 {game_title}", "",
                title_color=None, sub_color="transparent")

        QTimer.singleShot(
            200, lambda: self._evaluate_bg_check(game_title, url, meta))

    def _evaluate_bg_check(self, game_title, url, meta):
        # Re-read the live meta: the snapshot may be stale (save path or Steam
        # integration configured while the walk was running).
        live_meta = self.parent.installed_games.get(game_title)
        if live_meta is not None:
            meta = live_meta
            url = meta.get('source_url', url)

        actual_url = url.rstrip('/') if url and url != 'N/A' else ''
        web_version = ""
        found = False
        db_ready = hasattr(self.parent, 'master_catalog') and bool(self.parent.master_catalog)

        if actual_url and db_ready:
            catalog_entry = self.parent.master_catalog.get_entry(game_title)
            if isinstance(catalog_entry, dict) and catalog_entry.get("url", "").rstrip('/') == actual_url:
                found = True
                web_version = catalog_entry.get("version", "Release Base")

        # Look the item up again: the user may have uninstalled the game.
        item = self.parent.lib_manager._find_item_by_title(game_title)
        if item:
            is_orphan = meta.get('is_orphan', False) if meta else True
            installed_version = meta.get('version', '') if meta else ''

            issues = missing_setup(meta)

            if issues:
                log.info(
                    f"[BGCheck] '{game_title}' issues={issues} | "
                    f"win={meta.get('savegame_path_win')!r} "
                    f"linux={meta.get('savegame_path_linux')!r} "
                    f"custom={meta.get('savegame_path_custom')!r} "
                    f"steam={meta.get('steam_shortcut_id')!r}")

            update_available = False
            if found and meta:
                status = self.parent.master_catalog.get_version_status(game_title)
                update_available = compute_version_state(
                    installed_build=meta.get('build'),
                    posted_build=status.get('posted_build'),
                    steam_build=status.get('steam_build'),
                    installed_version=installed_version,
                    posted_version_text=web_version,
                    posted_version_only=status.get('posted_version', ''),
                    is_installed=True,
                    has_status=bool(status),
                ).get('state') == STATE_UPDATE_AVAILABLE

            title_prefix = game_title
            sub_text = ""
            sub_color = "transparent"

            if update_available:
                title_prefix = f"❗ {game_title}"
                sub_text = "(Update Available!)"
                sub_color = "#00ff88"
            elif not found and not is_orphan and actual_url and db_ready:
                sub_text = "(not in database)"
                sub_color = "#ff5555"
            elif issues:
                if is_orphan:
                    title_prefix = f"❓ {game_title}"
                # e.g. "(Missing: SAVES - STEAM)"
                sub_text = f"(Missing: {' - '.join(issues)})"
                sub_color = "#ffcc00"

            self.parent.lib_manager._update_library_item(
                item, title_prefix, sub_text,
                title_color=None, sub_color=sub_color,
                meta=meta, game_title=game_title)
            if getattr(self.parent, "current_game_data", {}).get("title") == game_title:
                try:
                    self.parent._update_explorer_status(game_title)
                except Exception:
                    # best-effort: the Explorer may not be open for this title
                    pass

        QTimer.singleShot(200, self._process_next_bg_check)
