from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QLabel, QListWidget, QFrame, QHBoxLayout, QGraphicsDropShadowEffect, QSizePolicy
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient

class QueueListWidget(QListWidget):
    order_changed = pyqtSignal()
    
    def dropEvent(self, event):
        super().dropEvent(event)
        self.order_changed.emit()


class NeonGraphWidget(QWidget):
    def __init__(self, color_hex, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.base_color = QColor(color_hex)
        self.data_points = [0] * 50
        self.max_val = 10.0
        self.current_speed = 0.0
        self.metric_label = "BANDWIDTH"
        
    def set_mode(self, hex_color, metric_name):
        self.base_color = QColor(hex_color)
        self.metric_label = metric_name
        self.update()

    def clear(self):
        self.data_points = [0] * 50
        self.max_val = 10.0
        self.current_speed = 0.0
        self.update()

    def add_value(self, value):
        self.current_speed = value
        if value > self.max_val:
            self.max_val = value * 1.1
            
        self.data_points.pop(0)
        self.data_points.append(value)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        path = QPainterPath()
        step = w / (len(self.data_points) - 1)
        
        path.moveTo(0, h - (self.data_points[0] / self.max_val) * (h * 0.7))
        for i in range(1, len(self.data_points)):
            path.lineTo(i * step, h - (self.data_points[i] / self.max_val) * (h * 0.7))
            
        fill_path = QPainterPath(path)
        fill_path.lineTo(w, h)
        fill_path.lineTo(0, h)
        fill_path.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c = QColor(self.base_color)
        c.setAlpha(40)
        grad.setColorAt(0, c)
        c.setAlpha(0)
        grad.setColorAt(1, c)
        painter.fillPath(fill_path, grad)
        
        glow_pen = QPen(self.base_color)
        glow_pen.setWidth(5)
        glow_color = QColor(self.base_color)
        glow_color.setAlpha(120)
        glow_pen.setColor(glow_color)
        painter.setPen(glow_pen)
        painter.drawPath(path)
        
        solid_pen = QPen(self.base_color)
        solid_pen.setWidth(2)
        solid_pen.setColor(self.base_color)
        painter.setPen(solid_pen)
        painter.drawPath(path)
        
        painter.setPen(self.base_color)
        font = painter.font()
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(10, 20, f"{self.metric_label}: {self.current_speed:.1f} MB/s")


class QueueItemWidget(QFrame):
    def __init__(self, task, theme, is_active=False, is_extracting=False):
        super().__init__()
        self.theme = theme
        self.neon_start = self.theme.get('neon_gradient_start', '#8d0bb1')
        self.neon_end = self.theme.get('neon_gradient_end', '#00d2ff')
        self.text_muted = self.theme.get('text_muted', '#8a7fa4')

        self.setFixedHeight(63)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 5, 15, 5)

        lbl_handle = QLabel("⣿")
        lbl_handle.setStyleSheet(f"color: {self.text_muted}; font-size: 16px; margin-right: 10px; border: none; background: transparent;")
        layout.addWidget(lbl_handle)

        lbl_title = QLabel(task.get('title', task['filename']))
        lbl_title.setStyleSheet(f"color: {self.theme['text_primary_white']}; font-weight: bold; font-size: 15px; border: none; background: transparent;")
        layout.addWidget(lbl_title, stretch=1)

        provider = str(
            task.get('provider') or task.get('provider_domain') or ''
        ).strip()
        if provider:
            lbl_provider = QLabel(f"via {provider}")
            lbl_provider.setStyleSheet(
                f"color: {self.text_muted}; font-size: 11px; "
                "margin-left: 12px; border: none; background: transparent;"
            )
            lbl_provider.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            layout.addWidget(lbl_provider)

        lbl_space = QLabel(f"Space Needed: <span style='color: white; font-weight: bold;'>{task.get('space_needed', 'Unknown')}</span>")
        lbl_space.setStyleSheet(f"color: {self.text_muted}; font-size: 13px; border: none; background: transparent;")
        lbl_space.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_space)

        eta_val = task.get('eta_text', 'Pending')
        lbl_eta = QLabel(f"ETA: <span style='color: {self.neon_start}; font-weight: bold;'>{eta_val}</span>")
        lbl_eta.setStyleSheet(f"color: {self.text_muted}; font-size: 13px; margin-left: 20px; border: none; background: transparent;")
        lbl_eta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(lbl_eta)

        if is_active:
            border_color = self.neon_start if is_extracting else self.neon_end
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {self.theme['surface_panel_purple']};
                    border: 1.5px solid {border_color};
                    border-radius: 8px;
                }}
            """)
            row_glow = QGraphicsDropShadowEffect(self)
            row_glow.setBlurRadius(15)
            row_glow.setColor(QColor(border_color))
            row_glow.setOffset(0, 0)
            self.setGraphicsEffect(row_glow)

            title_glow = QGraphicsDropShadowEffect(self)
            title_glow.setBlurRadius(10)
            title_glow.setColor(QColor(border_color))
            title_glow.setOffset(0, 0)
            lbl_title.setGraphicsEffect(title_glow)
        else:
            self.setStyleSheet(f"""
                QFrame {{
                    background-color: {self.theme['background_dark_purple']};
                    border: 1px solid rgba(255,255,255,0.1);
                    border-radius: 8px;
                }}
            """)
            self.setGraphicsEffect(None)
            lbl_title.setGraphicsEffect(None)
