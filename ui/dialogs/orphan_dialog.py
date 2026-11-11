from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

class OrphanDialog(QDialog):
    def __init__(self, folder_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Missing Metadata")
        self.choice = "Cancel"

        theme = {}
        if parent is not None and hasattr(parent, "app_config"):
            theme = parent.app_config.get("theme", {})
        pink = theme.get('text_secondary_pink', '#f3c6ff')
        green = theme.get('status_green', '#00ff88')

        layout = QVBoxLayout(self)
        lbl = QLabel(f"Folder <b>{folder_name}</b> does not have the <code>steamrip_meta.json</code> file.<br>What do you want to do?")
        layout.addWidget(lbl)

        lbl_warn = QLabel("<b>⚠️ WARNING:</b> GAME VERSION IS TAKEN FROM URL.<br>If you downloaded this game before now, either input the right version in the <i>steamrip_meta.json</i> file manually, or re-download the game if unsure of what version you have. Next update it will be automatically checked against the web version and you will be notified if it's outdated.")
        lbl_warn.setStyleSheet("color: #ff9900; font-size: 11px;")
        lbl_warn.setWordWrap(True)
        layout.addWidget(lbl_warn)

        lbl_local = QLabel(
            f"💜 <b>Add as Local Only</b>: for games downloaded from external sources <b>other than SteamRip</b> "
            f"(GOG, itch.io, physical copy, etc.). No update checks will be performed, but you still get the "
            f"local game card, save-game scanning and Steam integration."
        )
        lbl_local.setStyleSheet(f"color: {pink}; font-size: 11px;")
        lbl_local.setWordWrap(True)
        layout.addWidget(lbl_local)

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Ignore")
        btn_cancel.clicked.connect(self.reject)

        btn_dl = QPushButton("Re-download")
        btn_dl.clicked.connect(lambda: self.make_choice("Download"))

        btn_local = QPushButton("Add as Local Only")
        btn_local.setStyleSheet(f"background-color: {pink}; color: #000;")
        btn_local.clicked.connect(lambda: self.make_choice("Local"))

        btn_auto = QPushButton("Auto-Populate (Create JSON)")
        btn_auto.setStyleSheet(f"background-color: {green}; color: #000;")
        btn_auto.clicked.connect(lambda: self.make_choice("Populate"))

        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_dl)
        btn_layout.addWidget(btn_local)
        btn_layout.addWidget(btn_auto)
        layout.addLayout(btn_layout)

    def make_choice(self, choice):
        self.choice = choice
        self.accept()

# --- DIALOG FOR LINUX WRAPPER ---
