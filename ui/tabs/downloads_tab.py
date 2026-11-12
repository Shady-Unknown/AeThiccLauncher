import os
import time
import uuid
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QSize, QUrl, QVariantAnimation
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QHBoxLayout, QListWidgetItem, QGraphicsDropShadowEffect, QPushButton, QMenu, QListWidget, QMessageBox
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QIcon, QPixmap, QCursor, QDesktopServices
from core.downloader import SmartDownloaderWorker
from core.archive_utils import clear_incomplete, mark_incomplete
from core.config import get_base_dir
from core.download_queue_model import (
    KIND_DOWNLOAD,
    KIND_EXTRACT,
    build_layout,
    clear_extraction_state,
    format_bytes,
    state_color,
    temp_cleanup_candidates,
)
from core.download_utils import (
    dedupe_tasks,
    merge_task_data,
    normalize_dest_path,
    provider_key,
    task_created_at,
)
from ui.widgets.queue_items import (
    QueueListWidget,
    NeonGraphWidget,
    QueueItemWidget,
    SectionHeaderWidget,
)

class DownloadManagerTab(QWidget):
    progress_updated = pyqtSignal(int, float, float)
    download_started = pyqtSignal(str)
    download_completed = pyqtSignal(str)
    download_error = pyqtSignal(str)
    queue_empty = pyqtSignal()
    cf_bypass_requested = pyqtSignal(str)
    gate_fallback_requested = pyqtSignal(str)
    resume_detected = pyqtSignal(float, float)
    pause_state_changed = pyqtSignal(bool) # NUOVO SEGNALE: True se in pausa, False se in play
    extraction_cancel_requested = pyqtSignal()
    pending_extraction_ready = pyqtSignal(str, dict) # archive_path, game_data
    open_game_page_requested = pyqtSignal(dict)
    attention_changed = pyqtSignal(int)

    def __init__(self, theme_config, parent=None):
        super().__init__(parent)
        self.theme = theme_config
        self.neon_start = self.theme.get('neon_gradient_start', '#8d0bb1')
        self.neon_end = self.theme.get('neon_gradient_end', '#00d2ff')
        self.text_muted = self.theme.get('text_muted', '#8a7fa4')
        self.status_green = self.theme.get('status_green', '#00ff88')
        self.status_yellow = self.theme.get('status_yellow', '#ffcc00')
        self.status_red = self.theme.get('status_red', '#ff00a0')
        
        self.queue = []
        self.completed_tasks = []
        self.active_worker = None
        self.current_task = None
        self.is_paused = False
        self.is_extracting = False
        self.pending_extractions = []
        self.dismissed_archives = []
        self.thread_cemetery = []

        # Extraction bookkeeping: the pending entry that is being extracted.
        self.current_extraction_id = None
        self._extraction_started = False
        self._layout_model = {"top": None, "sections": []}
        self._last_attention_count = -1
        self._banner_dismissed_count = -1
        self._last_state_save = 0.0
        self.temp_folder = ''
        self._state_hidden = False
        
        self.download_start_time = None
        self.download_start_bytes = 0
        self.last_dl_bytes = 0
        self.speed_history = []
        self.current_pct = 0
        self.current_dl_mb = 0
        self.current_tot_mb = 0
        
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._update_math_ui)
        self.ui_timer.start(500)
        self.last_ui_time = time.time()

        self.init_ui()

    def _style_banner(self):
        accent = (
            self.theme.get('queue_state_partial')
            or self.theme.get('status_yellow')
            or '#ffcc00'
        )
        panel = self.theme.get('surface_panel_purple', '#2b1a42')
        self.banner.setStyleSheet(
            f"QFrame#AttentionBanner {{ background-color: {panel}; "
            f"border: 1px solid {accent}; border-radius: 7px; }}"
        )
        self.btn_banner_review.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {accent}; "
            f"border: 1px solid {accent}; border-radius: 5px; padding: 3px 10px; "
            "font-weight: bold; font-size: 11px; }"
            f"QPushButton:hover {{ background-color: {accent}; color: #120a1c; }}"
        )
        self.btn_banner_close.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {accent}; "
            "border: none; font-size: 14px; font-weight: bold; }"
            f"QPushButton:hover {{ color: {self.theme.get('text_primary_white', '#ffffff')}; }}"
        )

    def _dismiss_banner(self):
        self._banner_dismissed_count = self._last_attention_count
        self.banner.hide()

    def _focus_first_attention(self):
        top = self._layout_model.get('top')
        rows = []
        if top is not None:
            rows.append(top)
        for section in self._layout_model.get('sections', []):
            rows.extend(section.get('rows', []))
        target = next((row for row in rows if row.needs_attention), None)
        if target is None:
            return
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and data == (target.kind, target.row_id):
                self.list_widget.setCurrentItem(item)
                self.list_widget.scrollToItem(item)
                break

    def attention_count(self):
        return max(0, int(self._last_attention_count or 0))

    def _refresh_attention(self, layout):
        rows = []
        top = layout.get('top')
        if top is not None:
            rows.append(top)
        for section in layout.get('sections', []):
            rows.extend(section.get('rows', []))
        count = sum(1 for row in rows if row.needs_attention)
        self._last_attention_count = count
        if count:
            if count == 1:
                self.banner_label.setText(
                    "1 item needs attention \u2014 press the play button to "
                    "resume or extract."
                )
            else:
                self.banner_label.setText(
                    f"{count} items need attention \u2014 press the play button "
                    "to resume or extract."
                )
            if self._banner_dismissed_count != count:
                self.banner.show()
        else:
            self.banner.hide()
        self.attention_changed.emit(count)

    def _style_temp_buttons(self):
        muted = self.theme.get('text_muted', '#8a7fa4')
        accent = self.theme.get('queue_state_partial') or self.theme.get('status_yellow')
        for btn in (self.btn_open_temp, self.btn_clean_temp):
            btn.setStyleSheet(
                f"QPushButton {{ background-color: transparent; color: {muted}; "
                f"border: 1px solid {muted}; border-radius: 5px; padding: 3px 8px; "
                "font-size: 10px; font-weight: bold; }"
                f"QPushButton:hover {{ color: {accent}; border-color: {accent}; }}"
            )

    def _temp_dir(self):
        return str(getattr(self, 'temp_folder', '') or '')

    def open_temp_folder(self):
        folder = self._temp_dir()
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self.set_status("Temporary folder not available", self.status_yellow)

    def _protected_temp_paths(self):
        protected = set()
        tasks = ([self.current_task] if self.current_task else []) + list(self.queue or [])
        for task in tasks:
            dest = str((task or {}).get('dest_path') or '')
            if dest:
                protected.add(normalize_dest_path(dest))
        for entry in self.pending_extractions or []:
            archive = str((entry or {}).get('archive_path') or '')
            if archive:
                protected.add(normalize_dest_path(archive))
        return protected

    def clean_temp_files(self):
        """Delete cached files that are not referenced by the queue."""
        folder = self._temp_dir()
        if not folder or not os.path.isdir(folder):
            self.set_status("Temporary folder not available", self.status_yellow)
            return

        candidates = temp_cleanup_candidates(
            folder, self._protected_temp_paths()
        )
        total = sum(size for _path, size in candidates)

        if not candidates:
            self.set_status("No unused temp files to remove", self.status_green)
            return

        reply = QMessageBox.question(
            self,
            "Remove all temp. files",
            f"Delete {len(candidates)} unused file(s) ({format_bytes(total)}) "
            "from the temporary folder?\n\n"
            "Active downloads, queued archives and pending extractions are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        removed = 0
        freed = 0
        for path, size in candidates:
            try:
                os.remove(path)
                removed += 1
                freed += size
            except OSError as exc:
                print(f"[DLManager] Could not remove temp file {path}: {exc}")
        self.set_status(
            f"Removed {removed} temp file(s) - {format_bytes(freed)} freed",
            self.status_green,
        )

    def _hex_to_rgba_string(self, hex_str, opacity):
        hex_str = hex_str.lstrip('#')
        if len(hex_str) == 3:
            hex_str = ''.join([c*2 for c in hex_str])
        r = int(hex_str[0:2], 16)
        g = int(hex_str[2:4], 16)
        b = int(hex_str[4:6], 16)
        return f"rgba({r}, {g}, {b}, {opacity})"

    def _colorize_icon(self, path, theme_data):
        size = 64
        pixmap = QIcon(path).pixmap(size, size)
        result = QPixmap(pixmap.size())
        result.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0.0, QColor(theme_data.get('neon_gradient_start', '#8d0bb1')))
        grad.setColorAt(1.0, QColor(theme_data.get('neon_gradient_end', '#00d2ff')))
        
        painter.fillRect(result.rect(), grad)
        painter.end()
        
        return QIcon(result)

    def _set_empty_state(self):
        self.active_card.setStyleSheet(f"background-color: transparent; border-radius: 12px; border: 2px dashed rgba(255, 255, 255, 0.1);")
        self.active_card.setMaximumHeight(250)
        self.lbl_title.setText("No Active Downloads")
        self.lbl_title.setGraphicsEffect(None)
        self.lbl_versions.setText("Waiting for data...")
        
        # Reset colors back to standard
        self.lbl_pct.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 18px; font-weight: bold; border: none; background: transparent;")
        self.thin_bar_fill.setStyleSheet(f"background-color: {self.neon_end}; border-radius: 2px; border: none;")
        
        self.lbl_pct.setText("0%")
        self.thin_bar_fill.setFixedWidth(0)
        
        for key in self.stat_labels:
            lbl = self.stat_labels[key]
            lbl.setText("-")
            if key != "Status":
                lbl.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            
        html = f"""
        <html><head><style>
            body {{ margin: 0; padding: 0; background: transparent; display: flex; justify-content: center; align-items: center; overflow: hidden; height: 100vh; }}
            .empty-box {{ width: 104px; height: 159px; border: 2px dashed rgba(255, 255, 255, 0.1); border-radius: 8px; }}
        </style></head><body>
            <div class="empty-box"></div>
        </body></html>
        """
        self.cover_view.setHtml(html)
        self.btn_pause.hide()
        self.btn_stop.hide()
        if hasattr(self, 'graph'):
            self.graph.clear()
            self.graph.hide()

    def _set_active_state(self):
        self.active_card.setMaximumHeight(16777215)
        if hasattr(self, 'graph'):
            self.graph.show()
        self.active_card.setStyleSheet(f"background-color: {self.theme['surface_panel_purple']}; border-radius: 12px; border: 1px solid {self.neon_end};")
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(15)
        glow.setColor(QColor(self.neon_end))
        glow.setOffset(0, 0)
        self.lbl_title.setGraphicsEffect(glow)
        self.btn_pause.show()
        self.btn_stop.show()

    def init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        self.banner = QFrame()
        self.banner.setObjectName("AttentionBanner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 7, 8, 7)
        banner_layout.setSpacing(10)
        self.banner_label = QLabel("")
        self.banner_label.setStyleSheet(
            f"color: {self.theme.get('text_primary_white', '#ffffff')}; "
            "font-size: 12px; border: none; background: transparent;"
        )
        banner_layout.addWidget(self.banner_label, 1)
        self.btn_banner_review = QPushButton("Review")
        self.btn_banner_review.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_banner_review.clicked.connect(self._focus_first_attention)
        self.btn_banner_close = QPushButton("\u00d7")
        self.btn_banner_close.setFixedSize(24, 24)
        self.btn_banner_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_banner_close.clicked.connect(self._dismiss_banner)
        banner_layout.addWidget(self.btn_banner_review)
        banner_layout.addWidget(self.btn_banner_close)
        self._style_banner()
        self.banner.hide()
        main_layout.addWidget(self.banner)

        self.active_card = QFrame()
        self.active_card.setStyleSheet(f"background-color: {self.theme['surface_panel_purple']}; border-radius: 12px; border: 1px solid {self.neon_end};")
        card_layout = QVBoxLayout(self.active_card)
        card_layout.setContentsMargins(15, 15, 15, 5)
        card_layout.setSpacing(5)
        
        content_layout = QHBoxLayout()
        content_layout.setSpacing(15)
        
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        self.cover_view = QWebEngineView()
        self.cover_view.setFixedSize(130, 195)
        self.cover_view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        content_layout.addWidget(self.cover_view)
        
        center_col = QVBoxLayout()
        center_col.setSpacing(4)
        
        top_row = QHBoxLayout()
        title_box = QVBoxLayout()
        
        self.lbl_title = QLabel("No Active Downloads")
        self.lbl_title.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 22px; font-weight: bold; border: none; background: transparent;")
        
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(15)
        glow.setColor(QColor(self.neon_end))
        glow.setOffset(0, 0)
        self.lbl_title.setGraphicsEffect(glow)
        title_box.addWidget(self.lbl_title)
        
        self.lbl_versions = QLabel("Waiting for data...")
        self.lbl_versions.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 13px; border: none; background: transparent;")
        title_box.addWidget(self.lbl_versions)
        title_box.addStretch()
        
        top_row.addLayout(title_box, stretch=1)
        
        stats_col = QVBoxLayout()
        stats_col.setSpacing(2)
        
        self.stat_labels = {}
        for key in ["Game", "Web Version", "Total Size", "Status", "Speed (Avg)", "ETA"]:
            row = QHBoxLayout()
            row.setSpacing(10)
            lbl_k = QLabel(f"{key}:")
            lbl_k.setStyleSheet(f"color: {self.text_muted}; font-size: 11px; border: none; background: transparent;")
            lbl_v = QLabel("-")
            lbl_v.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            lbl_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.stat_labels[key] = lbl_v
            row.addWidget(lbl_k)
            row.addWidget(lbl_v)
            stats_col.addLayout(row)
            
        self.stats_container = QFrame()
        self.stats_container.setObjectName("StatsContainer")
        stats_col.setContentsMargins(8, 6, 8, 6)
        self.stats_container.setLayout(stats_col)
        self.stats_container.setStyleSheet(f"QFrame#StatsContainer {{ background-color: rgba(0,0,0,0.1); border: 1px solid rgba(255,255,255,0.05); border-radius: 6px; }}")
        
        top_row.addWidget(self.stats_container, alignment=Qt.AlignmentFlag.AlignTop)
        
        center_col.addLayout(top_row)
        center_col.addStretch()
        
        bar_layout = QVBoxLayout()
        bar_layout.setSpacing(2)
        
        self.lbl_pct = QLabel("0%")
        self.lbl_pct.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-size: 18px; font-weight: bold; border: none; background: transparent;")
        self.lbl_pct.setAlignment(Qt.AlignmentFlag.AlignRight)
        bar_layout.addWidget(self.lbl_pct)
        
        self.thin_bar_bg = QFrame()
        self.thin_bar_bg.setFixedHeight(4)
        self.thin_bar_bg.setStyleSheet(f"background-color: rgba(255,255,255,0.1); border-radius: 2px; border: none;")
        
        self.thin_bar_fill = QFrame(self.thin_bar_bg)
        self.thin_bar_fill.setFixedHeight(4)
        self.thin_bar_fill.setStyleSheet(f"background-color: {self.neon_start}; border-radius: 2px; border: none;")
        self.thin_bar_fill.setFixedWidth(0)
        
        bar_layout.addWidget(self.thin_bar_bg)
        
        center_col.addLayout(bar_layout)
        
        content_layout.addLayout(center_col, stretch=1)
        card_layout.addLayout(content_layout)
        
        # --- NUOVI PULSANTI IN-TAB ---
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 4, 0, 0)
        controls_layout.setSpacing(10)
        
        self.btn_pause = QPushButton()
        self.btn_stop = QPushButton()
        
        media_dir = os.path.join(get_base_dir(), ".media")
        self.icon_pause = self._colorize_icon(os.path.join(media_dir, "pause_round.svg"), self.theme)
        self.icon_play = self._colorize_icon(os.path.join(media_dir, "play_round.svg"), self.theme)
        self.icon_stop = self._colorize_icon(os.path.join(media_dir, "stop_round.svg"), self.theme)
        
        for btn in [self.btn_pause, self.btn_stop]:
            btn.setFixedSize(32, 32)
            btn.setIconSize(QSize(24, 24))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            btn.setStyleSheet(f"""
                QPushButton {{ background-color: transparent; border: 1px solid {self.neon_end}; border-radius: 16px; }}
                QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.15); }}
            """)
            
        self.btn_pause.setIcon(self.icon_pause)
        self.btn_stop.setIcon(self.icon_stop)
        
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_stop.clicked.connect(self.stop_current)
        
        controls_layout.addStretch()
        controls_layout.addWidget(self.btn_pause)
        controls_layout.addWidget(self.btn_stop)
        
        card_layout.addLayout(controls_layout)
        
        self.graph = NeonGraphWidget(self.neon_start)
        self.graph.setMinimumHeight(40)
        card_layout.addWidget(self.graph, stretch=1)
        
        main_layout.addWidget(self.active_card, stretch=1)
        self._set_empty_state()
        
        lower_layout = QHBoxLayout()
        lower_layout.setSpacing(20)
        
        # --- LEFT COLUMN: DL QUEUE (2/3) ---
        queue_layout = QVBoxLayout()
        queue_header = QHBoxLayout()
        queue_header.setSpacing(8)
        self.lbl_queue = QLabel("📦 Download Queue")
        self.lbl_queue.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.theme['text_primary_white']}; margin-top: 5px;")
        queue_header.addWidget(self.lbl_queue)
        queue_header.addStretch(1)
        self.btn_open_temp = QPushButton("Open Temporary Folder")
        self.btn_open_temp.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_open_temp.setToolTip("Open the folder where downloads are cached")
        self.btn_open_temp.clicked.connect(self.open_temp_folder)
        self.btn_clean_temp = QPushButton("Remove all temp. files")
        self.btn_clean_temp.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_clean_temp.setToolTip(
            "Delete cached files that are not used by the queue"
        )
        self.btn_clean_temp.clicked.connect(self.clean_temp_files)
        queue_header.addWidget(self.btn_open_temp)
        queue_header.addWidget(self.btn_clean_temp)
        self._style_temp_buttons()
        queue_layout.addLayout(queue_header)
        
        self.list_widget = QueueListWidget()
        self.list_widget.order_changed.connect(self._on_queue_order_changed)
        self.list_widget.setStyleSheet("QListWidget { background-color: transparent; border: none; outline: none; } QListWidget::item { margin-bottom: 2px; }")
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setSpacing(6)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_queue_context_menu)
        queue_layout.addWidget(self.list_widget)
        lower_layout.addLayout(queue_layout, stretch=2)
        
        # --- RIGHT COLUMN: HISTORY (1/3) ---
        history_layout = QVBoxLayout()
        self.lbl_history = QLabel("🕰️ History")
        self.lbl_history.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.theme['text_primary_white']}; margin-top: 5px;")
        history_layout.addWidget(self.lbl_history)
        
        self.history_widget = QListWidget()
        self.history_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.history_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history_widget.customContextMenuRequested.connect(self._show_history_context_menu)
        history_layout.addWidget(self.history_widget)
        lower_layout.addLayout(history_layout, stretch=1)
        
        main_layout.addLayout(lower_layout, stretch=1)

        # --- ETHICAL PIRACY DISCLAIMER PANEL (Bottom Anchor) ---
        self.lbl_disclaimer = QLabel(
            "If you like the game and the software house / publisher aren't aggressive monetizers, "
            "if they are pro-users, if they support the customer, "
            f"<b style='color:{self.theme.get('bright_neon_accent', '#ff00a0')}; text-transform: uppercase;'>BUY THE GAME. THEY DESERVE IT.</b>"
        )
        self.lbl_disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_disclaimer.setWordWrap(True)
        self.lbl_disclaimer.setStyleSheet(f"font-size: 11px; color: {self.text_muted}; padding: 4px 10px; background-color: rgba(0,0,0,0.1); border-radius: 4px; margin-top: 5px;")
        main_layout.addWidget(self.lbl_disclaimer)

    def apply_theme(self, theme_config):
        self.theme = theme_config
        self.neon_start = self.theme.get('neon_gradient_start', '#8d0bb1')
        self.neon_end = self.theme.get('neon_gradient_end', '#00d2ff')
        self.text_muted = self.theme.get('text_muted', '#8a7fa4')
        self.status_green = self.theme.get('status_green', '#00ff88')
        self.status_yellow = self.theme.get('status_yellow', '#ffcc00')
        self.status_red = self.theme.get('status_red', '#ff00a0')
        
        if hasattr(self, 'stats_container'):
            self.stats_container.setStyleSheet(f"QFrame#StatsContainer {{ background-color: rgba(0,0,0,0.1); border: 1px solid rgba(255,255,255,0.05); border-radius: 6px; }}")
        if hasattr(self, 'thin_bar_fill'):
            self.thin_bar_fill.setStyleSheet(f"background-color: {self.neon_end}; border-radius: 2px; border: none;")
        if hasattr(self, 'history_widget'):
            border_color = self.theme.get('border_color', 'rgba(255,255,255,0.1)')
            muted_color = self.theme.get('text_muted', '#8a7fa4')
            text_color = self.theme.get('text_primary_white', '#ffffff')
            self.history_widget.setStyleSheet(f"QListWidget {{ background-color: transparent; border-left: 1px solid {border_color}; outline: none; padding-left: 10px; }} QListWidget::item {{ margin-bottom: 5px; border-bottom: 1px solid {muted_color}; padding-bottom: 5px; color: {text_color}; font-weight: bold; }}")
        
        if hasattr(self, 'lbl_queue'):
            self.lbl_queue.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.theme['text_primary_white']}; margin-top: 5px;")
        if hasattr(self, 'lbl_history'):
            self.lbl_history.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.theme['text_primary_white']}; margin-top: 5px;")

        if hasattr(self, 'active_card'):
            if self.current_task:
                self.active_card.setStyleSheet(f"background-color: {self.theme['surface_panel_purple']}; border-radius: 12px; border: 1px solid {self.neon_end};")
                glow = QGraphicsDropShadowEffect(self)
                glow.setBlurRadius(15)
                glow.setColor(QColor(self.neon_end))
                glow.setOffset(0, 0)
                self.lbl_title.setGraphicsEffect(glow)
            else:
                self._set_empty_state()
            
        if hasattr(self, 'btn_pause'):
            media_dir = os.path.join(get_base_dir(), ".media")
            self.icon_pause = self._colorize_icon(os.path.join(media_dir, "pause_round.svg"), self.theme)
            self.icon_play = self._colorize_icon(os.path.join(media_dir, "play_round.svg"), self.theme)
            self.icon_stop = self._colorize_icon(os.path.join(media_dir, "stop_round.svg"), self.theme)
            
            if self.is_paused:
                self.btn_pause.setIcon(self.icon_play)
            else:
                self.btn_pause.setIcon(self.icon_pause)
            self.btn_stop.setIcon(self.icon_stop)
            
            for btn in [self.btn_pause, self.btn_stop]:
                btn.setStyleSheet(f"""
                    QPushButton {{ background-color: transparent; border: 1px solid {self.neon_end}; border-radius: 16px; }}
                    QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.15); }}
                """)

        if hasattr(self, 'banner'):
            self._style_banner()
        if hasattr(self, 'btn_open_temp'):
            self._style_temp_buttons()
        
        if self.current_task:
            self._update_cover_html(self.current_task.get('cover_url', ''))

        # Update Disclaimer with live theme colors
        if hasattr(self, 'lbl_disclaimer'):
            self.lbl_disclaimer.setText(
                "If you like the game and the software house / publisher aren't aggressive monetizers, "
                "if they are pro-users, if they support the customer, "
                f"<b style='color:{self.theme.get('bright_neon_accent', '#ff00a0')}; text-transform: uppercase;'>BUY THE GAME. THEY DESERVE IT.</b>"
            )
            self.lbl_disclaimer.setStyleSheet(f"font-size: 11px; color: {self.text_muted}; padding: 4px 10px; background-color: rgba(0,0,0,0.1); border-radius: 4px; margin-top: 5px;")

        self._rebuild_list_ui()

    def _update_math_ui(self):
        if not self.current_task:
            return

        now = time.time()
        time_delta = now - getattr(self, 'last_ui_time', now - 0.1)
        if time_delta <= 0: time_delta = 0.1 
        self.last_ui_time = now

        if getattr(self, 'is_extracting', False):
            # Abbandoniamo il delta dei 500ms. Usiamo la media globale dall'inizio dell'estrazione 
            # per avere un dato stabile, bypassando i "salti" percentuali giganti di 7-Zip.
            elapsed_session = now - self.download_start_time
            if elapsed_session > 0.5 and self.current_dl_mb > 0:
                avg_speed = self.current_dl_mb / elapsed_session
            else:
                avg_speed = 0.0
            
            # Disegniamo la velocità media sul grafico (sarà una linea fluida e coerente)
            self.graph.add_value(avg_speed)
            
            if avg_speed > 0.1 and self.current_tot_mb > 0:
                mb_rimanenti = self.current_tot_mb - self.current_dl_mb
                secondi = mb_rimanenti / avg_speed
                m, s = divmod(int(secondi), 60)
                h, m = divmod(m, 60)
                eta_str = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"
            else:
                eta_str = "Calculating..."

            self.stat_labels["Speed (Avg)"].setText(f"{avg_speed:.1f} MB/s")
            self.stat_labels["ETA"].setText(eta_str)
            
            dl_gb = self.current_dl_mb / 1024
            tot_gb = self.current_tot_mb / 1024
            self.stat_labels["Total Size"].setText(f"Unpacked: {dl_gb:.1f} GB / {tot_gb:.1f} GB")
            return

        if not self.active_worker or getattr(self, 'is_paused', False):
            self.graph.add_value(0)
            return

        bytes_now = getattr(self.active_worker, 'current_downloaded', self.last_dl_bytes)
        bytes_delta = bytes_now - self.last_dl_bytes
        self.last_dl_bytes = bytes_now
        
        mb_s = (bytes_delta / (1024 * 1024)) / time_delta
        self.graph.add_value(mb_s) 
        
        if getattr(self, 'download_start_time', None) is not None:
            elapsed_session = now - self.download_start_time
            bytes_session = bytes_now - self.download_start_bytes
            if elapsed_session > 0.5:
                avg_speed = (bytes_session / (1024 * 1024)) / elapsed_session
            else:
                avg_speed = mb_s
        else:
            avg_speed = 0.0

        if avg_speed > 0.1 and self.current_tot_mb > 0:
            mb_rimanenti = self.current_tot_mb - self.current_dl_mb
            secondi = mb_rimanenti / avg_speed
            m, s = divmod(int(secondi), 60)
            h, m = divmod(m, 60)
            eta_str = f"{h}h {m}m {s}s" if h > 0 else f"{m}m {s}s"
        else:
            eta_str = "Calculating..."

        self.stat_labels["Speed (Avg)"].setText(f"{avg_speed:.1f} MB/s")
        self.stat_labels["ETA"].setText(eta_str)
        
        dl_gb = self.current_dl_mb / 1024
        tot_gb = self.current_tot_mb / 1024
        self.stat_labels["Total Size"].setText(f"{dl_gb:.1f} GB / {tot_gb:.1f} GB")
        
        if self.list_widget.count() > 0:
            active_item = self.list_widget.item(0)
            widget = self.list_widget.itemWidget(active_item)
            if widget:
                lbl_eta = widget.findChildren(QLabel)[-1]
                lbl_eta.setText(f"ETA: <span style='color: {self.neon_start}; font-weight: bold;'>{eta_str}</span>")

    def _update_cover_html(self, url):
        html = f"""
        <html><head><style>
            body {{ margin: 0; padding: 0; background: transparent; display: flex; justify-content: center; align-items: center; overflow: hidden; height: 100vh; }}
            .card-wrapper {{ width: 110px; height: 165px; position: relative; border-radius: 8px; background: linear-gradient(135deg, {self.neon_start} 0%, #ff00a0 50%, {self.neon_end} 100%); padding: 2px; }}
            .cover-glow {{ position: absolute; top: -3px; left: -3px; right: -3px; bottom: -3px; border-radius: 8px; background: linear-gradient(135deg, {self.neon_start} 0%, #ff00a0 40%, {self.neon_end} 100%); filter: blur(5px); opacity: 0.8; z-index: 0; }}
            .card-wrapper img {{ width: 100%; height: 100%; object-fit: cover; border-radius: 6px; display: block; background-color: {self.theme['background_dark_purple']}; position: relative; z-index: 1; }}
        </style></head><body>
            <div class="card-wrapper"><div class="cover-glow"></div><img src="{url}"></div>
        </body></html>
        """
        if url and url.startswith("file://"):
            from core.cover_cache import CoverCache
            page_path = CoverCache.cover_page_url(html, f"dl_{uuid.uuid4().hex}")
            self.cover_view.setUrl(QUrl.fromLocalFile(page_path))
        else:
            self.cover_view.setHtml(html)

    def _find_task_by_dest(self, dest_path):
        key = normalize_dest_path(dest_path)
        if not key:
            return None
        tasks = ([self.current_task] if self.current_task else []) + self.queue
        for task in tasks:
            if task and normalize_dest_path(task.get('dest_path')) == key:
                return task
        return None

    @staticmethod
    def _has_partial_file(task):
        dest = (task or {}).get('dest_path', '')
        try:
            return bool(dest and os.path.isfile(dest) and os.path.getsize(dest) > 0)
        except OSError:
            return False

    def _dedupe_loaded_tasks(self, tasks):
        """Collapse saved duplicates by dest_path, keeping one row per file."""
        deduped = dedupe_tasks(tasks, self._has_partial_file)
        if len(deduped) != len([task for task in tasks if isinstance(task, dict)]):
            print("[DLManager] Collapsed duplicate download entries")
        for task in deduped:
            if task.get('_restart_partial'):
                print(
                    f"[DLManager] Provider conflict for "
                    f"{task.get('title') or task.get('filename')}: keeping "
                    f"{provider_key(task)}, partial from "
                    f"{task.get('_provider_conflict')} will restart from scratch"
                )
        return deduped

    def _switch_task_provider(self, task, incoming):
        """Replace a task's download credentials, dropping an unsafe partial.

        Returns False if the active worker could not be stopped safely.
        """
        dest = task.get('dest_path') or incoming.get('dest_path')
        worker = self.active_worker
        if task is self.current_task and worker is not None:
            worker.cancel()
            try:
                worker.wait(3000)
            except Exception:
                pass
            if worker.isRunning():
                return False
            self._cleanup_worker()
            self.is_paused = True
            self.btn_pause.setIcon(self.icon_play)

        try:
            if dest and os.path.exists(dest):
                os.remove(dest)
                print(f"[DLManager] Removed incompatible partial: {dest}")
        except OSError as exc:
            print(f"[DLManager] Could not remove partial file: {exc}")
        if dest:
            clear_incomplete(dest)

        old_id = task.get('_id')
        task.update(incoming)
        if old_id:
            task['_id'] = old_id
        task['_restart_partial'] = False
        task.pop('_provider_conflict', None)
        return True

    def add_to_queue(self, task_dict, auto_start=True):
        task_dict.setdefault('title', task_dict['filename'])
        task_dict.setdefault('web_version', 'Unknown')
        task_dict.setdefault('space_needed', 'Calculated')
        task_dict.setdefault('created_at', time.time())
        task_dict.setdefault('phase', 'queued')

        if '_id' not in task_dict:
            task_dict['_id'] = str(uuid.uuid4())

        # A new download for the same archive supersedes a pending extraction.
        self.remove_pending_extraction(task_dict.get('dest_path', ''))
        self.clear_dismissed(task_dict.get('dest_path', ''))

        existing = self._find_task_by_dest(task_dict.get('dest_path'))
        if existing is not None:
            existing_provider = provider_key(existing)
            incoming_provider = provider_key(task_dict)
            conflict = bool(
                self._has_partial_file(existing)
                and existing_provider
                and incoming_provider
                and existing_provider != incoming_provider
            )
            if conflict:
                existing_label = (
                    existing.get('provider')
                    or existing.get('provider_domain')
                    or existing_provider
                )
                incoming_label = (
                    task_dict.get('provider')
                    or task_dict.get('provider_domain')
                    or incoming_provider
                )
                title = (
                    existing.get('title')
                    or task_dict.get('title')
                    or 'This game'
                )
                reply = QMessageBox.question(
                    self,
                    "Different Provider",
                    f"'{title}' is already in the queue from "
                    f"{existing_label}.\n\n"
                    f"The new link uses {incoming_label}.\n\n"
                    "A partial file from a different provider may not be "
                    "compatible. Switch to the new provider and restart the "
                    "download from scratch?\n\n"
                    "Yes = switch provider and restart\n"
                    "No = keep the existing partial",
                    QMessageBox.StandardButton.Yes
                    | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    merge_task_data(
                        existing,
                        task_dict,
                        keep_download_credentials=True,
                    )
                    self._rebuild_list_ui()
                    self.save_state()
                    return
                if not self._switch_task_provider(existing, task_dict):
                    QMessageBox.information(
                        self,
                        "Download Active",
                        "The current download could not be stopped safely. "
                        "Pause or stop it first, then try again.",
                    )
                    return
            else:
                merge_task_data(existing, task_dict)
                existing['created_at'] = max(
                    task_created_at(existing), task_created_at(task_dict)
                )

            if existing is not self.current_task:
                try:
                    self.queue.remove(existing)
                except ValueError:
                    pass
                self.queue.insert(0, existing)

            self._rebuild_list_ui()
            self.save_state()

            if existing is self.current_task:
                if self.is_paused and not self.active_worker and not self.is_extracting:
                    self.resume_current()
            elif auto_start and not self.active_worker and not self.is_paused:
                self._process_queue()
            return

        self.queue.append(task_dict)
        self._rebuild_list_ui()
        self.save_state()
        
        if auto_start and not self.active_worker and not self.is_paused:
            self._process_queue()

    def _rebuild_list_ui(self):
        if not hasattr(self, 'list_widget'):
            return
        self._ensure_row_ids()
        current_for_layout = None if self.current_extraction_id else self.current_task
        layout = build_layout(
            self.pending_extractions,
            self.queue,
            current_for_layout,
            is_extracting=self.is_extracting,
            current_extraction_id=self.current_extraction_id,
            is_paused=self.is_paused,
        )
        self._layout_model = layout

        self.list_widget.clear()
        top = layout.get('top')
        if top is not None:
            self._add_queue_row(top, is_active=True, draggable=False)

        for section in layout.get('sections', []):
            self._add_section_header(section.get('label', ''))
            for row in section.get('rows', []):
                self._add_queue_row(row, is_active=False, draggable=True)

        self.history_widget.clear()
        for task in self.completed_tasks:
            item = QListWidgetItem(task.get('title', 'Unknown'))
            self.history_widget.addItem(item)

        self._refresh_attention(layout)

    def _add_queue_row(self, row, is_active=False, draggable=True):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, (row.kind, row.row_id))
        widget = QueueItemWidget(
            row,
            self.theme,
            is_active=is_active,
            play_icon=self.icon_play,
            on_play=self._on_row_play,
        )
        item.setSizeHint(QSize(0, widget.height()))
        flags = item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if draggable:
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        else:
            flags &= ~Qt.ItemFlag.ItemIsDragEnabled
        item.setFlags(flags)
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def _add_section_header(self, label):
        if not label:
            return
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        widget = SectionHeaderWidget(label, self.theme)
        item.setSizeHint(QSize(0, widget.height()))
        self.list_widget.addItem(item)
        self.list_widget.setItemWidget(item, widget)

    def _row_by_item(self, item):
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or len(data) != 2:
            return None
        kind, row_id = data
        layout = self._layout_model
        rows = []
        if layout.get('top') is not None:
            rows.append(layout['top'])
        for section in layout.get('sections', []):
            rows.extend(section.get('rows', []))
        return next(
            (row for row in rows if row.kind == kind and row.row_id == row_id),
            None,
        )

    def _on_row_play(self, row):
        if row is None:
            return
        if row.kind == KIND_EXTRACT:
            self._start_extraction(row.item)
        else:
            self._start_download(row.item)

    def _ensure_row_ids(self):
        tasks = ([self.current_task] if self.current_task else []) + list(self.queue or [])
        for task in tasks:
            if isinstance(task, dict) and not task.get('_id'):
                task['_id'] = str(uuid.uuid4())
        for entry in self.pending_extractions or []:
            if isinstance(entry, dict) and not entry.get('_id'):
                entry['_id'] = str(uuid.uuid4())

    def _pause_into_queue(self):
        """Pause the active download and put it back at the top of Up Next."""
        worker = self.active_worker
        if worker is not None:
            worker.cancel()
            try:
                worker.wait(3000)
            except Exception:
                pass
            self._cleanup_worker()
        task = self.current_task
        if task is not None:
            task['phase'] = 'paused'
            if not task.get('downloaded_bytes'):
                task['downloaded_bytes'] = self._file_size(task.get('dest_path'))
            if task not in self.queue:
                self.queue.insert(0, task)
        self.current_task = None
        self.is_paused = True
        self.pause_state_changed.emit(True)
        self.save_state()

    @staticmethod
    def _file_size(path):
        try:
            return os.path.getsize(path) if path and os.path.isfile(path) else 0
        except OSError:
            return 0

    def _start_download(self, task):
        if not isinstance(task, dict):
            return
        if not str(task.get('url') or '').strip():
            page_url = str(task.get('page_url') or '').strip()
            if page_url and page_url != 'N/A':
                self._open_item_page(task)
                self.set_status(
                    "No saved link - opening the game page",
                    self.status_yellow,
                )
            else:
                self.set_status(
                    "No saved link - searching the game page",
                    self.status_yellow,
                )
                self.open_game_page_requested.emit(dict(task))
            self._rebuild_list_ui()
            return
        if self.is_extracting or self.current_extraction_id:
            self._move_to_queue_front(task)
            self.set_status("Queued - starts after extraction", self.status_yellow)
            self._rebuild_list_ui()
            self.save_state()
            return
        if task is self.current_task:
            if self.is_paused:
                self.resume_current()
            return
        if self.current_task is not None:
            self._pause_into_queue()
        try:
            self.queue.remove(task)
        except ValueError:
            pass
        self.current_task = task
        self.is_paused = False
        self._process_queue()

    def _move_to_queue_front(self, task):
        if task is self.current_task:
            return
        try:
            self.queue.remove(task)
        except ValueError:
            pass
        self.queue.insert(0, task)

    def _detach_paused_current(self):
        """Move a paused current download back to Up Next without deleting it."""
        task = self.current_task
        if task is None:
            return
        task['phase'] = 'paused'
        if not task.get('downloaded_bytes'):
            task['downloaded_bytes'] = self._file_size(task.get('dest_path'))
        try:
            self.queue.remove(task)
        except ValueError:
            pass
        self.queue.insert(0, task)
        self.current_task = None
        self.is_paused = False
        self.save_state()

    def _start_extraction(self, entry):
        if not isinstance(entry, dict):
            return
        if self.is_extracting or self.current_extraction_id:
            self.set_status("Extraction already running", self.status_yellow)
            return
        archive = str(entry.get('archive_path') or '')
        if not archive or not os.path.exists(archive):
            self.set_status("Archive not found", self.status_red)
            self.dismiss_pending_extraction(entry)
            return
        if self.current_task is not None:
            if not self.is_paused:
                self._pause_into_queue()
            else:
                self._detach_paused_current()
        task_data = {
            'title': entry.get('title', ''),
            'web_version': entry.get('web_version', ''),
            'game_info': entry.get('game_info', ''),
            'description': entry.get('description', ''),
            'page_url': entry.get('page_url', ''),
            'cover_url': entry.get('cover_url', ''),
            'provider': entry.get('provider', ''),
            'provider_domain': entry.get('provider_domain', ''),
        }
        if self.prepare_extraction(archive, task_data, make_active=True) is None:
            return
        self.pending_extraction_ready.emit(archive, task_data)
        self._rebuild_list_ui()
        self.save_state()

    def dismiss_pending_extraction(self, entry):
        archive = str((entry or {}).get('archive_path') or '')
        if archive:
            key = normalize_dest_path(archive)
            if key and key not in self.dismissed_archives:
                self.dismissed_archives.append(key)
        self.remove_pending_extraction(archive)
        self._rebuild_list_ui()

    def clear_dismissed(self, archive_path):
        key = normalize_dest_path(archive_path)
        if key and key in self.dismissed_archives:
            self.dismissed_archives = [
                item for item in self.dismissed_archives if item != key
            ]

    def _on_queue_order_changed(self):
        if self.list_widget.count() == 0:
            return

        visible = []
        for index in range(self.list_widget.count()):
            data = self.list_widget.item(index).data(Qt.ItemDataRole.UserRole)
            if isinstance(data, tuple) and len(data) == 2:
                visible.append(data)

        if not visible:
            return

        top = self._layout_model.get('top')
        if top is not None and visible[0] != (top.kind, top.row_id):
            # The active/next slot is pinned to the top: undo the drop.
            self._rebuild_list_ui()
            return

        download_ids = [row_id for kind, row_id in visible if kind == KIND_DOWNLOAD]
        extract_ids = [row_id for kind, row_id in visible if kind == KIND_EXTRACT]

        by_id = {task.get('_id'): task for task in self.queue if isinstance(task, dict)}
        if self.current_task is not None:
            by_id.pop(self.current_task.get('_id'), None)
        ordered = [by_id.pop(rid) for rid in download_ids if rid in by_id]
        ordered.extend(task for task in self.queue if task.get('_id') in by_id)
        self.queue = ordered

        extraction_by_id = {
            entry.get('_id'): entry for entry in self.pending_extractions
        }
        ordered_entries = [
            extraction_by_id.pop(rid)
            for rid in extract_ids
            if rid in extraction_by_id
        ]
        ordered_entries.extend(
            entry for entry in self.pending_extractions
            if entry.get('_id') in extraction_by_id
        )
        self.pending_extractions = ordered_entries

        self._rebuild_list_ui()
        self.save_state()

    def _process_queue(self, auto_start=True):
        if not self.queue and not self.current_task:
            self._set_empty_state()
            self._rebuild_list_ui()
            self.queue_empty.emit()
            return

        if not self.current_task:
            if not auto_start:
                self._set_empty_state()
                self._rebuild_list_ui()
                self.queue_empty.emit()
                return
            self.current_task = self.queue.pop(0)
            self._rebuild_list_ui()
        
        self.is_paused = False
        self.download_start_time = None
        self.download_start_bytes = 0
        self._set_active_state()
        self.active_card.show()
        self.graph.show()
        self.graph.update()
        
        self.lbl_title.setText(self.current_task['title'])
        installed = self.current_task.get('installed_version', 'None')
        web = self.current_task['web_version']
        self.lbl_versions.setText(f"Installed: {installed}  |  <span style='color: {self.neon_end};'>Web: {web}</span>")
        
        self._update_cover_html(self.current_task.get('cover_url', ''))
        self.stat_labels["Game"].setText(self.current_task['title'])
        self.stat_labels["Web Version"].setText(web)
        self.set_status("Downloading...", self.status_green)
        
        self.last_dl_bytes = 0
        self.speed_history = []
        
        # Anima il colore di ritorno al blu (neon_end) se venivamo da un'estrazione, 
        # o imposta semplicemente il colore di base.
        self._animate_color_transition(self.neon_start, self.neon_end, "BANDWIDTH")
        
        self._update_progress_bar(0)
        
        self.btn_pause.setIcon(self.icon_pause)
        self.download_started.emit(self.current_task['filename'])
        self._start_worker(self.current_task)
        self.save_state()

    def set_status(self, text, hex_color):
        self.stat_labels["Status"].setText(f"<span style='color:{hex_color}; font-size:14px;'>●</span> {text}")

    def _update_progress_bar(self, pct):
        self.lbl_pct.setText(f"{pct}%")
        max_w = self.thin_bar_bg.width()
        new_w = int((pct / 100) * max_w)
        self.thin_bar_fill.setFixedWidth(new_w)

    def _restart_partial_download(self, task):
        """Drop a partial file that came from a different provider."""
        dest = task.get('dest_path', '')
        try:
            if dest and os.path.exists(dest):
                os.remove(dest)
                print(f"[DLManager] Removed incompatible partial: {dest}")
        except OSError as exc:
            print(f"[DLManager] Could not remove partial file: {exc}")
        if dest:
            clear_incomplete(dest)
        previous = task.pop('_provider_conflict', '')
        task['_restart_partial'] = False
        if previous:
            current = task.get('provider') or task.get('provider_domain') or 'new provider'
            self.set_status(
                f"Restarting from scratch ({previous} → {current})",
                self.status_yellow,
            )
        self.save_state()

    def _start_worker(self, task):
        if task.get('_restart_partial'):
            self._restart_partial_download(task)
        self.active_worker = SmartDownloaderWorker(
            task['url'], task['dest_path'],
            cookies=task.get('cookies', {}),
            user_agent=task.get('user_agent', ''),
            referer=task.get('referer', '')
        )
        self.active_worker.progress_update.connect(self._on_progress)
        self.active_worker.finished_success.connect(self._on_success)
        self.active_worker.finished_error.connect(self._on_error)
        self.active_worker.cf_bypass_needed.connect(self.cf_bypass_requested.emit)
        self.active_worker.gate_fallback_needed.connect(self.gate_fallback_requested.emit)
        self.active_worker.resume_detected.connect(self.resume_detected.emit)
        self.active_worker.start()

    def _cleanup_worker(self):
        if self.active_worker:
            self.thread_cemetery.append(self.active_worker)
            self.thread_cemetery = [t for t in self.thread_cemetery if t.isRunning()]
            self.active_worker = None

    def toggle_pause(self):
        if getattr(self, 'is_extracting', False):
            # non supportiamo la pausa dell'estrazione vera e propria, ma potremmo implementare un segnale se necessario.
            # per ora lasciamo stare.
            return
        if not self.current_task: return
        if self.is_paused:
            self.resume_current()
        else:
            self.pause_current()

    def pause_current(self):
        if getattr(self, 'is_extracting', False): return
        if self.active_worker and not self.is_paused:
            self.active_worker.cancel()
            self.is_paused = True
            if self.current_task is not None:
                self.current_task['phase'] = 'paused'
                if not self.current_task.get('downloaded_bytes'):
                    self.current_task['downloaded_bytes'] = self._file_size(
                        self.current_task.get('dest_path')
                    )
            self.btn_pause.setIcon(self.icon_play)
            self.set_status("Paused", self.status_yellow)
            self.stat_labels["Speed (Avg)"].setText("0.0 MB/s")
            self.stat_labels["ETA"].setText("Paused")
            self.pause_state_changed.emit(True)
            self._rebuild_list_ui()
            self.save_state()

    def resume_current(self):
        if getattr(self, 'is_extracting', False): return
        if self.current_task and self.is_paused:
            self.is_paused = False
            self.current_task['phase'] = 'downloading'
            self.btn_pause.setIcon(self.icon_pause)
            self.set_status("Downloading...", self.status_green)
            
            # Reset speed calculation variables for the resumed session
            self.download_start_time = None
            self.download_start_bytes = getattr(self.active_worker, 'current_downloaded', self.last_dl_bytes) if hasattr(self, 'active_worker') else 0
            self.speed_history = []
            if hasattr(self, 'graph'):
                self.graph.clear()
            
            self._start_worker(self.current_task)
            self._rebuild_list_ui()
            self.save_state()
            self.pause_state_changed.emit(False)

    def stop_current(self):
        if getattr(self, 'is_extracting', False):
            self.extraction_cancel_requested.emit()
            return
            
        if self.active_worker:
            self.active_worker.cancel()
        if self.current_task and os.path.exists(self.current_task['dest_path']):
            try: os.remove(self.current_task['dest_path'])
            except: pass
        if self.current_task:
            dest = self.current_task.get('dest_path')
            clear_incomplete(dest)
            self.clear_dismissed(dest)
        self._cleanup_worker()
        self.current_task = None
        self.is_paused = False
        self._process_queue(auto_start=False)
    
    def _show_history_context_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: {self.theme.get('background_dark_purple', '#1e1b2e')}; color: white; border: 1px solid {self.theme.get('neon_gradient_start', '#8d0bb1')}; }} QMenu::item:selected {{ background-color: {self.theme.get('surface_panel_purple', '#2d2546')}; }}")
        clear_action = menu.addAction("🗑️ Clear History")
        
        action = menu.exec(self.history_widget.mapToGlobal(pos))
        if action == clear_action:
            self.completed_tasks.clear()
            self.history_widget.clear()
            self.save_state()

    def _show_queue_context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        row = self._row_by_item(item)
        if row is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: {self.theme['background_dark_purple']}; color: white; border: 1px solid {self.neon_start}; }} QMenu::item:selected {{ background-color: {self.theme['surface_panel_purple']}; }}")

        actions = {}
        if row.kind == KIND_EXTRACT:
            actions['extract'] = menu.addAction("▶️ Extract now")
            actions['open'] = menu.addAction("📂 Open archive folder")
            actions['remove'] = menu.addAction("❌ Remove from list")
            actions['delete'] = menu.addAction("🗑️ Delete archive")
            actions['extract'].setVisible(not self.is_extracting)
        else:
            task = row.item
            is_active = task is self.current_task
            actions['start'] = menu.addAction("▶️ Start / Resume")
            actions['pause'] = menu.addAction("⏸️ Pause")
            actions['copy'] = menu.addAction("🔗 Copy Download Link")
            actions['open'] = menu.addAction("🌐 Open game page")
            actions['remove'] = menu.addAction("❌ Remove from queue")
            actions['delete'] = menu.addAction("🗑️ Delete partial file")
            actions['start'].setVisible(not is_active or self.is_paused)
            actions['pause'].setVisible(
                is_active and not self.is_paused and not self.is_extracting
            )
            actions['remove'].setVisible(not is_active)
            actions['delete'].setVisible(self._has_partial_file(task))
            page_url = str(task.get('page_url') or '').strip()
            actions['open'].setVisible(bool(page_url) and page_url != 'N/A')
            actions['copy'].setVisible(bool(task.get('url')))

        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action is None:
            return

        if action == actions.get('extract'):
            self._start_extraction(row.item)
        elif action == actions.get('start'):
            self._start_download(row.item)
        elif action == actions.get('pause'):
            self.pause_current()
        elif action == actions.get('copy'):
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(str(row.item.get('url', '')))
            self.set_status("Link copied to clipboard", self.status_green)
        elif action == actions.get('open'):
            if row.kind == KIND_EXTRACT:
                self._open_archive_folder(row.item)
            else:
                self._open_item_page(row.item)
        elif action == actions.get('remove'):
            if row.kind == KIND_EXTRACT:
                self.dismiss_pending_extraction(row.item)
            else:
                self._remove_download(row.item)
        elif action == actions.get('delete'):
            if row.kind == KIND_EXTRACT:
                self._delete_archive(row.item)
            else:
                self._delete_partial(row.item)

    def _open_item_page(self, item):
        url = str((item or {}).get('page_url') or '').strip()
        if not url or url == 'N/A':
            return
        QDesktopServices.openUrl(QUrl(url))

    def _open_archive_folder(self, entry):
        archive = str((entry or {}).get('archive_path') or '')
        folder = os.path.dirname(archive) if archive else ''
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _remove_download(self, task):
        if task is self.current_task:
            return
        try:
            self.queue.remove(task)
        except ValueError:
            pass
        self._rebuild_list_ui()
        self.save_state()

    def _delete_partial(self, task):
        dest = str((task or {}).get('dest_path') or '')
        if not dest:
            return
        reply = QMessageBox.question(
            self,
            "Delete partial file",
            f"Delete the partial file?\n\n{dest}\n\n"
            "The download progress will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError as exc:
            print(f"[DLManager] Could not delete partial file: {exc}")
        clear_incomplete(dest)
        self.clear_dismissed(dest)
        clear_extraction_state(dest)
        self._remove_download(task)

    def _delete_archive(self, entry):
        archive = str((entry or {}).get('archive_path') or '')
        reply = QMessageBox.question(
            self,
            "Delete archive",
            f"Delete this archive from disk?\n\n{archive}\n\n"
            "You will need to download it again to extract it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if archive and os.path.exists(archive):
                os.remove(archive)
        except OSError as exc:
            print(f"[DLManager] Could not delete archive: {exc}")
        clear_incomplete(archive)
        clear_extraction_state(archive)
        self.remove_pending_extraction(archive)
        self._rebuild_list_ui()
        self.save_state()

    def _on_progress(self, downloaded, total):
        if not self.active_worker:
            return
        if hasattr(self.active_worker, 'current_downloaded'):
            self.active_worker.current_downloaded = downloaded
        else:
            setattr(self.active_worker, 'current_downloaded', downloaded)
            
        if getattr(self, 'download_start_time', None) is None:
            self.download_start_time = time.time()
            self.download_start_bytes = downloaded

        if total > 0:
            pct = int((downloaded / total) * 100)
            self.current_pct = pct
            self.current_dl_mb = downloaded / (1024 * 1024)
            self.current_tot_mb = total / (1024 * 1024)
            self._update_progress_bar(pct)
            self.progress_updated.emit(pct, self.current_dl_mb, self.current_tot_mb)

        # Keep the resume metadata fresh without rewriting the state on every
        # 0.2 s chunk.
        task = self.current_task
        if task is not None:
            try:
                task['downloaded_bytes'] = int(downloaded)
                if total:
                    task['total_bytes'] = int(total)
                task['phase'] = 'downloading'
            except (TypeError, ValueError):
                pass
            now = time.time()
            if now - self._last_state_save > 5:
                self._last_state_save = now
                self.save_state()
            

    def _animate_color_transition(self, start_hex, end_hex, mode_text):
        if hasattr(self, '_color_anim'):
            self._color_anim.stop()
            
        self._color_anim = QVariantAnimation(self)
        self._color_anim.setDuration(800)
        self._color_anim.setStartValue(QColor(start_hex))
        self._color_anim.setEndValue(QColor(end_hex))
        
        def on_step(color):
            hex_color = color.name()
            self.lbl_pct.setStyleSheet(f"color: {hex_color}; font-size: 18px; font-weight: bold; border: none; background: transparent;")
            self.thin_bar_fill.setStyleSheet(f"background-color: {hex_color}; border-radius: 2px; border: none;")
            self.active_card.setStyleSheet(f"background-color: {self.theme['surface_panel_purple']}; border-radius: 12px; border: 1px solid {hex_color};")
            
            glow = self.lbl_title.graphicsEffect()
            if glow and isinstance(glow, QGraphicsDropShadowEffect):
                glow.setColor(color)
                
            for btn in [self.btn_pause, self.btn_stop]:
                btn.setStyleSheet(f"QPushButton {{ background-color: transparent; border: 1px solid {hex_color}; border-radius: 16px; }} QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.15); }}")
                
            self.graph.set_mode(hex_color, mode_text)
            
            if self.is_extracting:
                for key, lbl in self.stat_labels.items():
                    if key not in ["Game", "Web Version"]:
                        lbl.setStyleSheet(f"color: {hex_color}; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            else:
                for key, lbl in self.stat_labels.items():
                    if key not in ["Game", "Web Version", "Status"]:
                        lbl.setStyleSheet(f"color: white; font-size: 11px; font-weight: bold; border: none; background: transparent;")
                        
        self._color_anim.valueChanged.connect(on_step)
        self._color_anim.start()

    def set_extraction_mode(self, unpacked_mb):
        if not self.current_task: return
        self.is_extracting = True
        self._extraction_started = True
        self.current_pct = 0
        self.current_dl_mb = 0
        self.current_tot_mb = unpacked_mb
        self.last_dl_bytes = 0
        self.download_start_time = time.time()
        self.download_start_bytes = 0
        self.last_ui_time = time.time()
        
        # Tema estrazione: rosa
        pink = self.neon_start
        self.graph.clear()
        self.set_status("Extracting...", pink)
        self.stat_labels["Total Size"].setText(f"0.0 GB / {unpacked_mb/1024:.1f} GB")
        
        self._animate_color_transition(self.neon_end, pink, "WRITING")
        
        self._rebuild_list_ui()

    def update_extraction_progress(self, pct, mb_extracted):
        if not self.current_task: return
        self.current_pct = pct
        self.current_dl_mb = mb_extracted
        self._update_progress_bar(pct)

    def finish_extraction(self):
        extraction_id = self.current_extraction_id
        started = self._extraction_started
        archive = str((self.current_task or {}).get('dest_path') or '')
        self.is_extracting = False
        self.current_extraction_id = None
        self._extraction_started = False
        if self.current_task and (started or self.current_task.get('is_update')):
            completed = self.current_task.copy()
            completed['eta_text'] = 'COMPLETED'
            self.completed_tasks.append(completed)

        if extraction_id:
            entry = next(
                (e for e in self.pending_extractions if e.get('_id') == extraction_id),
                None,
            )
            if entry is not None:
                if archive and not os.path.exists(archive):
                    # 7-Zip deletes the archive on success.
                    self.remove_pending_extraction(archive)
                elif started:
                    entry['state'] = 'interrupted'
                    entry['pct'] = int(self.current_pct or 0)
                    entry['updated_at'] = time.time()
                else:
                    entry['state'] = 'ready'

        self._set_empty_state()
        self.current_task = None
        self.is_paused = False
        self._rebuild_list_ui()
        self.save_state()
        self._process_queue(auto_start=False)

    def add_pending_extraction(self, archive_path, task_data):
        """Salva un download completato che attende ancora estrazione."""
        if not archive_path or not os.path.exists(archive_path):
            return None
        entry = {
            "_id": str(uuid.uuid4()),
            "archive_path": archive_path,
            "title": task_data.get('title', ''),
            "web_version": task_data.get('web_version', ''),
            "game_info": task_data.get('game_info', ''),
            "description": task_data.get('description', ''),
            "page_url": task_data.get('page_url', ''),
            "cover_url": task_data.get('cover_url', ''),
            "provider": task_data.get('provider', ''),
            "provider_domain": task_data.get('provider_domain', ''),
            "install_dir": task_data.get('install_dir', ''),
            "state": "ready",
            "pct": 0,
            "timestamp": time.time(),
            "updated_at": time.time(),
        }
        self.pending_extractions = [
            existing for existing in self.pending_extractions
            if existing.get("archive_path") != archive_path
        ]
        self.pending_extractions.append(entry)
        self.save_state()
        print(f"[DLManager] Pending extraction salvata: {entry['title']} ({archive_path})")
        return entry

    def prepare_extraction(self, archive_path, task_data, make_active=False):
        """Register the extraction that is about to start."""
        task_data = task_data or {}
        entry = next(
            (e for e in self.pending_extractions
             if e.get('archive_path') == archive_path),
            None,
        )
        if entry is None:
            entry = self.add_pending_extraction(archive_path, task_data)
        if entry is None:
            return None
        entry['state'] = 'ready'
        self.current_extraction_id = entry.get('_id')
        self._extraction_started = False
        if make_active and self.current_task is None:
            synthetic = dict(task_data)
            synthetic.setdefault('title', entry.get('title') or os.path.basename(archive_path))
            synthetic.setdefault('filename', os.path.basename(archive_path))
            synthetic.setdefault('web_version', entry.get('web_version', 'Unknown'))
            synthetic['dest_path'] = archive_path
            synthetic['_id'] = f"extract-{entry.get('_id')}"
            self.current_task = synthetic
            self.is_paused = False
            self._set_active_state()
            self.lbl_title.setText(synthetic['title'])
            self._update_cover_html(synthetic.get('cover_url', ''))
            self.stat_labels["Game"].setText(synthetic['title'])
            self.stat_labels["Web Version"].setText(synthetic.get('web_version', 'Unknown'))
            self.set_status("Preparing extraction...", self.neon_start)
        self._rebuild_list_ui()
        self.save_state()
        return entry

    def set_extraction_install_dir(self, archive_path, install_dir):
        if not archive_path or not install_dir:
            return
        for entry in self.pending_extractions:
            if entry.get('archive_path') == archive_path:
                entry['install_dir'] = install_dir
                entry['updated_at'] = time.time()
                self.save_state()
                return

    def set_pending_extractions(self, entries):
        self.pending_extractions = [e for e in (entries or []) if isinstance(e, dict)]
        self._ensure_row_ids()
        self._rebuild_list_ui()
        self.save_state()

    def recover_pending_extractions(self):
        """Rimuove gli archivi non più presenti su disco e restituisce quelli validi."""
        valid = []
        stale = []
        for entry in self.pending_extractions:
            if os.path.exists(entry.get("archive_path", "")):
                valid.append(entry)
            else:
                stale.append(entry)
        if stale:
            print(f"[DLManager] Rimossi {len(stale)} pending extraction non più su disco")
        self.pending_extractions = valid
        if valid:
            self.save_state()
        return valid

    def remove_pending_extraction(self, archive_path):
        """Rimuove un archivio dalla lista pending dopo estrazione riuscita."""
        before = len(self.pending_extractions)
        self.pending_extractions = [
            e for e in self.pending_extractions
            if e.get("archive_path") != archive_path
        ]
        if len(self.pending_extractions) != before:
            self.save_state()

    def _on_success(self, final_path):
        self._cleanup_worker()
        self.set_status("Pending Install...", self.status_yellow)
        self.graph.clear()
        self.download_completed.emit(final_path)
        self.save_state()

    def _on_error(self, err_msg):
        self._cleanup_worker()
        self.set_status("Error", self.status_red)

        partial_kept = bool(self.current_task and self._has_partial_file(self.current_task))
        if partial_kept:
            # Keep the paused task so the partial file stays in the queue and
            # can be resumed instead of being rediscovered as an orphan.
            self.is_paused = True
            self.current_task['phase'] = 'paused'
            self.current_task['downloaded_bytes'] = self._file_size(
                self.current_task.get('dest_path')
            )
            self.btn_pause.setIcon(self.icon_play)
            self.stat_labels["Speed (Avg)"].setText("0.0 MB/s")
            self.stat_labels["ETA"].setText("Paused")
            self.set_status(
                "Paused after error - press play to resume",
                self.status_yellow,
            )
            self.pause_state_changed.emit(True)
            self.download_error.emit(err_msg)
            self._rebuild_list_ui()
            self.save_state()
            return

        self.current_task = None
        self.download_error.emit(err_msg)
        self.save_state()
        self._process_queue(auto_start=False)

    def mark_current_partial(self, final_path):
        """Keep a truncated archive visible as a resumable partial download."""
        task = self.current_task
        if task is None:
            return
        task['dest_path'] = final_path
        task['phase'] = 'partial'
        task['downloaded_bytes'] = self._file_size(final_path)
        try:
            self.queue.remove(task)
        except ValueError:
            pass
        self.queue.insert(0, task)
        self.current_task = None
        self.is_paused = True
        self._set_empty_state()
        self._rebuild_list_ui()
        self.save_state()

    def set_state_file(self, path):
        self.state_file = path
        self._migrate_state_file(path)
        self._hide_state_file()
        self.load_state()
        self._hide_state_file()

    def _migrate_state_file(self, path):
        """Move the old visible state file to the hidden dotfile name."""
        try:
            if os.path.basename(path) == "dl_state.json":
                return
            folder = os.path.dirname(path)
            legacy = os.path.join(folder, "dl_state.json")
            if not os.path.exists(path) and os.path.exists(legacy):
                os.replace(legacy, path)
                print(f"[DLManager] Migrated {legacy} to {path}")
        except OSError as exc:
            print(f"[DLManager] Could not migrate the state file: {exc}")

    def _set_state_attributes(self, attributes):
        if os.name != "nt" or not getattr(self, "state_file", ""):
            return False
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(
                str(self.state_file), attributes
            )
            return True
        except Exception:
            return False

    def _hide_state_file(self):
        """Hide the queue state so users do not edit it by accident."""
        if self._state_hidden or not getattr(self, "state_file", ""):
            return
        if os.name != "nt" or not os.path.exists(self.state_file):
            return
        if self._set_state_attributes(0x2):  # FILE_ATTRIBUTE_HIDDEN
            self._state_hidden = True

    def _unhide_state_file(self):
        """Windows refuses to write hidden files, so clear the attribute first."""
        if os.name != "nt" or not getattr(self, "state_file", ""):
            return
        if not os.path.exists(self.state_file):
            return
        if self._set_state_attributes(0x80):  # FILE_ATTRIBUTE_NORMAL
            self._state_hidden = False

    def save_state(self):
        if not hasattr(self, 'state_file') or not self.state_file: return
        import json
        state = {
            'version': 2,
            'queue': self.queue,
            'current_task': self.current_task,
            'pending_extractions': self.pending_extractions,
            'dismissed_archives': self.dismissed_archives,
        }
        self._unhide_state_file()
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[DLManager] Error saving state: {e}")
        finally:
            self._hide_state_file()

    def load_state(self):
        if not hasattr(self, 'state_file') or not self.state_file: return
        import os, json
        if not os.path.exists(self.state_file): return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            
            raw_queue = state.get('queue', []) or []
            curr = state.get('current_task')
            all_tasks = ([curr] if isinstance(curr, dict) else []) + list(raw_queue)
            all_tasks = self._dedupe_loaded_tasks(all_tasks)

            active_keys = {
                normalize_dest_path(task.get('dest_path'))
                for task in all_tasks if isinstance(task, dict)
            }
            pending = state.get('pending_extractions', []) or []
            filtered_pending = [
                entry for entry in pending
                if normalize_dest_path(entry.get('archive_path')) not in active_keys
            ]
            if len(filtered_pending) != len(pending):
                print(
                    "[DLManager] Removed pending extractions already present "
                    "in the download queue"
                )

            self.completed_tasks = []
            self.dismissed_archives = [
                str(item) for item in (state.get('dismissed_archives') or [])
            ]
            self.pending_extractions = filtered_pending
            self.queue = all_tasks
            self.current_task = None
            self.is_paused = True
            self.is_extracting = False
            self.current_extraction_id = None
            self._extraction_started = False
            self._ensure_row_ids()

            # Nothing starts automatically: the queue model picks the next row.
            self._set_empty_state()
            self._rebuild_list_ui()
            self.save_state()
                
        except Exception as e:
            print(f"[DLManager] Error loading state: {e}")
