import os
import json
#import shutil
import re
from datetime import datetime
from PyQt6.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QLabel, QComboBox, QPushButton #QFileDialog
from PyQt6.QtCore import QObject, pyqtSignal
from ui.dialogs.password_dialog import PasswordDialog
from core.installer import GameInstaller
from core.config import get_base_dir
from core.events import EVENT_UPDATE_APPLIED
from core.meta_utils import save_steamrip_meta


class InstallerFlowCoordinator(QObject):
    install_complete = pyqtSignal(str)
    task_update = pyqtSignal(str, str)

    def __init__(self, app_window):
        super().__init__()
        self.app_window = app_window
        self._current_preserve_meta = False

    def prompt_installation(self, temp_archive_path, task_data=None):
        self._current_install_task_data = task_data
        self._current_preserve_meta = False
        watched = self.app_window.app_config.get("watched_folders", [])
        dest_folder = ""
        
        if not watched:
            QMessageBox.critical(self.app_window, "Restricted Installation", 
                                 "No 'Game Libraries' configured.\n\n"
                                 "To maintain library integrity and enable advanced features (Auto-Backup, Updates), "
                                 "games must be installed in a managed folder. Please add one in Settings.")
            self.app_window.is_task_running = False
            self.app_window.task_label.setText("Idle: Ready for action")
            self.app_window.task_progress.hide()
            if hasattr(self.app_window, 'dl_manager'):
                self.app_window.dl_manager.add_pending_extraction(temp_archive_path, task_data or {})
                self.app_window.dl_manager.finish_extraction()
            return
        else:
            from PyQt6.QtWidgets import QHBoxLayout, QFileDialog
            from core.config import save_config

            app_config = self.app_window.app_config
            default_lib = app_config.get("default_library") or (watched[0] if watched else "")

            dialog = QDialog(self.app_window)
            dialog.setWindowTitle("Installation Destination")
            dialog_layout = QVBoxLayout(dialog)
            lbl = QLabel("Choose a library for extraction:")
            dialog_layout.addWidget(lbl)

            combo = QComboBox()
            combo.addItems(watched)
            if default_lib in watched:
                combo.setCurrentText(default_lib)
            dialog_layout.addWidget(combo)

            free_label = QLabel("")
            dialog_layout.addWidget(free_label)

            def update_free(*_args):
                path = combo.currentText().strip()
                try:
                    import psutil
                    free = psutil.disk_usage(path).free / (1024 ** 3)
                    free_label.setText(f"Free space on this library: {free:.1f} GB")
                except Exception:
                    free_label.setText("")

            combo.currentTextChanged.connect(update_free)
            update_free()

            row = QHBoxLayout()
            btn_new = QPushButton("Create new library…")
            row.addWidget(btn_new)
            row.addStretch(1)
            dialog_layout.addLayout(row)

            def on_new_library():
                folder = QFileDialog.getExistingDirectory(
                    dialog, "Select a folder for the new library"
                )
                if not folder:
                    return
                if folder not in watched:
                    watched.append(folder)
                    app_config["watched_folders"] = watched
                    save_config(app_config)
                    combo.addItem(folder)
                app_config["default_library"] = folder
                save_config(app_config)
                combo.setCurrentText(folder)
                update_free()

            btn_new.clicked.connect(on_new_library)

            btn_box = QPushButton("Confirm Installation")
            dialog_layout.addWidget(btn_box)

            self._selected_dest = ""
            def on_confirm():
                self._selected_dest = combo.currentText()
                dialog.accept()

            btn_box.clicked.connect(on_confirm)
            dialog.exec()
            dest_folder = self._selected_dest

        if not dest_folder:
            self.app_window.is_task_running = False
            self.app_window.task_label.setText("Extraction postponed - archive kept in cache")
            self.app_window.task_label.setStyleSheet("color: #ffcc00; font-weight: bold; border: none; background: transparent;")
            self.app_window.task_progress.hide()
            if hasattr(self.app_window, 'dl_manager'):
                self.app_window.dl_manager.add_pending_extraction(temp_archive_path, task_data or {})
                self.app_window.dl_manager.finish_extraction()
            return
        
        game_data_to_use = {}
        if task_data:
            game_data_to_use = {
                "title": task_data.get('title'),
                "version": task_data.get('web_version'),
                "game_info": task_data.get('game_info', ''),
                "description": task_data.get('description', ''),
                "page_url": task_data.get('page_url', 'N/A'),
                "version_status": task_data.get('version_status')
                    or self.app_window.master_catalog.get_version_status(task_data.get('title', '')),
            }
        else:
            game_data_to_use = self.app_window.current_game_data

        # --- DETECT IN-PLACE UPDATE ---
        # Se l'utente sceglie lo stesso folder in cui il gioco è già installato,
        # chiediamo se aggiornare in-place (conservando save path e Steam) o installare fresco.
        preserve_meta = False
        game_title = game_data_to_use.get('title', '')
        safe_title = re.sub(r'[\\/*?:"<>|]', "", game_title)
        target_final_path = os.path.join(dest_folder, safe_title)
        existing_meta = self.app_window.installed_games.get(game_title, {})

        is_same_location = (
            existing_meta
            and not existing_meta.get('is_orphan')
            and os.path.normpath(existing_meta.get('install_dir', '')) == os.path.normpath(target_final_path)
        )

        if is_same_location:
            reply = QMessageBox(
                QMessageBox.Icon.Question,
                "Update Detected",
                f"{game_title} is already installed in:\n{existing_meta['install_dir']}\n\n"
                "How do you want to install this new version?",
                QMessageBox.StandardButton.NoButton,
                self.app_window
            )
            btn_update = reply.addButton("🔄 Update in place", QMessageBox.ButtonRole.AcceptRole)
            btn_fresh = reply.addButton("✨ Install fresh copy", QMessageBox.ButtonRole.DestructiveRole)
            btn_cancel = reply.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            reply.exec()

            if reply.clickedButton() == btn_update:
                preserve_meta = True
            elif reply.clickedButton() == btn_fresh:
                preserve_meta = False
            else:
                self.app_window.is_task_running = False
                self.app_window.task_label.setText("Extraction postponed - archive kept in cache")
                self.app_window.task_label.setStyleSheet("color: white; font-weight: bold; border: none; background: transparent;")
                self.app_window.task_progress.hide()
                if hasattr(self.app_window, 'dl_manager'):
                    self.app_window.dl_manager.finish_extraction()
                return

        self._current_preserve_meta = preserve_meta

        if hasattr(self.app_window, 'dl_manager'):
            self.app_window.dl_manager.set_extraction_install_dir(
                temp_archive_path, target_final_path
            )

        # Setup UI per l'estrazione
        self.app_window.lbl_game_title.setText("Installation in progress...")
        self.app_window.task_label.setText("📦 Extracting archive. This may take a while...")
        self.app_window.task_progress.setRange(0, 0)
        self.app_window.task_progress.show()
        
        pb_col = self.app_window.app_config['theme'].get('progress_bar_color', '#00ff88')
        self.app_window.desc_display.setHtml(f"<h3 style='color:{pb_col};'>Extracting data to disk...</h3>")
        
        # Passiamo la variabile blindata al thread!
        self.installer_thread = GameInstaller(temp_archive_path, dest_folder, game_data_to_use, preserve_meta=preserve_meta)

        # Puoi ancora mostrare i log testuali nella descrizione del gioco se l'estrattore li emette
        self.installer_thread.progress_update.connect(lambda msg: self.app_window.desc_display.setHtml(f"<h3 style='color:{pb_col};'>{msg}</h3>"))
        
        # Connessioni verso dl_manager per l'estrazione visiva
        if hasattr(self.app_window, 'dl_manager'):
            self.installer_thread.extraction_started.connect(self.app_window.dl_manager.set_extraction_mode)
            self.installer_thread.extraction_progress.connect(self.app_window.dl_manager.update_extraction_progress)
            
        self.installer_thread.installation_complete.connect(self._on_install_complete)
        self.installer_thread.error_occurred.connect(self.app_window.handle_error)
        self.installer_thread.password_required.connect(self._on_password_required)
        self.installer_thread.start()

    def _on_install_complete(self, final_path):
        was_update = bool(self._current_preserve_meta)
        self.app_window.is_task_running = False # Sblocchiamo il sistema per nuovi download!
        self.app_window.task_label.setText("🚀 Installation Complete!")
        self.app_window.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
        self.app_window.task_progress.hide()
        
        # Pulisci eventuali pending extraction il cui archivio è stato cancellato
        if hasattr(self.app_window, 'dl_manager'):
            self.app_window.dl_manager.recover_pending_extractions()
        
        if hasattr(self.app_window, 'dl_manager'):
            self.app_window.dl_manager.finish_extraction()
            
        self.app_window.lbl_game_title.setText("Installation Complete! 🚀")
        self.app_window.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Game ready!</h3><p>Destination: {final_path}</p>")
        self.app_window.lib_manager.scan_library()
        installed_meta = self.app_window.lib_manager._find_installed_meta_by_folder(final_path)
        if installed_meta and not installed_meta.get('is_orphan'):
            self.install_complete.emit(final_path)
            self.app_window.savegames_manager.ensure_save_path_for_game(installed_meta)
            title = installed_meta.get('title')
            if was_update:
                events = getattr(self.app_window, 'events', None)
                if events is not None:
                    events.emit_event(
                        EVENT_UPDATE_APPLIED,
                        title=title or '',
                        install_dir=final_path,
                        version=installed_meta.get('version', ''),
                    )
            item = self.app_window.lib_manager._find_item_by_title(title)
            if item:
                self.app_window.lib_manager._update_library_item(item, title, "Installed", meta=installed_meta, game_title=title)
            if hasattr(self.app_window, 'savegames_tab'):
                self.app_window.savegames_tab.refresh_view()

    def _on_password_required(self, temp_archive_path, dest_folder):
        self.app_window.task_progress.hide()
        
        task_data = getattr(self, '_current_install_task_data', None)
        if task_data:
            game_title = task_data.get('title', 'Unknown Game')
            source_url = task_data.get('page_url', 'N/A')
            game_data_to_use = {
                "title": task_data.get('title'),
                "version": task_data.get('web_version'),
                "game_info": task_data.get('game_info', ''),
                "description": task_data.get('description', ''),
                "page_url": task_data.get('page_url', 'N/A'),
                "version_status": task_data.get('version_status')
                    or self.app_window.master_catalog.get_version_status(task_data.get('title', '')),
            }
        else:
            game_title = self.app_window.current_game_data.get('title', 'Unknown Game')
            source_url = self.app_window.current_game_data.get('source_url', 'N/A')
            game_data_to_use = self.app_window.current_game_data
        
        dialog = PasswordDialog(game_title, source_url, self.app_window.app_config['theme'], self.app_window)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.password:
            self.app_window.lbl_game_title.setText("Installation in progress...")
            self.app_window.task_label.setText("📦 Extracting archive with password...")
            self.app_window.task_progress.setRange(0, 0)
            self.app_window.task_progress.show()
            
            pb_col = self.app_window.app_config['theme'].get('progress_bar_color', '#00ff88')
            self.app_window.desc_display.setHtml(f"<h3 style='color:{pb_col};'>Extracting data to disk (with password)...</h3>")
            
            self.installer_thread = GameInstaller(temp_archive_path, dest_folder, game_data_to_use,
                                                  password=dialog.password, preserve_meta=self._current_preserve_meta)
            self.installer_thread.progress_update.connect(lambda msg: self.app_window.desc_display.setHtml(f"<h3 style='color:{pb_col};'>{msg}</h3>"))
            
            if hasattr(self.app_window, 'dl_manager'):
                self.installer_thread.extraction_started.connect(self.app_window.dl_manager.set_extraction_mode)
                self.installer_thread.extraction_progress.connect(self.app_window.dl_manager.update_extraction_progress)
                
            self.installer_thread.installation_complete.connect(self._on_install_complete)
            self.installer_thread.error_occurred.connect(self.app_window.handle_error)
            self.installer_thread.password_required.connect(self._on_password_required)
            self.installer_thread.start()
        else:
            self.app_window.handle_error("Extraction cancelled (password required).")

    def generate_local_metadata(self, dest_folder, data):
        mp_match = re.search(r'(?i)(multiplayer by|co-op by|coop by|online by|crack by)[\s:]*([^\n]+)', data.get('game_info', ''))
        if mp_match:
            raw_mp = mp_match.group(2).strip()
            clean_mp = re.sub(r'<[^>]+>', '', raw_mp)
            clean_mp = re.split(r'(?i)(version|pre-installed|\||<li>)', clean_mp)[0].strip()
            mp_crack = clean_mp
        else:
            mp_crack = "None / Unknown"

        version_status = data.get('version_status') or {}
        meta_dict = {
            "title": data.get('title'),
            "version": data.get('version'),
            "build": version_status.get('posted_build') or "",
            "build_date": version_status.get('posted_build_date', ''),
            "executable_path": "",
            "multiplayer_crack": mp_crack,
            "has_dlc": "📦 SHOW INCLUDED DLCs" in data.get('description', ''),
            "source_url": data.get('page_url', 'N/A'),
            "savegame_path_win": "",
            "savegame_path_linux": "",
            "savegame_scan_state": False,
            "steam_grid_id": ""
        }
        save_steamrip_meta(dest_folder, meta_dict)

    def create_local_only_metadata(self, dest_folder, title):
        """Creates a minimal steamrip_meta.json for a game installed locally
        from a source OTHER than SteamRip. No source_url/version → the game
        is flagged as local, update checks are disabled."""
        meta_dict = {
            "title": title,
            "version": "",
            "executable_path": "",
            "multiplayer_crack": "None / Unknown",
            "has_dlc": False,
            "source_url": "",
            "savegame_path_win": "",
            "savegame_path_linux": "",
            "savegame_scan_state": False,
            "steam_grid_id": "",
            "is_local": True
        }
        save_steamrip_meta(dest_folder, meta_dict)
