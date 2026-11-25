import logging
import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSizePolicy, QLineEdit,
    QComboBox, QCheckBox, QPushButton,
)

from ui.widgets.discovery_card import GLOW_INSET, GLOW_PAD, CARD_TITLE_H
from ui.widgets.alpha_mask import MaskOverlay
from core import secrets
from core.mini_cover_cache import MiniCoverManager
from ui.tabs.discovery_scroll import PhysicsScrollArea, _perf
from ui.tabs.discovery_rows import (
    CARD_SPACING, CategoryRow, FADE_MARGIN, InfiniteCategoryScroller,
)

log = logging.getLogger(__name__)

__all__ = [
    "CategoryRow",
    "DiscoveryTab",
    "InfiniteCategoryScroller",
    "PhysicsScrollArea",
]

SORT_CHOICES = (
    ("", "Default order"),
    ("title_asc", "Title A → Z"),
    ("title_desc", "Title Z → A"),
    ("added_desc", "Added: newest first"),
    ("added_asc", "Added: oldest first"),
    ("release_desc", "Release: newest first"),
    ("release_asc", "Release: oldest first"),
    ("rating_desc", "Rating: best first"),
)
# A row can match very few posts: without overflow there is no scroll event,
# so the next page was never requested and the row stayed at a handful of
# cards. Top it up while it is in focus.
MIN_ROW_CARDS = 20


COVER_WINDOW_ROWS = 6
COVER_WINDOW_CARDS = 15
#: Per-title IGDB metadata for the cards on screen is requested first, so the
#: provisional SteamRip cover is upgraded within a few seconds instead of
#: waiting for the whole row prefetch batch.
META_VISIBLE_FIRST = 8


class DiscoveryTab(QWidget):

    #: Banner shown while the optional IGDB credentials are missing.
    CONFIG_NOTICE_TITLE = "IGDB not configured"
    CONFIG_NOTICE_BODY = (
        "Trending and New Releases are hidden. Add your IGDB credentials "
        "in Settings → API Keys (optional)."
    )

    def __init__(self, main_window):
        super().__init__()
        self.mw = main_window
        self._category_rows = []
        self._categories = []
        self._category_keys = []
        self._row_loading = {}
        self._load_cooldown = {}
        self._search_mode = False
        self._search_row = None
        self._saved_focus = 1
        self._meta_requested_keys = set()
        self._dirty_keys = set()
        self._sort_value = ""
        self.setObjectName("DiscoveryTab")
        self._init_ui()

    def _discovery(self):
        return getattr(self.mw, 'discovery_manager', None)

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self._categories = [
            {"title": "\U0001f4da My Library", "provider": self._library_provider},
        ]

        discovery = self._discovery()
        if discovery is not None:
            for cat in discovery.categories():
                self._categories.append({"title": cat["title"], "provider": None})
                self._category_keys.append(cat["key"])

        self._build_filter_bar(root_layout)
        self._build_notice_bar(root_layout)
        self._build_category_rows()

        self._scroller = InfiniteCategoryScroller(self._category_rows)
        self._scroller.set_single(True)
        self._scroller.row_focus_changed.connect(self._update_cover_window)
        self._scroller.row_focus_changed.connect(self._sync_jump_combo)
        self._scroller.row_focus_changed.connect(self._sync_sort_combo)
        root_layout.addWidget(self._scroller, 1)

        self.footer_label = QLabel("")
        self.footer_label.setObjectName("DiscoveryFooter")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_label.setWordWrap(True)
        self.footer_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        root_layout.addWidget(self.footer_label, 0)

        self._sync_jump_combo()
        self._sync_sort_combo()
        self._rebuild_visible_rows()

        self._mask_overlay = MaskOverlay(self)
        self._mask_overlay.raise_()
        if hasattr(self, 'footer_label'):
            self.footer_label.raise_()
        if hasattr(self, '_filter_bar_widget'):
            self._filter_bar_widget.raise_()

        self._recalc_layout()
        self._apply_theme()
        self.refresh_config_notice()
        QTimer.singleShot(0, self._recalc_layout)

        if discovery is not None:
            discovery.categories_changed.connect(self._on_discovery_changed)
            discovery.page_changed.connect(self._on_discovery_page_changed)
            discovery.row_reset.connect(self._on_discovery_row_reset)
            discovery.cover_ready.connect(self._on_cover_ready)
            discovery.meta_ready.connect(self._on_meta_ready)
            discovery.start()

        # I trailer si caricano solo quando l'utente interagisce (focus row).

    def _build_category_rows(self):
        for i, cat in enumerate(self._categories):
            if i == 0:
                key = None
                cards_data = cat["provider"]()
            else:
                key = self._category_keys[i - 1]
                # Lazy: le card della riga si costruiscono solo quando è visibile.
                cards_data = []
            row = CategoryRow(cat["title"], cards_data,
                               theme=self._theme_data(), progressive=True)
            row._category_key = key
            self._category_rows.append(row)
            self._wire_row(row)
        self._dirty_keys = set(self._category_keys)

    # ── filters ──────────────────────────────────────────────────────

    def _build_filter_bar(self, root_layout):
        theme = self._theme_data() or {}
        muted = theme.get('text_muted', '#8a7fa4')
        text = theme.get('text_primary_white', '#ffffff')
        accent = theme.get('main_accent_color', '#733d91')
        neon = theme.get('bright_neon_accent', '#ff00a0')
        panel = theme.get('surface_panel_purple', '#2b1a42')

        self._filter_bar_widget = QWidget()
        self._filter_bar_widget.setFixedHeight(64)
        self._filter_bar_widget.setSizePolicy(QSizePolicy.Policy.Preferred,
                                              QSizePolicy.Policy.Fixed)
        bar = QHBoxLayout(self._filter_bar_widget)
        bar.setContentsMargins(18, 10, 18, 8)
        bar.setSpacing(10)

        self.filter_search = QLineEdit()
        self.filter_search.setPlaceholderText("Search games...")
        self.filter_search.setClearButtonEnabled(True)
        self.filter_search.setMinimumHeight(38)
        self.filter_search.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,0.07); color: {text};"
            f" border: 1px solid {accent}; border-radius: 9px;"
            f" padding: 6px 14px; font-size: 15px; }}"
            f"QLineEdit:focus {{ border: 1px solid {neon};"
            f" background: rgba(255,255,255,0.11); }}"
        )
        self.filter_search.textChanged.connect(self._schedule_filters)
        bar.addWidget(self.filter_search, 1)

        caption = QLabel("GO TO")
        caption.setStyleSheet(
            f"color: {muted}; font-size: 11px; font-weight: bold;"
            f" letter-spacing: 1px; background: transparent;")
        bar.addWidget(caption)

        self.filter_jump = QComboBox()
        self.filter_jump.addItem("Jump to category...", None)
        for i, cat in enumerate(self._categories):
            self.filter_jump.addItem(cat["title"], i)
        self.filter_jump.setMinimumWidth(250)
        self.filter_jump.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,0.06); color: {text};"
            f" border: 1px solid rgba(255,255,255,0.14); border-radius: 7px;"
            f" padding: 5px 10px; font-size: 12px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text};"
            f" selection-background-color: {accent}; outline: none; }}"
        )
        self.filter_jump.activated.connect(self._on_jump_selected)
        bar.addWidget(self.filter_jump)

        self.filter_sort = QComboBox()
        for value, label in SORT_CHOICES:
            self.filter_sort.addItem(label, value)
        self.filter_sort.setMinimumWidth(175)
        self.filter_sort.setStyleSheet(
            f"QComboBox {{ background: rgba(255,255,255,0.06); color: {text};"
            f" border: 1px solid rgba(255,255,255,0.14); border-radius: 7px;"
            f" padding: 5px 10px; font-size: 12px; }}"
            f"QComboBox:hover {{ border-color: {accent}; }}"
            f"QComboBox:disabled {{ color: {muted};"
            f" border-color: rgba(255,255,255,0.07); }}"
            f"QComboBox::drop-down {{ border: none; width: 18px; }}"
            f"QComboBox QAbstractItemView {{ background: {panel}; color: {text};"
            f" selection-background-color: {accent}; outline: none; }}"
        )
        self.filter_sort.activated.connect(self._on_sort_selected)
        self.filter_sort.setToolTip(
            "Sorting applies to rows that do not already follow a date order.")
        bar.addWidget(self.filter_sort)

        self.filter_hide_installed = QCheckBox("Hide installed")
        self.filter_hide_installed.setStyleSheet(f"color: {muted}; font-size: 12px;")
        self.filter_hide_installed.toggled.connect(self._schedule_filters)
        bar.addWidget(self.filter_hide_installed)

        root_layout.addWidget(self._filter_bar_widget)

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(350)
        self._filter_timer.timeout.connect(self._apply_filters)

        # Watchdog: rileva freeze del thread grafico anche quando nessuno
        # slot supera la soglia (es. paint storm durante il drag).
        self._wd_last = time.monotonic()
        self._wd_grace = time.monotonic() + 6.0   # ignora la fase di avvio
        self._wd_timer = QTimer(self)
        self._wd_timer.setInterval(100)
        self._wd_timer.timeout.connect(self._watchdog_tick)
        self._wd_timer.start()

    def _build_notice_bar(self, root_layout):
        """Dismissible banner for non-fatal network/config notices."""
        self._notice_bar = QWidget()
        self._notice_bar.setVisible(False)
        self._notice_kind = None
        self._dismissed_notices = set()
        self._notice_bar.setStyleSheet(
            "background: rgba(255, 180, 0, 0.10);"
            "border-bottom: 1px solid rgba(255, 180, 0, 0.45);"
        )
        row = QHBoxLayout(self._notice_bar)
        row.setContentsMargins(18, 8, 18, 8)
        row.setSpacing(12)
        self._notice_label = QLabel("")
        self._notice_label.setWordWrap(True)
        self._notice_label.setStyleSheet("color: #ffcc66; font-size: 12px;")
        row.addWidget(self._notice_label, 1)
        btn = QPushButton("Dismiss")
        btn.setStyleSheet(
            "QPushButton { background: transparent; color: #ffcc66;"
            " border: 1px solid #ffcc66; border-radius: 5px; padding: 3px 10px; }"
        )
        btn.clicked.connect(self._dismiss_notice)
        row.addWidget(btn)
        root_layout.addWidget(self._notice_bar)

    def _show_notice(self, kind, title, body=""):
        if not hasattr(self, "_notice_bar"):
            return
        self._notice_kind = kind
        text = f"<b>{title}</b>"
        if body:
            text += (
                "<br><span style='color:#9c9c9c;'>"
                + str(body).replace("<", "&lt;").replace(">", "&gt;")
                + "</span>"
            )
        self._notice_label.setText(text)
        self._notice_bar.show()
        if hasattr(self, "_mask_overlay"):
            QTimer.singleShot(0, self._layout_fades)

    def show_network_notice(self, message):
        detail = str(message or "").strip()
        self._show_notice(
            "network",
            "⚠ Unable to reach SteamRip. Check your internet connection or add "
            "an exception for the launcher in your antivirus/firewall.",
            detail,
        )

    def show_config_notice(self, title, body=""):
        self._show_notice("config", title, body)

    def refresh_config_notice(self):
        """Show or hide the IGDB banner to match the saved credentials.

        The banner used to be evaluated once, when the tab was built, so adding
        the keys later left it on screen until a restart.
        """
        if self._discovery() is None:
            return
        if secrets.has_igdb_credentials():
            self.hide_notice("config")
            return
        if "config" in self._dismissed_notices:
            return
        self.show_config_notice(
            self.CONFIG_NOTICE_TITLE,
            self.CONFIG_NOTICE_BODY,
        )

    def hide_notice(self, kind=None):
        if kind is not None and self._notice_kind != kind:
            return
        if hasattr(self, "_notice_bar") and self._notice_bar.isVisible():
            self._notice_bar.hide()
            if hasattr(self, "_mask_overlay"):
                QTimer.singleShot(0, self._layout_fades)

    def hide_network_notice(self):
        self.hide_notice("network")

    def _dismiss_notice(self):
        if self._notice_kind:
            self._dismissed_notices.add(self._notice_kind)
        self.hide_notice()

    def _watchdog_tick(self):
        now = time.monotonic()
        gap = now - self._wd_last
        self._wd_last = now
        if now < self._wd_grace:
            return
        if gap > 0.25:
            log.info(f"[HITCH] ui blocked {gap:.2f}s at {now:.2f}")

    def _schedule_filters(self, *args):
        if hasattr(self, '_filter_timer'):
            self._filter_timer.start()

    def _on_sort_selected(self, _index):
        self._sort_value = self.filter_sort.currentData() or ""
        self._apply_filters()
        if self._search_mode:
            return
        # Ordine nuovo: si riparte dall'inizio della riga.
        for row in self._visible_rows():
            sb = row._h_scroll.horizontalScrollBar()
            if sb is not None:
                sb.setValue(0)

    def _sort_key(self):
        """Chiave della riga in focus (None = My Library)."""
        if self._search_mode:
            return None
        rows = self._category_rows
        idx = self._scroller._focus_idx
        if rows and 0 <= idx < len(rows):
            return getattr(rows[idx], '_category_key', None)
        return None

    def _sync_sort_combo(self):
        if not hasattr(self, "filter_sort"):
            return
        key = self._sort_key()
        discovery = self._discovery()
        if self._search_mode:
            sortable = False
        elif key is None:
            sortable = True          # My Library: ordinabile in locale
        else:
            sortable = bool(discovery is not None
                            and discovery.is_sortable_key(key))

        self.filter_sort.blockSignals(True)
        try:
            if sortable:
                idx = self.filter_sort.findData(self._sort_value)
                self.filter_sort.setCurrentIndex(max(0, idx))
                self.filter_sort.setEnabled(True)
                self.filter_sort.setToolTip(
                    "Sorting applies to this row. Date-based rows keep "
                    "their own order.")
            else:
                self.filter_sort.setCurrentIndex(0)
                self.filter_sort.setEnabled(False)
                self.filter_sort.setToolTip(
                    "This row keeps its own order (date or site order).")
        finally:
            self.filter_sort.blockSignals(False)

    def _on_jump_selected(self, _index):
        idx = self.filter_jump.currentData()
        if idx is None:
            return
        self._scroller.focus_index(idx)

    def _sync_jump_combo(self):
        if not hasattr(self, 'filter_jump') or self._search_mode:
            return
        focus = self._scroller._focus_idx
        self.filter_jump.blockSignals(True)
        self.filter_jump.setCurrentIndex(focus + 1)
        self.filter_jump.blockSignals(False)

    def _is_searching(self):
        return len(self.filter_search.text().strip()) >= 2

    def _apply_filters(self):
        discovery = self._discovery()
        if discovery is None:
            return
        discovery.set_filters({
            "search": self.filter_search.text(),
            "hide_installed": self.filter_hide_installed.isChecked(),
            "sr_category": None,
            "igdb_genre": None,
            "sort": self._sort_value,
        })
        if self._is_searching():
            self._show_search_results()
            return
        self._exit_search_mode()
        self._mark_all_rows_dirty()
        self._rebuild_visible_rows()

    def _show_search_results(self):
        """Una sola riga dinamica "Search Results" al posto delle categorie."""
        discovery = self._discovery()
        if discovery is None:
            return
        if not self._search_mode:
            self._search_mode = True
            self._saved_focus = self._scroller._focus_idx
            if hasattr(self, "filter_jump"):
                self.filter_jump.setEnabled(False)

        cards = discovery.search_cards()
        old = self._search_row
        row = CategoryRow("Search Results", cards,
                          theme=self._theme_data(), progressive=True)
        row.set_card_size(*self._current_card_size())
        row._category_key = None
        self._search_row = row
        self._wire_row(row)
        self._scroller.set_pool([row])
        self._scroller.set_single(True)
        if old is not None:
            old.setParent(None)
            old.deleteLater()
        self._dirty_keys.clear()
        self._sync_sort_combo()
        QTimer.singleShot(0, self._update_cover_window)

    def _exit_search_mode(self):
        if not self._search_mode:
            return
        self._search_mode = False
        if hasattr(self, "filter_jump"):
            self.filter_jump.setEnabled(True)
        old = self._search_row
        self._search_row = None
        self._scroller.set_pool(self._category_rows)
        self._scroller.set_single(True)
        self._scroller.focus_index(getattr(self, "_saved_focus", 1))
        self._sync_sort_combo()
        if old is not None:
            old.setParent(None)
            old.deleteLater()

    @_perf("on_meta_ready")
    def _on_meta_ready(self, results):
        if not results:
            return
        if self._search_mode:
            self._show_search_results()
            return
        # Niente reset di _cover_loaded: ri-enqueued copriva le stesse cover
        # a ogni batch di metadati (download + decode ripetuti).
        discovery = self._discovery()
        if discovery is None:
            return
        # The cover jobs usually run before the per-title IGDB metadata is
        # fetched, so they fall back to the horizontal SteamRip featured image
        # (or the slow, throttled SGDB name lookup). Now that the cover URL is
        # known, re-enqueue every card that is still missing a *vertical* cover;
        # a horizontal fallback is provisional and must be upgraded too.
        titles = []
        for title, meta in results.items():
            if not isinstance(meta, dict) or not meta.get("cover_url"):
                continue
            card = discovery._find_card(title)
            if card is None:
                continue
            cover = card.get("cover_path") or ""
            if cover and MiniCoverManager.is_vertical_cover(cover):
                continue
            titles.append(title)
        if titles:
            discovery.cancel_covers(titles)
            discovery.enqueue_covers(titles, high=True, force=True)

    def _mark_all_rows_dirty(self):
        self._dirty_keys = set(self._category_keys)

    def _visible_rows(self):
        pool = self._scroller._pool
        if not pool:
            return []
        return [pool[self._scroller._focus_idx % len(pool)]]

    def _visible_keys(self):
        return {
            getattr(row, '_category_key', None)
            for row in self._visible_rows()
            if getattr(row, '_category_key', None)
        }

    def _rebuild_visible_rows(self):
        """Ricostruisce solo le righe visibili: evita il freeze da full rebuild."""
        keys = []
        for row in self._visible_rows():
            key = getattr(row, '_category_key', None)
            if key is None:
                if (not self._search_mode and self._category_rows
                        and row is self._category_rows[0]):
                    new_row = CategoryRow(
                        self._categories[0]["title"], self._library_provider(),
                        theme=self._theme_data(), progressive=True)
                    self._replace_row(0, new_row)
                continue
            if key in self._dirty_keys and key not in keys:
                keys.append(key)
        for key in keys:
            idx = self._index_of_key(key)
            if idx is None:
                continue
            new_row = CategoryRow(self._discovery_title(key), self._cards_for_key(key),
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)
            self._dirty_keys.discard(key)
        QTimer.singleShot(0, self._update_cover_window)

    def _cards_for_key(self, key):
        discovery = self._discovery()
        if discovery is None:
            return []
        return discovery.get_cards(key)

    def _discovery_title(self, key):
        discovery = self._discovery()
        if discovery is None:
            return ""
        for cat in discovery.categories():
            if cat["key"] == key:
                return cat["title"]
        return ""

    def _index_of_key(self, key):
        try:
            return self._category_keys.index(key) + 1
        except ValueError:
            return None

    def _wire_row(self, row):
        sb = row._h_scroll.horizontalScrollBar()
        if sb is not None:
            sb.valueChanged.connect(lambda v, r=row: self._maybe_load_more(r, v))
            # Lo scorrimento orizzontale deve far scaricare le cover delle
            # card che entrano in scena (prima si fermava alle prime 15).
            sb.valueChanged.connect(
                lambda _v: self._schedule_cover_window())
        row.card_clicked.connect(self._on_card_clicked)

    def _on_card_clicked(self, card):
        title = str(getattr(card, "_title", "") or "").strip()
        if not title:
            return
        if time.monotonic() < getattr(self.mw, "_discovery_click_guard", 0):
            return
        self._open_details(title)

    def _open_details(self, title):
        popup = getattr(self, "_detail_popup", None)
        if popup is None:
            from ui.widgets.game_detail_popup import GameDetailPopup
            popup = GameDetailPopup(self.mw)
            self._detail_popup = popup
        popup.show_game(title)

    @_perf("replace_row")
    def _replace_row(self, idx, new_row, reset_scroll=False):
        old_row = self._category_rows[idx]
        prev_scroll = 0
        if not reset_scroll:
            prev_scroll = old_row._h_scroll.horizontalScrollBar().value()
        new_row._category_key = old_row._category_key
        new_row.setParent(self._scroller)
        self._category_rows[idx] = new_row
        self._scroller._pool[idx] = new_row
        self._scroller._attach_row(new_row)
        card_w, card_h = self._current_card_size()
        new_row.set_card_size(card_w, card_h)
        self._scroller._last_card_h = card_h
        if not getattr(new_row, '_themed', False):
            theme = self._theme_data()
            if theme:
                new_row.set_theme(theme)
        self._wire_row(new_row)
        self._scroller._position_slots()
        if prev_scroll > 0:
            step = max(1, card_w + CARD_SPACING)
            vw = new_row._h_scroll.viewport().width() or 800
            needed = int((prev_scroll + vw) / step) + 4
            new_row.build_until(needed)
        new_row._h_scroll.horizontalScrollBar().setValue(prev_scroll)
        old_row.setParent(None)
        old_row.deleteLater()

    def refresh_library(self):
        if not self._category_rows:
            return
        new_row = CategoryRow(self._categories[0]["title"], self._library_provider(),
                              theme=self._theme_data())
        self._replace_row(0, new_row)

    def _library_provider(self):
        games = self.mw.installed_games if hasattr(self.mw, 'installed_games') else {}
        sync_folder = ""
        manager = getattr(self.mw, 'savegames_manager', None)
        if manager is not None:
            try:
                sync_folder = manager.get_sync_folder()
            except Exception:
                sync_folder = ""

        cards = []
        for title in sorted(games.keys()):
            cover = MiniCoverManager.resolve_cover_path(title, games, sync_folder)
            cards.append({
                "title": title,
                "cover_path": cover,
                "meta": dict(games.get(title) or {}),
            })
        discovery = self._discovery()
        if discovery is not None:
            cards = discovery.sort_cards(cards)
        return cards

    # ── discovery manager wiring ────────────────────────────────────

    @_perf("discovery_changed")
    def _on_discovery_changed(self):
        if self._search_mode:
            self._show_search_results()
            return
        self._mark_all_rows_dirty()
        self._rebuild_visible_rows()

    @_perf("page_changed")
    def _on_discovery_page_changed(self, key):
        self.hide_network_notice()
        self._row_loading.pop(key, None)
        self._meta_requested_keys.discard(key)
        if key not in self._visible_keys():
            # Riga fuori schermo: niente rebuild adesso, la calcoliamo quando entra.
            self._dirty_keys.add(key)
            return
        idx = self._index_of_key(key)
        if idx is None:
            return

        discovery = self._discovery()
        if discovery is not None and discovery.row_sort_active(key):
            new_row = CategoryRow(self._discovery_title(key),
                                  self._cards_for_key(key),
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)
            self._dirty_keys.discard(key)
            self._update_cover_window()
            return

        # Append incrementale: non ricreiamo la riga (evita il softlock durante il drag).
        row = self._category_rows[idx]
        new_cards = self._cards_for_key(key)
        existing = {c.get("title") for c in row.data_cards() if c.get("title")}
        added = [c for c in new_cards if c.get("title") not in existing]

        if added:
            row.append_cards(added)
        elif not getattr(row, '_card_data', None):
            # Riga vuota (prima visualizzazione): serve il rebuild completo.
            new_row = CategoryRow(self._discovery_title(key), new_cards,
                                  theme=self._theme_data(), progressive=True)
            self._replace_row(idx, new_row)

        self._dirty_keys.discard(key)
        self._update_cover_window()

    @_perf("row_reset")
    def _on_discovery_row_reset(self, key):
        """The manager replaced a row snapshot (first page refresh).

        Rebuilding the row while it is being dragged used to softlock the
        scroll area, so a moving row is retried once it settles.
        """
        self.hide_network_notice()
        self._row_loading.pop(key, None)
        self._meta_requested_keys.discard(key)
        idx = self._index_of_key(key)
        if idx is None:
            return
        if key not in self._visible_keys():
            self._dirty_keys.add(key)
            return
        row = self._category_rows[idx]
        scroll = row._h_scroll
        moving = (
            getattr(scroll, "_is_dragging", False)
            or scroll._momentum_timer.isActive()
            or scroll._snap_anim is not None
        )
        if moving:
            QTimer.singleShot(600, lambda k=key: self._on_discovery_row_reset(k))
            return
        new_row = CategoryRow(self._discovery_title(key),
                              self._cards_for_key(key),
                              theme=self._theme_data(), progressive=True)
        self._replace_row(idx, new_row, reset_scroll=True)
        self._dirty_keys.discard(key)
        self._update_cover_window()

    @_perf("cover_ready")
    def _on_cover_ready(self, title, path):
        discovery = self._discovery()
        if discovery is not None:
            discovery.set_cover_path(title, path)
        rows = list(self._visible_rows())
        if self._search_row is not None and self._search_row not in rows:
            rows.append(self._search_row)
        for row in rows:
            for card in row._cards:
                if card._title == title:
                    card.set_cover(path)

    def _ensure_row_capacity(self, row):
        if not hasattr(row, "has_pending") or not row.has_pending():
            return
        card_w, _h = self._current_card_size()
        step = max(1, card_w + CARD_SPACING)
        vw = row._h_scroll.viewport().width()
        value = max(0, row._h_scroll.horizontalScrollBar().value()
                    - getattr(row, '_pad', 0))
        built = max(1, len(row._card_data))
        if value + vw >= built * step * 0.8:
            row.build_more()

    def _maybe_top_up_row(self, row):
        """Fill a row that matched too few games to scroll.

        With no overflow there is no ``valueChanged`` event, so the next page
        was never requested and the row stayed at a handful of cards.
        """
        key = getattr(row, '_category_key', None)
        if key is None:
            return
        # Count the data, not the widgets: a row builds its cards 10 at a time,
        # so ``_card_data`` alone would look sparse right after a rebuild.
        if len(row.data_cards()) >= MIN_ROW_CARDS:
            return
        discovery = self._discovery()
        if discovery is None:
            return
        if self._row_loading.get(key):
            return
        now = time.monotonic()
        if now < self._load_cooldown.get(key, 0):
            return
        if not discovery.request_more(key):
            return
        self._row_loading[key] = True
        self._load_cooldown[key] = now + 0.9

    @_perf("maybe_load_more")
    def _maybe_load_more(self, row, value):
        key = getattr(row, '_category_key', None)
        if key is not None:
            self._ensure_row_capacity(row)
        discovery = self._discovery()
        if key is None or discovery is None:
            return
        wrap = getattr(row._h_scroll, '_wrap_width', 0)
        vw = row._h_scroll.viewport().width()
        if wrap <= 0 or vw <= 0:
            return
        pad = getattr(row, '_pad', 0)
        if value < (wrap - pad) - vw - 60:
            return
        if self._row_loading.get(key):
            return
        now = time.monotonic()
        if now < self._load_cooldown.get(key, 0):
            return  # evita cascate di pagine mentre si trascina all'estremo
        if not discovery.request_more(key):
            return
        self._row_loading[key] = True
        self._load_cooldown[key] = now + 0.9
        self._update_cover_window()

    def _window_rows(self):
        pool = self._scroller._pool
        n = len(pool)
        if n == 0:
            return []
        if n == 1:
            return [pool[0]]
        focus = self._scroller._focus_idx
        return [pool[(focus + i) % n] for i in range(-3, 3)]

    def _cover_target_count(self, row):
        step = self._current_card_size()[0] + CARD_SPACING
        n_cards = len(row.original_cards())
        if step <= 0:
            return 0
        value = max(0, row._h_scroll.horizontalScrollBar().value()
                    - getattr(row, '_pad', 0))
        seen = int(value / step) if value > 0 else 0
        return max(0, min(n_cards, COVER_WINDOW_CARDS + seen))

    def _schedule_cover_window(self):
        timer = getattr(self, "_cover_window_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(180)
            timer.timeout.connect(self._update_cover_window)
            self._cover_window_timer = timer
        if not timer.isActive():
            timer.start()

    @_perf("cover_window")
    def _update_cover_window(self):
        discovery = self._discovery()
        if discovery is None:
            return
        pool = self._scroller._pool
        n = len(pool)
        if n == 0:
            return
        focus = self._scroller._focus_idx
        # Una sola riga visibile: niente prefetch delle righe adiacenti
        # (era una valanga di download/decode di cover a ogni cambio).
        window = self._visible_rows()
        focus_row = pool[focus]

        # Righe sporche (filtri/categorie cambiate): ricostruisci solo le visibili.
        dirty_slots = any(getattr(r, '_category_key', None) in self._dirty_keys
                          for r in self._visible_rows())
        if dirty_slots:
            self._rebuild_visible_rows()
            return

        self._maybe_top_up_row(focus_row)

        if not hasattr(self, '_cover_active_keys'):
            self._cover_active_keys = set()
        active = {getattr(r, '_category_key', None) for r in window}
        active.discard(None)
        gone = self._cover_active_keys - active
        if gone:
            titles = set()
            for row in self._category_rows:
                if getattr(row, '_category_key', None) in gone:
                    for card in row.original_cards():
                        if getattr(card, '_title', ''):
                            titles.add(card._title)
            if titles:
                discovery.cancel_covers(sorted(titles))
        self._cover_active_keys = active

        # Arricchimento IGDB della riga in focus (una volta per riga/sessione).
        # Start it before the covers: their jobs use the IGDB cover URL when
        # it is available and fall back to the throttled SGDB lookup anyway.
        focus_key = getattr(focus_row, '_category_key', None)
        if focus_key and focus_key not in self._meta_requested_keys:
            self._meta_requested_keys.add(focus_key)
            meta_titles = [c._title for c in
                           focus_row.original_cards()[:COVER_WINDOW_CARDS + 15]
                           if c._title]
            if meta_titles:
                # Cards on screen first: their IGDB cover upgrades the
                # provisional SteamRip cover while the prefetch tail loads.
                discovery.enqueue_igdb_meta(meta_titles[:META_VISIBLE_FIRST])
                if len(meta_titles) > META_VISIBLE_FIRST:
                    discovery.enqueue_igdb_meta(meta_titles[META_VISIBLE_FIRST:])

        for row in window:
            is_focus = row is focus_row
            target = self._cover_target_count(row)
            start = getattr(row, '_cover_loaded', 0)
            if target > start:
                row._cover_loaded = target
                # Use the card dicts directly: search rows are generated on
                # the fly and are not present in discovery._data, so the
                # title-based lookup used to enqueue nothing for them.
                card_data = getattr(row, '_card_data', [])
                window_cards = [
                    card for card in card_data[start:target]
                    if isinstance(card, dict) and card.get("title")
                ]
                if window_cards:
                    import os as _os
                    allow = is_focus and _os.environ.get("SRP_COVER_SCRAPE") == "1"
                    discovery.enqueue_cards(
                        window_cards, high=is_focus, allow_scrape=allow,
                        allow_page_fetch=is_focus)

    # ── trailer previews ─────────────────────────────────────────────

    def _current_card_size(self):
        vw = self.width()
        vh = self.height()
        if vw <= 0:
            vw = 1400
        if vh <= 0:
            vh = 800

        available_w = vw - 2 * FADE_MARGIN
        card_w = max(140, int((available_w - 5 * CARD_SPACING) / 6))
        card_h = max(
            200,
            int((card_w - 2 * GLOW_INSET) * 1.5)
            + GLOW_PAD + CARD_TITLE_H,
        )
        return card_w, card_h

    def _theme_data(self):
        if hasattr(self.mw, 'app_config'):
            return self.mw.app_config.get('theme', {})
        return {}

    @_perf("recalc_layout")
    def _recalc_layout(self):
        if not self._category_rows:
            return

        card_w, card_h = self._current_card_size()

        self._scroller.set_card_size(card_w, card_h)
        self._scroller._last_card_h = card_h
        self._scroller._position_slots()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._recalc_layout()
        self._layout_fades()

    def showEvent(self, event):
        super().showEvent(event)
        if hasattr(self, 'mw') and hasattr(self.mw, 'installed_games'):
            if self.mw.installed_games and not self._category_rows[0]._cards:
                self.refresh_library()
            self._update_cover_window()
        discovery = self._discovery()
        if discovery is not None and hasattr(discovery, "refresh_if_stale"):
            discovery.refresh_if_stale()

    def _layout_fades(self):
        w, h = self.width(), self.height()
        top = 0
        if hasattr(self, '_filter_bar_widget') and self._filter_bar_widget.isVisible():
            top = self._filter_bar_widget.height()
        if hasattr(self, '_notice_bar') and self._notice_bar.isVisible():
            top += self._notice_bar.height()
        # The footer sits below the masked scroller: keep the fade above it so
        # the ethical disclaimer stays fully readable.
        footer_top = h
        if hasattr(self, 'footer_label') and self.footer_label.height() > 0:
            footer_top = self.footer_label.geometry().top()
        self._mask_overlay.setGeometry(0, top, w, max(0, footer_top - top))
        self._mask_overlay.raise_()
        if hasattr(self, '_notice_bar'):
            self._notice_bar.raise_()
        if hasattr(self, '_filter_bar_widget'):
            self._filter_bar_widget.raise_()
        if hasattr(self, 'footer_label'):
            self.footer_label.raise_()

    def _apply_theme(self):
        if not hasattr(self.mw, 'app_config'):
            return
        theme = self.mw.app_config.get('theme', {})
        bg = theme.get('main_background_color',
                        theme.get('background_dark_purple', '#120a1c'))
        self.setStyleSheet(f"background-color: {bg};")
        self._mask_overlay.set_fade_color(bg)
        muted = theme.get('text_muted', '#8a7fa4')
        accent = theme.get('bright_neon_accent', '#ff00a0')
        self.footer_label.setText(
            "If you like the game and the software house / publisher aren't "
            "aggressive monetizers, if they are pro-users, if they support the "
            f"customer, <b style='color:{accent}; text-transform: uppercase;'>"
            "BUY THE GAME. THEY DESERVE IT.</b>"
        )
        self.footer_label.setStyleSheet(
            f"font-size: 11px; color: {muted}; padding: 4px 10px; "
            "background-color: rgba(0,0,0,0.1); border-radius: 4px; "
            "margin-top: 5px;"
        )
        for row in self._category_rows:
            row.set_theme(theme)

    def apply_theme(self):
        self._apply_theme()
