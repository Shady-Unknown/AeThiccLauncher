import html
from urllib.parse import quote

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout,
)

PREVIEW_COLOR_OK = "#00ff88"
PREVIEW_COLOR_NATIVE = "#e6b84a"
PREVIEW_COLOR_ERROR = "#ff6b6b"

SUPPORTED_TOKENS = (
    "<code>%localappdata%</code>, <code>%appdata%</code>, "
    "<code>%userprofile%</code>, <code>%userprofile%\\Saved Games</code>, "
    "<code>&lt;winDocuments&gt;</code>, <code>&lt;winSavedGames&gt;</code>, "
    "<code>&lt;winLocalAppDataLow&gt;</code>, <code>&lt;steam-folder&gt;</code>, "
    "<code>&lt;storeUserId&gt;</code>"
)


def format_preview(details):
    """Turn a translation result into ``(html_text, color)`` for the dialog."""
    if not isinstance(details, dict):
        return "", ""
    path = str(details.get("path") or "")
    kind = str(details.get("kind") or "")
    reason = str(details.get("reason") or "")

    if reason == "empty" or reason == "not_linux":
        return "", ""

    if path and kind == "native":
        return (
            "Native Linux path (used as-is): "
            f"<code>{html.escape(path)}</code>",
            PREVIEW_COLOR_NATIVE,
        )
    if path:
        label = "Steam" if kind == "steam" else "Proton prefix"
        suffix = "" if details.get("exists") else " — not created yet"
        return (
            f"Linux path ({label}): <code>{html.escape(path)}</code>{suffix}",
            PREVIEW_COLOR_OK if details.get("exists") else PREVIEW_COLOR_NATIVE,
        )
    if reason == "unknown_placeholder":
        token = html.escape(str(details.get("token") or "?"))
        return (
            f"Unknown placeholder <code>{token}</code>. Supported: {SUPPORTED_TOKENS}",
            PREVIEW_COLOR_ERROR,
        )
    if reason == "no_compatdata":
        return (
            "This game has no Steam appid / compatdata folder yet: install it "
            "through Steam first, then paste the path again.",
            PREVIEW_COLOR_ERROR,
        )
    if reason == "steam_userdata_not_found":
        return (
            "Steam userdata folder not found: is Steam installed for this user?",
            PREVIEW_COLOR_ERROR,
        )
    if reason == "steam_folder_not_found":
        return (
            "Steam folder not found: is Steam installed for this user?",
            PREVIEW_COLOR_ERROR,
        )
    return (
        "Path not recognised: use a Windows variable path, a Steam path or a "
        "Linux path.",
        PREVIEW_COLOR_ERROR,
    )


class ManualSavePathDialog(QDialog):
    def __init__(self, game_title, parent=None, preview=None):
        super().__init__(parent)
        self.setWindowTitle("Identify Save Path")
        self.resize(620, 320)
        self.game_title = game_title
        self.chosen_path = ""
        self._preview = preview

        layout = QVBoxLayout(self)
        lbl = QLabel(
            f"<b>{game_title}</b> was not found in the Ludusavi manifest or no save path could be inferred.<br><br>"
            "🔍 Search online, then paste the <b>Windows</b> save folder location below, "
            "for example: <code>%localappdata%\\Ride6\\Saved\\SaveGames</code><br><br>"
            "Use <b>variable paths</b> so the folder is recognised across Windows and "
            "Linux: <code>%localappdata%</code>, <code>%userprofile%</code>, "
            "<code>&lt;winDocuments&gt;</code>, <code>&lt;steam-folder&gt;\\userdata\\"
            "&lt;storeUserId&gt;\\&lt;appid&gt;\\remote</code>.<br>"
            "If you're not sure about the exact path, use <b>Browse to Folder</b> instead."
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Example: %localappdata%\\Librarian\\Saved\\SaveGames")
        layout.addWidget(self.path_input)

        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextFormat(Qt.TextFormat.RichText)
        self.preview_label.setStyleSheet("font-size: 11px; padding: 2px 0;")
        layout.addWidget(self.preview_label)

        # Typing is cheap, but the translation probes the filesystem: debounce.
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(200)
        self._preview_timer.timeout.connect(self._update_preview)
        self.path_input.textChanged.connect(lambda _text: self._preview_timer.start())

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

    def _update_preview(self):
        if self._preview is None:
            return
        try:
            details = self._preview(self.path_input.text())
        except Exception:
            text, color = format_preview({"reason": "unsupported"})
        else:
            text, color = format_preview(details)
        self.preview_label.setText(text)
        self.preview_label.setStyleSheet(
            f"color: {color}; font-size: 11px; padding: 2px 0;" if color
            else "font-size: 11px; padding: 2px 0;"
        )

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
