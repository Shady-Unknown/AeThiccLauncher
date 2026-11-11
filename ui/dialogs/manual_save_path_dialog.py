import re
from urllib.parse import quote
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, QMessageBox
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtCore import QUrl

class ManualSavePathDialog(QDialog):
    def __init__(self, game_title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Identify Save Path")
        self.resize(560, 240)
        self.game_title = game_title
        self.chosen_path = ""

        layout = QVBoxLayout(self)
        lbl = QLabel(
            f"<b>{game_title}</b> was not found in the Ludusavi manifest or no save path could be inferred.<br><br>"
            "🔍 Search online, then paste the <b>Windows</b> save folder location below, "
            "for example: <code>%localappdata%\\Ride6\\Saved\\SaveGames</code><br><br>"
            "Use <b>variable paths</b> (<code>%localappdata%</code>, <code>%userprofile%</code>, "
            "<code>%userprofile%\\Saved Games</code>, ...) — that's the only way the system recognizes "
            "and maps the folder across Windows/Linux.<br>"
            "If you're not sure about the exact path, use <b>Browse to Folder</b> instead."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Example: %localappdata%\\Librarian\\Saved\\SaveGames")
        layout.addWidget(self.path_input)

        tip = QLabel(
            "💡 Launch the game at least once and save your progress before selecting a path. "
            "This creates the necessary folder structure, making it easier to identify the correct location."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #8a7fa4; font-size: 11px; padding: 4px 0;")
        layout.addWidget(tip)

        link_btn = QPushButton("Search save path online")
        link_btn.clicked.connect(self.open_search)
        layout.addWidget(link_btn)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.on_ok)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def open_search(self):
        query = quote(f"{self.game_title} save files location windows")
        QDesktopServices.openUrl(QUrl(f"https://www.google.com/search?q={query}"))

    def on_ok(self):
        value = self.path_input.text().strip()
        if not value:
            QMessageBox.warning(self, "Missing path", "Please paste a save path before proceeding.")
            return
        self.chosen_path = value
        self.accept()

