import os
import time
import uuid
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QSize, QUrl, QVariantAnimation
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame, QHBoxLayout, QListWidgetItem, QGraphicsDropShadowEffect, QPushButton, QMenu, QListWidget, QMessageBox
from PyQt6.QtGui import QColor, QPainter, QLinearGradient, QIcon, QPixmap, QCursor
from core.downloader import SmartDownloaderWorker
from core.archive_utils import clear_incomplete
from core.config import get_base_dir
from core.download_utils import (
    dedupe_tasks,
    merge_task_data,
    normalize_dest_path,
    provider_key,
    task_created_at,
)
from ui.widgets.queue_items import QueueListWidget, NeonGraphWidget, QueueItemWidget

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
        self.thread_cemetery = []
        
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

    def _set_active_state(self):
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
        self.lbl_queue = QLabel("📦 Download Queue")
        self.lbl_queue.setStyleSheet(f"font-size: 15px; font-weight: bold; color: {self.theme['text_primary_white']}; margin-top: 5px;")
        queue_layout.addWidget(self.lbl_queue)
        
        self.list_widget = QueueListWidget()
        self.list_widget.order_changed.connect(self._on_queue_order_changed)
        self.list_widget.setStyleSheet("QListWidget { background-color: transparent; border: none; outline: none; } QListWidget::item { margin-bottom: 5px; }")
        self.list_widget.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list_widget.setSpacing(15)
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

    def add_to_queue(self, task_dict):
        task_dict.setdefault('title', task_dict['filename'])
        task_dict.setdefault('web_version', 'Unknown')
        task_dict.setdefault('space_needed', 'Calculated')
        task_dict.setdefault('created_at', time.time())

        if '_id' not in task_dict:
            task_dict['_id'] = str(uuid.uuid4())

        # A new download for the same archive supersedes a pending extraction.
        self.remove_pending_extraction(task_dict.get('dest_path', ''))

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
            elif not self.active_worker and not self.is_paused:
                self._process_queue()
            return

        self.queue.append(task_dict)
        self._rebuild_list_ui()
        self.save_state()
        
        if not self.active_worker and not self.is_paused:
            self._process_queue()

    def _rebuild_list_ui(self):
        self.list_widget.clear()
        if self.current_task:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, self.current_task.get('_id', ''))
            widget = QueueItemWidget(self.current_task, self.theme, True, self.is_extracting)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            
        for task in self.queue:
            item = QListWidgetItem()
            # Fissiamo l'ID anche qui
            item.setData(Qt.ItemDataRole.UserRole, task.get('_id', ''))
            widget = QueueItemWidget(task, self.theme, False)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            
        self.history_widget.clear()
        for task in self.completed_tasks:
            item = QListWidgetItem(task.get('title', 'Unknown'))
            # Item formatting handled by history_widget stylesheet
            self.history_widget.addItem(item)

    def _on_queue_order_changed(self):
        if self.list_widget.count() == 0:
            return

        # 1. Ricostruiamo la lista completa (attivo + coda) seguendo il nuovo ordine UI
        new_full_list = []
        all_tasks = ([(self.current_task)] if self.current_task else []) + self.queue
        task_map = {t.get('_id'): t for t in all_tasks if t}

        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            task_id = item.data(Qt.ItemDataRole.UserRole)
            if task_id in task_map:
                new_full_list.append(task_map[task_id])

        if not new_full_list:
            return

        new_active = new_full_list[0]

        # 2. Se l'utente ha trascinato un elemento SOPRA il download attuale
        if self.current_task and new_active.get('_id') != self.current_task.get('_id'):
            if getattr(self, 'is_extracting', False):
                print("[DLManager] Estrazione in corso, non possiamo scambiare task.")
                self._rebuild_list_ui() # Annulla lo spostamento visivo
                return

            # Fermiamo il worker in corso
            if self.active_worker:
                self.active_worker.cancel()
                self._cleanup_worker()

            # Passiamo lo scettro al nuovo elemento in cima e riavviamo la catena
            self.current_task = None
            self.queue = new_full_list
            self._process_queue() 
        else:
            # Ordine cambiato, ma il task in download è rimasto dov'era
            if self.current_task:
                self.queue = new_full_list[1:]
            else:
                self.queue = new_full_list
            self._rebuild_list_ui()
            self.save_state()

    def _process_queue(self):
        if not self.queue and not self.current_task:
            self._set_empty_state()
            self._rebuild_list_ui()
            self.queue_empty.emit()
            return
            
        if not self.current_task:
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
            self.btn_pause.setIcon(self.icon_play)
            self.set_status("Paused", self.status_yellow)
            self.stat_labels["Speed (Avg)"].setText("0.0 MB/s")
            self.stat_labels["ETA"].setText("Paused")
            self.pause_state_changed.emit(True)

    def resume_current(self):
        if getattr(self, 'is_extracting', False): return
        if self.current_task and self.is_paused:
            self.is_paused = False
            self.btn_pause.setIcon(self.icon_pause)
            self.set_status("Downloading...", self.status_green)
            
            # Reset speed calculation variables for the resumed session
            self.download_start_time = None
            self.download_start_bytes = getattr(self.active_worker, 'current_downloaded', self.last_dl_bytes) if hasattr(self, 'active_worker') else 0
            self.speed_history = []
            if hasattr(self, 'graph'):
                self.graph.clear()
            
            self._start_worker(self.current_task)
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
        self._cleanup_worker()
        self.current_task = None
        self.is_paused = False
        self._process_queue() 
    
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
        if not item: return

        row = self.list_widget.row(item)
        # Capisce se hai cliccato il download in corso (riga 0) o uno in attesa
        is_active_row = (self.current_task is not None and row == 0)

        if is_active_row:
            task_data = self.current_task
            queue_index = -1
        else:
            # Sfalsa l'indice se c'è un task attivo in cima
            queue_index = row - 1 if self.current_task else row
            if queue_index < 0 or queue_index >= len(self.queue): return
            task_data = self.queue[queue_index]

        # Crea il menu e applica lo stile neon
        menu = QMenu(self)
        menu.setStyleSheet(f"QMenu {{ background-color: {self.theme['background_dark_purple']}; color: white; border: 1px solid {self.neon_start}; }} QMenu::item:selected {{ background-color: {self.theme['surface_panel_purple']}; }}")

        copy_action = menu.addAction("🔗 Copy Download Link")
        start_action = menu.addAction("▶️ Start Now")
        remove_action = menu.addAction("❌ Cancel / Remove")

        # Se è il task attivo, non puoi rimuoverlo o avviarlo (è già attivo)
        if is_active_row:
            remove_action.setVisible(False)
            start_action.setVisible(False)

        action = menu.exec(self.list_widget.mapToGlobal(pos))

        if action == copy_action:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(task_data.get('url', ''))
            print(f"[UI] Copied to clipboard: {task_data.get('url', '')}")

        elif action == start_action and not is_active_row:
            if getattr(self, 'is_extracting', False):
                print("[UI] Cannot start new task while extracting.")
                return
            
            # Put current task back into queue
            if self.current_task:
                if self.active_worker:
                    self.active_worker.cancel()
                self._cleanup_worker()
                self.queue.insert(0, self.current_task)
            
            # Make selected task the current one
            self.current_task = task_data
            if task_data in self.queue:
                self.queue.remove(task_data)
                
            self._process_queue()

        elif action == remove_action and not is_active_row:
            self.queue.pop(queue_index)
            self._rebuild_list_ui()

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
        self.is_extracting = False
        if self.current_task:
            completed = self.current_task.copy()
            completed['eta_text'] = 'COMPLETED'
            self.completed_tasks.append(completed)
        self._set_empty_state()
        self.current_task = None
        self.save_state()
        self._process_queue()

    def add_pending_extraction(self, archive_path, task_data):
        """Salva un download completato che attende ancora estrazione."""
        if not archive_path or not os.path.exists(archive_path):
            return
        entry = {
            "archive_path": archive_path,
            "title": task_data.get('title', ''),
            "web_version": task_data.get('web_version', ''),
            "game_info": task_data.get('game_info', ''),
            "description": task_data.get('description', ''),
            "page_url": task_data.get('page_url', ''),
            "cover_url": task_data.get('cover_url', ''),
            "provider": task_data.get('provider', ''),
            "provider_domain": task_data.get('provider_domain', ''),
            "timestamp": time.time(),
        }
        self.pending_extractions = [
            existing for existing in self.pending_extractions
            if existing.get("archive_path") != archive_path
        ]
        self.pending_extractions.append(entry)
        self.save_state()
        print(f"[DLManager] Pending extraction salvata: {entry['title']} ({archive_path})")

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
            self.btn_pause.setIcon(self.icon_play)
            self.stat_labels["Speed (Avg)"].setText("0.0 MB/s")
            self.stat_labels["ETA"].setText("Paused")
            self.set_status(
                "Paused after error - press play to resume",
                self.status_yellow,
            )
            self.pause_state_changed.emit(True)
            self.download_error.emit(err_msg)
            self.save_state()
            return

        self.current_task = None
        self.download_error.emit(err_msg)
        self.save_state()
        self._process_queue()

    def set_state_file(self, path):
        self.state_file = path
        self.load_state()

    def save_state(self):
        if not hasattr(self, 'state_file') or not self.state_file: return
        import json
        state = {
            'queue': self.queue,
            'current_task': self.current_task,
            'pending_extractions': self.pending_extractions,
        }
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[DLManager] Error saving state: {e}")

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
            self.pending_extractions = filtered_pending
            self.queue = all_tasks
            self.current_task = None
            self._rebuild_list_ui()
            
            if self.queue:
                self.current_task = self.queue.pop(0)
                self._rebuild_list_ui()
                self._set_active_state()
                self.active_card.show()
                self.graph.show()
                
                self.lbl_title.setText(self.current_task.get('title', 'Unknown'))
                installed = self.current_task.get('installed_version', 'None')
                web = self.current_task.get('web_version', 'Unknown')
                self.lbl_versions.setText(f"Installed: {installed}  |  <span style='color: {self.neon_end};'>Web: {web}</span>")
                self._update_cover_html(self.current_task.get('cover_url', ''))
                self.stat_labels["Game"].setText(self.current_task.get('title', 'Unknown'))
                self.stat_labels["Web Version"].setText(web)
                
                self.is_paused = True
                self.btn_pause.setIcon(self.icon_play)
                self.set_status("Paused (Auto-Recovered)", self.status_yellow)
                self.stat_labels["Speed (Avg)"].setText("0.0 MB/s")
                self.stat_labels["ETA"].setText("Paused")
                self.pause_state_changed.emit(True)
                self._update_progress_bar(0)

            self.save_state()
                
        except Exception as e:
            print(f"[DLManager] Error loading state: {e}")
