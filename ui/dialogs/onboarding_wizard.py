"""First-run setup wizard.

Guides a new user through the three choices that matter:

1. where the writable data/cache root lives
2. which game libraries to manage (one is marked as default)
3. temp/download and savegame folders

It also offers to copy existing data from the legacy platform directory
(``%LOCALAPPDATA%/AeThiccLauncher`` on Windows, the XDG data dir on Linux,
inside the Flatpak sandbox when applicable).

The wizard never touches Qt from a worker thread and never assumes it can
write next to the executable: on Flatpak ``/app`` is read-only, so the data
root defaults to the sandbox data dir and the bootstrap file falls back to it.

Cross-platform notes:
- drive enumeration uses ``psutil.disk_partitions`` with a fallback list that
  also works inside a sandbox (``/``, ``$HOME``, ``/mnt``, ``/media``, ``/run/media``);
- every destination is validated by creating and deleting a probe file, which
  is reliable on Linux even when ``os.access`` is misleading.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import psutil
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from core import config as style_settings
from core import secrets
from core.paths import PathManager
from ui.widgets.api_keys_form import ApiKeysForm

WIN = sys.platform == "win32"


# ── helpers ───────────────────────────────────────────────────────────

def _is_writable(path) -> bool:
    try:
        target = Path(path)
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".aethicc_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fmt_bytes(value: int) -> str:
    value = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _free_space(path) -> str:
    try:
        return _fmt_bytes(shutil.disk_usage(str(path)).free)
    except OSError:
        return "?"


def _candidate_roots() -> list[str]:
    roots: list[str] = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        parts = []
    for part in parts:
        mount = part.mountpoint
        if not mount:
            continue
        if "cdrom" in (part.opts or "") or part.fstype in ("", "squashfs"):
            continue
        if os.path.isdir(mount):
            roots.append(mount)
    # Sandbox / Flatpak fallbacks: the physical list can be incomplete.
    for extra in ("/", str(Path.home()), "/mnt", "/media", "/run/media"):
        if os.path.isdir(extra):
            roots.append(extra)
    # Windows system dirs are never useful as a library root.
    deduped = list(dict.fromkeys(roots))
    if WIN:
        deduped = [r for r in deduped if not r.lower().startswith(("c:\\windows",))]
    return deduped


def default_data_root() -> str:
    """Default to the historical per-user folder; the user may move it."""
    if PathManager.has_custom_root():
        # A bootstrap/env already chose a root (portable install or a previous run).
        return str(PathManager.get_data_root())
    return str(PathManager.get_legacy_root())


def default_library_path(*_args) -> str:
    """Default library: [system drive]\\SteamRip on Windows, ~/SteamRip elsewhere."""
    if WIN:
        drive = os.environ.get("SystemDrive") or "C:"
        return str(Path(drive + "\\") / "SteamRip")
    return str(Path.home() / "SteamRip")


# ── pages ─────────────────────────────────────────────────────────────

class _Page(QWizardPage):
    def __init__(self, wizard: "OnboardingWizard"):
        super().__init__()
        self.wiz = wizard


class _DataRootPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Data & cache folder")
        self.setSubTitle("Where the launcher keeps config, caches, stats and logs.")
        layout = QVBoxLayout(self)
        intro = QLabel(
            "By default this is your user AppData folder, outside the application "
            "folder: uninstalling the launcher never deletes it. You can keep it "
            "here or move it to another drive (for example the same drive as your "
            "games). Everything the launcher writes at runtime will live here."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.txt = QLineEdit()
        self.txt.setText(wizard._data_root)
        self.txt.textChanged.connect(self._on_changed)
        row.addWidget(self.txt, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        layout.addStretch(1)
        self._on_changed()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select data folder", self.txt.text())
        if folder:
            self.txt.setText(folder)

    def _on_changed(self):
        path = self.txt.text().strip()
        if not path:
            self.info.setText("Choose a folder.")
            self.completeChanged.emit()
            return
        if os.path.isdir(path):
            self.info.setText(f"Free space: <b>{_free_space(path)}</b>")
        else:
            parent = os.path.dirname(path) or path
            self.info.setText(f"Will be created. Free space: <b>{_free_space(parent)}</b>")
        self.completeChanged.emit()

    def isComplete(self):
        # Do not touch the filesystem on every keystroke; validatePage does the
        # writability probe once, when the user moves on.
        return bool(self.txt.text().strip())

    def validatePage(self):
        path = self.txt.text().strip()
        if not _is_writable(path):
            QMessageBox.warning(self, "Folder not writable",
                                f"Cannot write to:\n{path}\n\nChoose another folder.")
            return False
        self.wiz._data_root = path
        return True


class _LibrariesPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Game libraries")
        self.setSubTitle(
            f"Folders where games are installed and scanned. Default: {default_library_path()}"
        )
        layout = QVBoxLayout(self)

        self.list = QListWidget()
        layout.addWidget(self.list)

        buttons = QHBoxLayout()
        btn_existing = QPushButton("Add existing…")
        btn_existing.clicked.connect(self._add_existing)
        btn_create = QPushButton("Create on drive…")
        btn_create.clicked.connect(self._create_on_drive)
        btn_default = QPushButton("Set as default")
        btn_default.clicked.connect(self._set_default)
        btn_remove = QPushButton("Remove")
        btn_remove.clicked.connect(self._remove)
        for btn in (btn_existing, btn_create, btn_default, btn_remove):
            buttons.addWidget(btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        drive_row = QHBoxLayout()
        drive_row.addWidget(QLabel("Drive:"))
        self.combo = QComboBox()
        for root in _candidate_roots():
            self.combo.addItem(f"{root} — {_free_space(root)} free", root)
        drive_row.addWidget(self.combo, 1)
        layout.addLayout(drive_row)

        self._refresh()

    # ── actions ──

    def _add_existing(self):
        folder = QFileDialog.getExistingDirectory(self, "Add existing library")
        if not folder:
            return
        self.wiz._add_library(folder)
        self._refresh()

    def _create_on_drive(self):
        root = self.combo.currentData() or self.combo.currentText()
        if not root:
            return
        name, ok = QInputDialog.getText(
            self, "New library", "Library folder name:", text="AeThiccLibrary"
        )
        if not ok or not name.strip():
            return
        target = os.path.join(root, name.strip())
        if not _is_writable(target):
            QMessageBox.warning(self, "Not writable",
                                f"Cannot create the library in:\n{target}")
            return
        self.wiz._add_library(target)
        self._refresh()

    def _current_path(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _set_default(self):
        path = self._current_path()
        if path:
            self.wiz._default_library = path
            self._refresh()

    def _remove(self):
        path = self._current_path()
        if not path:
            return
        self.wiz._libraries = [p for p in self.wiz._libraries if p != path]
        if self.wiz._default_library == path:
            self.wiz._default_library = self.wiz._libraries[0] if self.wiz._libraries else ""
        self._refresh()

    def _refresh(self):
        self.list.clear()
        for path in self.wiz._libraries:
            star = "★ " if path == self.wiz._default_library else "   "
            item = QListWidgetItem(f"{star}{path}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list.addItem(item)
        self.completeChanged.emit()

    def isComplete(self):
        # Avoid filesystem side effects while the user navigates; validatePage
        # performs the writability probe (and creates the folders).
        return bool(self.wiz._libraries)

    def validatePage(self):
        bad = [p for p in self.wiz._libraries if not _is_writable(p)]
        if not self.wiz._libraries:
            QMessageBox.warning(self, "No library", "Add at least one game library.")
            return False
        if bad:
            QMessageBox.warning(self, "Not writable",
                                "These libraries are not writable:\n" + "\n".join(bad))
            return False
        return True


class _MetadataPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Metadata & artwork (optional)")
        self.setSubTitle(
            "Optional API keys for covers and discovery. You can skip this page."
        )
        layout = QVBoxLayout(self)
        self.form = ApiKeysForm(self)
        layout.addWidget(self.form)

    def validatePage(self):
        ok, message = self.form.validate()
        if not ok:
            QMessageBox.warning(self, "API keys", message)
            return False
        return True


class _FoldersPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Downloads & savegames")
        self.setSubTitle("Temporary/download folder and savegame sync folder.")
        layout = QFormLayout(self)

        self.txt_temp = QLineEdit()
        self.txt_temp.textChanged.connect(lambda *_: self.completeChanged.emit())
        layout.addRow("Temp / download folder:", self._with_browse(self.txt_temp, "temp"))

        self.txt_saves = QLineEdit()
        self.txt_saves.textChanged.connect(lambda *_: self.completeChanged.emit())
        layout.addRow("Savegames sync folder:", self._with_browse(self.txt_saves, "saves"))

        hint = QLabel(
            "The temp folder holds downloads before extraction; the savegames "
            "folder keeps the last synced snapshots."
        )
        hint.setWordWrap(True)
        layout.addRow(hint)

    def _with_browse(self, line: QLineEdit, kind: str) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(line, 1)
        btn = QPushButton("Browse…")
        btn.clicked.connect(lambda: self._browse(line, kind))
        row.addWidget(btn)
        return container

    def _browse(self, line, kind):
        folder = QFileDialog.getExistingDirectory(self, "Select folder", line.text())
        if folder:
            line.setText(folder)

    def initializePage(self):
        root = Path(self.wiz._data_root)
        if not self.txt_temp.text():
            self.txt_temp.setText(str(root / "Temp"))
        if not self.txt_saves.text():
            self.txt_saves.setText(str(root / "Savegames"))

    def isComplete(self):
        return bool(self.txt_temp.text().strip()) and bool(self.txt_saves.text().strip())

    def validatePage(self):
        for label, value in (("temp", self.txt_temp.text()), ("savegames", self.txt_saves.text())):
            if not _is_writable(value.strip()):
                QMessageBox.warning(self, "Not writable",
                                    f"The {label} folder is not writable:\n{value}")
                return False
        self.wiz._temp = self.txt_temp.text().strip()
        self.wiz._saves = self.txt_saves.text().strip()
        return True


class _MigrationPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Import existing data")
        self.setSubTitle("Optionally copy data from the previous location.")
        layout = QVBoxLayout(self)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.boxes: dict[str, QCheckBox] = {}
        self._built = False

    def _items(self):
        legacy = PathManager.get_legacy_root()
        return [
            ("local_dbs", legacy / "local_dbs", "Database & catalog", True),
            ("stats", legacy / "stats", "Stats & history", True),
            ("cache", PathManager.get_legacy_cache_dir(), "Covers & downloads", False),
            ("minicovers", legacy / "minicovers_cache", "Mini covers", False),
            ("avatars", legacy / "avatars", "Steam avatars", False),
            ("data", legacy / "data", "User data overrides", False),
            ("discovery", legacy / "discovery_cache.json", "Discovery cache", True),
            ("igdb", legacy / "igdb_details.json", "IGDB details", True),
            ("trailer", legacy / "trailer_cache.json", "Trailer cache", False),
        ]

    def initializePage(self):
        legacy = PathManager.get_legacy_root()
        new_root = Path(self.wiz._data_root).resolve()
        if not legacy.exists() or str(legacy.resolve()) == str(new_root):
            self.intro.setText("No previous data to import — nothing to do here.")
            return
        self.intro.setText(
            "Existing data found in<br><b>%s</b><br><br>"
            "Select what to copy into the new data folder. Nothing is deleted."
            % legacy
        )
        if self._built:
            return
        layout = self.layout()
        for key, path, label, default in self._items():
            if not path.exists():
                continue
            box = QCheckBox(f"{label}  ({path})")
            box.setChecked(default)
            self.boxes[key] = box
            layout.addWidget(box)
        layout.addStretch(1)
        self._built = True

    def selected(self):
        legacy = PathManager.get_legacy_root()
        new_root = Path(self.wiz._data_root).resolve()
        if not legacy.exists() or str(legacy.resolve()) == str(new_root):
            return []
        chosen = [(k, p) for k, p, _l, _d in self._items()
                  if k in self.boxes and self.boxes[k].isChecked()]
        return chosen


class _SummaryPage(_Page):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.setTitle("Ready")
        self.setSubTitle("Review your choices.")
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.summary)
        layout.addStretch(1)

    def initializePage(self):
        libs = "<br>".join(f"&nbsp;&nbsp;• {p}" for p in self.wiz._libraries)
        default = self.wiz._default_library or (self.wiz._libraries[0] if self.wiz._libraries else "—")
        sgdb = "configured" if secrets.has_sgdb_key() else "not configured (optional)"
        igdb = "configured" if secrets.has_igdb_credentials() else "not configured (optional)"
        self.summary.setText(
            f"<b>Data &amp; cache:</b><br>&nbsp;&nbsp;{self.wiz._data_root}<br><br>"
            f"<b>Libraries:</b><br>{libs}<br>"
            f"<b>Default library:</b> {default}<br><br>"
            f"<b>Temp folder:</b><br>&nbsp;&nbsp;{self.wiz._temp}<br><br>"
            f"<b>Savegames folder:</b><br>&nbsp;&nbsp;{self.wiz._saves}<br><br>"
            f"<b>SteamGridDB key:</b> {sgdb}<br>"
            f"<b>IGDB credentials:</b> {igdb}"
        )


# ── wizard ────────────────────────────────────────────────────────────

class OnboardingWizard(QWizard):
    def __init__(self, app_config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ae Thicc Launcher — Setup")
        self.resize(780, 580)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        config = app_config or {}
        self.config = config
        theme = config.get("theme") or style_settings.DEFAULT_PURPLE_RIP_THEME
        try:
            self.setStyleSheet(style_settings.get_stylesheet(theme))
        except Exception:
            pass

        self._libraries = [p for p in (config.get("watched_folders") or []) if p]
        self._default_library = config.get("default_library") or (self._libraries[0] if self._libraries else "")
        self._data_root = default_data_root()
        self._temp = config.get("default_temp_folder") or ""
        self._saves = (config.get("savegames") or {}).get("sync_folder") or ""
        self.data_root_changed = False

        self._page_root = _DataRootPage(self)
        self._page_libs = _LibrariesPage(self)
        self._page_metadata = _MetadataPage(self)
        self._page_folders = _FoldersPage(self)
        self._page_migration = _MigrationPage(self)
        self._page_summary = _SummaryPage(self)

        self.addPage(self._page_root)
        self.addPage(self._page_libs)
        self.addPage(self._page_metadata)
        self.addPage(self._page_folders)
        self.addPage(self._page_migration)
        self.addPage(self._page_summary)

        self.setButtonText(QWizard.WizardButton.FinishButton, "Finish & save")

        # A brand-new user must end up with at least one library; pre-seed a
        # sensible default so the wizard is usable with a single click.
        if not self._libraries:
            self._add_library(default_library_path(), refresh=False)

    # ── model helpers ──

    def _add_library(self, path, refresh=True):
        path = str(Path(path))
        if path not in self._libraries:
            self._libraries.append(path)
        if not self._default_library:
            self._default_library = path
        if refresh and hasattr(self, "_page_libs"):
            self._page_libs._refresh()

    # ── apply ──

    def accept(self):
        try:
            self._apply()
        except Exception as exc:
            QMessageBox.critical(self, "Setup failed", f"Could not save the setup:\n{exc}")
            return
        super().accept()

    def _apply(self):
        from core import config as style_settings

        if hasattr(self, "_page_metadata"):
            ok, message = self._page_metadata.form.apply()
            if not ok:
                raise ValueError(message)

        old_root = PathManager.get_data_root().resolve()
        new_root = Path(self._data_root).resolve()
        self.data_root_changed = str(old_root) != str(new_root)

        if self.data_root_changed:
            PathManager.set_data_root(new_root)
        else:
            PathManager.initialize()

        # Migration (copy, never move) — before writing the new config.
        chosen = self._page_migration.selected()
        if chosen:
            self._migrate(chosen, new_root)

        config = self.config
        config["watched_folders"] = list(self._libraries)
        config["default_library"] = self._default_library or (
            self._libraries[0] if self._libraries else ""
        )
        config["default_temp_folder"] = self._temp
        config.setdefault("savegames", {})
        config["savegames"]["sync_folder"] = self._saves
        config["onboarding"] = {"completed": True, "version": 1}
        themes = config.get("themes") or {}
        current = config.get("current_theme")
        if current in themes:
            config["theme"] = themes[current]
        style_settings.save_config(config)

    def _migrate(self, chosen, new_root: Path):
        progress = QProgressDialog("Copying existing data…", None, 0, len(chosen), self)
        progress.setWindowTitle("Importing")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setCancelButton(None)
        progress.show()
        try:
            for index, (_key, source) in enumerate(chosen):
                QApplication.processEvents()
                progress.setValue(index)
                progress.setLabelText(f"Copying {source}…")
                target = new_root / source.name
                if source.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            progress.setValue(len(chosen))
        finally:
            progress.close()
