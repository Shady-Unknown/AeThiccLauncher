import os
import json
import shutil
from datetime import datetime
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QMessageBox, QMenu, QDialog, QFileDialog
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap, QAction, QCursor

from managers.save_manager import safe_game_folder_name
from managers.playtime_manager import PlaytimeManager
from core.mini_cover_cache import MiniCoverManager
from core import steam_integration
from ui.widgets.flow_layout import FlowLayout
from ui.widgets.savegame_card import SavegameCard


class SavegamesTab(QWidget):
    def __init__(self, app_config, installed_games, manager, parent=None):
        super().__init__(parent)
        self.app_config = app_config
        self.installed_games = installed_games
        self.manager = manager
        self.app_window = parent
        self._cards = []
        self._cover_worker = None
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        self.sync_info_label = QLabel()
        self.sync_info_label.setWordWrap(True)
        header_layout.addWidget(self.sync_info_label, stretch=1)

        self.btn_refresh = QPushButton("Refresh view")
        self.btn_refresh.clicked.connect(self.refresh_view)
        header_layout.addWidget(self.btn_refresh)

        self.btn_sync_all = QPushButton("Sync all game saves")
        self.btn_sync_all.clicked.connect(self.sync_all)
        header_layout.addWidget(self.btn_sync_all)

        self.btn_sync_playtime = QPushButton("⏱ Sync Play Time")
        self.btn_sync_playtime.clicked.connect(self.sync_playtime)
        header_layout.addWidget(self.btn_sync_playtime)

        layout.addLayout(header_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.scroll_container = QWidget()
        self.scroll_container.setStyleSheet("background: transparent;")
        self.flow_layout = FlowLayout(self.scroll_container, margin=10, hspacing=10, vspacing=10)
        self.scroll_area.setWidget(self.scroll_container)

        layout.addWidget(self.scroll_area, stretch=1)

        self.message_label = QLabel()
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        bottom_layout = QHBoxLayout()

        self.lbl_disclaimer = QLabel()
        self.lbl_disclaimer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_disclaimer.setWordWrap(True)
        bottom_layout.addWidget(self.lbl_disclaimer, stretch=1)

        self.btn_open_sync = QPushButton(" Open Synced Folder")
        self.btn_open_sync.clicked.connect(self.open_sync_folder)
        bottom_layout.addWidget(self.btn_open_sync)

        layout.addLayout(bottom_layout)

        self.refresh_view()

        if 'theme' in self.app_config:
            self.apply_theme(self.app_config['theme'])

    def apply_theme(self, theme_data):
        main_accent = theme_data.get('main_accent_color', '#733d91')
        bright_neon = theme_data.get('bright_neon_accent', '#ff00a0')
        text_white = theme_data.get('text_primary_white', '#ffffff')
        text_muted = theme_data.get('text_muted', '#8a7fa4')

        self.btn_refresh.setStyleSheet("""
            QPushButton {{
                background-color: {main}; color: white; border-radius: 5px; padding: 8px 12px;
            }}
            QPushButton:hover {{
                background-color: {neon};
            }}
        """.format(main=main_accent, neon=bright_neon))

        self.btn_sync_all.setStyleSheet("""
            QPushButton {{
                background-color: {neon}; color: #1a0f2b; border-radius: 5px; padding: 8px 12px; font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {white};
            }}
        """.format(neon=bright_neon, white=text_white))

        self.btn_sync_playtime.setStyleSheet("""
            QPushButton {{
                background-color: transparent; color: {white}; border: 1px solid {main};
                border-radius: 5px; padding: 8px 12px; font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid {neon}; color: {neon};
            }}
        """.format(white=text_white, main=main_accent, neon=bright_neon))

        self.btn_open_sync.setStyleSheet("""
            QPushButton {{
                background-color: transparent; color: {white}; border: 1px solid {main};
                border-radius: 5px; padding: 8px 12px; font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid {neon}; color: {neon};
            }}
        """.format(white=text_white, main=main_accent, neon=bright_neon))

        self.lbl_disclaimer.setStyleSheet(
            "font-size: 11px; color: {muted}; padding: 4px 10px; "
            "background-color: rgba(0,0,0,0.1); border-radius: 4px;".format(muted=text_muted)
        )
        self.lbl_disclaimer.setText(
            "If you like the game and the software house / publisher aren't aggressive monetizers, "
            "if they are pro-users, if they support the customer, "
            "<b style='color:{neon}; text-transform: uppercase;'>BUY THE GAME. THEY DESERVE IT.</b>".format(neon=bright_neon)
        )

        card_style = """
            SavegameCard {{
                background-color: {surface};
                border: 2px solid transparent;
                border-radius: 12px;
            }}
            SavegameCard:hover {{
                background-color: {surface_hover};
                border: 2px solid {neon};
            }}
        """.format(
            surface=theme_data.get('surface_panel_purple', '#2b1a42'),
            surface_hover=theme_data.get('surface_hover', '#3b2a52'),
            neon=bright_neon,
        )
        for card in self._cards:
            card.setStyleSheet(card_style)

    def open_sync_folder(self):
        sync_folder = self.manager.get_sync_folder()
        if sync_folder and os.path.exists(sync_folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(sync_folder))
        else:
            QMessageBox.warning(self, "Folder Not Found", "The sync folder does not exist yet or is not configured.")

    def refresh_view(self):
        sync_folder = self.manager.get_sync_folder()

        self._clear_cards()
        self.sync_info_label.setText("")

        if not sync_folder:
            self.message_label.setText(
                "Savegames Manager is not configured. Please set a Sync Folder in the Configuration Panel."
            )
            self.btn_sync_all.setEnabled(False)
            self.btn_open_sync.setEnabled(False)
            return

        self.btn_sync_all.setEnabled(True)
        self.btn_open_sync.setEnabled(True)
        self.message_label.setText("")
        self.sync_info_label.setText(
            "Sync folder: <b>{}</b> | Interval: {} {}".format(
                sync_folder,
                self.app_config.get('savegames', {}).get('sync_interval', 30),
                self.app_config.get('savegames', {}).get('sync_unit', 'minutes'),
            )
        )

        if not os.path.exists(sync_folder):
            self.message_label.setText(
                "The configured sync folder does not exist yet. It will be created when you run a sync."
            )
            self.btn_open_sync.setEnabled(False)
            return

        self._populate_cards(sync_folder)

    def _clear_cards(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards.clear()

    def _populate_cards(self, sync_folder):
        try:
            names = sorted(os.listdir(sync_folder), key=str.lower)
        except Exception:
            return

        for name in names:
            game_root = os.path.join(sync_folder, name)
            if not os.path.isdir(game_root):
                continue
            saves_dir = os.path.join(game_root, 'saves')
            has_saves = os.path.isdir(saves_dir)

            game_title = name
            last_sync_str = "Never synced"
            playtime_seconds = 0
            meta_path = os.path.join(game_root, 'savegames.json')
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        smeta = json.load(f)
                    game_title = smeta.get('game_title', name)
                    last_sync_str = smeta.get('last_synced', 'Never synced')
                    playtime_seconds = int(smeta.get('playtime_seconds', 0) or 0)
                except Exception:
                    pass

            # Skip cartelle vuote (né backup né playtime)
            if not has_saves and not playtime_seconds:
                continue

            if has_saves:
                latest_mtime = self.manager.get_latest_save_time(saves_dir)
                if latest_mtime:
                    last_save_str = datetime.fromtimestamp(latest_mtime).strftime('%Y-%m-%d %H:%M')
                else:
                    last_save_str = "\u2014"
            else:
                last_save_str = "Playtime Only"

            cover_path = MiniCoverManager.resolve_cover_path(
                game_title, self.installed_games, sync_folder
            )
            if cover_path and cover_path.startswith("file://"):
                # QUrl handles file:///C:/... on Windows; slicing [7:] leaves
                # a leading slash (/C:/...) and QPixmap silently fails.
                local_cover = QUrl(cover_path).toLocalFile()
                px = QPixmap(local_cover) if local_cover else QPixmap()
            else:
                px = QPixmap()

            display_mode = self.app_config.get('playtime', {}).get('display', 'hours')
            playtime_str = PlaytimeManager.format_playtime(playtime_seconds, display_mode) if playtime_seconds else ""

            card = SavegameCard(game_title, px, last_save_str, last_sync_str, game_root, playtime_str=playtime_str)
            card.clicked.connect(self._on_card_clicked)
            card.context_menu_requested.connect(self._on_card_context_menu)
            if px.isNull():
                # No installed cover and no minicover yet: resolve it lazily
                # from the catalog/SGDB and update this card when ready.
                self._request_cover(game_title)

            theme = self.app_config.get('theme', {})
            neon = theme.get('bright_neon_accent', '#ff00a0')
            accent = theme.get('main_accent_color', '#733d91')
            surface = theme.get('surface_panel_purple', '#2b1a42')
            card.setStyleSheet("""
                SavegameCard {{
                    background-color: {surface};
                    border: 1px solid {accent};
                    border-radius: 12px;
                }}
                SavegameCard:hover {{
                    background-color: #3b2a52;
                    border: 2px solid {neon};
                }}
            """.format(surface=surface, accent=accent, neon=neon))

            self.flow_layout.addWidget(card)
            self._cards.append(card)

    def _ensure_cover_worker(self):
        if self._cover_worker is None:
            from network.cover_worker import CoverWorker
            self._cover_worker = CoverWorker(
                self.installed_games,
                self.manager.get_sync_folder(),
                self,
            )
            self._cover_worker.cover_ready.connect(self._on_cover_ready)
            self._cover_worker.start()
        else:
            self._cover_worker.update_context(
                self.installed_games,
                self.manager.get_sync_folder(),
            )
        return self._cover_worker

    def _request_cover(self, game_title):
        title = str(game_title or "").strip()
        if not title:
            return
        catalog = getattr(self.app_window, "master_catalog", None)
        entry = catalog.get_entry(title) if catalog is not None else None
        job = {"title": title}
        if isinstance(entry, dict):
            job["catalog_entry"] = entry
        self._ensure_cover_worker().enqueue([job], high=True)

    def _on_cover_ready(self, title, file_url):
        local = QUrl(file_url).toLocalFile() if file_url else ""
        if not local:
            return
        px = QPixmap(local)
        if px.isNull():
            return
        for card in self._cards:
            if card.title_label.text() != title:
                continue
            card.cover_label.setPixmap(
                px.scaled(
                    card.cover_label.width(),
                    card.cover_label.height(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            break

    def _on_card_clicked(self, game_root):
        if os.path.isdir(game_root):
            QDesktopServices.openUrl(QUrl.fromLocalFile(game_root))

    def sync_all(self):
        if not self.manager.get_sync_folder():
            QMessageBox.warning(self, "No Sync Folder", "Configure a Sync Folder in Settings before syncing savegames.")
            return

        summary = self.manager.sync_all_games()
        if summary.get('errors'):
            QMessageBox.warning(
                self, "Sync finished with warnings",
                "Some savegames could not be synced. Check the log or the messages below."
            )
        else:
            QMessageBox.information(
                self, "Sync complete",
                "Synced {} game(s).".format(summary.get('synced_games', 0))
            )

        self.refresh_view()

    def sync_playtime(self):
        """Sincronizzazione manuale del playtime con Steam (merge massimo).

        Se Steam è aperto gli aggiornamenti non possono essere scritti subito:
        si chiede all'utente se uccidere/riavviare Steam ora oppure rimandare
        al prossimo riavvio di Steam (funziona anche a launcher chiuso).
        """
        pm = getattr(self.app_window, 'playtime_manager', None)
        if not pm:
            QMessageBox.warning(self, "Play Time Sync", "Playtime manager not available.")
            return

        # 1. Merge bidirezionale: importa da Steam se Steam ha più tempo
        try:
            pm.sync_all()
        except Exception as e:
            QMessageBox.warning(self, "Play Time Sync", f"Sync failed:\n{e}")
            return

        # 2. Steam chiuso → scrivi subito
        if pm.try_flush_pending():
            QMessageBox.information(
                self, "Play Time Sync",
                "Play time synced with Steam successfully."
            )
            self.refresh_view()
            return

        if not pm.pending_games():
            QMessageBox.information(
                self, "Play Time Sync",
                "Play time is already in sync. No changes to push to Steam."
            )
            self.refresh_view()
            return

        # 3. Steam è attivo e ci sono aggiornamenti in coda
        if not steam_integration.is_steam_running():
            self.refresh_view()
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("Sync Play Time")
        msg.setText("Steam is running, so the updated play time is queued.")
        msg.setInformativeText(
            "While Steam is open it would overwrite the play time file on disk, "
            "so the launcher waits for Steam to close.<br><br>"
            "Choose <b>Kill &amp; Restart Steam Now</b> to write the play time immediately, "
            "or <b>Wait</b> to apply the updates at the next Steam restart "
            "(it works even if the launcher is closed)."
        )
        msg.setIcon(QMessageBox.Icon.Question)
        btn_restart = msg.addButton("Kill & Restart Steam Now", QMessageBox.ButtonRole.AcceptRole)
        btn_wait = msg.addButton("Wait, I'll Restart Later", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()

        if clicked == btn_restart:
            try:
                steam_integration.kill_steam_safely()
                pm.write_playtime_to_steam()
                steam_integration._restart_steam()
            except Exception as e:
                QMessageBox.critical(self, "Play Time Sync", f"Could not write playtime:\n{e}")
                self.refresh_view()
                return
            QMessageBox.information(
                self, "Play Time Sync",
                "Steam has been restarted and the play time was written to Steam."
            )
        else:
            QMessageBox.information(
                self, "Play Time Sync",
                "Updates are queued. They will be applied automatically at the next Steam restart."
            )
        self.refresh_view()

    def _on_card_context_menu(self, game_root):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #2b1a42;
                border: 1px solid #733d91;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                color: #ffffff;
                padding: 8px 24px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #3b2a52;
            }
            QMenu::item:disabled {
                color: #5a4a6a;
            }
            QMenu::separator {
                height: 1px;
                background: #3d2a5a;
                margin: 4px 8px;
            }
        """)

        act_open_sync = QAction("Open Sync Folder", self)
        act_open_sync.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(game_root)))

        act_open_source = QAction("Open Game Save Folder", self)
        act_open_source.triggered.connect(lambda: self._open_game_save_folder(game_root))

        act_point_save = QAction("📍 Point to Saves Folder", self)
        act_point_save.triggered.connect(lambda: self._set_custom_save_path(game_root))

        menu.addAction(act_open_sync)
        menu.addAction(act_open_source)
        menu.addAction(act_point_save)
        menu.addSeparator()

        act_restore = QAction("Move savegame to Game Folder", self)
        act_restore.triggered.connect(lambda: self._show_restore_dialog(game_root))
        menu.addAction(act_restore)
        menu.addSeparator()

        act_restore_playtime = QAction("⏱ Restore Play Time", self)
        act_restore_playtime.triggered.connect(lambda: self._restore_playtime(game_root))
        menu.addAction(act_restore_playtime)
        menu.addSeparator()

        act_cloud = QAction("Force Cloud Backup", self)
        act_cloud.setEnabled(False)
        menu.addAction(act_cloud)
        menu.addSeparator()

        act_delete = QAction("Delete Game\u2019s Backups", self)
        act_delete.triggered.connect(lambda: self._confirm_delete_saves(game_root))
        menu.addAction(act_delete)

        menu.exec(QCursor.pos())

    def _resolve_source_path(self, smeta):
        def _expand(path):
            return os.path.expandvars(path) if os.name == 'nt' else path

        custom = smeta.get('savegame_path_custom', '')
        if custom:
            expanded = _expand(custom)
            if os.path.exists(expanded):
                return expanded

        path = _expand(smeta.get('source_path', ''))
        if path and os.path.exists(path):
            return path

        win_path = smeta.get('savegame_path_win', '')
        if os.name == 'nt' and win_path:
            expanded = _expand(win_path)
            if not re.match(r'^[a-zA-Z]:\\', expanded) and not expanded.startswith('%'):
                install_dir = smeta.get('install_dir', '')
                if install_dir:
                    expanded = os.path.join(install_dir, expanded.lstrip('\\/'))
            if os.path.exists(expanded):
                return expanded

        game_title = smeta.get('game_title', '')
        grid_id = ''

        if game_title and self.installed_games:
            live_meta = self.installed_games.get(game_title, {})
            grid_id = live_meta.get('steam_grid_id', '')
            if not grid_id:
                exe_path = live_meta.get('exe_path', '')
                if exe_path:
                    from core.steam_integration import get_steam_ids
                    _, grid_id, _ = get_steam_ids(exe_path, game_title)

        if not grid_id:
            grid_id = smeta.get('steam_grid_id', '')

        if win_path and grid_id and os.name != 'nt':
            reconstructed = self.manager.translate_win_path_to_linux(win_path, grid_id)
            if reconstructed:
                return reconstructed

        if path:
            return path
        if win_path:
            return _expand(win_path)
        return _expand(smeta.get('savegame_path_linux', ''))

    def _open_game_save_folder(self, game_root):
        meta_path = os.path.join(game_root, 'savegames.json')
        if not os.path.exists(meta_path):
            QMessageBox.warning(self, "No Metadata", "No savegames.json found for this game.")
            return
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                smeta = json.load(f)
        except Exception:
            QMessageBox.warning(self, "Error", "Could not read savegames.json.")
            return

        source_path = self._resolve_source_path(smeta)
        if not source_path or not os.path.exists(source_path):
            QMessageBox.warning(self, "Folder Not Found",
                                "The original save folder could not be found on disk.\n"
                                "The game may not have been launched yet or the path has moved.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(source_path))

    def _set_custom_save_path(self, game_root):
        meta_path = os.path.join(game_root, 'savegames.json')
        smeta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    smeta = json.load(f)
            except Exception:
                pass

        game_title = smeta.get('game_title', '')
        install_dir = smeta.get('install_dir', '')
        if not game_title and not install_dir:
            QMessageBox.warning(self, "Error", "Could not identify the game for this backup.")
            return

        live_meta = self.installed_games.get(game_title, {}) if game_title else {}
        if not live_meta and install_dir:
            for t, m in self.installed_games.items():
                if m.get('install_dir') == install_dir:
                    live_meta = m
                    game_title = t
                    break

        has_custom = bool(live_meta.get('savegame_path_custom') or smeta.get('savegame_path_custom'))
        if has_custom:
            current = live_meta.get('savegame_path_custom') or smeta.get('savegame_path_custom', '')
            msg = QMessageBox(self)
            msg.setWindowTitle("Custom Save Path")
            msg.setText(f"<b>{game_title}</b> has a custom save path:<br><code>{current}</code>")
            msg.setInformativeText("What would you like to do?")
            msg.setIcon(QMessageBox.Icon.Question)
            btn_browse = msg.addButton("Change Path", QMessageBox.ButtonRole.AcceptRole)
            btn_revert = msg.addButton("Revert to Auto-Detect", QMessageBox.ButtonRole.RejectRole)
            msg.addButton(QMessageBox.StandardButton.Cancel)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == btn_revert:
                self.manager.clear_custom_save_path(live_meta)
                QMessageBox.information(self, "Reverted", f"Custom save path removed for <b>{game_title}</b>.")
                self.refresh_view()
                return
            elif clicked != btn_browse:
                return

        folder = QFileDialog.getExistingDirectory(self, f"Select Save Folder for {game_title}")
        if not folder:
            return

        if live_meta:
            self.manager.set_custom_save_path(live_meta, folder)
        else:
            smeta['savegame_path_custom'] = folder
            try:
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(smeta, f, indent=4, ensure_ascii=False)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not save metadata: {e}")
                return

        QMessageBox.information(self, "Custom Save Path Set",
            f"Save path for <b>{game_title}</b> set to:<br><code>{folder}</code><br><br>"
            "<i>Tip: Launch the game at least once so it can create the save folder structure if it doesn't exist yet.</i>")
        self.refresh_view()

    def _show_restore_dialog(self, game_root):
        meta_path = os.path.join(game_root, 'savegames.json')
        if not os.path.exists(meta_path):
            QMessageBox.warning(self, "No Metadata", "No savegames.json found for this game.")
            return
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                smeta = json.load(f)
        except Exception:
            QMessageBox.warning(self, "Error", "Could not read savegames.json.")
            return

        source_path = self._resolve_source_path(smeta)
        if not source_path:
            QMessageBox.warning(self, "No Source Path",
                                "The original save path is not recorded for this game.\n"
                                "Try running Sync All to generate path data.")
            return

        saves_dir = os.path.join(game_root, 'saves')
        if not os.path.isdir(saves_dir):
            QMessageBox.information(self, "No Backups", "No backup snapshots found for this game.")
            return

        snapshots = sorted(
            [d for d in os.listdir(saves_dir) if os.path.isdir(os.path.join(saves_dir, d))],
            reverse=True,
        )
        if not snapshots:
            QMessageBox.information(self, "No Backups", "No backup snapshots found for this game.")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Backup to Restore")
        dialog.setMinimumWidth(360)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1a0f2b;
                border: 1px solid #733d91;
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-size: 13px;
                padding: 4px;
            }
            QPushButton {
                background-color: #733d91;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #ff00a0;
            }
        """)

        dl = QVBoxLayout(dialog)
        dl.setSpacing(6)
        dl.setContentsMargins(16, 16, 16, 16)

        header = QLabel("Choose a backup snapshot to restore:")
        header.setStyleSheet("font-weight: bold; font-size: 14px;")
        dl.addWidget(header)

        for snap in snapshots:
            try:
                ts = datetime.strptime(snap.split('_')[0] + '_' + snap.split('_')[1], '%Y%m%d_%H%M%S')
                label = ts.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                label = snap

            btn = QPushButton(label)
            snap_path = os.path.join(saves_dir, snap)
            btn.clicked.connect(lambda checked, sp=snap_path, ssp=source_path: self._do_restore(sp, ssp, dialog))
            dl.addWidget(btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dialog.reject)
        dl.addWidget(cancel_btn)

        dialog.exec()

    def _do_restore(self, snap_path, source_path, dialog):
        try:
            if os.path.exists(source_path):
                shutil.rmtree(source_path, ignore_errors=True)
            os.makedirs(source_path, exist_ok=True)
            shutil.copytree(snap_path, source_path, dirs_exist_ok=True)
            QMessageBox.information(self, "Restore Complete",
                                    "The selected save has been restored to the game folder.")
            dialog.accept()
        except Exception as e:
            QMessageBox.critical(self, "Restore Failed",
                                 "Could not restore the save:\n{}".format(e))

    def _restore_playtime(self, game_root):
        """Ripristina il playtime del backup nel launcher e accoda la sync a Steam."""
        meta_path = os.path.join(game_root, 'savegames.json')
        if not os.path.exists(meta_path):
            return
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                smeta = json.load(f)
        except Exception:
            return

        seconds = int(smeta.get('playtime_seconds', 0) or 0)
        if not seconds:
            QMessageBox.information(self, "Restore Play Time",
                                    "This backup has no recorded play time.")
            return

        game_title = smeta.get('game_title', '')
        install_dir = smeta.get('install_dir', '')
        live_meta = self.installed_games.get(game_title, {}) if game_title else {}
        if not live_meta and install_dir:
            for t, m in self.installed_games.items():
                if m.get('install_dir') == install_dir:
                    live_meta = m
                    game_title = t
                    break
        if not live_meta:
            QMessageBox.warning(self, "Restore Play Time",
                                "The game is not installed, so its play time cannot be restored.")
            return

        reply = QMessageBox.question(
            self, "Restore Play Time",
            f"Restore play time <b>{seconds // 3600:.0f}h {seconds % 3600 // 60}m</b> "
            f"for <b>{game_title}</b>?\n\n"
            "Steam play time is never decreased, only increased.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        pm = getattr(self.app_window, 'playtime_manager', None)
        if pm:
            pm.restore_playtime(game_title, live_meta, seconds)
            QMessageBox.information(
                self, "Restore Play Time",
                "Play time restored. It will be pushed to Steam at the next Steam restart."
            )
        self.refresh_view()

    def _confirm_delete_saves(self, game_root):
        reply = QMessageBox.warning(
            self,
            "Delete Game's Backups",
            "This will permanently delete all save backups for this game.\n\n"
            "Are you sure?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(game_root, ignore_errors=True)
                self.refresh_view()
            except Exception as e:
                QMessageBox.critical(self, "Error",
                                     "Could not delete backups:\n{}".format(e))
