"""Small custom QPainter charts used by the Stats tab.

S2 ships the timeline area chart and the top-games bar chart. The richer set
(heatmap, hour/day, genre donut) arrives in S3 and can live in this module.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter,
    QPainterPath, QPen,
)
from PyQt6.QtWidgets import QWidget

from managers.stats_sorting import (
    bar_context, bar_ratio, format_primary_value, format_secondary_values,
    sort_spec,
)


def _color(theme, key, fallback):
    value = (theme or {}).get(key) or fallback
    return QColor(value)


class AreaLineChart(QWidget):
    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._labels = []
        self._values = []
        self._unit = "h"
        self._empty_text = "No sessions yet"
        self.setMinimumHeight(125)

    def sizeHint(self):
        return QSize(620, 150)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_empty_text(self, text):
        self._empty_text = str(text or "No sessions yet")
        self.update()

    def set_data(self, labels, values, unit="h"):
        self._labels = list(labels or [])
        self._values = [max(0.0, float(value or 0)) for value in (values or [])]
        self._unit = unit
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(QFont("Segoe UI", 8))

        muted = _color(self._theme, "text_muted", "#8a7fa4")
        text = _color(self._theme, "text_primary_white", "#ffffff")
        accent = _color(self._theme, "bright_neon_accent", "#ff00a0")
        rect = QRectF(self.rect()).adjusted(44, 14, -14, -30)
        if rect.width() <= 10 or rect.height() <= 10:
            return

        if not self._values or max(self._values) <= 0:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        max_value = max(self._values)
        grid_pen = QPen(QColor(muted))
        grid_pen.setWidth(1)
        grid_pen.setColor(QColor(muted.red(), muted.green(), muted.blue(), 70))
        painter.setPen(grid_pen)
        for step in range(5):
            y = rect.top() + rect.height() * step / 4.0
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            label = f"{max_value * (4 - step) / 4.0:.1f}{self._unit}"
            painter.setPen(QPen(muted))
            painter.drawText(
                QRectF(0, y - 8, rect.left() - 8, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                label,
            )
            painter.setPen(grid_pen)

        points = []
        last_index = max(1, len(self._values) - 1)
        for index, value in enumerate(self._values):
            x = rect.left() + rect.width() * index / last_index
            y = rect.bottom() - rect.height() * value / max_value
            points.append(QPointF(x, y))

        area = QPainterPath()
        area.moveTo(points[0].x(), rect.bottom())
        for point in points:
            area.lineTo(point)
        area.lineTo(points[-1].x(), rect.bottom())
        area.closeSubpath()

        gradient = QLinearGradient(0, rect.top(), 0, rect.bottom())
        top_color = QColor(accent)
        top_color.setAlpha(100)
        bottom_color = QColor(accent)
        bottom_color.setAlpha(0)
        gradient.setColorAt(0.0, top_color)
        gradient.setColorAt(1.0, bottom_color)
        painter.fillPath(area, QBrush(gradient))

        line = QPainterPath()
        line.moveTo(points[0])
        for point in points[1:]:
            line.lineTo(point)
        painter.setPen(QPen(accent, 2))
        painter.drawPath(line)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(accent))
        painter.drawEllipse(points[-1], 3.5, 3.5)

        if self._labels:
            indices = sorted({0, len(self._labels) // 2, len(self._labels) - 1})
            painter.setPen(QPen(muted))
            for index in indices:
                if index >= len(self._labels):
                    continue
                x = rect.left() + rect.width() * index / last_index
                painter.drawText(
                    QRectF(x - 44, rect.bottom() + 7, 88, 18),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    str(self._labels[index]),
                )


class HorizontalBarChart(QWidget):
    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._items = []
        self._formatter = None
        self._empty_text = "No data"
        self._hover_index = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(120)

    def sizeHint(self):
        return QSize(520, max(120, len(self._items) * 32 + 16))

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_data(self, items, formatter=None, empty_text=None):
        self._formatter = formatter
        if empty_text is not None:
            self._empty_text = str(empty_text)
        self._items = []
        for item in (items or []):
            if not isinstance(item, dict):
                continue
            value = max(0.0, float(item.get("value") or 0))
            bar = item.get("bar")
            self._items.append({
                "label": str(item.get("label") or ""),
                "value": value,
                "bar": max(0.0, float(bar)) if bar is not None else value,
                "display": str(item.get("display") or ""),
                "tooltip": str(item.get("tooltip") or ""),
            })
        self.setMinimumHeight(max(120, len(self._items) * 32 + 16))
        self.updateGeometry()
        self.update()

    def _row_at(self, y):
        if not self._items:
            return -1
        rect = QRectF(self.rect()).adjusted(8, 6, -8, -6)
        row_height = rect.height() / len(self._items)
        index = int((float(y) - rect.top()) // row_height)
        return index if 0 <= index < len(self._items) else -1

    def mouseMoveEvent(self, event):
        index = self._row_at(event.position().y())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
            if index >= 0:
                item = self._items[index]
                self.setToolTip(item.get("tooltip") or item.get("label") or "")
            else:
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_index = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        muted = _color(self._theme, "text_muted", "#8a7fa4")
        text = _color(self._theme, "text_primary_white", "#ffffff")
        accent_start = _color(self._theme, "neon_gradient_start", "#8d0bb1")
        accent_end = _color(self._theme, "bright_neon_accent", "#ff00a0")
        track = QColor(accent_start)
        track.setAlpha(55)

        rect = QRectF(self.rect()).adjusted(8, 6, -8, -6)
        if not self._items:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        max_value = max(
            (item.get("bar", item["value"]) for item in self._items),
            default=0.0,
        ) or 1.0
        label_width = min(190.0, rect.width() * 0.42)
        value_width = 64.0
        bar_left = rect.left() + label_width + 10.0
        bar_right = rect.right() - value_width
        bar_width = max(10.0, bar_right - bar_left)
        row_height = rect.height() / len(self._items)

        metrics = QFontMetrics(painter.font())
        for index, item in enumerate(self._items):
            y = rect.top() + index * row_height
            if index == self._hover_index:
                hover = QColor(accent_start)
                hover.setAlpha(18)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(hover))
                painter.drawRoundedRect(
                    QRectF(rect.left(), y + 1, rect.width(), row_height - 2), 5, 5
                )

            label = metrics.elidedText(item["label"], Qt.TextElideMode.ElideRight, int(label_width))
            painter.setPen(QPen(text))
            painter.drawText(
                QRectF(rect.left(), y, label_width, row_height),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                label,
            )

            bar_rect = QRectF(bar_left, y + row_height * 0.30, bar_width, row_height * 0.40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(track))
            painter.drawRoundedRect(bar_rect, 4, 4)

            fill_width = bar_width * item.get("bar", item["value"]) / max_value
            if fill_width > 0:
                fill_rect = QRectF(bar_left, bar_rect.top(), fill_width, bar_rect.height())
                gradient = QLinearGradient(fill_rect.left(), 0, fill_rect.right(), 0)
                gradient.setColorAt(0.0, accent_start)
                gradient.setColorAt(1.0, accent_end)
                painter.setBrush(QBrush(gradient))
                painter.drawRoundedRect(fill_rect, 4, 4)

            display = item.get("display") or (
                self._formatter(item["value"], item)
                if self._formatter else f"{item['value']:.1f}h"
            )
            painter.setPen(QPen(muted))
            painter.drawText(
                QRectF(bar_right + 8, y, value_width, row_height),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                display,
            )


class VerticalBarChart(QWidget):
    """Simple vertical bars for hours-by-hour/day and session buckets."""

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._labels = []
        self._values = []
        self._unit = "h"
        self._formatter = None
        self._y_formatter = None
        self._tooltips = []
        self._empty_text = "No sessions yet"
        self._hover_index = -1
        self.setMouseTracking(True)
        self.setMinimumHeight(180)

    def sizeHint(self):
        return QSize(500, 210)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_data(self, labels, values, unit="h", formatter=None,
                 tooltips=None, empty_text=None, y_formatter=None):
        self._labels = [str(label) for label in (labels or [])]
        self._values = [max(0.0, float(value or 0)) for value in (values or [])]
        self._unit = unit
        self._formatter = formatter
        self._y_formatter = y_formatter
        self._tooltips = list(tooltips or [])
        if empty_text is not None:
            self._empty_text = str(empty_text)
        self.update()

    def _index_at(self, x):
        if not self._values:
            return -1
        rect = QRectF(self.rect()).adjusted(38, 14, -12, -28)
        step = rect.width() / len(self._values)
        index = int((float(x) - rect.left()) // step)
        return index if 0 <= index < len(self._values) else -1

    def mouseMoveEvent(self, event):
        index = self._index_at(event.position().x())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
            if index >= 0:
                tooltip = (
                    self._tooltips[index] if index < len(self._tooltips)
                    else f"{self._labels[index]}: {self._values[index]:.1f}{self._unit}"
                )
                self.setToolTip(tooltip)
            else:
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_index = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        muted = _color(self._theme, "text_muted", "#8a7fa4")
        accent_start = _color(self._theme, "neon_gradient_start", "#8d0bb1")
        accent_end = _color(self._theme, "bright_neon_accent", "#ff00a0")
        track = QColor(accent_start)
        track.setAlpha(38)

        rect = QRectF(self.rect()).adjusted(38, 14, -12, -28)
        if not self._values or max(self._values) <= 0:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        max_value = max(self._values)
        grid_pen = QPen(muted)
        grid_pen.setColor(QColor(muted.red(), muted.green(), muted.blue(), 65))
        for step in range(5):
            y = rect.top() + rect.height() * step / 4.0
            painter.setPen(grid_pen)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            grid_value = max_value * (4 - step) / 4.0
            label = (
                self._y_formatter(grid_value)
                if self._y_formatter else f"{grid_value:.1f}{self._unit}"
            )
            painter.setPen(QPen(muted))
            painter.drawText(
                QRectF(0, y - 8, rect.left() - 6, 16),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                label,
            )

        count = len(self._values)
        step = rect.width() / count
        bar_width = max(2.0, step * 0.62)
        show_every = 1 if count <= 12 else 3
        for index, value in enumerate(self._values):
            x = rect.left() + index * step + (step - bar_width) / 2.0
            height = rect.height() * value / max_value
            bar = QRectF(x, rect.bottom() - height, bar_width, height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(track))
            painter.drawRoundedRect(
                QRectF(x, rect.top(), bar_width, rect.height()), 3, 3
            )
            if height > 0:
                gradient = QLinearGradient(0, bar.top(), 0, bar.bottom())
                gradient.setColorAt(0.0, accent_end)
                gradient.setColorAt(1.0, accent_start)
                painter.setBrush(QBrush(gradient))
                painter.drawRoundedRect(bar, 3, 3)
                if index == self._hover_index:
                    painter.setPen(QPen(accent_end, 1.2))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRoundedRect(bar, 3, 3)

            if index % show_every == 0 or index == count - 1:
                painter.setPen(QPen(muted))
                painter.drawText(
                    QRectF(x - step / 2.0, rect.bottom() + 6, step * 2.0, 16),
                    int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                    self._labels[index],
                )


class CalendarHeatmap(QWidget):
    """52-week activity heatmap, Monday-first, with hover tooltips."""

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._values = {}
        self._weeks = 52
        self._cells = []
        self._empty_text = "No sessions yet"
        self.setMouseTracking(True)
        self.setMinimumHeight(160)

    def sizeHint(self):
        return QSize(900, 180)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_data(self, days, weeks=52):
        self._weeks = max(1, int(weeks))
        self._values = {}
        for point in days or []:
            if not isinstance(point, dict):
                continue
            try:
                day = date.fromisoformat(str(point.get("date") or ""))
            except ValueError:
                continue
            self._values[day] = max(0.0, float(point.get("hours") or 0))
        self.update()

    def _build_cells(self, rect):
        cells = []
        if not self._values:
            return cells
        end = max(self._values)
        end_monday = end - timedelta(days=end.weekday())
        start = end_monday - timedelta(days=(self._weeks - 1) * 7)
        cell_w = rect.width() / self._weeks
        cell_h = rect.height() / 7.0
        cell = max(2.0, min(cell_w, cell_h) - 1.0)
        grid_w = cell * self._weeks + (self._weeks - 1)
        grid_h = cell * 7 + 6
        left = rect.left() + (rect.width() - grid_w) / 2.0
        top = rect.top() + (rect.height() - grid_h) / 2.0
        self._grid_left = left
        self._grid_top = top
        self._grid_cell = cell
        self._grid_start = start
        for week in range(self._weeks):
            for weekday in range(7):
                day = start + timedelta(days=week * 7 + weekday)
                cells.append((
                    QRectF(
                        left + week * (cell + 1),
                        top + weekday * (cell + 1),
                        cell,
                        cell,
                    ),
                    day,
                    self._values.get(day, 0.0),
                ))
        return cells

    def mouseMoveEvent(self, event):
        point = event.position()
        for cell, day, value in self._cells:
            if cell.contains(point):
                self.setToolTip(f"{day.isoformat()} · {value:.1f}h")
                break
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        muted = _color(self._theme, "text_muted", "#8a7fa4")
        accent_start = _color(self._theme, "neon_gradient_start", "#8d0bb1")
        accent_end = _color(self._theme, "bright_neon_accent", "#ff00a0")
        track = QColor(accent_start)
        track.setAlpha(30)

        rect = QRectF(self.rect()).adjusted(30, 20, -10, -10)
        if not self._values:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        max_value = max(self._values.values()) or 1.0
        self._cells = self._build_cells(rect)
        for cell, day, value in self._cells:
            if value <= 0:
                painter.setBrush(QBrush(track))
            else:
                ratio = min(1.0, value / max_value)
                color = QColor(accent_start)
                end_color = QColor(accent_end)
                color.setRed(int(color.red() * (1 - ratio) + end_color.red() * ratio))
                color.setGreen(int(color.green() * (1 - ratio) + end_color.green() * ratio))
                color.setBlue(int(color.blue() * (1 - ratio) + end_color.blue() * ratio))
                color.setAlpha(int(110 + 130 * ratio))
                painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cell, 2, 2)

        painter.setPen(QPen(muted))
        painter.setFont(QFont(painter.font().family(), 8))
        for weekday, label in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
            painter.drawText(
                QRectF(0, self._grid_top + weekday * (self._grid_cell + 1) - 2, 26,
                       self._grid_cell + 4),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                label,
            )

        prev_month = None
        for week in range(self._weeks):
            day = self._grid_start + timedelta(days=week * 7)
            if day.month != prev_month:
                if prev_month is None or week == 0 or day.day <= 7:
                    painter.drawText(
                        QRectF(
                            self._grid_left + week * (self._grid_cell + 1) - 8,
                            max(0, self._grid_top - 16),
                            34, 16,
                        ),
                        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                        day.strftime("%b"),
                    )
                prev_month = day.month


class GenreDonut(QWidget):
    """Donut + legend for IGDB genres."""

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._items = []
        self._segments = []
        self._empty_text = "No genre data yet"
        self.setMouseTracking(True)
        self.setMinimumHeight(220)

    def sizeHint(self):
        return QSize(520, 230)

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_data(self, items, limit=6):
        values = [
            {
                "name": str(item.get("name") or ""),
                "hours": max(0.0, float(item.get("hours") or 0)),
                "titles": int(item.get("titles") or 0),
            }
            for item in (items or []) if isinstance(item, dict)
        ]
        values.sort(key=lambda item: -item["hours"])
        if len(values) > limit:
            other = {
                "name": "Other",
                "hours": sum(item["hours"] for item in values[limit:]),
                "titles": sum(item["titles"] for item in values[limit:]),
            }
            values = values[:limit] + [other]
        self._items = [item for item in values if item["hours"] > 0]
        self.update()

    def _palette(self):
        theme = self._theme
        return [
            _color(theme, "bright_neon_accent", "#ff00a0"),
            _color(theme, "neon_gradient_start", "#8d0bb1"),
            _color(theme, "neon_gradient_end", "#00d2ff"),
            _color(theme, "status_green", "#00ff88"),
            _color(theme, "status_yellow", "#ffcc00"),
            _color(theme, "text_secondary_pink", "#f3c6ff"),
            _color(theme, "text_muted", "#8a7fa4"),
        ]

    def mouseMoveEvent(self, event):
        center = getattr(self, "_hit_center", QPointF(0, 0))
        dx = event.position().x() - center.x()
        dy = event.position().y() - center.y()
        radius = math.hypot(dx, dy)
        outer = getattr(self, "_hit_outer", 0.0)
        inner = getattr(self, "_hit_inner", 0.0)
        if inner <= radius <= outer:
            angle = math.degrees(math.atan2(dx, -dy)) % 360.0
            for segment in self._segments:
                if segment["start"] <= angle < segment["start"] + segment["span"]:
                    self.setToolTip(
                        f"{segment['name']}: {segment['hours']:.1f}h "
                        f"({segment['percent']:.0f}%)"
                    )
                    break
            else:
                self.setToolTip("")
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        muted = _color(self._theme, "text_muted", "#8a7fa4")
        text = _color(self._theme, "text_primary_white", "#ffffff")
        panel = _color(self._theme, "surface_panel_purple", "#2b1a42")
        if not self._items:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        total = sum(item["hours"] for item in self._items) or 1.0
        palette = self._palette()
        size = min(self.width() * 0.58, self.height() - 20)
        donut = QRectF(12, (self.height() - size) / 2.0, size, size)
        self._hit_center = donut.center()
        self._hit_outer = size / 2.0
        self._hit_inner = size * 0.29
        start = 0.0
        self._segments = []
        for index, item in enumerate(self._items):
            span = item["hours"] / total * 360.0
            color = palette[index % len(palette)]
            painter.setBrush(QBrush(color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPie(
                donut,
                int((90.0 - start) * 16),
                int(-span * 16),
            )
            self._segments.append({
                "start": start,
                "span": span,
                "name": item["name"],
                "hours": item["hours"],
                "percent": item["hours"] / total * 100.0,
            })
            start += span

        inner = size * 0.29
        center = donut.center()
        painter.setBrush(QBrush(panel))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center, inner, inner)
        painter.setPen(QPen(text))
        total_font = QFont(painter.font().family(), 12)
        total_font.setBold(True)
        painter.setFont(total_font)
        painter.drawText(
            QRectF(center.x() - inner, center.y() - 12, inner * 2, 20),
            int(Qt.AlignmentFlag.AlignCenter),
            f"{total:.0f}h",
        )
        painter.setPen(QPen(muted))
        painter.setFont(QFont(painter.font().family(), 8))
        painter.drawText(
            QRectF(center.x() - inner, center.y() + 8, inner * 2, 14),
            int(Qt.AlignmentFlag.AlignCenter),
            "played",
        )

        legend_x = donut.right() + 18
        legend_width = self.width() - legend_x - 10
        metrics = QFontMetrics(painter.font())
        row_height = min(24.0, (self.height() - 20) / max(1, len(self._items)))
        legend_y = (self.height() - row_height * len(self._items)) / 2.0
        for index, item in enumerate(self._items):
            y = legend_y + index * row_height
            painter.setBrush(QBrush(palette[index % len(palette)]))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(legend_x, y + 5, 9, 9), 2, 2)
            name = metrics.elidedText(
                item["name"], Qt.TextElideMode.ElideRight,
                max(40, int(legend_width * 0.62)),
            )
            painter.setPen(QPen(text))
            painter.drawText(
                QRectF(legend_x + 15, y, legend_width * 0.62, row_height),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                name,
            )
            painter.setPen(QPen(muted))
            painter.drawText(
                QRectF(
                    legend_x + legend_width * 0.64, y,
                    legend_width * 0.36, row_height,
                ),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                f"{item['hours']:.0f}h · {item['hours'] / total * 100.0:.0f}%",
            )


class GameBarList(QWidget):
    """Scrollable, clickable bar list used as the Stats library browser.

    The primary value inside the bar follows the active ordering: playtime,
    official price/hour, sessions or last-played recency. Everything else moves
    to the secondary line above the bar. Rows are two-line and never draw text
    over each other.
    """

    game_selected = pyqtSignal(dict)
    ROW_HEIGHT = 58

    def __init__(self, theme=None, parent=None):
        super().__init__(parent)
        self._theme = dict(theme or {})
        self._games = []
        self._selected_title = ""
        self._hover_index = -1
        self._sort_key = "installed_first"
        self._spec = sort_spec(self._sort_key)
        self._now = int(time.time())
        self._context = bar_context([], self._spec["primary"], self._now)
        self._empty_text = "No games yet"
        self.setMouseTracking(True)
        self.setMinimumHeight(120)

    def sizeHint(self):
        return QSize(420, max(120, len(self._games) * self.ROW_HEIGHT + 12))

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_sort_key(self, sort_key):
        self._sort_key = str(sort_key or "installed_first")
        self._spec = sort_spec(self._sort_key)
        self._context = bar_context(self._games, self._spec["primary"], self._now)
        self.update()

    def set_empty_text(self, text):
        self._empty_text = str(text or "No games yet")
        self.update()

    def set_data(self, games, now=None):
        self._games = [game for game in (games or []) if isinstance(game, dict)]
        self._now = int(now or time.time())
        self._context = bar_context(self._games, self._spec["primary"], self._now)
        self.setMinimumHeight(max(120, len(self._games) * self.ROW_HEIGHT + 12))
        self.updateGeometry()
        self.update()

    def set_selected_title(self, title):
        self._selected_title = str(title or "")
        self.update()

    def _row_at(self, y):
        if not self._games:
            return -1
        index = int((float(y) - 6.0) // self.ROW_HEIGHT)
        return index if 0 <= index < len(self._games) else -1

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._row_at(event.position().y())
            if index >= 0:
                game = self._games[index]
                self._selected_title = str(game.get("title") or "")
                self.update()
                self.game_selected.emit(dict(game))
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        index = self._row_at(event.position().y())
        if index != self._hover_index:
            self._hover_index = index
            self.update()
            if index >= 0:
                game = self._games[index]
                primary = format_primary_value(
                    game, self._spec["primary"], self._now
                )
                secondary = " · ".join(
                    format_secondary_values(game, self._spec, self._now)
                )
                tooltip = f"{game.get('title') or ''}\n{primary}"
                if secondary:
                    tooltip += f" · {secondary}"
                self.setToolTip(tooltip)
            else:
                self.setToolTip("")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_index = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def _draw_bar_label(self, painter, rect, text, with_backdrop, text_color):
        if with_backdrop:
            backdrop = QColor(0, 0, 0)
            backdrop.setAlpha(145)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(backdrop))
            painter.drawRoundedRect(rect.adjusted(-2, -1, 2, 1), 5, 5)
        painter.setPen(QPen(text_color))
        painter.drawText(
            rect.adjusted(0, 0, -3, 0),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            text,
        )

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        muted = _color(self._theme, "text_muted", "#8a7fa4")
        text = _color(self._theme, "text_primary_white", "#ffffff")
        accent = _color(self._theme, "bright_neon_accent", "#ff00a0")
        accent_start = _color(self._theme, "neon_gradient_start", "#8d0bb1")
        track = QColor(accent_start)
        track.setAlpha(38)

        if not self._games:
            painter.setPen(QPen(muted))
            painter.drawText(
                self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._empty_text
            )
            return

        title_font = QFont(painter.font())
        title_font.setPointSize(9)
        title_font.setBold(True)
        sub_font = QFont(painter.font())
        sub_font.setPointSize(8)
        value_font = QFont(painter.font())
        value_font.setPointSize(9)
        value_font.setBold(True)
        sub_metrics = QFontMetrics(sub_font)
        title_metrics = QFontMetrics(title_font)
        value_metrics = QFontMetrics(value_font)
        primary = self._spec["primary"]

        for index, game in enumerate(self._games):
            row = QRectF(
                4, 6 + index * self.ROW_HEIGHT,
                self.width() - 8, self.ROW_HEIGHT - 4,
            )
            selected = str(game.get("title") or "") == self._selected_title
            hovered = index == self._hover_index
            installed = bool(game.get("installed"))

            if selected:
                background = QColor(accent)
                background.setAlpha(20)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(background))
                painter.drawRoundedRect(row, 7, 7)
                left_bar = QColor(accent)
                painter.setBrush(QBrush(left_bar))
                painter.drawRoundedRect(
                    QRectF(row.left(), row.top() + 7, 3, row.height() - 14), 1.5, 1.5
                )
            elif hovered:
                background = QColor(accent)
                background.setAlpha(10)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(background))
                painter.drawRoundedRect(row, 7, 7)

            x = row.left() + 10
            if installed:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(accent))
                painter.drawEllipse(QPointF(x + 3, row.top() + 13), 3, 3)
                x += 12

            secondary_values = format_secondary_values(game, self._spec, self._now)
            secondary_text = " · ".join(secondary_values)
            max_secondary = int(row.width() * 0.46)
            secondary_width = sub_metrics.horizontalAdvance(secondary_text)
            if secondary_width > max_secondary:
                secondary_text = secondary_values[0] if secondary_values else ""
                secondary_width = sub_metrics.horizontalAdvance(secondary_text)
            if secondary_width > max_secondary:
                secondary_text = sub_metrics.elidedText(
                    secondary_text, Qt.TextElideMode.ElideRight, max_secondary
                )
                secondary_width = max_secondary

            title_width = max(24, int(row.right() - x - secondary_width - 16))
            title = title_metrics.elidedText(
                str(game.get("title") or ""),
                Qt.TextElideMode.ElideRight,
                title_width,
            )
            painter.setFont(title_font)
            painter.setPen(QPen(text if (selected or installed) else muted))
            painter.drawText(
                QRectF(x, row.top() + 2, title_width, 18),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                title,
            )

            if secondary_text:
                painter.setFont(sub_font)
                painter.setPen(QPen(muted))
                painter.drawText(
                    QRectF(
                        row.right() - secondary_width - 8,
                        row.top() + 2,
                        secondary_width,
                        18,
                    ),
                    int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                    secondary_text,
                )

            bar = QRectF(
                row.left() + 8, row.top() + 25,
                row.width() - 16, row.height() - 29,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(track))
            painter.drawRoundedRect(bar, 5, 5)

            ratio = bar_ratio(game, primary, self._context)
            fill_width = bar.width() * ratio
            if fill_width > 0:
                fill = QRectF(bar.left(), bar.top(), fill_width, bar.height())
                gradient = QLinearGradient(fill.left(), 0, fill.right(), 0)
                start = QColor(accent_start)
                end = QColor(accent)
                alpha = 225 if selected else 145
                start.setAlpha(alpha)
                end.setAlpha(alpha)
                gradient.setColorAt(0.0, start)
                gradient.setColorAt(1.0, end)
                painter.setBrush(QBrush(gradient))
                painter.drawRoundedRect(fill, 5, 5)

            primary_text = format_primary_value(game, primary, self._now)
            label_width = value_metrics.horizontalAdvance(primary_text) + 16
            fill_end = bar.left() + fill_width
            inside = fill_width >= label_width + 8 and fill_width > 0
            if inside:
                label_x = fill_end - label_width
                label_rect = QRectF(label_x, bar.top(), label_width, bar.height())
                self._draw_bar_label(painter, label_rect, primary_text, True, text)
            else:
                label_x = min(fill_end + 6, bar.right() - label_width - 2)
                label_x = max(bar.left() + 4, label_x)
                label_rect = QRectF(label_x, bar.top(), label_width, bar.height())
                overlaps_fill = label_x < fill_end + 2
                self._draw_bar_label(
                    painter, label_rect, primary_text, overlaps_fill, text
                )

