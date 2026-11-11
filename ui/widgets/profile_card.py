import os
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCursor, QPixmap, QPainter, QPainterPath
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel

class ProfileCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, name, acc_id, path, avatar_path=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedSize(160, 220)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        # Stile della card a riposo e in hover (Sfrutta i colori del tuo tema!)
        self.setStyleSheet("""
            ProfileCard {
                background-color: #2b1a42;
                border: 2px solid transparent;
                border-radius: 12px;
            }
            ProfileCard:hover {
                background-color: #3b2a52;
                border: 2px solid #ff00a0;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)

        # 1. Avatar (Immagine se presente, altrimenti Cerchio con iniziale e gradiente Cyberpunk)
        self.avatar = QLabel()
        self.avatar.setFixedSize(80, 80)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)

        if avatar_path and os.path.exists(avatar_path):
            pixmap = QPixmap(avatar_path)
            # Create a rounded pixmap
            target = QPixmap(80, 80)
            target.fill(Qt.GlobalColor.transparent)
            painter = QPainter(target)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            path_bg = QPainterPath()
            path_bg.addRoundedRect(0, 0, 80, 80, 40, 40)
            painter.setClipPath(path_bg)
            painter.drawPixmap(0, 0, 80, 80, pixmap.scaled(80, 80, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation))
            painter.end()
            self.avatar.setPixmap(target)
            self.avatar.setStyleSheet("background: transparent;")
        else:
            initial = name[0].upper() if name else "?"
            self.avatar.setText(initial)
            self.avatar.setStyleSheet("""
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #8d0bb1, stop:1 #00d2ff);
                color: white;
                font-size: 36px;
                font-weight: bold;
                border-radius: 40px;
            """)

        # 2. Nome Profilo
        self.name_label = QLabel(name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("color: #ffffff; font-size: 16px; font-weight: bold; margin-top: 10px; border: none; background: transparent;")

        # 3. ID Profilo
        self.id_label = QLabel(f"ID: {acc_id}")
        self.id_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.id_label.setStyleSheet("color: #8a7fa4; font-size: 12px; border: none; background: transparent;")

        layout.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.name_label)
        layout.addWidget(self.id_label)

    # Rende il frame cliccabile
    def mousePressEvent(self, event):
        self.clicked.emit(self.path)
        super().mousePressEvent(event)


# --- DIALOG FOR STEAM ACCOUNT SELECTION (MODERN UI) ---
