import copy
import re
import tempfile
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QListWidget, QStackedWidget, QVBoxLayout, QPushButton, QWidget, QFormLayout, QLineEdit, QFileDialog, QLabel, QSpinBox, QComboBox, QColorDialog, QCheckBox, QInputDialog, QMessageBox, QSlider
from core import config as style_settings
from ui.widgets.api_keys_form import ApiKeysForm

HEX_RE = re.compile(r'^#?[0-9a-fA-F]{6}$')

class SettingsDialog(QDialog):
    config_saved = pyqtSignal(dict)

    def __init__(self, current_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configuration Panel")
        self.resize(900, 640)
        self.config = copy.deepcopy(current_config)
        self.config.setdefault("themes", {})
        self.config.setdefault("current_theme", "PurpleRip")
        if self.config.get("current_theme") not in self.config["themes"]:
            self.config["themes"] = {"PurpleRip": copy.deepcopy(style_settings.DEFAULT_PURPLE_RIP_THEME)}
            self.config["current_theme"] = "PurpleRip"
        self.config["theme"] = self.config["themes"][self.config["current_theme"]]
        self._loading_theme = False
        watched = self.config.get("watched_folders") or []
        self._default_library = self.config.get("default_library") or (watched[0] if watched else "")
        self.run_wizard_after_close = False
        # Coalesce previews: re-theming the whole app is expensive and was
        # previously triggered once per colour editor (10-15 times per switch).
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self._apply_preview_now)
        self.init_ui()

    def init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(160)
        self.sidebar.addItem("General")
        self.sidebar.addItem("Theme Customization")
        self.sidebar.addItem("Game Libraries")
        self.sidebar.addItem("Savegames Manager")
        self.sidebar.addItem("API Keys")
        main_layout.addWidget(self.sidebar)

        self.pages_container = QStackedWidget()
        main_layout.addWidget(self.pages_container)

        self.create_general_page()
        self.create_theme_page()
        self.create_folders_page()
        self.create_savegames_page()
        self.create_api_keys_page()

        self.sidebar.currentRowChanged.connect(self.pages_container.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        right_side_layout = QVBoxLayout()
        right_side_layout.addWidget(self.pages_container)
        
        btn_layout = QHBoxLayout()
        btn_reset = QPushButton("Reset Defaults")
        btn_reset.clicked.connect(self.reset_defaults)
        btn_save = QPushButton("Save & Apply All")
        btn_save.clicked.connect(self.save_and_close)
        btn_layout.addWidget(btn_reset)
        btn_layout.addWidget(btn_save)
        right_side_layout.addLayout(btn_layout)
        
        main_layout.addLayout(right_side_layout)
        
        # Combiniamo lo stile globale con quello specifico del pannello settings
        base_style = style_settings.get_stylesheet(self.config.get("theme", {}))
        self.setStyleSheet(base_style + """
            QListWidget { background-color: #2b1a42; border: 1px solid #733d91; border-radius: 6px; }
            QListWidget::item { padding: 10px; color: white; }
            QListWidget::item:selected { background-color: #733d91; border-radius: 4px; }
        """)

    def create_general_page(self):
        page = QWidget()
        layout = QFormLayout(page)
        self.txt_temp = QLineEdit()
        self.txt_temp.setText(self.config.get("default_temp_folder", ""))
        self.txt_temp.setPlaceholderText(tempfile.gettempdir() + " (System Default)")
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.browse_temp_folder)
        row_layout = QHBoxLayout()
        row_layout.addWidget(self.txt_temp)
        row_layout.addWidget(btn_browse)
        layout.addRow("Temporary Cache Folder:", row_layout)

        # --- DATA ROOT (config/cache/stats) ---
        from core.paths import PathManager
        data_root = PathManager.get_data_root()
        data_row = QHBoxLayout()
        lbl_data = QLabel(str(data_root))
        lbl_data.setWordWrap(True)
        lbl_data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_row.addWidget(lbl_data, 1)
        btn_open_data = QPushButton("Open")
        btn_open_data.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_root)))
        )
        data_row.addWidget(btn_open_data)
        layout.addRow("Data & cache folder:", data_row)

        btn_wizard = QPushButton("Run setup wizard…")
        btn_wizard.clicked.connect(self.request_setup_wizard)
        layout.addRow(btn_wizard)

        # --- PLAY TIME (HOUR COUNTER) ---
        playtime_cfg = self.config.get('playtime', {})
        self.combo_playtime_display = QComboBox()
        self.combo_playtime_display.addItem("Hours (e.g. 12.4h)", "hours")
        self.combo_playtime_display.addItem("Hours & Minutes (e.g. 12h 24m)", "timestamp")
        current_display = playtime_cfg.get('display', 'hours')
        idx = self.combo_playtime_display.findData(current_display)
        self.combo_playtime_display.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addRow("Play Time Display:", self.combo_playtime_display)

        self.chk_playtime_auto_sync = QCheckBox("Automatically sync play time with Steam (keep the maximum)")
        self.chk_playtime_auto_sync.setChecked(playtime_cfg.get('auto_sync', True))
        layout.addRow(self.chk_playtime_auto_sync)

        help_playtime = QLabel(
            "Play time is tracked for every launched session. Steam writes are deferred "
            "until Steam is closed, so the value is never overwritten."
        )
        help_playtime.setWordWrap(True)
        layout.addRow(help_playtime)

        # --- EXPLORER BACKGROUND BLUR ---
        ui_cfg = self.config.setdefault("ui", {})
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(0, 100)
        self.slider_blur.setSingleStep(5)
        self.slider_blur.setPageStep(10)
        self.slider_blur.setValue(int(ui_cfg.get("explorer_blur", 0)))
        self.slider_blur.setToolTip(
            "0% = sharp background, 100% = very soft (a colored blob)."
        )
        self.lbl_blur_value = QLabel(f"{self.slider_blur.value()}%")
        self.lbl_blur_value.setMinimumWidth(44)
        self.lbl_blur_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        blur_row = QHBoxLayout()
        blur_row.addWidget(self.slider_blur, 1)
        blur_row.addWidget(self.lbl_blur_value)
        layout.addRow("Explorer background blur:", blur_row)

        help_blur = QLabel(
            "How soft the blurred game art behind the Explorer panels is. "
            "Changes are previewed live."
        )
        help_blur.setWordWrap(True)
        layout.addRow(help_blur)
        self.slider_blur.valueChanged.connect(self._on_blur_changed)

        self.pages_container.addWidget(page)

    def _on_blur_changed(self, value):
        self.lbl_blur_value.setText(f"{value}%")
        self.config.setdefault("ui", {})["explorer_blur"] = int(value)
        parent = self.parent()
        tab = getattr(parent, "tab_explorer", None)
        if tab is not None:
            tab.set_blur_percent(value)

    def browse_temp_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Temporary Folder",
            options=QFileDialog.Option.DontUseNativeDialog)
        if folder: self.txt_temp.setText(folder)

    def request_setup_wizard(self):
        """Close the dialog and let the main window run the onboarding wizard."""
        self.run_wizard_after_close = True
        self.done(QDialog.DialogCode.Accepted)

    def create_theme_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(3)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Toolbar: selezione tema + azioni globali ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(self.config.get("themes", {}).keys()))
        self.theme_combo.setCurrentText(self.config.get("current_theme", ""))
        toolbar.addWidget(self.theme_combo, 1)

        btn_duplicate = QPushButton("Duplicate")
        btn_duplicate.clicked.connect(self.duplicate_theme)
        toolbar.addWidget(btn_duplicate)

        btn_delete = QPushButton("Delete")
        btn_delete.clicked.connect(self.delete_theme)
        toolbar.addWidget(btn_delete)

        btn_revert = QPushButton("Revert Theme")
        btn_revert.clicked.connect(self.revert_theme)
        toolbar.addWidget(btn_revert)

        layout.addLayout(toolbar)

        # --- Righe colore compatte ---
        form = QFormLayout()
        form.setSpacing(2)
        form.setVerticalSpacing(2)
        form.setContentsMargins(2, 2, 2, 2)
        self.color_editors = {}
        for key in style_settings.DEFAULT_PURPLE_RIP_THEME:
            row = QHBoxLayout()
            row.setSpacing(3)
            row.setContentsMargins(0, 0, 0, 0)

            swatch = QPushButton()
            swatch.setFixedSize(28, 18)
            swatch.setStyleSheet(f"background-color: {self._current_colors()[key]}; border: 1px solid #888; border-radius: 3px; padding: 0;")
            swatch.setToolTip("Click to pick a color")
            swatch.clicked.connect(lambda checked, k=key: self.pick_color(k))

            hex_edit = QLineEdit(self._current_colors()[key])
            hex_edit.setFixedWidth(85)
            hex_edit.setMaxLength(7)
            hex_edit.setStyleSheet("padding: 1px 4px; font-weight: bold;")
            hex_edit.textChanged.connect(lambda text, k=key: self.on_hex_edited(k, text))

            btn_reset = QLabel(
                f"<a href='reset' style='color: {self.config['theme'].get('text_secondary_pink', '#f3c6ff')}; text-decoration: none; font-size: 14px;'>↺</a>"
            )
            btn_reset.setToolTip("Reset this color to default")
            btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_reset.linkActivated.connect(lambda url, k=key: self.reset_color(k))

            row.addWidget(swatch)
            row.addWidget(hex_edit)
            row.addWidget(btn_reset)
            row.addStretch(1)

            lbl_name = QLabel(key.replace('_', ' ').title())
            lbl_name.setMinimumWidth(170)
            form.addRow(lbl_name, row)
            self.color_editors[key] = {"swatch": swatch, "hex": hex_edit, "reset": btn_reset}

        layout.addLayout(form)
        layout.addStretch(1)

        self.theme_combo.currentTextChanged.connect(self.select_theme)
        self.pages_container.addWidget(page)

    def create_folders_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("<b>Game Libraries:</b> folders scanned for installed games "
                                "and offered as install destinations."))
        self.folder_list = QListWidget()
        layout.addWidget(self.folder_list)
        btn_folder_layout = QHBoxLayout()
        btn_add = QPushButton("Add Folder")
        btn_add.clicked.connect(self.add_folder)
        btn_remove = QPushButton("Remove Selected")
        btn_remove.clicked.connect(self.remove_folder)
        btn_default = QPushButton("Set as Default")
        btn_default.clicked.connect(self.set_default_folder)
        btn_folder_layout.addWidget(btn_add)
        btn_folder_layout.addWidget(btn_remove)
        btn_folder_layout.addWidget(btn_default)
        layout.addLayout(btn_folder_layout)
        self._refresh_folder_list()
        self.pages_container.addWidget(page)

    def _refresh_folder_list(self):
        if not hasattr(self, "folder_list"):
            return
        current = self.folder_list.currentItem()
        current_path = current.text().lstrip("★ ").strip() if current else None
        self.folder_list.clear()
        for folder in self.config.get("watched_folders", []):
            star = "★ " if folder == self._default_library else "   "
            self.folder_list.addItem(f"{star}{folder}")
            if folder == current_path:
                self.folder_list.setCurrentRow(self.folder_list.count() - 1)

    def _folder_paths(self):
        return [self.folder_list.item(i).text().lstrip("★ ").strip()
                for i in range(self.folder_list.count())]

    def set_default_folder(self):
        item = self.folder_list.currentItem()
        if not item:
            return
        self._default_library = item.text().lstrip("★ ").strip()
        self._refresh_folder_list()

    def create_savegames_page(self):
        page = QWidget()
        layout = QFormLayout(page)

        from core.paths import PathManager
        self.txt_sync_folder = QLineEdit(self.config.get('savegames', {}).get('sync_folder', ''))
        self.txt_sync_folder.setPlaceholderText(str(PathManager.get_staging_dir()) + " (System Default)")
        btn_sync_folder = QPushButton("Browse...")
        btn_sync_folder.clicked.connect(self.browse_sync_folder)
        row_sync = QHBoxLayout()
        row_sync.addWidget(self.txt_sync_folder)
        row_sync.addWidget(btn_sync_folder)
        layout.addRow("Sync Folder:", row_sync)

        self.spin_sync_interval = QSpinBox()
        self.spin_sync_interval.setRange(1, 9999)
        self.spin_sync_interval.setValue(self.config.get('savegames', {}).get('sync_interval', 30))
        self.combo_sync_unit = QComboBox()
        self.combo_sync_unit.addItems(["minutes", "hours"])
        self.combo_sync_unit.setCurrentText(self.config.get('savegames', {}).get('sync_unit', 'minutes'))
        row_interval = QHBoxLayout()
        row_interval.addWidget(self.spin_sync_interval)
        row_interval.addWidget(self.combo_sync_unit)
        layout.addRow("Sync Interval:", row_interval)

        help_label = QLabel(
            "Set the local sync folder where savegame snapshots are stored. "
            "The app will keep the last 3 synced snapshots per game."
        )
        help_label.setWordWrap(True)
        layout.addRow(help_label)

        self.pages_container.addWidget(page)

    def create_api_keys_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        note = QLabel("Changes to API keys are applied after restarting the launcher.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #ffcc66; font-size: 11px;")
        layout.addWidget(note)
        self.api_form = ApiKeysForm(page)
        layout.addWidget(self.api_form)
        self.pages_container.addWidget(page)

    def browse_sync_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Savegames Sync Folder",
            options=QFileDialog.Option.DontUseNativeDialog)
        if folder:
            self.txt_sync_folder.setText(folder)

    def pick_color(self, key):
        initial_color = QColor(self._current_colors()[key])
        color = QColorDialog.getColor(initial_color, self, f"Select color for {key}")
        if color.isValid():
            hex_color = color.name()
            self._current_colors()[key] = hex_color
            self.color_editors[key]["hex"].setText(hex_color)
            self.color_editors[key]["swatch"].setStyleSheet(self._swatch_style(key))
            self._apply_preview()

    def _current_colors(self):
        return self.config["themes"][self.config["current_theme"]]

    def _factory_defaults(self):
        name = self.config["current_theme"]
        return style_settings.DEFAULT_THEMES.get(name, style_settings.DEFAULT_PURPLE_RIP_THEME)

    def _swatch_style(self, key):
        color = self._current_colors().get(key, "#000000")
        return f"background-color: {color}; border: 1px solid #888; border-radius: 3px; padding: 0;"

    def select_theme(self, name):
        if not name or name not in self.config["themes"]:
            return
        self.config["current_theme"] = name
        self._loading_theme = True
        try:
            for key, editors in self.color_editors.items():
                editors["hex"].setText(self._current_colors()[key])
                editors["swatch"].setStyleSheet(self._swatch_style(key))
        finally:
            self._loading_theme = False
        self._apply_preview()

    def on_hex_edited(self, key, text):
        if self._loading_theme:
            return
        text = text.strip()
        if not HEX_RE.match(text):
            self.color_editors[key]["hex"].setStyleSheet("padding: 1px 4px; font-weight: bold; border: 1px solid #ff4444;")
            return
        hex_color = text if text.startswith('#') else '#' + text
        self._current_colors()[key] = hex_color.lower()
        self.color_editors[key]["hex"].setStyleSheet("padding: 1px 4px; font-weight: bold;")
        self.color_editors[key]["swatch"].setStyleSheet(self._swatch_style(key))
        self._apply_preview()

    def reset_color(self, key):
        default_color = self._factory_defaults()[key]
        self._current_colors()[key] = default_color
        self.color_editors[key]["hex"].setText(default_color)
        self.color_editors[key]["swatch"].setStyleSheet(self._swatch_style(key))
        self._apply_preview()

    def revert_theme(self):
        name = self.config["current_theme"]
        self.config["themes"][name] = copy.deepcopy(self._factory_defaults())
        self.select_theme(name)

    def duplicate_theme(self):
        base_name = self.config["current_theme"]
        new_name, ok = QInputDialog.getText(
            self, "Duplicate Theme", "New theme name:", text=f"{base_name} Copy"
        )
        if not ok or not new_name.strip():
            return
        new_name = new_name.strip()
        if new_name in self.config["themes"]:
            QMessageBox.warning(self, "Duplicate Theme", f"A theme named '{new_name}' already exists.")
            return
        self.config["themes"][new_name] = copy.deepcopy(self.config["themes"][self.config["current_theme"]])
        self.theme_combo.addItem(new_name)
        self.theme_combo.setCurrentText(new_name)

    def delete_theme(self):
        if len(self.config["themes"]) <= 1:
            QMessageBox.information(self, "Delete Theme", "At least one theme must remain.")
            return
        name = self.config["current_theme"]
        confirm = QMessageBox.question(
            self, "Delete Theme",
            f"Delete theme '{name}'?", QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.config["themes"].pop(name)
        self.theme_combo.removeItem(self.theme_combo.currentIndex())
        self.theme_combo.setCurrentText(self.config["current_theme"])

    def _apply_preview(self):
        """Schedule a single coalesced preview of the current colours."""
        self.config["theme"] = self._current_colors()
        if self.parent():
            self._preview_timer.start()

    def _apply_preview_now(self):
        self.config["theme"] = self._current_colors()
        if self.parent():
            self.parent().apply_current_theme(self.config["theme"])

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Game Library",
            options=QFileDialog.Option.DontUseNativeDialog)
        if folder:
            existing = self._folder_paths()
            if folder not in existing:
                self.config.setdefault("watched_folders", []).append(folder)
                if not self._default_library:
                    self._default_library = folder
                self._refresh_folder_list()

    def remove_folder(self):
        current_item = self.folder_list.currentItem()
        if not current_item:
            return
        path = current_item.text().lstrip("★ ").strip()
        self.config["watched_folders"] = [p for p in self._folder_paths() if p != path]
        if self._default_library == path:
            self._default_library = self.config["watched_folders"][0] if self.config["watched_folders"] else ""
        self._refresh_folder_list()

    def reset_defaults(self):
        self.config = copy.deepcopy(style_settings.DEFAULT_CONFIG)
        if hasattr(self, 'theme_combo'):
            self.theme_combo.clear()
            self.theme_combo.addItems(list(self.config["themes"].keys()))
            self.theme_combo.setCurrentText(self.config["current_theme"])
        if hasattr(self, "slider_blur"):
            self.slider_blur.setValue(int(self.config.get("ui", {}).get("explorer_blur", 0)))
        if self.parent(): self.parent().apply_current_theme(self.config["theme"])
        self.accept()

    def save_and_close(self):
        if hasattr(self, "_preview_timer"):
            self._preview_timer.stop()
        if hasattr(self, "api_form"):
            ok, message = self.api_form.apply()
            if not ok:
                QMessageBox.warning(self, "API keys", message)
                return
        self.config["default_temp_folder"] = self.txt_temp.text().strip()
        watched = self._folder_paths()
        self.config["watched_folders"] = watched
        if self._default_library not in watched:
            self._default_library = watched[0] if watched else ""
        self.config["default_library"] = self._default_library
        self.config["onboarding"] = {"completed": True, "version": 1}
        self.config.setdefault('savegames', {})
        self.config['savegames']['sync_folder'] = self.txt_sync_folder.text().strip()
        self.config['savegames']['sync_interval'] = self.spin_sync_interval.value()
        self.config['savegames']['sync_unit'] = self.combo_sync_unit.currentText()
        self.config.setdefault('playtime', {})
        self.config['playtime']['display'] = self.combo_playtime_display.currentData()
        self.config['playtime']['auto_sync'] = self.chk_playtime_auto_sync.isChecked()
        self.config["theme"] = self.config["themes"][self.config["current_theme"]]
        style_settings.save_config(self.config)
        self.config_saved.emit(self.config)
        self.accept()


# --- APP WINDOW PRINCIPALE ---
