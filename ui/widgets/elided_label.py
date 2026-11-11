from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QLabel, QSizePolicy


class ElidedLabel(QLabel):
    """
    QLabel che accorcia il testo con "…" quando lo spazio non basta e mostra
    il testo completo nel tooltip. Il testo viene ri-accorciato a ogni resize.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def set_style(self, color, font_px=12, bold=True):
        font = self.font()
        font.setPixelSize(font_px)
        font.setBold(bold)
        self.setFont(font)

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.WindowText, QColor(color))
        self.setPalette(palette)

        self._refresh()

    def set_full_text(self, text):
        self._full_text = text or ""
        self._refresh()

    def full_text(self):
        return self._full_text

    def _refresh(self):
        metrics = self.fontMetrics()
        available = max(self.width() - 2, 24)
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available)
        super().setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()
