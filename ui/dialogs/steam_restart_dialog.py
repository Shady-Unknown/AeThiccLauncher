from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

class SteamRestartDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Steam Restart Required")
        self.restart_now = True
        self.resize(500, 250)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)

        lbl_title = QLabel("<h2>Steam Restart Required</h2>")
        layout.addWidget(lbl_title)

        lbl_warn = QLabel(
            "To apply the library changes, Steam needs to be restarted.<br><br>"
            "Choose <b>Kill &amp; Restart Now</b> to complete the integration immediately. "
            "Steam will be safely closed, the new shortcut added, and Steam restarted automatically.<br><br>"
            "Choose <b>Wait</b> if you're downloading something or want to add more games first. "
            "The shortcut will still be added &mdash; just restart Steam later to see it."
        )
        lbl_warn.setWordWrap(True)
        lbl_warn.setStyleSheet(
            "font-size: 13px; color: #e0e0e0; "
            "background-color: rgba(0, 255, 136, 0.1); "
            "padding: 10px; border: 1px solid #00ff88; border-radius: 5px;"
        )
        layout.addWidget(lbl_warn)

        btn_layout = QHBoxLayout()

        self.btn_restart = QPushButton("Kill && Restart Steam Now (Recommended)")
        self.btn_restart.setStyleSheet(
            "QPushButton { background-color: #2a475e; color: white; border: 1px solid #66c0f4; padding: 8px 16px; }"
            "QPushButton:hover { background-color: #3b6d8a; }"
        )
        self.btn_restart.clicked.connect(lambda: self.make_choice(True))

        self.btn_wait = QPushButton("Wait, I'll Restart Later")
        self.btn_wait.setStyleSheet(
            "QPushButton { background-color: transparent; color: #8a7fa4; border: 1px solid #8a7fa4; padding: 8px 16px; }"
            "QPushButton:hover { color: white; border: 1px solid white; }"
        )
        self.btn_wait.clicked.connect(lambda: self.make_choice(False))

        btn_layout.addWidget(self.btn_restart)
        btn_layout.addWidget(self.btn_wait)

        layout.addLayout(btn_layout)

    def make_choice(self, restart_now):
        self.restart_now = restart_now
        self.accept()
