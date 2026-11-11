import os

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QWidget

from core.config import get_base_dir


GRADIENT_MASK_PATH = os.path.join(get_base_dir(), ".media", "gradient-overlay.png")


def alpha_from_luminance(image):
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    data = gray.constBits().asstring(gray.sizeInBytes())
    return QImage(
        data, gray.width(), gray.height(), gray.bytesPerLine(),
        QImage.Format.Format_Alpha8,
    )


def colorize_mask(mask, color, size):
    result = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor(color))
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, mask)
    painter.end()
    return result


class MaskOverlay(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor("#120a1c")
        self._mask = QImage(GRADIENT_MASK_PATH)
        self._scaled_alpha = None
        self._scaled_key = None
        self._composite = None
        self._composite_key = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent;")

    def set_fade_color(self, color):
        self._color = QColor(color)
        self._composite = None
        self._composite_key = None
        self.update()

    def _alpha_for(self, w, h):
        key = (w, h)
        if self._scaled_key != key:
            scaled = self._mask.scaled(
                w, h,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._scaled_alpha = alpha_from_luminance(scaled)
            self._scaled_key = key
        return self._scaled_alpha

    def _render(self, w, h):
        key = (w, h, self._color.rgba())
        if self._composite_key != key:
            self._composite = colorize_mask(
                self._alpha_for(w, h), self._color, QSize(w, h)
            )
            self._composite_key = key
        return self._composite

    def paintEvent(self, event):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0 or self._mask.isNull():
            return
        painter = QPainter(self)
        painter.drawImage(0, 0, self._render(w, h))
        painter.end()
