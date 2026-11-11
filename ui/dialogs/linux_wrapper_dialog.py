from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

class LinuxWrapperDialog(QDialog):
    def __init__(self, game_title, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Linux Launcher Setup")
        self.choice = None
        self.resize(500, 250)
        
        layout = QVBoxLayout(self)
        
        lbl_title = QLabel(f"<h2>Linux Setup for {game_title}</h2>")
        layout.addWidget(lbl_title)
        
        lbl_warn = QLabel(
            "To launch this game on Linux, it is highly recommended to integrate it into your Steam Library as a Non-Steam game.<br><br>"
            "This method leverages Proton compatibility layers, which offer superior performance and stability. "
            "<b style='color:#ff00a0;'>Note:</b> Games requiring external launchers (Microsoft, Rockstar, etc.) are best handled via Steam's ecosystem."
        )
        lbl_warn.setWordWrap(True)
        lbl_warn.setStyleSheet("font-size: 13px; color: #e0e0e0; background-color: rgba(255,0,160,0.1); padding: 10px; border: 1px solid #ff00a0; border-radius: 5px;")
        layout.addWidget(lbl_warn)
        
        btn_layout = QHBoxLayout()
        
        self.btn_steam = QPushButton("Automatic Steam Integration (Recommended)")
        self.btn_steam.setStyleSheet("background-color: #2a475e; color: white; border: 1px solid #66c0f4;")
        self.btn_steam.clicked.connect(lambda: self.make_choice("steam"))
        
        btn_layout.addWidget(self.btn_steam)
        
        layout.addLayout(btn_layout)
        
    def make_choice(self, choice):
        self.choice = choice
        self.accept()

# --- CUSTOM WIDGET: PROFILE CARD ---
