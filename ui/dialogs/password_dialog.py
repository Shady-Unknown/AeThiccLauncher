from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QLineEdit

class PasswordDialog(QDialog):
    def __init__(self, game_title, source_url, theme_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Password Required")
        self.setMinimumWidth(450)
        self.password = ""
        
        # Tema ereditato dall'app
        self.setStyleSheet(f"""
            QDialog {{ background-color: {theme_config['background_dark_purple']}; color: {theme_config['text_primary_white']}; }}
            QLabel {{ color: {theme_config['text_primary_white']}; font-size: 13px; }}
            QLineEdit {{ background-color: {theme_config['input_bg_light_pink']}; color: #1a0f2b; padding: 6px; border-radius: 4px; font-weight: bold; }}
            QPushButton {{ background-color: {theme_config['main_accent_color']}; color: white; padding: 8px 16px; border-radius: 4px; font-weight: bold; }}
            QPushButton:hover {{ background-color: {theme_config['bright_neon_accent']}; }}
            a {{ color: {theme_config['neon_gradient_end']}; text-decoration: none; font-weight: bold; }}
            a:hover {{ text-decoration: underline; }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        
        lbl_icon = QLabel("🔒")
        lbl_icon.setStyleSheet("font-size: 42px; background: transparent;")
        lbl_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_icon)
        
        lbl = QLabel(f"The archive for <b>{game_title}</b> is password protected.")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl)
        
        url_to_show = source_url if source_url and source_url != 'N/A' else "https://steamrip.com"
        
        lbl_info = QLabel(f"You can usually find the password on the game's page on SteamRip:<br><br><div align='center'><a href='{url_to_show}'>{url_to_show}</a></div><br><span style='color: {theme_config['text_muted']}; font-size: 11px;'>(Hint: the password is often <b>steamrip.com</b>)</span>")
        lbl_info.setOpenExternalLinks(True)
        lbl_info.setWordWrap(True)
        lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_info)
        
        self.pwd_input = QLineEdit()
        self.pwd_input.setPlaceholderText("Enter password here...")
        layout.addWidget(self.pwd_input)
        
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet(f"background-color: transparent; border: 1px solid {theme_config['text_muted']}; color: {theme_config['text_muted']};")
        btn_cancel.clicked.connect(self.reject)
        
        btn_ok = QPushButton("Extract")
        btn_ok.clicked.connect(self.on_ok)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        
    def on_ok(self):
        self.password = self.pwd_input.text().strip()
        self.accept()

# --- ASYNCHRONOUS STORE LINK FETCHER ---
