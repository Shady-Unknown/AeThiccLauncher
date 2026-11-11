"""Sfondo sfocato della tab Explorer.

L'immagine (featured image SteamRIP, oppure lo splash screen come fallback)
viene sfocata con Pillow in un thread separato, tenuta in cache per dimensione
e disegnata dalla tab stessa nel suo ``paintEvent``: niente widget impilati,
quindi nessun problema di compositing con QSplitter e QWebEngineView.

Sopra l'immagine c'è sempre uno scrim: è quello che garantisce il contrasto del
testo bianco flottante.
"""
from __future__ import annotations

import os

from PIL import Image, ImageEnhance, ImageFilter, ImageStat
from PyQt6.QtCore import QObject, QRect, QRunnable, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from core.config import get_base_dir

MAX_BLUR_WIDTH = 1600
DEBOUNCE_MS = 120
#: Percentuale di sfocatura applicata allo sfondo (0 = nitido, 100 = massima).
DEFAULT_BLUR_PERCENT = 0
SAMPLE_STEP = 8
TARGET_MEAN_LUMA = 96
LIGHT_SCRIM_BOOST = 0.22
LIGHT_THRESHOLD_RAW = 0.60

_image_cache: dict[tuple, QImage] = {}
_IMAGE_CACHE_LIMIT = 6


def splash_path() -> str:
    return os.path.join(get_base_dir(), ".media", "SplashScreen.png")


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return image
    scale = max(width / src_w, height / src_h)
    new_size = (max(1, int(src_w * scale + 0.5)), max(1, int(src_h * scale + 0.5)))
    resized = image.resize(new_size, Image.LANCZOS)
    left = max(0, (new_size[0] - width) // 2)
    top = max(0, (new_size[1] - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def render_blurred(path: str, width: int, height: int,
                   blur_percent: int = DEFAULT_BLUR_PERCENT) -> QImage:
    """Immagine sfocata, con luminanza normalizzata, pronta da disegnare."""
    width = max(64, min(MAX_BLUR_WIDTH, int(width)))
    height = max(64, min(MAX_BLUR_WIDTH, int(height)))
    with Image.open(path) as raw:
        image = raw.convert("RGB")
    image = _cover_crop(image, width, height)
    percent = max(0, min(100, int(blur_percent)))
    radius = max(0.0, (8.0 + 52.0 * percent / 100.0) * (width / 1600.0))
    if radius < 0.5:
        radius = 0.0
    image = image.filter(ImageFilter.GaussianBlur(radius=radius))
    image = ImageEnhance.Color(image).enhance(0.90)
    # Normalizza la luminanza: uno sfondo scuro non deve diventare nero, uno
    # chiaro non deve abbagliare (lo scrim pensa al resto).
    mean = ImageStat.Stat(image.convert("L")).mean[0]
    gain = max(1.0, min(2.4, TARGET_MEAN_LUMA / max(12.0, mean)))
    image = ImageEnhance.Brightness(image).enhance(gain)
    buffer = image.tobytes("raw", "RGB")
    qimage = QImage(buffer, width, height, width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


class _BlurSignals(QObject):
    done = pyqtSignal(int, QImage)


class _BlurJob(QRunnable):
    """Job QRunnable: calcola il blur fuori dal thread UI."""

    def __init__(self, job_id: int, path: str, width: int, height: int,
                 signals: _BlurSignals, blur_percent: int = DEFAULT_BLUR_PERCENT):
        super().__init__()
        self.job_id = job_id
        self.path = path
        self.width = width
        self.height = height
        self.signals = signals
        self.blur_percent = blur_percent

    def run(self):
        try:
            image = render_blurred(self.path, self.width, self.height,
                                   self.blur_percent)
        except Exception as exc:  # pragma: no cover - dipende dal filesystem
            print(f"[Background] blur failed for {self.path}: {exc}")
            return
        self.signals.done.emit(self.job_id, image)


class BlurredBackground(QObject):
    """Sorgente + cache del sfondo; disegna su richiesta nel ``paintEvent``."""

    background_changed = pyqtSignal()

    def __init__(self, theme: dict, host: QWidget,
                 blur_percent: int = DEFAULT_BLUR_PERCENT):
        super().__init__(host)
        self._host = host
        self._theme = theme or {}
        self._blur_percent = max(0, min(100, int(blur_percent)))
        self._source_path = ""
        self._image: QImage | None = None
        self._pixmap: QPixmap | None = None
        self._job_id = 0
        self._active_key: tuple | None = None

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._request_render)

        self._signals = _BlurSignals()
        self._signals.done.connect(self._on_blur_done)

        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)

        self.set_source(None)

    # ── API ──────────────────────────────────────────────────────────

    def set_theme(self, theme: dict):
        self._theme = theme or {}
        self._host.update()

    def set_blur_percent(self, value: int):
        """Percentuale di sfocatura (0..100); il render riparte con debounce."""
        value = max(0, min(100, int(value)))
        if value == self._blur_percent:
            return
        self._blur_percent = value
        self._active_key = None
        self._debounce.start()

    def set_source(self, path: str | None):
        """Sorgente dell'immagine; ``None`` = splash screen.

        La pixmap precedente resta visibile finché la nuova non è pronta: evita
        il flash nero al cambio gioco.
        """
        candidate = path if path and os.path.isfile(path) else ""
        if not candidate:
            candidate = splash_path()
        if candidate == self._source_path and self._image is not None:
            return
        self._source_path = candidate
        self._request_render()
        self._host.update()

    def current_source(self) -> str:
        return self._source_path

    def has_image(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    def schedule_render(self):
        """Da chiamare quando cambia la dimensione dell'host."""
        self._debounce.start()

    def luminance_under(self, rect: QRect) -> float:
        """Luminanza 0..1 dello sfondo (post-scrim) sotto ``rect``."""
        raw = self.raw_luminance_under(rect)
        host_h = max(1, self._host.height())
        scrim_alpha = self._scrim_alpha_at((rect.center().y()) / host_h)
        dark = QColor(self._theme.get("background_dark_purple", "#1a0f2b"))
        dark_luma = (0.2126 * dark.red() + 0.7152 * dark.green() + 0.0722 * dark.blue()) / 255.0
        return raw * (1.0 - scrim_alpha) + dark_luma * scrim_alpha

    def raw_luminance_under(self, rect: QRect) -> float:
        """Luminanza 0..1 dell'immagine sfocata, senza scrim."""
        if self._image is None or self._image.isNull():
            return 0.0
        host_w = max(1, self._host.width())
        host_h = max(1, self._host.height())
        img_w, img_h = self._image.width(), self._image.height()
        scale_x = img_w / float(host_w)
        scale_y = img_h / float(host_h)
        left = max(0, int(rect.left() * scale_x))
        right = min(img_w - 1, int(rect.right() * scale_x))
        top = max(0, int(rect.top() * scale_y))
        bottom = min(img_h - 1, int(rect.bottom() * scale_y))
        if right < left or bottom < top:
            return 0.0
        total = 0.0
        count = 0
        for y in range(top, bottom + 1, SAMPLE_STEP):
            for x in range(left, right + 1, SAMPLE_STEP):
                pixel = self._image.pixelColor(x, y)
                total += (0.2126 * pixel.red() + 0.7152 * pixel.green()
                          + 0.0722 * pixel.blue()) / 255.0
                count += 1
        return total / count if count else 0.0

    def set_light_mode(self, light: bool):
        """Sfondi molto chiari: scrim più denso per non abbagliare."""
        light = bool(light)
        if getattr(self, "_light_mode", False) == light:
            return
        self._light_mode = light
        self._host.update()

    # ── Rendering ────────────────────────────────────────────────────

    def _request_render(self):
        width = self._host.width()
        height = self._host.height()
        if width <= 0 or height <= 0 or not self._source_path:
            return
        dpr = float(self._host.devicePixelRatioF() or 1.0)
        target_w = min(MAX_BLUR_WIDTH, max(64, int(width * dpr)))
        ratio = height / max(1, width)
        target_h = min(MAX_BLUR_WIDTH, max(64, int(target_w * ratio)))
        key = (self._source_path, target_w, target_h, self._blur_percent)
        if key == self._active_key and self._image is not None:
            return
        cached = _image_cache.get(key)
        if cached is not None:
            self._apply_image(key, cached, dpr)
            return
        self._job_id += 1
        self._pool.start(_BlurJob(self._job_id, self._source_path,
                                  target_w, target_h, self._signals,
                                  self._blur_percent))

    def _on_blur_done(self, job_id: int, image: QImage):
        if job_id != self._job_id or image is None or image.isNull():
            return
        dpr = float(self._host.devicePixelRatioF() or 1.0)
        key = (self._source_path, image.width(), image.height(), self._blur_percent)
        if len(_image_cache) >= _IMAGE_CACHE_LIMIT:
            _image_cache.pop(next(iter(_image_cache)))
        _image_cache[key] = image
        self._apply_image(key, image, dpr)

    def _apply_image(self, key: tuple, image: QImage, dpr: float = 1.0):
        self._active_key = key
        self._image = image
        pixmap = QPixmap.fromImage(image)
        if dpr and abs(dpr - 1.0) > 0.01:
            pixmap.setDevicePixelRatio(dpr)
        self._pixmap = pixmap
        self._host.update()
        self.background_changed.emit()

    def _scrim_alpha_at(self, ratio: float) -> float:
        """Alpha dello scrim alla posizione verticale 0..1."""
        ratio = max(0.0, min(1.0, ratio))
        top, mid, bottom = 0.48, 0.18, 0.46
        if ratio <= 0.5:
            local = ratio / 0.5
            alpha = top + (mid - top) * local
        else:
            local = (ratio - 0.5) / 0.5
            alpha = mid + (bottom - mid) * local
        if getattr(self, "_light_mode", False):
            alpha += LIGHT_SCRIM_BOOST
        return max(0.0, min(0.88, alpha))

    # ── Painting ─────────────────────────────────────────────────────

    def paint(self, painter: QPainter, rect):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.drawPixmap(rect, self._pixmap)

        base = QColor(self._theme.get("background_dark_purple", "#1a0f2b"))
        gradient = QLinearGradient(0, 0, 0, max(1, self._host.height()))
        for stop in (0.0, 0.5, 1.0):
            color = QColor(base)
            color.setAlphaF(self._scrim_alpha_at(stop))
            gradient.setColorAt(stop, color)
        painter.fillRect(rect, gradient)

        accent = QColor(self._theme.get("main_accent_color", "#733d91"))
        accent.setAlphaF(0.08)
        painter.fillRect(rect, accent)
