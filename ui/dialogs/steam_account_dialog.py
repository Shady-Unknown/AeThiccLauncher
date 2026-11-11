from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
from ui.widgets.profile_card import ProfileCard

class SteamAccountDialog(QDialog):
    def __init__(self, accounts, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Steam Account")
        self.selected_path = None
        self.setStyleSheet("background-color: #1a0f2b;") # Sfondo unificato

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        # Header centrale
        header = QLabel("Chi sta giocando?")
        header.setStyleSheet("color: white; font-size: 26px; font-weight: bold; margin-bottom: 20px;")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        # Contenitore orizzontale per le Card
        cards_layout = QHBoxLayout()
        cards_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cards_layout.setSpacing(20)

        # Popoliamo le card affiancate
        for acc in accounts:
            card = ProfileCard(acc['name'], acc['id'], acc['path'], acc.get('local_avatar_path'))
            card.clicked.connect(self.select_account)
            cards_layout.addWidget(card)

        layout.addLayout(cards_layout)        
        # Pulsante Annulla discreto in basso
        btn_cancel = QPushButton("Annulla")
        btn_cancel.setFixedSize(120, 35)
        btn_cancel.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8a7fa4;
                border: 1px solid #8a7fa4;
                border-radius: 5px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: white;
                border: 1px solid white;
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        
        btn_layout = QHBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.setContentsMargins(0, 20, 0, 0)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)
        
    def select_account(self, path):
        # Riceve il path direttamente dalla card cliccata e chiude
        self.selected_path = path
        self.accept()

# --- DIALOG FOR PASSWORD PROTECTED ARCHIVES ---
