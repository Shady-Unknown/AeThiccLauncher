"""Stats tab (S2 base).

Global overview on top (hero cards + timeline), a subtle divider, then the
interactive per-game section: clickable scrollable library on the left, selected
game metrics + an accent-coloured advice card on the right. The ethical footer
closes the page like the Explorer tab. All data comes from the real launcher
state and StatsManager; there is no demo dataset.
"""
from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from core.config import save_config
from core.events import (
    EVENT_BACKUP_CREATED,
    EVENT_GAME_INSTALLED,
    EVENT_GAME_REMOVED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_PREFS_CHANGED,
    EVENT_PRICE_REFRESHED,
    EVENT_REGION_CHANGED,
    EVENT_SESSION_END,
)
from core.mini_cover_cache import MiniCoverManager
from core.price_utils import format_cents
from managers.stats_phrases import StatsPhrases
from managers.stats_sorting import (
    DEFAULT_SORT_KEY, SORT_OPTIONS, filter_games, normalize_sort_key,
    sort_games,
)
from ui.widgets.charts import (
    AreaLineChart, CalendarHeatmap, GameBarList, GenreDonut,
    HorizontalBarChart, VerticalBarChart,
)
from ui.widgets.cover_thumb import CoverThumb
from ui.widgets.stat_card import StatCard

RANGES = (
    ("30d", "30d"),
    ("90d", "90d"),
    ("365d", "365d"),
    ("all", "All"),
)
RANGE_LABELS = {
    "30d": "Last 30 days",
    "90d": "Last 90 days",
    "365d": "Last 365 days",
    "all": "All time",
}
REGIONS = (
    ("US", "United States"),
    ("IT", "Italy"),
    ("GB", "United Kingdom"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("ES", "Spain"),
    ("CA", "Canada"),
    ("AU", "Australia"),
    ("JP", "Japan"),
    ("BR", "Brazil"),
)
REGION_CURRENCY = {
    "US": "USD",
    "IT": "EUR",
    "GB": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    "ES": "EUR",
    "CA": "CAD",
    "AU": "AUD",
    "JP": "JPY",
    "BR": "BRL",
}
REFRESH_EVENTS = (
    EVENT_SESSION_END,
    EVENT_GAME_INSTALLED,
    EVENT_GAME_REMOVED,
    EVENT_PRICE_REFRESHED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_REGION_CHANGED,
    EVENT_PREFS_CHANGED,
    EVENT_BACKUP_CREATED,
)

ADVICE_INTERVAL_MS = 20000

CARD_DEFINITIONS = (
    ("hours", "Hours played"),
    ("sessions", "Sessions"),
    ("games", "Games"),
    ("cost", "Value per hour"),
    ("value", "Library value"),
    ("streak", "Streak"),
    ("night", "Night owl"),
    ("coverage", "Data coverage"),
)

METRIC_DEFINITIONS = (
    ("hours", "Hours played"),
    ("sessions", "Sessions"),
    ("cost", "Value per hour"),
    ("last", "Last played"),
    ("genres", "Genres"),
    ("developer", "Developer"),
    ("publisher", "Publisher"),
    ("gold", "Gold Tier"),
    ("backups", "Backups"),
)


class StatsTab(QWidget):
    def __init__(self, app_window, stats_manager, parent=None):
        super().__init__(parent)
        self.app_window = app_window
        self.stats_manager = stats_manager
        self.events = getattr(app_window, "events", None)
        self.phrases = StatsPhrases()
        self._snapshot = {}
        self._empty_state = False
        self._games = []
        self._selected_game = {}
        self._phrase_text = ""
        self._updating_region = False
        self._updating_library = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(250)
        self._refresh_timer.timeout.connect(self.refresh)

        self._advice_timer = QTimer(self)
        self._advice_timer.setInterval(ADVICE_INTERVAL_MS)
        self._advice_timer.timeout.connect(self._new_phrase)

        self._build_ui()
        self._bind_events()
        self.apply_theme()
        self.refresh()

    # ------------------------------------------------------------------ config

    def _config(self):
        return getattr(self.app_window, "app_config", {}) or {}

    def _stats_config(self):
        value = self._config().get("stats", {})
        return value if isinstance(value, dict) else {}

    def _range_key(self):
        key = str(self._stats_config().get("range") or "90d").lower()
        return key if key in RANGE_LABELS else "90d"

    def _region(self):
        return str(self._stats_config().get("region") or "US").upper()

    def _library_sort_key(self):
        return normalize_sort_key(
            self._stats_config().get("library_sort") or DEFAULT_SORT_KEY
        )

    def _library_installed_only(self):
        return bool(self._stats_config().get("library_installed_only", False))

    def _save_config(self):
        try:
            save_config(self._config())
        except Exception as exc:
            print(f"[Stats] config save failed: {exc}")

    # ------------------------------------------------------------------ UI

    def _build_ui(self):
        self.setObjectName("StatsRoot")
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("STATS")
        title.setObjectName("StatsTitle")
        subtitle = QLabel("Your library, your hours, your wallet.")
        subtitle.setObjectName("StatsSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self._range_buttons = {}
        self.range_group = QButtonGroup(self)
        self.range_group.setExclusive(True)
        for key, label in RANGES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("StatsSegment")
            self.range_group.addButton(button)
            self._range_buttons[key] = button
            button.clicked.connect(lambda _checked=False, k=key: self._set_range(k))
            controls.addWidget(button)

        self.region_combo = QComboBox()
        for code, name in REGIONS:
            self.region_combo.addItem(f"{code} — {name}", code)
        controls.addWidget(self.region_combo)
        header.addLayout(controls)
        root.addLayout(header)

        self.advice_banner = QFrame()
        self.advice_banner.setObjectName("AdviceBanner")
        self.advice_banner.setMinimumHeight(72)
        advice_layout = QHBoxLayout(self.advice_banner)
        advice_layout.setContentsMargins(14, 8, 14, 8)
        advice_layout.setSpacing(12)

        advice_text = QVBoxLayout()
        advice_text.setSpacing(2)
        advice_header = QHBoxLayout()
        advice_header.setSpacing(8)
        advice_title = QLabel("From the launcher")
        advice_title.setObjectName("PanelTitle")
        self.advice_category = QLabel("")
        self.advice_category.setObjectName("PhraseCategory")
        advice_header.addWidget(advice_title)
        advice_header.addWidget(self.advice_category)
        advice_header.addStretch(1)
        self.advice_phrase = QLabel("Select a game from the library.")
        self.advice_phrase.setObjectName("PhraseText")
        self.advice_phrase.setWordWrap(True)
        self.advice_phrase.setMinimumHeight(34)
        self.advice_phrase.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        advice_text.addLayout(advice_header)
        advice_text.addWidget(self.advice_phrase, 1)

        advice_buttons = QVBoxLayout()
        advice_buttons.setSpacing(5)
        self.btn_new_phrase = QPushButton("New line")
        self.btn_copy_phrase = QPushButton("Copy")
        for button in (self.btn_new_phrase, self.btn_copy_phrase):
            button.setObjectName("StatsAction")
        self.btn_new_phrase.clicked.connect(self._new_phrase)
        self.btn_copy_phrase.clicked.connect(self._copy_phrase)
        advice_buttons.addWidget(self.btn_new_phrase)
        advice_buttons.addWidget(self.btn_copy_phrase)
        advice_buttons.addStretch(1)
        advice_layout.addLayout(advice_text, 1)
        advice_layout.addLayout(advice_buttons)
        root.addWidget(self.advice_banner)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.btn_overview = QPushButton("Overview")
        self.btn_charts = QPushButton("Charts")
        for button in (self.btn_overview, self.btn_charts):
            button.setCheckable(True)
            button.setObjectName("StatsMode")
            self.mode_group.addButton(button)
            mode_row.addWidget(button)
        self.btn_overview.setChecked(True)
        self.btn_overview.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.btn_charts.clicked.connect(self._show_charts)
        mode_row.addStretch(1)
        root.addLayout(mode_row)

        self.stack = QStackedWidget()
        self.overview = QWidget()
        self.overview.setObjectName("StatsOverview")
        overview_layout = QVBoxLayout(self.overview)
        overview_layout.setContentsMargins(0, 0, 0, 0)
        overview_layout.setSpacing(8)

        self.empty_label = QLabel("No sessions yet. Play something and come back.")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setWordWrap(True)
        self.empty_label.setVisible(False)
        overview_layout.addWidget(self.empty_label)

        self.hero_grid = QGridLayout()
        self.hero_grid.setHorizontalSpacing(8)
        self.hero_grid.setVerticalSpacing(8)
        self.cards = {}
        for index, (key, title_text) in enumerate(CARD_DEFINITIONS):
            card = StatCard(title_text)
            self.cards[key] = card
            self.hero_grid.addWidget(card, index // 4, index % 4)
        for column in range(4):
            self.hero_grid.setColumnStretch(column, 1)
        overview_layout.addLayout(self.hero_grid)

        self.timeline_panel, timeline_layout = self._panel("Hours over time")
        self.timeline_range_label = QLabel("")
        self.timeline_range_label.setObjectName("PanelHint")
        self.timeline_chart = AreaLineChart()
        timeline_layout.addWidget(self.timeline_range_label)
        timeline_layout.addWidget(self.timeline_chart)
        overview_layout.addWidget(self.timeline_panel)

        self.per_game_widget = QWidget()
        self.per_game_widget.setObjectName("PerGameSection")
        self.per_game_widget.setMinimumHeight(330)
        per_game_layout = QVBoxLayout(self.per_game_widget)
        per_game_layout.setContentsMargins(0, 0, 0, 0)
        per_game_layout.setSpacing(8)

        divider = QHBoxLayout()
        divider.setSpacing(8)
        left_line = QFrame()
        left_line.setObjectName("StatsDivider")
        left_line.setFixedHeight(1)
        divider_label = QLabel("PER GAME")
        divider_label.setObjectName("DividerLabel")
        right_line = QFrame()
        right_line.setObjectName("StatsDivider")
        right_line.setFixedHeight(1)
        divider.addWidget(left_line, 1)
        divider.addWidget(divider_label)
        divider.addWidget(right_line, 1)
        per_game_layout.addLayout(divider)

        browser = QHBoxLayout()
        browser.setSpacing(10)
        browser.addWidget(self._build_library_panel(), 2)
        browser.addWidget(self._build_details_column(), 3)
        per_game_layout.addLayout(browser, 1)
        overview_layout.addWidget(self.per_game_widget, 1)

        self.overview_scroll = QScrollArea()
        self.overview_scroll.setWidgetResizable(True)
        self.overview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.overview_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.overview_scroll.setWidget(self.overview)
        self.stack.addWidget(self.overview_scroll)

        self.charts_scroll = self._build_charts_page()
        self.stack.addWidget(self.charts_scroll)

        root.addWidget(self.stack, 1)

        self.footer_label = QLabel("")
        self.footer_label.setObjectName("StatsFooter")
        self.footer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.footer_label.setWordWrap(True)
        root.addWidget(self.footer_label)

        self.region_combo.currentIndexChanged.connect(self._on_region_changed)
        self.sort_combo.currentIndexChanged.connect(self._on_library_options_changed)
        self.installed_only.toggled.connect(self._on_library_options_changed)
        self._sync_controls()
        self._sync_library_controls()

    def _panel(self, title):
        frame = QFrame()
        frame.setObjectName("StatsPanel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("PanelTitle")
        layout.addWidget(label)
        return frame, layout

    def _build_library_panel(self):
        panel = QFrame()
        panel.setObjectName("StatsPanel")
        panel.setMinimumHeight(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("Library")
        title.setObjectName("PanelTitle")
        self.game_count_label = QLabel("")
        self.game_count_label.setObjectName("GameCount")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.game_count_label)
        layout.addLayout(header)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.sort_combo = QComboBox()
        self.sort_combo.setObjectName("StatsSort")
        for option in SORT_OPTIONS:
            self.sort_combo.addItem(option["label"], option["key"])
        self.installed_only = QCheckBox("Hide non-installed games")
        self.installed_only.setObjectName("StatsCheck")
        toolbar.addWidget(self.sort_combo, 1)
        toolbar.addWidget(self.installed_only)
        layout.addLayout(toolbar)

        self.game_scroll = QScrollArea()
        self.game_scroll.setObjectName("GameScroll")
        self.game_scroll.setWidgetResizable(True)
        self.game_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.game_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.game_bar_list = GameBarList()
        self.game_bar_list.game_selected.connect(self._on_game_selected)
        self.game_scroll.setWidget(self.game_bar_list)
        layout.addWidget(self.game_scroll, 1)
        return panel

    def _build_details_column(self):
        column = QWidget()
        column.setObjectName("GameDetailsColumn")
        column.setMinimumHeight(300)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.selected_panel = QFrame()
        self.selected_panel.setObjectName("StatsPanel")
        selected_layout = QVBoxLayout(self.selected_panel)
        selected_layout.setContentsMargins(12, 10, 12, 10)
        selected_layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        self.cover_thumb = CoverThumb()
        top_row.addWidget(self.cover_thumb, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(3)
        self.selected_title_label = QLabel("Select a game")
        self.selected_title_label.setObjectName("SelectedGameTitle")
        self.selected_title_label.setWordWrap(True)
        self.selected_meta_label = QLabel(
            "Click a row in the library to see its stats."
        )
        self.selected_meta_label.setObjectName("SelectedGameMeta")
        self.selected_meta_label.setWordWrap(True)
        info.addWidget(self.selected_title_label)
        info.addWidget(self.selected_meta_label)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(16)
        metrics.setVerticalSpacing(8)
        self.selected_metrics = {}
        for index, (key, title_text) in enumerate(METRIC_DEFINITIONS):
            box, value_label, subtitle_label = self._metric_box(title_text)
            self.selected_metrics[key] = (value_label, subtitle_label)
            metrics.addWidget(box, index // 3, index % 3)
        for column_index in range(3):
            metrics.setColumnStretch(column_index, 1)
        info.addLayout(metrics)
        info.addStretch(1)
        top_row.addLayout(info, 1)
        selected_layout.addLayout(top_row)

        chart_header = QHBoxLayout()
        chart_header.setSpacing(6)
        self.selected_chart_title = QLabel("This game · hours over time")
        self.selected_chart_title.setObjectName("PanelHint")
        self.selected_chart_range = QLabel("")
        self.selected_chart_range.setObjectName("PanelHint")
        chart_header.addWidget(self.selected_chart_title)
        chart_header.addStretch(1)
        chart_header.addWidget(self.selected_chart_range)
        self.selected_chart = AreaLineChart()
        self.selected_chart.setMinimumHeight(90)
        self.selected_chart.setMaximumHeight(105)
        self.selected_chart.set_empty_text("No sessions for this game yet")
        selected_layout.addLayout(chart_header)
        selected_layout.addWidget(self.selected_chart, 1)
        layout.addWidget(self.selected_panel, 1)
        return column

    @staticmethod
    def _metric_box(title):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)
        title_label = QLabel(str(title).upper())
        title_label.setObjectName("MetricTitle")
        value_label = QLabel("—")
        value_label.setObjectName("MetricValue")
        subtitle_label = QLabel("")
        subtitle_label.setObjectName("MetricSubtitle")
        subtitle_label.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        layout.addWidget(subtitle_label)
        return box, value_label, subtitle_label

    # ------------------------------------------------------------------ charts

    def _build_charts_page(self):
        scroll = QScrollArea()
        scroll.setObjectName("ChartsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        content = QWidget()
        content.setObjectName("ChartsContent")
        grid = QGridLayout(content)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)

        self.heatmap_panel, heat_layout = self._panel("Activity · last 52 weeks")
        self.heatmap_hint = QLabel("")
        self.heatmap_hint.setObjectName("PanelHint")
        self.heatmap = CalendarHeatmap()
        heat_layout.addWidget(self.heatmap_hint)
        heat_layout.addWidget(self.heatmap)
        grid.addWidget(self.heatmap_panel, 0, 0, 1, 2)

        self.hour_panel, hour_layout = self._panel("Hours by time of day")
        self.hour_hint = QLabel("")
        self.hour_hint.setObjectName("PanelHint")
        self.hour_chart = VerticalBarChart()
        hour_layout.addWidget(self.hour_hint)
        hour_layout.addWidget(self.hour_chart)
        grid.addWidget(self.hour_panel, 1, 0)

        self.weekday_panel, weekday_layout = self._panel("Hours by weekday")
        self.weekday_hint = QLabel("")
        self.weekday_hint.setObjectName("PanelHint")
        self.weekday_chart = VerticalBarChart()
        weekday_layout.addWidget(self.weekday_hint)
        weekday_layout.addWidget(self.weekday_chart)
        grid.addWidget(self.weekday_panel, 1, 1)

        self.genre_panel, genre_layout = self._panel("Genres")
        self.genre_hint = QLabel("")
        self.genre_hint.setObjectName("PanelHint")
        self.genre_donut = GenreDonut()
        genre_layout.addWidget(self.genre_hint)
        genre_layout.addWidget(self.genre_donut)
        grid.addWidget(self.genre_panel, 2, 0)

        self.dev_panel, dev_layout = self._panel("Top developers")
        self.dev_hint = QLabel("")
        self.dev_hint.setObjectName("PanelHint")
        self.dev_chart = HorizontalBarChart()
        dev_layout.addWidget(self.dev_hint)
        dev_layout.addWidget(self.dev_chart)
        grid.addWidget(self.dev_panel, 2, 1)

        self.session_panel, session_layout = self._panel("Session length")
        self.session_hint = QLabel("")
        self.session_hint.setObjectName("PanelHint")
        self.session_chart = VerticalBarChart()
        session_layout.addWidget(self.session_hint)
        session_layout.addWidget(self.session_chart)
        grid.addWidget(self.session_panel, 3, 0)

        self.value_panel, value_layout = self._panel("Best value per hour")
        self.value_hint = QLabel("")
        self.value_hint.setObjectName("PanelHint")
        self.value_chart = HorizontalBarChart()
        value_layout.addWidget(self.value_hint)
        value_layout.addWidget(self.value_chart)
        grid.addWidget(self.value_panel, 3, 1)

        for column in range(2):
            grid.setColumnStretch(column, 1)
        grid.setRowStretch(4, 1)

        self._chart_widgets = (
            self.heatmap, self.hour_chart, self.weekday_chart,
            self.genre_donut, self.dev_chart, self.session_chart,
            self.value_chart,
        )
        scroll.setWidget(content)
        return scroll

    def _show_charts(self):
        self.stack.setCurrentIndex(1)
        self._update_charts(self._snapshot)

    def _update_charts(self, snapshot):
        if not hasattr(self, "heatmap"):
            return
        snapshot = snapshot or {}
        range_key = self._range_key()
        range_label = RANGE_LABELS.get(range_key, "Last 90 days")
        timeline = snapshot.get("timeline") or {}

        try:
            heat_days = (
                self.stats_manager.global_timeline("365d").get("days") or []
                if self.stats_manager is not None else []
            )
        except Exception as exc:
            print(f"[Stats] heatmap failed: {exc}")
            heat_days = []
        self.heatmap.set_data(heat_days, 52)
        self.heatmap_hint.setText(
            f"Last 52 weeks · {sum(float(point.get('hours') or 0) for point in heat_days):.0f}h"
        )

        by_hour = timeline.get("by_hour") or [0.0] * 24
        self.hour_chart.set_data(
            [str(hour) for hour in range(24)],
            by_hour,
            "h",
            tooltips=[
                f"{hour:02d}:00 · {float(value or 0):.1f}h"
                for hour, value in enumerate(by_hour)
            ],
            empty_text="No sessions in this range",
        )
        self.hour_hint.setText(range_label)

        by_weekday = timeline.get("by_weekday") or [0.0] * 7
        weekday_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        self.weekday_chart.set_data(
            weekday_labels,
            by_weekday,
            "h",
            tooltips=[
                f"{label} · {float(value or 0):.1f}h"
                for label, value in zip(weekday_labels, by_weekday)
            ],
            empty_text="No sessions in this range",
        )
        self.weekday_hint.setText(range_label)

        self.genre_donut.set_data(snapshot.get("genres") or [])
        self.genre_hint.setText("All time")

        developers = snapshot.get("developers") or []
        dev_items = [
            {
                "label": item.get("name") or "",
                "value": float(item.get("hours") or 0),
                "tooltip": f"{int(item.get('titles') or 0)} titles · {float(item.get('hours') or 0):.1f}h",
            }
            for item in developers[:10]
        ]
        self.dev_chart.set_data(
            dev_items,
            formatter=lambda value, _item: f"{value:.1f}h",
            empty_text="No developer data yet",
        )
        self.dev_hint.setText("All time · top 10")

        buckets = timeline.get("duration_buckets") or []
        self.session_chart.set_data(
            [bucket.get("label") or "" for bucket in buckets],
            [float(bucket.get("count") or 0) for bucket in buckets],
            "",
            formatter=lambda value: f"{value:.0f}",
            y_formatter=lambda value: f"{value:.0f}",
            tooltips=[
                f"{int(bucket.get('count') or 0)} sessions · {float(bucket.get('hours') or 0):.1f}h"
                for bucket in buckets
            ],
            empty_text="No sessions in this range",
        )
        self.session_hint.setText(range_label)

        currency = self._currency(snapshot)
        priced = [
            game for game in (snapshot.get("games") or [])
            if game.get("cost_per_hour") is not None
            and float(game.get("hours_total") or 0) > 0
        ]
        priced.sort(key=lambda game: float(game.get("cost_per_hour") or 0))
        value_items = []
        if priced:
            minimum = min(float(game.get("cost_per_hour") or 0) for game in priced) or 1.0
            for game in priced[:10]:
                cost = float(game.get("cost_per_hour") or 0)
                display = format_cents(
                    int(round(cost * 100)),
                    game.get("price_currency") or currency,
                ) + "/h"
                value_items.append({
                    "label": game.get("title") or "",
                    "value": cost,
                    "bar": (minimum / cost) if cost > 0 else 1.0,
                    "display": display,
                    "tooltip": f"{display} · {float(game.get('hours_total') or 0):.1f}h",
                })
        self.value_chart.set_data(
            value_items, empty_text="No priced games yet"
        )
        self.value_hint.setText("All time · best 10")

    # ------------------------------------------------------------------ events

    def _bind_events(self):
        if self.events is None:
            return
        for name in REFRESH_EVENTS:
            self.events.subscribe(name, self._on_event)

    def _on_event(self, _payload):
        self._schedule_refresh()

    def _schedule_refresh(self):
        self._refresh_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_refresh()
        if self._snapshot and not self._empty_state:
            self._advice_timer.start()

    def hideEvent(self, event):
        self._advice_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------ data

    def refresh(self):
        if not hasattr(self, "cards"):
            return
        self._sync_controls()
        self._sync_library_controls()
        range_key = self._range_key()
        region = self._region()

        try:
            snapshot = (
                self.stats_manager.snapshot(range_key=range_key, region=region)
                if self.stats_manager is not None else {}
            )
        except Exception as exc:
            print(f"[Stats] snapshot failed: {exc}")
            snapshot = {}

        self._snapshot = snapshot
        self._apply_snapshot(snapshot)

    def _sync_controls(self):
        key = self._range_key()
        button = self._range_buttons.get(key)
        if button is not None:
            button.setChecked(True)

        region = self._region()
        index = self.region_combo.findData(region)
        if index < 0:
            self.region_combo.addItem(region, region)
            index = self.region_combo.count() - 1
        self._updating_region = True
        self.region_combo.setCurrentIndex(index)
        self._updating_region = False

    def _sync_library_controls(self):
        key = self._library_sort_key()
        index = self.sort_combo.findData(key)
        if index < 0:
            index = 0
        self._updating_library = True
        self.sort_combo.setCurrentIndex(index)
        self.installed_only.setChecked(self._library_installed_only())
        self._updating_library = False

    def _on_library_options_changed(self, _value=None):
        if self._updating_library:
            return
        stats_cfg = self._config().setdefault("stats", {})
        stats_cfg["library_sort"] = normalize_sort_key(
            self.sort_combo.currentData() or DEFAULT_SORT_KEY
        )
        stats_cfg["library_installed_only"] = bool(self.installed_only.isChecked())
        self._save_config()
        self._populate_games(self._games)

    def _currency(self, snapshot):
        for game in snapshot.get("games") or []:
            if game.get("price_currency"):
                return game["price_currency"]
        return REGION_CURRENCY.get(self._region(), "USD")

    def _apply_snapshot(self, snapshot):
        hero = snapshot.get("hero") or {}
        coverage = hero.get("coverage") or {}
        currency = self._currency(snapshot)
        games = snapshot.get("games") or []
        played = int(hero.get("games_played") or 0)
        installed = int(hero.get("games_installed") or 0)
        never = int(hero.get("games_never_played") or 0)

        self.cards["hours"].set_value(
            f"{float(hero.get('hours_total') or 0):.1f}h",
            f"{float(hero.get('hours_tracked') or 0):.1f}h tracked · "
            f"{float(hero.get('hours_imported') or 0):.1f}h imported",
        )
        self.cards["sessions"].set_value(
            str(int(hero.get("sessions") or 0)),
            f"avg {float(hero.get('avg_session_minutes') or 0):.0f}m · "
            f"median {float(hero.get('median_session_minutes') or 0):.0f}m",
        )
        self.cards["games"].set_value(
            f"{installed} / {len(games)}",
            f"{played} played · {never} never launched",
        )
        cost = hero.get("avg_cost_per_hour")
        self.cards["cost"].set_value(
            format_cents(int(round(cost * 100)), currency) if cost is not None else "—",
            "official price ÷ hours played",
        )
        self.cards["value"].set_value(
            format_cents(int(hero.get("library_value_cents") or 0), currency),
            f"{int((snapshot.get('finance') or {}).get('known_prices') or 0)} priced games",
        )
        self.cards["streak"].set_value(
            f"{int(hero.get('current_streak') or 0)} days",
            f"best {int(hero.get('best_streak') or 0)} · "
            f"night owl {float(hero.get('night_owl_percent') or 0):.0f}%",
        )
        self.cards["night"].set_value(
            f"{float(hero.get('night_owl_percent') or 0):.0f}%",
            f"weekend {float(hero.get('weekend_percent') or 0):.0f}%",
        )
        self.cards["coverage"].set_value(
            f"{float(coverage.get('tracked_percent') or 0):.0f}%",
            f"IGDB {float(coverage.get('igdb_percent') or 0):.0f}% · "
            f"prices {float(coverage.get('price_percent') or 0):.0f}%",
        )

        days = (snapshot.get("timeline") or {}).get("days") or []
        labels = [str(point.get("date") or "")[5:] for point in days]
        values = [float(point.get("hours") or 0) for point in days]
        self.timeline_chart.set_data(labels, values, "h")
        shown_hours = sum(values)
        self.timeline_range_label.setText(
            f"{RANGE_LABELS.get(self._range_key(), 'Last 90 days')} · {shown_hours:.1f}h shown"
        )

        self._games = games
        self._populate_games(games)

        empty = not games or played <= 0
        self._empty_state = empty
        self.empty_label.setVisible(empty)
        self.timeline_panel.setVisible(not empty)
        self.per_game_widget.setVisible(not empty)
        if empty:
            self._selected_game = {}
            self._update_selected_game({})
            self._phrase_text = ""
            self.advice_phrase.setText("No sessions yet. Play something and come back.")
            self.advice_category.setText("")

        self.footer_label.setVisible(True)
        self._update_charts(snapshot)

    # ------------------------------------------------------------------ games

    def _populate_games(self, games):
        selected_title = self._selected_game.get("title")
        sort_key = self._library_sort_key()
        installed_only = self._library_installed_only()
        visible = filter_games(games, installed_only)
        ordered = sort_games(visible, sort_key)

        self.game_bar_list.set_empty_text(
            "No games match the filter." if installed_only else "No games yet"
        )
        self.game_bar_list.set_sort_key(sort_key)
        self.game_bar_list.set_data(ordered, now=time.time())

        installed = sum(1 for game in visible if game.get("installed"))
        if installed_only:
            self.game_count_label.setText(
                f"{len(visible)} shown · {installed} installed"
            )
        else:
            self.game_count_label.setText(
                f"{len(visible)} games · {installed} installed"
            )

        if not ordered:
            self.game_bar_list.set_selected_title("")
            self._selected_game = {}
            self._update_selected_game({})
            self._new_phrase()
            return

        selected = None
        if selected_title:
            selected = next(
                (game for game in ordered if game.get("title") == selected_title),
                None,
            )
        if selected is None:
            selected = ordered[0]
        self.game_bar_list.set_selected_title(selected.get("title"))
        self._on_game_selected(selected)

    def _on_game_selected(self, game):
        if not isinstance(game, dict) or not game:
            self._selected_game = {}
            self._update_selected_game({})
            self._new_phrase()
            return
        self._selected_game = game
        self._update_selected_game(game)
        self._new_phrase()

    def _update_selected_game(self, game):
        if not isinstance(game, dict) or not game:
            self.selected_title_label.setText("Select a game")
            self.selected_meta_label.setText(
                "Click a row in the library to see its stats."
            )
            for value_label, subtitle_label in self.selected_metrics.values():
                value_label.setText("—")
                subtitle_label.setText("")
            self._phrase_text = ""
            self.advice_phrase.setText("Select a game from the library.")
            self.advice_category.setText("")
            self.cover_thumb.set_cover("", "")
            self.selected_chart.set_data([], [])
            self.selected_chart_range.setText("")
            self._advice_timer.stop()
            return

        currency = game.get("price_currency") or "USD"
        title = str(game.get("title") or "Unknown")
        installed = "Installed" if game.get("installed") else "From your history"
        meta = installed
        if game.get("gold_tier"):
            meta += " · Gold Tier"
        if game.get("rating"):
            meta += f" · rating {float(game['rating']):.0f}"
        self.selected_title_label.setText(title)
        self.selected_meta_label.setText(meta)
        self.cover_thumb.set_cover(self._cover_for_game(game), title)

        hours = float(game.get("hours_total") or 0)
        tracked = float(game.get("hours_tracked") or 0)
        imported = float(game.get("hours_imported") or 0)
        sessions = int(game.get("sessions") or 0)
        avg = game.get("avg_session_minutes")
        cost = game.get("cost_per_hour")
        price = game.get("price_cents")
        last = int(game.get("last_played") or 0)

        self.selected_metrics["hours"][0].setText(f"{hours:.1f}h")
        self.selected_metrics["hours"][1].setText(
            f"{tracked:.1f}h tracked · {imported:.1f}h imported"
        )
        self.selected_metrics["sessions"][0].setText(str(sessions))
        self.selected_metrics["sessions"][1].setText(
            f"avg {float(avg):.0f}m" if avg is not None else "no sessions yet"
        )
        self.selected_metrics["cost"][0].setText(
            format_cents(int(round(cost * 100)), currency)
            if cost is not None else "—"
        )
        self.selected_metrics["cost"][1].setText(
            f"official {format_cents(price, currency)}"
            if price is not None else "no official price"
        )
        self.selected_metrics["last"][0].setText(self._format_date(last))
        self.selected_metrics["last"][1].setText(
            f"Steam 2wks {float(game.get('steam_2wks_hours') or 0):.1f}h"
        )
        self.selected_metrics["genres"][0].setText(
            " · ".join(game.get("genres") or []) or "—"
        )
        self.selected_metrics["genres"][1].setText(
            f"rating {float(game.get('rating') or 0):.0f}"
            if game.get("rating") else ""
        )
        developers = game.get("developers") or []
        publishers = game.get("publishers") or []
        self.selected_metrics["developer"][0].setText(developers[0] if developers else "—")
        self.selected_metrics["developer"][1].setText(
            f"publisher: {publishers[0]}" if publishers else ""
        )
        self.selected_metrics["publisher"][0].setText(publishers[0] if publishers else "—")
        self.selected_metrics["publisher"][1].setText(
            f"store: {str(game.get('price_store') or '—').upper()}"
        )
        self.selected_metrics["gold"][0].setText("Yes" if game.get("gold_tier") else "No")
        self.selected_metrics["gold"][1].setText("community verified")
        self.selected_metrics["backups"][0].setText(str(int(game.get("backup_count") or 0)))
        self.selected_metrics["backups"][1].setText(
            self._format_size(game.get("backup_size_bytes"))
        )

        timeline = self._selected_timeline(game)
        days = timeline.get("days") or []
        labels = [str(point.get("date") or "")[5:] for point in days]
        values = [float(point.get("hours") or 0) for point in days]
        self.selected_chart.set_data(labels, values, "h")
        self.selected_chart_range.setText(
            f"{RANGE_LABELS.get(self._range_key(), 'Last 90 days')} · "
            f"{float(timeline.get('total_hours') or 0):.1f}h shown"
        )

    def _selected_timeline(self, game):
        title = str(game.get("title") or "")
        if not title or self.stats_manager is None:
            return {"days": [], "total_hours": 0}
        try:
            return self.stats_manager.game_timeline(title, self._range_key())
        except Exception as exc:
            print(f"[Stats] selected timeline failed: {exc}")
            return {"days": [], "total_hours": 0}

    def _cover_for_game(self, game):
        title = str(game.get("title") or "")
        installed = getattr(self.app_window, "installed_games", {}) or {}
        sync_folder = (self._config().get("savegames", {}) or {}).get(
            "sync_folder", ""
        )
        try:
            local = MiniCoverManager.resolve_cover_path(
                title, installed, sync_folder
            )
        except Exception:
            local = None
        if local:
            return local
        cover_url = str(game.get("cover_url") or "")
        if cover_url.startswith("file://"):
            return cover_url
        return ""

    @staticmethod
    def _format_date(timestamp):
        if not timestamp:
            return "—"
        try:
            return time.strftime("%Y-%m-%d", time.localtime(int(timestamp)))
        except (TypeError, ValueError, OSError):
            return "—"

    @staticmethod
    def _format_size(size):
        try:
            size = float(size or 0)
        except (TypeError, ValueError):
            return "—"
        if size <= 0:
            return "no backups"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if size < 1024 or unit == "TB":
                return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
            size /= 1024.0
        return "—"

    # ------------------------------------------------------------------ actions

    def _set_range(self, key):
        if key not in RANGE_LABELS:
            return
        stats_cfg = self._config().setdefault("stats", {})
        stats_cfg["range"] = key
        self._save_config()
        self.refresh()

    def _on_region_changed(self, _index):
        if self._updating_region:
            return
        code = self.region_combo.currentData()
        if not code:
            return
        stats_cfg = self._config().setdefault("stats", {})
        stats_cfg["region"] = str(code).upper()
        self._save_config()
        if self.events is not None:
            self.events.emit_event(EVENT_REGION_CHANGED, region=str(code).upper())
        self.refresh()

    def _new_phrase(self):
        if not self._snapshot or self._empty_state:
            return
        stats_cfg = self._stats_config()
        phrase = self.phrases.next(
            self._snapshot,
            context={
                "cinema_rate": stats_cfg.get("cinema_rate", 6.0),
                "currency": (
                    self._selected_game.get("price_currency")
                    or self._currency(self._snapshot)
                ),
            },
            game=self._selected_game or None,
        )
        self._phrase_text = phrase.get("text", "")
        self.advice_phrase.setText(self._phrase_text)
        self.advice_category.setText(str(phrase.get("category_label") or "").upper())
        if self.isVisible():
            self._advice_timer.start()

    def _copy_phrase(self):
        if not self._phrase_text:
            return
        QApplication.clipboard().setText(self._phrase_text)
        self.btn_copy_phrase.setText("Copied")
        QTimer.singleShot(1200, lambda: self.btn_copy_phrase.setText("Copy"))

    # ------------------------------------------------------------------ theme

    @staticmethod
    def _blend(color_a, color_b, ratio):
        a = QColor(color_a)
        b = QColor(color_b)
        if not a.isValid():
            a = QColor("#ff00a0")
        if not b.isValid():
            b = QColor("#2b1a42")
        return QColor(
            int(a.red() * ratio + b.red() * (1.0 - ratio)),
            int(a.green() * ratio + b.green() * (1.0 - ratio)),
            int(a.blue() * ratio + b.blue() * (1.0 - ratio)),
        ).name()

    def _update_footer(self):
        accent = (self._config().get("theme", {}) or {}).get(
            "bright_neon_accent", "#ff00a0"
        )
        self.footer_label.setText(
            "If you like the game and the software house / publisher aren't "
            "aggressive monetizers, if they are pro-users, if they support the "
            f"customer, <b style='color:{accent}; text-transform: uppercase;'>"
            "BUY THE GAME. THEY DESERVE IT.</b>"
        )

    def apply_theme(self):
        theme = self._config().get("theme", {}) or {}
        panel = theme.get("surface_panel_purple") or "#2b1a42"
        background = theme.get("main_background_color") or "#120a1c"
        border = theme.get("main_accent_color") or "#733d91"
        text = theme.get("text_primary_white") or "#ffffff"
        muted = theme.get("text_muted") or "#8a7fa4"
        pink = theme.get("text_secondary_pink") or "#f3c6ff"
        accent = theme.get("bright_neon_accent") or "#ff00a0"
        advice_bg = self._blend(accent, panel, 0.16)

        self.setStyleSheet(f"""
            QWidget#StatsRoot, QWidget#StatsOverview, QWidget#PerGameSection,
            QWidget#GameDetailsColumn {{
                background: transparent;
            }}
            QFrame#StatsPanel {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 12px;
            }}
            QFrame#AdviceBanner {{
                background: {advice_bg};
                border: 1px solid {accent};
                border-radius: 12px;
            }}
            QLabel#StatsTitle {{
                color: {text};
                font-size: 24px;
                font-weight: bold;
                letter-spacing: 2px;
            }}
            QLabel#StatsSubtitle, QLabel#PanelHint, QLabel#GameCount {{
                color: {muted};
                font-size: 11px;
            }}
            QLabel#PanelTitle {{
                color: {text};
                font-size: 14px;
                font-weight: bold;
            }}
            QLabel#SelectedGameTitle {{
                color: {text};
                font-size: 20px;
                font-weight: bold;
            }}
            QLabel#SelectedGameMeta {{
                color: {muted};
                font-size: 11px;
            }}
            QLabel#MetricTitle {{
                color: {pink};
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#MetricValue {{
                color: {text};
                font-size: 15px;
                font-weight: bold;
            }}
            QLabel#MetricSubtitle {{
                color: {muted};
                font-size: 10px;
            }}
            QLabel#PhraseText {{
                color: {text};
                font-size: 16px;
                font-style: italic;
            }}
            QLabel#PhraseCategory {{
                color: {accent};
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }}
            QLabel#EmptyState {{
                color: {muted};
                font-size: 14px;
                padding: 18px;
            }}
            QLabel#DividerLabel {{
                color: {muted};
                font-size: 9px;
                font-weight: bold;
                letter-spacing: 2px;
                padding: 0 8px;
            }}
            QFrame#StatsDivider {{
                background: {border};
                max-height: 1px;
            }}
            QLabel#StatsFooter {{
                color: {muted};
                font-size: 11px;
                padding: 4px 10px;
                background: rgba(0,0,0,0.1);
                border-radius: 4px;
                margin-top: 5px;
            }}
            QPushButton#StatsSegment, QPushButton#StatsMode {{
                background: transparent;
                color: {muted};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 11px;
                font-weight: bold;
            }}
            QPushButton#StatsSegment:hover, QPushButton#StatsMode:hover {{
                color: {text};
                border-color: {accent};
            }}
            QPushButton#StatsSegment:checked, QPushButton#StatsMode:checked {{
                background: {accent};
                color: #ffffff;
                border-color: {accent};
            }}
            QPushButton#StatsAction {{
                background: {panel};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 5px 12px;
                font-weight: bold;
            }}
            QPushButton#StatsAction:hover {{
                border-color: {accent};
                color: {accent};
            }}
            QComboBox {{
                background: {background};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px 8px;
                min-width: 150px;
            }}
            QComboBox QAbstractItemView {{
                background: {panel};
                color: {text};
                selection-background-color: {accent};
            }}
            QComboBox#StatsSort {{
                min-width: 0;
            }}
            QCheckBox#StatsCheck {{
                color: {muted};
                font-size: 10px;
                spacing: 6px;
            }}
            QCheckBox#StatsCheck::indicator {{
                width: 13px;
                height: 13px;
                border: 1px solid {border};
                border-radius: 3px;
                background: transparent;
            }}
            QCheckBox#StatsCheck::indicator:checked {{
                background: {accent};
                border-color: {accent};
            }}
            QScrollArea, QStackedWidget {{
                border: none;
                background: transparent;
            }}
            QScrollArea#GameScroll {{
                background: transparent;
            }}
            QScrollArea#GameScroll QWidget {{
                background: transparent;
            }}
            QScrollArea#GameScroll QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollArea#GameScroll QScrollBar::handle:vertical {{
                background: {border};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollArea#GameScroll QScrollBar::add-line:vertical,
            QScrollArea#GameScroll QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """)

        for card in self.cards.values():
            card.set_theme(theme)
        self.timeline_chart.set_theme(theme)
        self.selected_chart.set_theme(theme)
        self.game_bar_list.set_theme(theme)
        self.cover_thumb.set_theme(theme)
        for chart in getattr(self, "_chart_widgets", ()):
            chart.set_theme(theme)
        self._update_footer()
