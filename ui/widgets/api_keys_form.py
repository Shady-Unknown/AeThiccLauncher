"""Reusable API-key form for onboarding and Settings.

Shows SteamGridDB and IGDB (Twitch) credential fields, a test button per
service, remove actions and a status line. Existing secrets are never shown:
fields stay empty with a "configured" placeholder, and leaving them empty
keeps the stored value.
"""
from __future__ import annotations

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core import secrets


class _SecretTestWorker(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, kind, values, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._values = values

    def run(self):
        if self._kind == "sgdb":
            ok, message = secrets.validate_sgdb_key(self._values[0])
        else:
            ok, message = secrets.validate_igdb_credentials(*self._values)
        self.done.emit(bool(ok), message)


class ApiKeysForm(QWidget):
    """Signal-free form; callers use load/validate/apply."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_worker = None
        self._build_ui()
        self.load()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        intro = QLabel(
            "Both services are optional. Keys are stored in your system keychain "
            "(or an encrypted fallback file) and never written to config.json."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #9c9c9c; font-size: 11px;")
        root.addWidget(intro)

        root.addWidget(self._build_sgdb_group())
        root.addWidget(self._build_igdb_group())
        root.addStretch(1)

    def _build_sgdb_group(self):
        box = QGroupBox("SteamGridDB (covers, grids, heroes, logos)")
        form = QFormLayout(box)

        row = QHBoxLayout()
        self.sgdb_edit = QLineEdit()
        self.sgdb_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.sgdb_edit.setPlaceholderText("Paste your SteamGridDB API key")
        row.addWidget(self.sgdb_edit, 1)

        self.sgdb_show = QCheckBox("Show")
        self.sgdb_show.toggled.connect(
            lambda checked: self.sgdb_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        row.addWidget(self.sgdb_show)

        self.sgdb_test_btn = QPushButton("Test")
        self.sgdb_test_btn.clicked.connect(self._test_sgdb)
        row.addWidget(self.sgdb_test_btn)

        self.sgdb_remove_btn = QPushButton("Remove")
        self.sgdb_remove_btn.clicked.connect(self._remove_sgdb)
        row.addWidget(self.sgdb_remove_btn)
        form.addRow("API key:", row)

        self.sgdb_status = QLabel("")
        self.sgdb_status.setWordWrap(True)
        form.addRow("Status:", self.sgdb_status)

        link = QLabel(
            "<a href='https://www.steamgriddb.com/profile/preferences/api'>"
            "Get a SteamGridDB API key</a>"
        )
        link.setOpenExternalLinks(True)
        link.setStyleSheet("font-size: 11px;")
        form.addRow("", link)
        return box

    def _build_igdb_group(self):
        box = QGroupBox("IGDB / Twitch (Trending and New Releases)")
        form = QFormLayout(box)

        self.igdb_id_edit = QLineEdit()
        self.igdb_id_edit.setPlaceholderText("Twitch client ID")
        form.addRow("Client ID:", self.igdb_id_edit)

        secret_row = QHBoxLayout()
        self.igdb_secret_edit = QLineEdit()
        self.igdb_secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.igdb_secret_edit.setPlaceholderText("Twitch client secret")
        secret_row.addWidget(self.igdb_secret_edit, 1)

        self.igdb_show = QCheckBox("Show")
        self.igdb_show.toggled.connect(
            lambda checked: self.igdb_secret_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        secret_row.addWidget(self.igdb_show)
        form.addRow("Client secret:", secret_row)

        buttons = QHBoxLayout()
        self.igdb_test_btn = QPushButton("Test")
        self.igdb_test_btn.clicked.connect(self._test_igdb)
        buttons.addWidget(self.igdb_test_btn)

        self.igdb_remove_btn = QPushButton("Remove")
        self.igdb_remove_btn.clicked.connect(self._remove_igdb)
        buttons.addWidget(self.igdb_remove_btn)
        buttons.addStretch(1)
        form.addRow("", buttons)

        self.igdb_status = QLabel("")
        self.igdb_status.setWordWrap(True)
        form.addRow("Status:", self.igdb_status)

        link = QLabel(
            "<a href='https://dev.twitch.tv/console/apps'>"
            "Create a Twitch application for IGDB</a>"
        )
        link.setOpenExternalLinks(True)
        link.setStyleSheet("font-size: 11px;")
        form.addRow("", link)
        return box

    # ── state ─────────────────────────────────────────────────────────

    def load(self):
        self.sgdb_edit.clear()
        self.igdb_id_edit.clear()
        self.igdb_secret_edit.clear()
        self.sgdb_edit.setPlaceholderText(
            "Configured — leave blank to keep"
            if secrets.has_sgdb_key()
            else "Paste your SteamGridDB API key"
        )
        igdb_configured = secrets.has_igdb_credentials()
        if igdb_configured:
            self.igdb_id_edit.setPlaceholderText("Configured — leave blank to keep")
            self.igdb_secret_edit.setPlaceholderText("Configured — leave blank to keep")
        else:
            self.igdb_id_edit.setPlaceholderText("Twitch client ID")
            self.igdb_secret_edit.setPlaceholderText("Twitch client secret")
        self._refresh_status()

    def _refresh_status(self):
        self._set_status(self.sgdb_status, self._status(secrets.SGDB_API_KEY))
        if secrets.has_igdb_credentials():
            self._set_status(self.igdb_status, "Configured")
        elif secrets.get_secret(secrets.IGDB_CLIENT_ID) or secrets.get_secret(
            secrets.IGDB_CLIENT_SECRET
        ):
            self._set_status(self.igdb_status, "Incomplete (need client ID + secret)")
        else:
            self._set_status(self.igdb_status, "Not configured")

    def _status(self, name):
        if not secrets.has_secret(name):
            return "Not configured"
        source = secrets.secret_source(name)
        return f"Configured ({source})" if source else "Configured"

    @staticmethod
    def _set_status(label, text):
        label.setText(text)
        low = text.lower()
        if low.startswith("configured"):
            label.setStyleSheet("color: #00ff88; font-size: 11px;")
        elif low.startswith("incomplete") or low.startswith("invalid"):
            label.setStyleSheet("color: #ffcc66; font-size: 11px;")
        else:
            label.setStyleSheet("color: #9c9c9c; font-size: 11px;")

    # ── validate / apply ──────────────────────────────────────────────

    def validate(self) -> tuple[bool, str]:
        cid = self.igdb_id_edit.text().strip()
        secret = self.igdb_secret_edit.text().strip()
        if not cid and not secret:
            return True, ""
        stored_id, stored_secret = secrets.get_igdb_credentials()
        if not (cid or stored_id) or not (secret or stored_secret):
            return False, "IGDB: enter both client ID and client secret."
        return True, ""

    def apply(self) -> tuple[bool, str]:
        ok, message = self.validate()
        if not ok:
            return False, message

        sgdb = self.sgdb_edit.text().strip()
        if sgdb:
            secrets.set_secret(secrets.SGDB_API_KEY, sgdb)

        cid = self.igdb_id_edit.text().strip()
        secret = self.igdb_secret_edit.text().strip()
        if cid or secret:
            stored_id, stored_secret = secrets.get_igdb_credentials()
            new_id = cid or stored_id
            new_secret = secret or stored_secret
            if new_id and new_secret:
                secrets.set_secret(secrets.IGDB_CLIENT_ID, new_id)
                secrets.set_secret(secrets.IGDB_CLIENT_SECRET, new_secret)

        self.load()
        return True, ""

    # ── test / remove ─────────────────────────────────────────────────

    def _start_test(self, kind, values, button, status_label):
        try:
            if self._test_worker is not None and self._test_worker.isRunning():
                return
        except RuntimeError:
            self._test_worker = None
        button.setEnabled(False)
        status_label.setText("Testing…")
        status_label.setStyleSheet("color: #9c9c9c; font-size: 11px;")
        worker = _SecretTestWorker(kind, values, self)
        worker.done.connect(
            lambda ok, msg: self._on_test_done(button, status_label, ok, msg)
        )
        worker.finished.connect(lambda w=worker: self._clear_test_worker(w))
        worker.finished.connect(worker.deleteLater)
        self._test_worker = worker
        worker.start()

    def _clear_test_worker(self, worker):
        if self._test_worker is worker:
            self._test_worker = None

    def _on_test_done(self, button, status_label, ok, message):
        button.setEnabled(True)
        status_label.setText(message)
        status_label.setStyleSheet(
            f"color: {'#00ff88' if ok else '#ff6666'}; font-size: 11px;"
        )

    def _test_sgdb(self):
        value = self.sgdb_edit.text().strip()
        if not value:
            value = secrets.get_sgdb_key()
        if not value:
            self._set_status(self.sgdb_status, "Not configured")
            return
        self._start_test("sgdb", (value,), self.sgdb_test_btn, self.sgdb_status)

    def _test_igdb(self):
        cid = self.igdb_id_edit.text().strip()
        secret = self.igdb_secret_edit.text().strip()
        stored_id, stored_secret = secrets.get_igdb_credentials()
        cid = cid or stored_id
        secret = secret or stored_secret
        if not cid or not secret:
            self._set_status(self.igdb_status, "Incomplete (need client ID + secret)")
            return
        self._start_test(
            "igdb", (cid, secret), self.igdb_test_btn, self.igdb_status
        )

    def _remove_sgdb(self):
        secrets.delete_secret(secrets.SGDB_API_KEY)
        self.load()

    def _remove_igdb(self):
        secrets.delete_secret(secrets.IGDB_CLIENT_ID)
        secrets.delete_secret(secrets.IGDB_CLIENT_SECRET)
        self.load()
