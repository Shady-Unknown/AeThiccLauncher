"""Small portrait cover used by the Stats selected-game panel.

It resolves local covers with the same MiniCoverManager used by Explorer and
Discovery; when nothing is cached yet it paints a neutral ``NO IMAGE`` stub.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QRectF, Qt, QUrl
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QWidget


class CoverThumb(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(96, 144)
        self._pixmap = None
        self._title = ""
        self._theme = {}

    def set_theme(self, theme):
        self._theme = dict(theme or {})
        self.update()

    def set_cover(self, path_or_url, title=""):
        self._title = str(title or "")
        local = str(path_or_url or "")
        if local.startswith("file://"):
            local = QUrl(local).toLocalFile()
        pixmap = QPixmap(local) if local and os.path.isfile(local) else QPixmap()
        self._pixmap = None if pixmap.isNull() else pixmap
        self.setToolTip(self._title)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        theme = self._theme
        panel = QColor(theme.get("surface_panel_purple") or "#2b1a42")
        border = QColor(theme.get("main_accent_color") or "#733d91")
        muted = QColor(theme.get("text_muted") or "#8a7fa4")
        accent = QColor(theme.get("bright_neon_accent") or "#ff00a0")

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 10, 10)
        painter.setClipPath(clip)

        if self._pixmap is not None:
            target_w = int(rect.width())
            target_h = int(rect.height())
            scaled = self._pixmap.scaled(
                target_w,
                target_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = max(0, (scaled.width() - target_w) // 2)
            y = max(0, (scaled.height() - target_h) // 2)
            painter.drawPixmap(
                QRectF(rect.left(), rect.top(), target_w, target_h),
                scaled,
                QRectF(x, y, target_w, target_h),
            )
        else:
            gradient = QLinearGradient(rect.left(), rect.top(), rect.right(), rect.bottom())
            start = QColor(panel)
            end = QColor(accent)
            end.setAlpha(55)
            gradient.setColorAt(0.0, start)
            gradient.setColorAt(1.0, end)
            painter.fillRect(rect, QBrush(gradient))

            initials = "".join(
                part[0].upper() for part in self._title.split()[:2] if part
            )
            if initials:
                painter.setPen(QPen(QColor(accent.red(), accent.green(), accent.blue(), 45)))
                big = QFont(painter.font())
                big.setPointSize(30)
                big.setBold(True)
                painter.setFont(big)
                painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), initials)

            painter.setPen(QPen(muted))
            font = QFont(painter.font())
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                QRectF(rect.left(), rect.bottom() - 26, rect.width(), 20),
                int(Qt.AlignmentFlag.AlignCenter),
                "NO IMAGE",
            )

        painter.setClipping(False)
        painter.setPen(QPen(border, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 10, 10)
