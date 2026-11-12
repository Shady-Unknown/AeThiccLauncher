"""Popup scheda gioco per la Discovery.

Mostra i dati che abbiamo gia' (master catalog + igdb_meta + cache pagina
SteamRip) e completa in background con i dettagli IGDB. Il trailer viene
risolto all'apertura ma riprodotto solo su richiesta.
"""
import html as html_lib
import os
import re
import sys
import time
from urllib.parse import quote

import requests
from PyQt6.QtCore import (
    QEasingCurve, QEvent, QObject, QPropertyAnimation, QSize, Qt, QTimer, QUrl,
    pyqtSignal
)
from PyQt6.QtGui import QColor, QDesktopServices, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PyQt6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QProgressBar,
    QPushButton, QStackedWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from core.config import save_config
from core.events import EVENT_TRAILER_WATCHED
from core.mini_cover_cache import MiniCoverManager
from core.paths import PathManager
from managers.igdb_details_cache import IgdbDetailsCache
from managers.playtime_manager import PlaytimeManager
from managers.save_manager import safe_game_folder_name
from network.igdb_details_worker import IGDBDetailsWorker
from network.trailer_downloader import TrailerDownloadWorker, cached_path
from network.trailer_worker import TrailerWorker
from ui.widgets.discovery_card import VideoSurface

COVER_W = 300
COVER_H = 450
PANEL_RATIO = 0.6
PANEL_MIN_W = 900
PANEL_MIN_H = 560
# The optional Add to Steam button adds a second footer row: the panel needs
# extra height or the fixed-size cover overlaps the footer.
PANEL_MIN_H_ADD_STEAM = 630
FADE_MS = 180

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


class _CoverFetchWorker(QObject):
    """Piccolo downloader async per la cover grande (bytes sul thread UI)."""

    ready = pyqtSignal(str, bytes)

    def __init__(self, title, url, parent=None):
        super().__init__(parent)
        self._title = title
        self._url = url
        self._thread = None

    def start(self):
        if not self._url:
            return
        import threading
        url = self._url
        title = self._title

        def run():
            data = b""
            try:
                r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
                if r.status_code == 200:
                    data = r.content
            except Exception:
                data = b""
            if data:
                try:
                    self.ready.emit(title, data)
                except RuntimeError:
                    pass

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()


class GameDetailPopup(QWidget):

    def __init__(self, mw):
        super().__init__(mw)
        self.mw = mw
        self._theme = dict((getattr(mw, "app_config", {}) or {}).get("theme") or {})
        self._title = ""
        self._entry = {}
        self._url = ""
        self._gen = 0
        self._details = None
        self._trailer = None
        self._local_trailer = ""
        self._play_when_ready = False
        self._streaming = False
        self._selected_url = None
        self._selected_label = "Auto"
        self._falling_back = False
        self._player = None
        self._audio = None
        self._video = None
        self._want_play = False
        self._trailer_mode = "steam"
        self._immersive = False
        self._player_muted = False
        self._details_worker = None
        self._trailer_worker = None
        self._download_worker = None
        self._local_trailer = ""
        self._play_when_ready = False
        self._streaming = False
        self._cover_worker = None
        self._extract_retries = 0
        self._extract_timer = None
        self._extract_pending = False
        self._action_mode = "install"

        self.setObjectName("GameDetailOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

        self._build_ui()
        try:
            mw.installEventFilter(self)
        except Exception:
            pass
        scraper = getattr(mw, "scraper", None)
        if scraper is not None:
            try:
                scraper.extract_ready.connect(self._on_extract_ready)
            except Exception:
                pass

    # ── theme helpers ────────────────────────────────────────────────

    def _c(self, key, default):
        return self._theme.get(key) or default

    @property
    def _bg(self):
        return self._c("main_background_color",
                       self._c("background_dark_purple", "#120a1c"))

    @property
    def _panel_bg(self):
        return self._c("surface_panel_purple", "#2b1a42")

    @property
    def _accent(self):
        return self._c("main_accent_color", "#733d91")

    @property
    def _neon(self):
        return self._c("bright_neon_accent", "#ff00a0")

    @property
    def _text(self):
        return self._c("text_primary_white", "#ffffff")

    @property
    def _muted(self):
        return self._c("text_muted", "#8a7fa4")

    # ── UI ───────────────────────────────────────────────────────────

    def _build_ui(self):
        self.setStyleSheet(
            "#GameDetailOverlay { background: rgba(5, 3, 9, 200); }"
        )

        self._panel = QFrame(self)
        self._panel.setObjectName("GameDetailPanel")
        self._panel.setStyleSheet(
            f"QFrame#GameDetailPanel {{ background: {self._bg};"
            f" border: 1px solid {self._accent}; border-radius: 14px; }}"
        )
        self._panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._panel.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation, True)

        outer = QVBoxLayout(self._panel)
        outer.setContentsMargins(26, 18, 26, 20)
        outer.setSpacing(12)

        header = QHBoxLayout()
        header.addStretch(1)
        self._close_btn = QToolButton(self._panel)
        self._close_btn.setText("✕")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setStyleSheet(
            f"QToolButton {{ color: {self._muted}; background: transparent;"
            f" border: none; font-size: 18px; padding: 2px 6px; }}"
            f"QToolButton:hover {{ color: {self._neon}; }}"
        )
        self._close_btn.clicked.connect(self._on_close_clicked)
        header.addWidget(self._close_btn)
        outer.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(22)

        self._cover = QLabel(self._panel)
        self._cover.setFixedSize(COVER_W, COVER_H)
        self._cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cover.setStyleSheet(
            f"background: {self._bg}; border: 1px solid {self._accent};"
            f" border-radius: 8px;"
        )
        body.addWidget(self._cover, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(6)

        self._title_label = QLabel("")
        self._title_label.setWordWrap(True)
        self._title_label.setStyleSheet(
            f"color: {self._text}; font-size: 26px; font-weight: bold;"
            f" background: transparent;"
        )
        info.addWidget(self._title_label)

        self._meta_label = QLabel("")
        self._meta_label.setStyleSheet(
            f"color: {self._neon}; font-size: 14px; background: transparent;"
        )
        info.addWidget(self._meta_label)

        self._genres_label = QLabel("")
        self._genres_label.setWordWrap(True)
        self._genres_label.setStyleSheet(
            f"color: {self._muted}; font-size: 13px; background: transparent;"
        )
        info.addWidget(self._genres_label)

        self._chips_host = QWidget(self._panel)
        self._chips_host.setStyleSheet("background: transparent;")
        self._chips_layout = QHBoxLayout(self._chips_host)
        self._chips_layout.setContentsMargins(0, 2, 0, 2)
        self._chips_layout.setSpacing(6)
        self._chips_layout.addStretch(1)
        info.addWidget(self._chips_host)

        self._version_label = QLabel("")
        self._version_label.setWordWrap(True)
        self._version_label.setStyleSheet(
            f"color: {self._text}; font-size: 13px; background: transparent;"
        )
        info.addWidget(self._version_label)

        self._company_label = QLabel("")
        self._company_label.setWordWrap(True)
        self._company_label.setStyleSheet(
            f"color: {self._muted}; font-size: 12px; background: transparent;"
        )
        info.addWidget(self._company_label)

        self._info_host = QWidget(self._panel)
        self._info_host.setStyleSheet("background: transparent;")
        self._info_host.setLayout(info)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(self._info_host, 0)

        self._stack = QStackedWidget(self._panel)
        self._stack.setStyleSheet("background: transparent;")

        self._desc = QTextBrowser(self._stack)
        self._desc.setOpenExternalLinks(False)
        self._desc.setFrameShape(QFrame.Shape.NoFrame)
        self._desc.setStyleSheet(
            f"QTextBrowser {{ background: rgba(0, 0, 0, 60);"
            f" color: {self._text}; border: 1px solid rgba(255, 255, 255, 18);"
            f" border-radius: 8px; padding: 10px; font-size: 13px; }}"
        )
        self._stack.addWidget(self._desc)

        self._player_page = QWidget(self._stack)
        self._player_page.setStyleSheet("background: transparent;")
        player_layout = QVBoxLayout(self._player_page)
        player_layout.setContentsMargins(0, 0, 0, 0)
        self._player_layout = player_layout
        self._stack.addWidget(self._player_page)

        right.addWidget(self._stack, 1)
        body.addLayout(right, 1)
        outer.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(
            f"color: {self._muted}; font-size: 12px; background: transparent;"
        )
        footer.addWidget(self._status_label, 1)

        # Playtime uses the horizontal footer space so it never adds a row.
        self._playtime_label = QLabel("")
        self._playtime_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._playtime_label.setStyleSheet(
            "color: rgba(255, 255, 255, 0.62); font-size: 12px;"
            " font-weight: bold; background: transparent;"
        )
        self._playtime_label.hide()

        self._play_btn = QPushButton("▶  Play trailer")
        self._play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._play_btn.setEnabled(False)
        self._play_btn.setStyleSheet(self._btn_style(primary=False))
        self._play_btn.clicked.connect(self._toggle_trailer)
        footer.addWidget(self._play_btn, 0, Qt.AlignmentFlag.AlignTop)

        # Kept next to the main action: it separates the trailer from Play
        # without spending vertical space.
        footer.addWidget(self._playtime_label, 0)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)

        # Same primary style as the old Install button: the in-library actions
        # (Play / Set Executable / Add to Steam) must look homogeneous.
        self._action_btn = QPushButton("Install (Open in Games Explorer)")
        self._action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._action_btn.setStyleSheet(self._btn_style(primary=True))
        self._action_btn.clicked.connect(self._on_action_clicked)
        actions.addWidget(self._action_btn)

        self._steam_btn = QPushButton("➕ Add to Steam (Optional)")
        self._steam_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._steam_btn.setStyleSheet(self._sub_btn_style())
        self._steam_btn.clicked.connect(self._on_add_to_steam)
        self._steam_btn.hide()
        actions.addWidget(self._steam_btn)

        footer.addLayout(actions)

        self._trailer_progress = QProgressBar(self._panel)
        self._trailer_progress.setFixedHeight(3)
        self._trailer_progress.setTextVisible(False)
        self._trailer_progress.setRange(0, 0)
        self._trailer_progress.setStyleSheet(
            "QProgressBar { background: rgba(255, 255, 255, 18);"
            " border: none; border-radius: 1px; }"
            f" QProgressBar::chunk {{ background: {self._neon};"
            " border-radius: 1px; }"
        )
        self._trailer_progress.hide()
        outer.addWidget(self._trailer_progress)

        self._footer_host = QWidget(self._panel)
        self._footer_host.setStyleSheet("background: transparent;")
        self._footer_host.setLayout(footer)
        outer.addWidget(self._footer_host)

        self._fade = QGraphicsOpacityEffect(self._panel)
        self._fade.setOpacity(0.0)
        self._panel.setGraphicsEffect(self._fade)
        self._anim = None

    def _btn_style(self, primary):
        if primary:
            bg, fg, border = self._neon, "#ffffff", self._neon
        else:
            bg, fg, border = "rgba(255, 255, 255, 16)", self._text, self._accent
        return (
            f"QPushButton {{ background: {bg}; color: {fg};"
            f" border: 1px solid {border}; border-radius: 8px;"
            f" padding: 9px 18px; font-size: 13px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {self._accent}; }}"
            f"QPushButton:disabled {{ color: {self._muted};"
            f" background: rgba(255, 255, 255, 10); border-color: transparent; }}"
        )

    def _sub_btn_style(self):
        """Discreet dashed style for the optional Add to Steam action."""
        return (
            f"QPushButton {{ background: transparent; color: {self._muted};"
            f" border: 1px dashed {self._muted}; border-radius: 6px;"
            f" padding: 4px 12px; font-size: 11px; font-weight: normal; }}"
            f"QPushButton:hover {{ color: {self._text};"
            f" border: 1px solid {self._text};"
            f" background: rgba(255, 255, 255, 20); }}"
        )

    # ── geometry / fade ──────────────────────────────────────────────

    def _cover_window(self):
        panel = getattr(self, "_panel", None)
        if panel is None:
            return
        try:
            rect = self.mw.rect()
        except RuntimeError:
            return
        self.setGeometry(rect)
        w = max(rect.width(), 640)
        h = max(rect.height(), 480)
        if getattr(self, "_immersive", False):
            panel.setGeometry(0, 0, w, h)
            panel.setStyleSheet(
                f"QFrame#GameDetailPanel {{ background: #050308;"
                f" border: none; border-radius: 0px; }}"
            )
            return
        pw = max(PANEL_MIN_W, int(w * PANEL_RATIO))
        min_h = PANEL_MIN_H
        steam_btn = getattr(self, "_steam_btn", None)
        if steam_btn is not None and not steam_btn.isHidden():
            min_h = PANEL_MIN_H_ADD_STEAM
        ph = max(min_h, int(h * PANEL_RATIO))
        pw = min(pw, w - 40)
        ph = min(ph, h - 40)
        panel.setGeometry((w - pw) // 2, (h - ph) // 2, pw, ph)
        panel.setStyleSheet(
            f"QFrame#GameDetailPanel {{ background: {self._bg};"
            f" border: 1px solid {self._accent}; border-radius: 14px; }}"
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cover_window()

    def eventFilter(self, obj, event):
        if obj is self.mw and event.type() == QEvent.Type.Resize and self.isVisible():
            self._cover_window()
        return super().eventFilter(obj, event)

    def _fade_in(self):
        anim = QPropertyAnimation(self._fade, b"opacity", self)
        anim.setDuration(FADE_MS)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim = anim
        anim.start()

    # ── public API ───────────────────────────────────────────────────

    def show_game(self, title):
        title = str(title or "").strip()
        if not title:
            return
        self._gen += 1
        self._title = title
        self._entry = self._catalog_entry(title)
        self._url = str(self._entry.get("url") or "").rstrip("/")
        self._details = None
        self._trailer = None
        # The local MP4 belongs to the previous title: if it survives here,
        # _trailer_url() serves it while the new download is still running.
        self._local_trailer = ""
        self._play_when_ready = False
        self._selected_url = None
        self._selected_label = "Auto"
        self._extract_retries = 0
        self._extract_pending = False

        if self._immersive:
            self._set_immersive(False)
        self._stop_download()
        self._teardown_player()
        self._stack.setCurrentIndex(0)
        self._trailer_mode = "steam"
        self._play_btn.setText("▶  Play trailer")
        self._play_btn.setEnabled(False)
        self._hide_trailer_progress()
        self._play_btn.setVisible(True)
        self._status_label.setText("")
        self._cover_set = False
        self._big_cover_done = False
        self._cover.clear()

        self._apply_local_data()
        self._refresh_action_state()
        self._cover_window()
        self.show()
        self.raise_()
        self.setFocus()
        self._fade_in()

        gen = self._gen
        QTimer.singleShot(0, lambda: self._kickoff(gen))

    def close_popup(self):
        self._stop_download()
        self._teardown_player()
        self.hide()
        # Evita il "click-through" sulla card sotto il popup.
        try:
            self.mw._discovery_click_guard = time.monotonic() + 0.35
        except Exception:
            pass

    # ── data ─────────────────────────────────────────────────────────

    def _catalog_entry(self, title):
        catalog = getattr(self.mw, "master_catalog", None)
        if catalog is None:
            return {}
        entry = catalog.get_entry(title)
        return entry if isinstance(entry, dict) else {}

    def _igdb(self):
        meta = self._entry.get("igdb_meta")
        return meta if isinstance(meta, dict) else {}

    def _session_page(self):
        cache = getattr(self.mw, "session_cache", None) or {}
        return cache.get(self._url) if self._url else None

    def _apply_local_data(self):
        igdb = self._igdb()
        details = self._details or IgdbDetailsCache.get(self._title) or {}

        self._title_label.setText(self._title)

        bits = []
        year = igdb.get("release_year")
        if not year:
            year = str(details.get("release_date") or "")[:4]
        if year:
            bits.append(str(year))
        rating = igdb.get("rating") or details.get("aggregated_rating") or details.get("rating")
        if rating:
            bits.append(f"★ {rating}")
        age = details.get("age_ratings") or {}
        if age.get("pegi"):
            bits.append(f"PEGI {age['pegi']}")
        if age.get("esrb"):
            bits.append(f"ESRB {age['esrb']}")
        self._meta_label.setText("   ".join(bits))

        def _names_or_empty(values):
            # Le cache vecchie possono contenere id IGDB ("31") invece dei nomi.
            return [str(v) for v in (values or [])
                    if v and not str(v).strip().isdigit()]

        genres = _names_or_empty(igdb.get("genres")) or _names_or_empty(details.get("genres"))
        themes = _names_or_empty(igdb.get("themes")) or _names_or_empty(details.get("themes"))
        line = " · ".join(dict.fromkeys(genres + themes))
        self._genres_label.setText(line)

        self._fill_chips(self._mode_chips(igdb, details))
        self._apply_version_line()

        company = ""
        if details:
            dev = ", ".join(details.get("developers") or [])
            pub = ", ".join(details.get("publishers") or [])
            if dev and pub and dev != pub:
                company = f"Developer: {dev}   ·   Publisher: {pub}"
            elif dev:
                company = f"Developer: {dev}"
            elif pub:
                company = f"Publisher: {pub}"
            platforms = ", ".join((details.get("platforms") or [])[:4])
            if platforms:
                company = (company + "\n" if company else "") + f"Platforms: {platforms}"
        self._company_label.setText(company)

        text = ""
        if details:
            text = (details.get("summary") or details.get("storyline") or "").strip()
        if not text:
            sr = self._session_page()
            if sr:
                text = _plain_text(sr.get("description"))
        self._desc.setPlainText(text or "Description loading…")

        self._apply_cover_from_cache()

    def _mode_chips(self, igdb, details=None):
        chips = []
        modes = [m for m in (igdb.get("modes") or [])
                 if m and not str(m).strip().isdigit()]
        if not modes:
            modes = [m for m in ((details or {}).get("modes") or [])
                     if m and not str(m).strip().isdigit()]
        for mode in modes:
            chips.append(str(mode))
        mp = str(self._entry.get("mp_tag") or "").strip()
        if mp and mp.lower() not in {c.lower() for c in chips}:
            chips.append(mp)
        sr = self._session_page()
        if sr:
            sr_mp = str(sr.get("mp_tag") or "").strip()
            if sr_mp and sr_mp.lower() not in {c.lower() for c in chips}:
                chips.append(sr_mp)
        return chips

    def _fill_chips(self, labels):
        while self._chips_layout.count() > 1:
            item = self._chips_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for label in labels[:6]:
            chip = QLabel(str(label))
            chip.setStyleSheet(
                f"QLabel {{ color: {self._text}; background: rgba(115, 61, 145, 90);"
                f" border: 1px solid {self._accent}; border-radius: 9px;"
                f" padding: 2px 9px; font-size: 12px; }}"
            )
            self._chips_layout.insertWidget(self._chips_layout.count() - 1, chip)

    def _apply_version_line(self):
        entry = self._entry
        vs = entry.get("version_status") or {}
        parts = []
        version = str(entry.get("version") or "").strip()
        if version:
            parts.append(f"SteamRip: {version}")
        posted = vs.get("posted_build")
        if posted:
            date = str(vs.get("posted_build_date") or "").strip()
            parts.append(f"SR build {posted}" + (f" ({date})" if date else ""))
        steam = vs.get("steam_build")
        if steam:
            date = str(vs.get("steam_build_date") or "").strip()
            parts.append(f"Steam build {steam}" + (f" ({date})" if date else ""))

        alert = str(vs.get("alert") or "").strip()
        if alert:
            color = _alert_color(vs.get("alert_color"))
            parts.append(f"<span style='color:{color};'>{html_lib.escape(alert)}</span>")
        self._version_label.setText("   ·   ".join(parts))

    # ── async loading ────────────────────────────────────────────────

    def _kickoff(self, gen):
        if gen != self._gen:
            return
        self._load_details(gen)
        self._load_page(gen)
        self._load_trailer(gen)
        self._load_cover(gen)

    def _load_details(self, gen):
        cached = IgdbDetailsCache.get(self._title)
        if cached:
            self._details = cached
            self._apply_local_data()
            return

        igdb = self._igdb()
        vs = self._entry.get("version_status") or {}
        appid = (igdb.get("steam_appid")
                 or self._entry.get("steam_id")
                 or (vs.get("app_id") if isinstance(vs, dict) else "")
                 or "")
        worker = IGDBDetailsWorker(
            self._title,
            igdb_id=igdb.get("igdb_id") or "",
            appid=appid,
            parent=self,
        )
        worker.details_ready.connect(
            lambda title, details, g=gen: self._on_details_ready(g, title, details)
        )
        self._details_worker = worker
        worker.start()

    def _on_details_ready(self, gen, title, details):
        if gen != self._gen or not details:
            return
        IgdbDetailsCache.set(title, details)
        self._details = details
        self._apply_local_data()
        self._refresh_trailer_mode()
        if not self._big_cover_done:
            self._load_cover(gen)

    def _load_page(self, gen):
        page = self._session_page()
        if page:
            self._apply_sr_page(page)
            return
        if not self._url:
            return
        self._request_extract(gen)

    def _request_extract(self, gen):
        if not self.isVisible() or gen != self._gen:
            return
        scraper = getattr(self.mw, "scraper", None)
        if scraper is None or not self._url:
            return
        if getattr(scraper, "current_mode", None) is not None:
            if self._extract_retries >= 8:
                return
            self._extract_retries += 1
            QTimer.singleShot(1200, lambda: self._request_extract(gen))
            return
        self._extract_pending = True
        self._status_label.setText("Loading SteamRip page…")
        try:
            scraper._popup_extract = self._url
            scraper.extract_game(self._url)
        except Exception as e:
            print(f"[GameDetail] extract error: {e}")
            self._extract_pending = False

    def _on_extract_ready(self, data):
        if not isinstance(data, dict) or not self.isVisible():
            return
        page_url = str(data.get("page_url") or "").rstrip("/")
        if not self._url or page_url != self._url:
            return
        self._extract_pending = False
        cache = getattr(self.mw, "session_cache", None)
        if isinstance(cache, dict):
            try:
                cache[page_url] = dict(data, cached_at=time.time())
            except Exception:
                pass
        self._apply_sr_page(data)

    def _apply_sr_page(self, page):
        if not isinstance(page, dict):
            return
        vs = page.get("version_status")
        if isinstance(vs, dict) and vs and not (self._entry.get("version_status") or {}):
            self._entry = dict(self._entry, version_status=vs)
        if page.get("version") and not self._entry.get("version"):
            self._entry = dict(self._entry, version=str(page.get("version")))
        if page.get("mp_tag") and not self._entry.get("mp_tag"):
            self._entry = dict(self._entry, mp_tag=str(page.get("mp_tag")))

        self._apply_version_line()
        self._fill_chips(self._mode_chips(self._igdb(), self._details))

        if not (self._details and (self._details.get("summary") or
                                   self._details.get("storyline"))):
            text = _plain_text(page.get("description"))
            if text:
                self._desc.setPlainText(text)
        self._status_label.setText("")

    def _load_trailer(self, gen):
        meta = dict(self._entry)
        igdb = self._igdb()
        meta.setdefault("title", self._title)
        appid = igdb.get("steam_appid") or meta.get("steam_id") or ""
        sgdb = meta.get("sgdb_assets")
        if not appid and isinstance(sgdb, dict):
            appid = sgdb.get("steam_app_id") or ""
        vs = meta.get("version_status")
        if not appid and isinstance(vs, dict):
            appid = vs.get("app_id") or ""
        if appid:
            meta["steam_id"] = str(appid)

        worker = TrailerWorker([{"title": self._title, "meta": meta}], self)
        worker.trailers_ready.connect(
            lambda result, g=gen: self._on_trailer_ready(g, result)
        )
        self._trailer_worker = worker
        worker.start()

    def _on_trailer_ready(self, gen, result):
        if gen != self._gen:
            return
        # A new resolution invalidates any local file from the previous title.
        self._local_trailer = ""
        self._trailer = (result or {}).get(self._title) or None
        self._refresh_trailer_mode()
        if self._trailer:
            self._start_trailer_download(gen)

    def _trailer_key(self):
        igdb = self._igdb()
        appid = (igdb.get("steam_appid")
                 or self._entry.get("steam_id")
                 or (self._entry.get("sgdb_assets") or {}).get("steam_app_id")
                 or (self._entry.get("version_status") or {}).get("app_id")
                 or "")
        return str(appid) if str(appid).strip() else self._title

    def _show_trailer_progress(self, value=None):
        bar = getattr(self, "_trailer_progress", None)
        if bar is None:
            return
        if value is None:
            bar.setRange(0, 0)      # indeterminate while resolving/downloading
        else:
            bar.setRange(0, 100)
            bar.setValue(max(0, min(100, int(value))))
        bar.show()

    def _hide_trailer_progress(self):
        bar = getattr(self, "_trailer_progress", None)
        if bar is not None:
            bar.hide()

    def _start_trailer_download(self, gen):
        key = self._trailer_key()
        existing = cached_path(key)
        if existing and os.path.isfile(existing):
            self._local_trailer = existing
            self._apply_playback_source()
            return
        # No cached file for THIS title: make sure the previous title's local
        # MP4 cannot be selected by _trailer_url() while we download.
        self._local_trailer = ""
        if not isinstance(self._trailer, dict):
            return
        url = str(self._trailer.get("hls") or "")
        if not url:
            return
        worker = TrailerDownloadWorker(url, key, duration_ms=0, parent=self)
        worker.progress.connect(lambda pct, g=gen: self._on_download_progress(g, pct))
        worker.ready.connect(lambda path, g=gen: self._on_download_ready(g, path))
        worker.failed.connect(lambda msg, g=gen: self._on_download_failed(g, msg))
        self._download_worker = worker
        worker.start()
        self._show_trailer_progress()

    def _on_download_progress(self, gen, pct):
        if gen != self._gen:
            return
        self._show_trailer_progress(pct)
        self._status_label.setText(f"Preparing trailer… {pct}%")

    def _on_download_ready(self, gen, path):
        if gen != self._gen:
            return
        self._local_trailer = path
        self._hide_trailer_progress()
        self._status_label.setText("")
        was_playing = (self._player is not None and not _deleted(self._player)
                       and self._player.playbackState()
                       == QMediaPlayer.PlaybackState.PlayingState)
        position = self._player.position() if was_playing else 0
        self._apply_playback_source()
        if was_playing:
            # passa al file locale mantenendo la posizione: da qui il seek va
            try:
                self._player.setPosition(int(position))
                self._player.play()
            except RuntimeError:
                pass
        if self._play_when_ready:
            self._play_when_ready = False
            self._play_trailer()

    def _on_download_failed(self, gen, msg):
        if gen != self._gen:
            return
        print(f"[GameDetail] trailer download failed: {msg}")
        self._hide_trailer_progress()
        # Drop the finished worker reference before retrying: otherwise
        # _play_trailer() sees a "running" worker and waits forever.
        self._download_worker = None
        self._status_label.setText("")
        if self._play_when_ready:
            self._play_when_ready = False
            self._play_trailer()
        else:
            self._status_label.setText("Trailer preparation failed")

    def _load_cover(self, gen):
        if getattr(self, "_big_cover_done", False):
            return
        url = ""
        sgdb = self._entry.get("sgdb_assets")
        if isinstance(sgdb, dict):
            url = str(sgdb.get("grid") or "")
        if not url and self._details:
            url = str(self._details.get("cover_url") or "")
        if not url:
            return

        path = self._cover_cache_path()
        if path and os.path.isfile(path):
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                self._set_cover_pixmap(pixmap)
                self._promote_cover_to_mini(pixmap)
                return

        worker = _CoverFetchWorker(self._title, url, self)
        worker.ready.connect(lambda title, data, g=gen: self._on_cover_bytes(g, title, data))
        self._cover_worker = worker
        worker.start()

    def _on_cover_bytes(self, gen, title, data):
        if gen != self._gen:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return
        self._big_cover_done = True
        self._set_cover_pixmap(pixmap)
        path = self._cover_cache_path()
        if path:
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                pixmap.save(path, "JPEG", 88)
            except Exception:
                pass
        self._promote_cover_to_mini(pixmap)

    def _notify_discovery_cover(self, file_url):
        if not file_url:
            return
        discovery = getattr(self.mw, "discovery_manager", None)
        if discovery is not None:
            try:
                discovery.cover_ready.emit(self._title, file_url)
            except Exception:
                pass

    def _promote_cover_to_mini(self, pixmap):
        """Replace a horizontal SteamRIP fallback with this vertical cover.

        The detail sheet is the first place where a good IGDB/SGDB box art is
        guaranteed to be available. Persisting it here also updates the
        Discovery card, so the next row rebuild does not go back to the
        featured image.
        """
        if pixmap is None or pixmap.isNull():
            return
        if pixmap.width() >= pixmap.height():
            return
        existing = MiniCoverManager.local_cache_path(self._title)
        if os.path.isfile(existing):
            current = QPixmap(existing)
            if not current.isNull() and current.height() >= current.width():
                # The cache is already good, but the visible Discovery card
                # may still hold the old horizontal path: notify it anyway.
                self._notify_discovery_cover(
                    MiniCoverManager.local_cache_url(self._title))
                return
        sync = ""
        try:
            sync = (self.mw.app_config.get("savegames", {}) or {}).get(
                "sync_folder", "")
        except Exception:
            sync = ""
        file_url = MiniCoverManager.persist_pixmap(self._title, pixmap, sync)
        if not file_url:
            return
        self._notify_discovery_cover(file_url)

    def _cover_cache_path(self):
        try:
            base = PathManager.get_app_data_dir() / "detail_covers"
            os.makedirs(str(base), exist_ok=True)
            return os.path.join(str(base), safe_game_folder_name(self._title) + ".jpg")
        except Exception:
            return ""

    def _apply_cover_from_cache(self):
        if getattr(self, "_cover_set", False):
            return
        installed = getattr(self.mw, "installed_games", {}) or {}
        sync = ""
        try:
            sync = (self.mw.app_config.get("savegames", {}) or {}).get("sync_folder", "")
        except Exception:
            sync = ""
        path = MiniCoverManager.resolve_cover_path(self._title, installed, sync)
        if not path:
            self._cover.clear()
            return
        local = QUrl(path).toLocalFile() or path.replace("file://", "")
        pixmap = QPixmap(local)
        if not pixmap.isNull():
            self._set_cover_pixmap(pixmap)
            if pixmap.height() > pixmap.width():
                self._notify_discovery_cover(
                    MiniCoverManager.local_cache_url(self._title))

    def _set_cover_pixmap(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._cover_set = True
        scaled = pixmap.scaled(
            QSize(COVER_W - 6, COVER_H - 6),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._cover.setPixmap(scaled)

    # ── trailer player ───────────────────────────────────────────────

    def _ensure_player(self):
        if self._video is not None and not _deleted(self._video):
            return self._player
        self._video = VideoSurface(self._player_page)
        self._video.set_theme({
            "accent": self._accent, "neon": self._neon,
            "text": self._text, "muted": self._muted, "bg": self._panel_bg,
        })
        self._video.set_play_toggle_handler(self._toggle_trailer)
        self._video.set_seek_handler(self._seek)
        self._video.set_menu_builder(self._build_res_menu)
        self._video.set_volume_handler(self._on_volume_changed)
        self._video.set_volume_release_handler(self._persist_volume)
        self._video.set_mute_handler(self._toggle_mute)
        self._video.set_fullscreen_handler(self._toggle_immersive)
        self._player_layout.addWidget(self._video)

        cfg = getattr(self.mw, "app_config", {}) or {}
        try:
            volume = max(0.0, min(1.0, float(cfg.get("player_volume", 0.75))))
        except (TypeError, ValueError):
            volume = 0.75
        self._player_muted = bool(cfg.get("player_muted", False))

        self._audio = QAudioOutput(self._player_page)
        self._audio.setVolume(volume)
        self._audio.setMuted(self._player_muted)
        self._video.set_volume(volume)
        self._video.set_muted(self._player_muted)
        self._player = QMediaPlayer(self._player_page)
        self._player.setAudioOutput(self._audio)
        self._player.setLoops(QMediaPlayer.Loops.Once)

        sink = QVideoSink(self._player)
        sink.videoFrameChanged.connect(self._video.set_frame)
        self._player.setVideoOutput(sink)

        self._player.positionChanged.connect(self._video.update_position)
        self._player.durationChanged.connect(self._video.update_duration)
        self._player.playbackStateChanged.connect(self._video.set_playing_state)
        self._player.errorOccurred.connect(self._on_player_error)
        return self._player

    def _toggle_trailer(self):
        if self._trailer_mode != "steam" or not self._trailer:
            self._open_external_trailer()
            return
        if self._player is not None and not _deleted(self._player):
            if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._stop_trailer()
                return
        self._play_trailer()

    def _play_trailer(self):
        if not self._trailer:
            return
        if self._local_trailer and not os.path.isfile(self._local_trailer):
            self._local_trailer = ""
        url = self._selected_url or self._trailer_url()
        if not url:
            return
        if not self._local_trailer:
            worker = self._download_worker
            if worker is not None and worker.isRunning():
                # Download in progress: wait for the local file (seek works).
                self._play_when_ready = True
                self._show_trailer_progress()
                self._status_label.setText("Preparing trailer…")
                return
        player = self._ensure_player()
        if player.source().toString() != url:
            player.stop()
            player.setSource(QUrl(url))
        self._want_play = True
        self._apply_playback_source()
        self._stack.setCurrentIndex(1)
        self._video.set_active(True)
        self._video.show()
        self._video.show_controls_now()
        player.play()
        self._play_btn.setText("■  Close trailer")
        events = getattr(self.mw, "events", None)
        if events is not None:
            events.emit_event(
                EVENT_TRAILER_WATCHED,
                title=self._title,
                mode=self._trailer_mode,
            )

    def _stop_trailer(self):
        self._want_play = False
        # pause + rewind: chiudere il trailer non deve smontare la pipeline
        # HLS (il teardown sincrono era la causa del freeze).
        if self._player is not None and not _deleted(self._player):
            try:
                self._player.pause()
                self._player.setPosition(0)
            except RuntimeError:
                pass
        if self._video is not None and not _deleted(self._video):
            self._video.set_active(False)
            self._video.reset_controls()
        self._stack.setCurrentIndex(0)
        self._play_btn.setText("▶  Play trailer")

    def _stop_download(self):
        worker = self._download_worker
        self._download_worker = None
        self._hide_trailer_progress()
        if worker is not None and worker.isRunning():
            try:
                worker.stop()
                worker.wait(1500)
            except RuntimeError:
                pass

    def _teardown_player(self):
        if self._player is not None and not _deleted(self._player):
            try:
                self._player.stop()
                self._player.setSource(QUrl())
                self._player.setVideoOutput(None)
                self._player.setParent(None)
                self._player.deleteLater()
            except RuntimeError:
                pass
        if self._audio is not None and not _deleted(self._audio):
            try:
                self._audio.deleteLater()
            except RuntimeError:
                pass
        if self._video is not None and not _deleted(self._video):
            try:
                self._video.setParent(None)
                self._video.deleteLater()
            except RuntimeError:
                pass
        self._player = None
        self._audio = None
        self._video = None

    def _trailer_url(self):
        if self._local_trailer and os.path.isfile(self._local_trailer):
            return QUrl.fromLocalFile(self._local_trailer).toString()
        if not isinstance(self._trailer, dict):
            return ""
        return str(self._trailer.get("mp4") or self._trailer.get("webm")
                   or self._trailer.get("hls") or "")

    def _apply_playback_source(self):
        if self._video is None or _deleted(self._video):
            return
        local = bool(self._local_trailer and os.path.isfile(self._local_trailer))
        self._video.set_seek_enabled(local)

    def _seek(self, ms):
        player = self._player
        if player is None or _deleted(player):
            return
        try:
            duration = int(player.duration())
            if duration > 0:
                ms = max(0, min(int(ms), duration - 250))
            self._want_play = True
            player.setPosition(int(ms))
        except RuntimeError:
            return
        # Alcuni backend HLS restano in pausa o staccano l'audio dopo un seek.
        QTimer.singleShot(280, self._ensure_playing)

    def _ensure_playing(self):
        player = self._player
        if player is None or _deleted(player) or not self._want_play:
            return
        try:
            if player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                player.play()
            if self._audio is not None and player.audioOutput() is not self._audio:
                player.setAudioOutput(self._audio)
        except RuntimeError:
            pass

    def _on_volume_changed(self, volume):
        if self._audio is not None and not _deleted(self._audio):
            try:
                self._audio.setVolume(float(volume))
                if self._player_muted and volume > 0:
                    self._player_muted = False
                    self._audio.setMuted(False)
                    if self._video is not None and not _deleted(self._video):
                        self._video.set_muted(False)
            except RuntimeError:
                pass

    def _persist_volume(self, volume):
        self._save_player_prefs(volume=float(volume))

    def _toggle_mute(self):
        self._player_muted = not self._player_muted
        if self._audio is not None and not _deleted(self._audio):
            try:
                self._audio.setMuted(self._player_muted)
            except RuntimeError:
                pass
        if self._video is not None and not _deleted(self._video):
            self._video.set_muted(self._player_muted)
        self._save_player_prefs()

    def _save_player_prefs(self, volume=None):
        cfg = getattr(self.mw, "app_config", None)
        if not isinstance(cfg, dict):
            return
        if volume is not None:
            cfg["player_volume"] = max(0.0, min(1.0, float(volume)))
        cfg["player_muted"] = bool(self._player_muted)
        try:
            save_config(cfg)
        except Exception as e:
            print(f"[GameDetail] config save error: {e}")

    def _toggle_immersive(self):
        self._set_immersive(not self._immersive)

    def _set_immersive(self, on):
        on = bool(on)
        if on == self._immersive:
            return
        if on and (self._trailer_mode != "steam" or not self._trailer):
            return
        if on and self._stack.currentIndex() != 1:
            self._play_trailer()
        if not on:
            self._stop_trailer()
        self._immersive = on
        self._cover.setVisible(not on)
        self._info_host.setVisible(not on)
        self._footer_host.setVisible(not on)
        self._close_btn.setVisible(not on)
        self._cover_window()
        if not on:
            self._stack.setCurrentIndex(0)

    def _on_close_clicked(self):
        if self._immersive:
            self._set_immersive(False)
            return
        self.close_popup()

    def _youtube_id(self):
        details = self._details or IgdbDetailsCache.get(self._title) or {}
        videos = details.get("videos") or []
        return str(videos[0]) if videos else ""

    def _open_external_trailer(self):
        vid = self._youtube_id()
        if vid:
            url = f"https://www.youtube.com/watch?v={quote(str(vid))}"
        else:
            url = ("https://www.youtube.com/results?search_query="
                   + quote(f"{self._title} trailer"))
        QDesktopServices.openUrl(QUrl(url))

    def _refresh_trailer_mode(self):
        if self._trailer:
            self._trailer_mode = "steam"
            label = "\u25b6  Play trailer"
        elif self._youtube_id():
            self._trailer_mode = "youtube"
            label = "\u25b6  Watch on YouTube"
        else:
            self._trailer_mode = "search"
            label = "\U0001f50e  Search trailer on YouTube"
        self._play_btn.setText(label)
        self._play_btn.setVisible(True)
        self._play_btn.setEnabled(True)

    def _build_res_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: {self._panel_bg}; color: {self._text};"
            f" border: 1px solid {self._accent}; padding: 4px; }}"
            f" QMenu::item {{ padding: 4px 18px; }}"
            f" QMenu::item:selected {{ background: {self._accent}; }}"
        )
        variants = []
        if isinstance(self._trailer, dict):
            variants = self._trailer.get("variants") or []
        auto = menu.addAction("Auto")
        auto.setCheckable(True)
        auto.setChecked(self._selected_url is None)
        auto.triggered.connect(lambda: self._select_resolution(None, "Auto"))
        if variants:
            menu.addSeparator()
            for v in variants:
                url = v.get("url")
                label = v.get("label", "Auto")
                act = menu.addAction(label)
                act.setCheckable(True)
                act.setChecked(self._selected_url == url)
                act.triggered.connect(
                    lambda checked=False, u=url, l=label: self._select_resolution(u, l)
                )
        return menu

    def _select_resolution(self, url, label):
        self._selected_url = url
        self._selected_label = label
        if self._video is not None and not _deleted(self._video):
            self._video.set_resolution_label(label)
        if self._player is not None and not _deleted(self._player):
            self._player.setSource(QUrl(url or self._trailer_url()))
            self._player.play()

    def _on_player_error(self, error, error_string):
        if error == QMediaPlayer.Error.NoError:
            return
        if self._selected_url is None or self._falling_back:
            print(f"[GameDetail] trailer error: {error_string}")
            return
        self._falling_back = True
        self._selected_url = None
        self._selected_label = "Auto"
        if self._video is not None and not _deleted(self._video):
            self._video.set_resolution_label("Auto")
        if self._player is not None and not _deleted(self._player):
            self._player.setSource(QUrl(self._trailer_url()))
            self._player.play()
        QTimer.singleShot(3000, lambda: setattr(self, "_falling_back", False))

    # ── actions ──────────────────────────────────────────────────────

    def _installed_meta(self):
        games = getattr(self.mw, "installed_games", {}) or {}
        meta = games.get(self._title)
        return meta if isinstance(meta, dict) else {}

    def _refresh_action_state(self):
        """Mirror the Explorer rail: Install, or Play/Set Executable/Add to Steam."""
        meta = self._installed_meta()
        if not meta:
            self._action_mode = "install"
            self._action_btn.setText("Install (Open in Games Explorer)")
            self._action_btn.setStyleSheet(self._btn_style(primary=True))
            self._steam_btn.hide()
            self._playtime_label.hide()
            return

        steam_id = str(meta.get("steam_shortcut_id") or "").strip()
        # On Linux, adding to Steam is the mandatory path (Proton wrapper):
        # the executable picker lives inside that flow, so no sub-button.
        self._steam_btn.setVisible(sys.platform != "linux" and not steam_id)

        if sys.platform == "linux":
            self._action_mode = "play" if steam_id else "add_steam"
            self._action_btn.setText("➕  ADD TO STEAM" if not steam_id else "▶  PLAY")
        else:
            exe_path = str(meta.get("executable_path") or "").strip()
            self._action_mode = "play" if exe_path else "set_executable"
            self._action_btn.setText("▶  PLAY" if exe_path else "⚙  SET EXECUTABLE")

        cfg = getattr(self.mw, "app_config", {}) or {}
        mode = (cfg.get("playtime") or {}).get("display", "hours")
        seconds = int(meta.get("playtime_seconds", 0) or 0)
        self._playtime_label.setText(
            f"⏱ {PlaytimeManager.format_playtime(seconds, mode)}"
        )
        self._playtime_label.show()

    def _on_action_clicked(self):
        if self._action_mode == "install":
            self._on_install()
            return
        was_play = self._action_mode == "play"
        self.mw.game_launcher.launch_game(self._title)
        if was_play:
            self.close_popup()
            return
        # Set Executable / Add to Steam may have changed the metadata: keep the
        # popup in sync instead of forcing the user to reopen it.
        self._refresh_action_state()
        self._cover_window()

    def _on_add_to_steam(self):
        if not self._installed_meta():
            return
        self.mw.game_launcher._add_windows_game_to_steam_optional(self._title)
        self._refresh_action_state()
        self._cover_window()

    def _on_install(self):
        url = self._url or self._title
        self.close_popup()
        mw = self.mw
        try:
            tabs = getattr(mw, "tabs", None)
            explorer = getattr(mw, "tab_explorer", None)
            if tabs is not None and explorer is not None:
                tabs.setCurrentWidget(explorer)
            search = getattr(mw, "search_input", None)
            if search is not None:
                search.setText(url)
                mw.start_search()
        except Exception as e:
            print(f"[GameDetail] install handoff error: {e}")

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self._immersive:
                self._set_immersive(False)
            else:
                self.close_popup()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        self.close_popup()


def _deleted(obj):
    try:
        import PyQt6.sip as sip
        return sip.isdeleted(obj)
    except Exception:
        return False


def _alert_color(color):
    mapping = {
        "green": "#3ddc84", "success": "#3ddc84",
        "red": "#ff4d6d", "danger": "#ff4d6d",
        "yellow": "#ffd166", "orange": "#ffb020", "warning": "#ffb020",
    }
    return mapping.get(str(color or "").lower(), "#8a7fa4")


def _plain_text(html):
    if not html:
        return ""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all(["style", "script"]):
            tag.decompose()
        return soup.get_text("\n", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", str(html))
        return re.sub(r"\s+\n", "\n", text).strip()
