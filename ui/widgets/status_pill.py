from PyQt6.QtCore import Qt, QPointF, QRectF, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPolygonF
from PyQt6.QtWidgets import QPushButton


class StatusPill(QPushButton):
    """
    Pill disegnata a mano per lo stato versione.

    - modalità solida: un colore (es. verde "Updated", rosa "Update available")
    - modalità outline: sfondo trasparente, bordo/testo colorati (es. viola "v1.0")
    - modalità split: due colori con taglio diagonale (verde SteamRIP | viola Steam)
    """

    HEIGHT = 26
    PADDING_X = 12
    GAP_SPLIT = 10
    CHEVRON_W = 16
    RADIUS_RATIO = 0.50
    SLANT_RATIO = 0.55

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFlat(True)
        self.setFixedHeight(self.HEIGHT)

        self._font = QFont(self.font())
        self._font.setPixelSize(11)
        self._font.setBold(True)

        self._split = False
        self._outline = False
        self._left_text = ""
        self._right_text = ""
        self._solid_text = ""
        self._left_color = QColor("#22c55e")
        self._right_color = QColor("#8b5cf6")
        self._text_color = QColor("#ffffff")
        self._left_text_color = QColor("#101010")
        self._right_text_color = QColor("#ffffff")
        self._expanded = False

        self._sync_accessible()

    # --- API ---

    def set_solid(self, text, color, text_color=None, outline=False):
        self._split = False
        self._outline = outline
        self._solid_text = text or ""
        self._left_color = QColor(color)
        self._text_color = QColor(text_color) if text_color else (
            QColor(color) if outline else QColor("#ffffff")
        )
        self._sync_accessible()
        self.updateGeometry()
        self.update()

    def set_split(self, left_text, right_text, left_color, right_color,
                  left_text_color="#101010", right_text_color="#ffffff"):
        self._split = True
        self._outline = False
        self._left_text = left_text or ""
        self._right_text = right_text or ""
        self._left_color = QColor(left_color)
        self._right_color = QColor(right_color)
        self._left_text_color = QColor(left_text_color)
        self._right_text_color = QColor(right_text_color)
        self._sync_accessible()
        self.updateGeometry()
        self.update()

    def set_expanded(self, expanded):
        self._expanded = bool(expanded)
        self.update()

    def is_expanded(self):
        return self._expanded

    # --- Geometry ---

    def _summary_text(self):
        if self._split:
            return f"{self._left_text} | {self._right_text}".strip(" |")
        return self._solid_text

    def _sync_accessible(self):
        text = self._summary_text()
        self.setAccessibleName(text)
        self.setText(text)  # usato solo per tooltip/automation, non disegnato

    def sizeHint(self):
        metrics = QFontMetrics(self._font)
        if self._split:
            # Metà sinistra/destra: entrambe devono contenere il testo più lungo
            # (la destra riserva anche lo spazio della freccia) → nessun troncamento
            longest = max(
                metrics.horizontalAdvance(self._left_text),
                metrics.horizontalAdvance(self._right_text),
            )
            half = longest + self.CHEVRON_W + self.PADDING_X + 4
            return QSize(half * 2, self.HEIGHT)

        width = (self.PADDING_X
                 + metrics.horizontalAdvance(self._solid_text)
                 + self.CHEVRON_W
                 + self.PADDING_X)
        return QSize(width, self.HEIGHT)

    def minimumSizeHint(self):
        return self.sizeHint()

    # --- Painting ---

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        rect = QRectF(self.rect()).adjusted(0.9, 0.9, -0.9, -0.9)
        radius = rect.height() / 2
        pill = QPainterPath()
        pill.addRoundedRect(rect, radius, radius)

        if self._outline:
            pen = QPen(self._left_color, 1.4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(pill)
        elif self._split:
            painter.fillPath(pill, self._right_color)
            split_x = rect.left() + rect.width() * self.RADIUS_RATIO
            slant = rect.height() * self.SLANT_RATIO
            left_poly = QPolygonF([
                QPointF(rect.left(), rect.top()),
                QPointF(split_x + slant / 2, rect.top()),
                QPointF(split_x - slant / 2, rect.bottom()),
                QPointF(rect.left(), rect.bottom()),
            ])
            poly_path = QPainterPath()
            poly_path.addPolygon(left_poly)
            poly_path.closeSubpath()
            left_clip = pill.intersected(poly_path)
            painter.fillPath(left_clip, self._left_color)
        else:
            painter.fillPath(pill, self._left_color)

        if self.underMouse():
            hover = QPainterPath(pill)
            painter.fillPath(hover, QColor(255, 255, 255, 18))

        painter.setFont(self._font)
        painter.setPen(QPen(self._text_color))

        text_top = rect.top()
        text_h = rect.height()
        chevron_space = self.CHEVRON_W

        if self._split:
            split_x = rect.left() + rect.width() * self.RADIUS_RATIO
            left_rect = QRectF(rect.left() + self.PADDING_X, text_top,
                               max(split_x - rect.left() - self.PADDING_X * 2, 1), text_h)
            right_left = split_x
            right_width = max(rect.right() - chevron_space - right_left - self.PADDING_X, 1)
            right_rect = QRectF(right_left, text_top, right_width, text_h)
            painter.setPen(QPen(self._left_text_color))
            painter.drawText(left_rect, int(Qt.AlignmentFlag.AlignCenter), self._left_text)
            painter.setPen(QPen(self._right_text_color))
            painter.drawText(right_rect, int(Qt.AlignmentFlag.AlignCenter), self._right_text)
        else:
            text_rect = QRectF(rect.left() + self.PADDING_X, text_top,
                               max(rect.width() - self.PADDING_X * 2 - chevron_space, 1), text_h)
            painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), self._solid_text)

        self._paint_chevron(painter, rect)
        painter.end()

    def _paint_chevron(self, painter, rect):
        painter.save()
        chevron_color = self._right_text_color if self._split else self._text_color
        painter.setPen(QPen(chevron_color, 1.6))
        cx = rect.right() - 10
        cy = rect.center().y()
        if self._expanded:
            painter.drawLine(QPointF(cx - 3, cy + 1.5), QPointF(cx, cy - 1.5))
            painter.drawLine(QPointF(cx, cy - 1.5), QPointF(cx + 3, cy + 1.5))
        else:
            painter.drawLine(QPointF(cx - 3, cy - 1.5), QPointF(cx, cy + 1.5))
            painter.drawLine(QPointF(cx, cy + 1.5), QPointF(cx + 3, cy - 1.5))
        painter.restore()
