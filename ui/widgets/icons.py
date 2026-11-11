"""Icone PNG per le righe requisiti / game info.

I glifi arrivano da ``tools/split_sysicons.py`` (sprite sheet bianco su nero
diviso in ``.media/icons/<key>.png``, bianco su trasparente). Qui c'è solo il
caricamento con cache, l'eventuale tinta e un fallback a glifo Unicode se un
file manca.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from core.config import get_base_dir

ICONS_SUBDIR = "icons"

#: Glifi di fallback quando il PNG non esiste (o non è leggibile).
FALLBACK_GLYPHS = {
    "spec_os": "\U0001F5A5",
    "spec_cpu": "\U0001F9E0",
    "spec_gpu": "\U0001F3AE",
    "spec_ram": "\U0001F4BE",
    "spec_dx": "\u2699",
    "spec_storage": "\U0001F4BD",
    "info_genre": "\U0001F3AE",
    "info_dev": "\U0001F3E2",
    "info_platform": "\U0001F5A5",
    "info_coop": "\U0001F465",
    "info_version": "\U0001F3F7",
    "info_size": "\U0001F4E6",
    "info_pub": "\U0001F4E3",
}

_pixmap_cache: dict[tuple, QPixmap] = {}


def icons_dir() -> str:
    return os.path.join(get_base_dir(), ".media", ICONS_SUBDIR)


def spec_icon_path(key: str) -> str:
    return os.path.join(icons_dir(), f"{key}.png")


def spec_icon_available(key: str) -> bool:
    return bool(key) and os.path.isfile(spec_icon_path(key))


def spec_icon_pixmap(key: str, size: int = 26, color: QColor | str | None = None) -> QPixmap:
    """Pixmap quadrata dell'icona ``key``, opzionalmente tinta di ``color``.

    La cache è per (key, size, color, dpr), quindi cambiare tema non lascia
    pixmap sporche in giro.
    """
    app = None
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
    except Exception:  # pragma: no cover - solo in test headless estremi
        app = None
    dpr = float(app.devicePixelRatio()) if app is not None else 1.0
    tint = QColor(color).name() if color is not None else ""
    cache_key = (key, int(size), tint, round(dpr, 3))
    cached = _pixmap_cache.get(cache_key)
    if cached is not None:
        return cached

    path = spec_icon_path(key)
    pixmap = QPixmap(path) if os.path.isfile(path) else QPixmap()
    if pixmap.isNull():
        return QPixmap()

    target = max(1, int(round(size * dpr)))
    scaled = pixmap.scaled(
        target, target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if tint:
        tinted = QPixmap(scaled.size())
        tinted.setDevicePixelRatio(dpr)
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawPixmap(0, 0, scaled)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor(color))
        painter.end()
        scaled = tinted
    else:
        scaled.setDevicePixelRatio(dpr)

    _pixmap_cache[cache_key] = scaled
    return scaled


def spec_icon_qicon(key: str, size: int = 26, color: QColor | str | None = None) -> QIcon:
    pixmap = spec_icon_pixmap(key, size, color)
    return QIcon(pixmap) if not pixmap.isNull() else QIcon()


def fallback_glyph(key: str) -> str:
    return FALLBACK_GLYPHS.get(key or "", "\u25CF")


def search_icon_pixmap(size: int = 14, color: QColor | str = "#ffffff") -> QPixmap:
    """Lente disegnata a mano: nessun asset esterno, colore arbitrario."""
    try:
        from PyQt6.QtWidgets import QApplication

        dpr = float(QApplication.instance().devicePixelRatio()) if QApplication.instance() else 1.0
    except Exception:  # pragma: no cover
        dpr = 1.0
    target = max(8, int(round(size * dpr)))
    pixmap = QPixmap(target, target)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen_color = QColor(color)
    pen = QPen(pen_color)
    pen.setWidthF(max(1.2, target * 0.10))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    margin = target * 0.16
    diameter = target * 0.56
    painter.drawEllipse(QRectF(margin, margin, diameter, diameter))
    start = QPointF(margin + diameter * 0.82, margin + diameter * 0.82)
    end = QPointF(target - margin * 1.1, target - margin * 1.1)
    painter.drawLine(start, end)
    painter.end()
    return pixmap
