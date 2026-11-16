import os
import json
import html
import shutil
from datetime import datetime, timezone
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QScrollArea, QMessageBox, QMenu, QDialog, QFileDialog
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap, QAction, QCursor

from managers.save_manager import safe_game_folder_name
from managers.playtime_manager import PlaytimeManager
from managers.backup_service import BackupService
from core import game_version
from core.mini_cover_cache import MiniCoverManager
from core import steam_integration
from ui.widgets.flow_layout import FlowLayout
from ui.widgets.savegame_card import SavegameCard

MIRROR_STATE_LABELS = {
    'local_only': 'not mirrored yet',
    'mirror_only': 'only in the mirror',
    'synced': 'synced',
    'local_newer': 'local is newer',
    'mirror_newer': 'mirror is newer',
    'conflict': 'conflict: both sides have new backups',
}


def _format_manifest_time(value):
    """Render a ``savegames.json`` timestamp in local time.

    Schema 2 stores UTC ISO strings; version 1 manifests stored local time
    without a timezone, so both formats are accepted.
    """
    text = str(value or '').strip()
    if not text:
        return ""
    try:
        if text.endswith('Z'):
            parsed = datetime.strptime(text, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            return parsed.astimezone().strftime('%Y-%m-%d %H:%M')
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return text


class SavegamesTab(QWidget):
    def __init__(self, app_config, installed_games, manager, parent=None, backup_service=None):
        super().__init__(parent)
        self.app_config = app_config
        self.installed_games = installed_games
        self.manager = manager
        self.backup_service = backup_service or BackupService(manager)
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

        self.btn_mirror = QPushButton("↗ Update mirror")
        self.btn_mirror.clicked.connect(self.push_mirror)
        header_layout.addWidget(self.btn_mirror)

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

        self.btn_open_sync = QPushButton(" Open Local Backup Folder")
        self.btn_open_sync.clicked.connect(self.open_sync_folder)
        bottom_layout.addWidget(self.btn_open_sync)

        self.btn_open_mirror = QPushButton(" Open Mirror Folder")
        self.btn_open_mirror.clicked.connect(self.open_mirror_folder)
        bottom_layout.addWidget(self.btn_open_mirror)

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

        secondary_style = """
            QPushButton {{
                background-color: transparent; color: {white}; border: 1px solid {main};
                border-radius: 5px; padding: 8px 12px; font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.1);
                border: 1px solid {neon}; color: {neon};
            }}
            QPushButton:disabled {{
                color: {muted}; border-color: {muted};
            }}
        """.format(white=text_white, main=main_accent, neon=bright_neon, muted=text_muted)
        self.btn_mirror.setStyleSheet(secondary_style)
        self.btn_open_mirror.setStyleSheet(secondary_style)

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
            QMessageBox.warning(self, "Folder Not Found",
                                "The local backup folder does not exist yet. Run a sync first.")

    def open_mirror_folder(self):
        mirror = self.manager.get_mirror_folder()
        if mirror and os.path.exists(mirror):
            QDesktopServices.openUrl(QUrl.fromLocalFile(mirror))
        else:
            QMessageBox.warning(self, "Folder Not Found",
                                "The mirror folder is not configured or does not exist yet.")

    def push_mirror(self):
        if not self.manager.get_mirror_folder():
            QMessageBox.information(
                self, "Mirror Folder",
                "No mirror folder is configured. Choose one in Settings → Savegames Manager."
            )
            return

        report = self.manager.push_mirror()
        if report.get('errors'):
            QMessageBox.warning(
                self, "Mirror finished with warnings",
                "{} file(s) copied, {} already up to date, {} issue(s).\n"
                "The local backups are intact; see the log for details.".format(
                    report.get('copied', 0), report.get('skipped', 0), len(report['errors'])
                )
            )
        else:
            QMessageBox.information(
                self, "Mirror updated",
                "{} file(s) copied, {} already up to date.".format(
                    report.get('copied', 0), report.get('skipped', 0)
                )
            )
        self.refresh_view()

    def refresh_view(self):
        local_folder = self.manager.get_sync_folder()
        mirror_folder = self.manager.get_mirror_folder()

        self._clear_cards()
        self.sync_info_label.setText("")
        self.message_label.setText("")

        self.btn_sync_all.setEnabled(True)
        self.btn_open_sync.setEnabled(True)
        self.btn_mirror.setEnabled(bool(mirror_folder))
        self.btn_open_mirror.setEnabled(bool(mirror_folder) and os.path.exists(mirror_folder))

        # First run with a configured mirror: bring in backups that only exist
        # there (one-time, copy only, nothing is deleted).
        try:
            self.manager.ensure_mirror_import()
        except Exception as exc:
            print(f"[Savegames] Mirror import failed: {exc}")

        interval = self.app_config.get('savegames', {}).get('sync_interval', 30)
        unit = self.app_config.get('savegames', {}).get('sync_unit', 'minutes')
        mirror_html = html.escape(mirror_folder) if mirror_folder else "not configured"
        self.sync_info_label.setText(
            "Local backup folder: <b>{}</b> | Mirror: {} | Interval: {} {}".format(
                html.escape(local_folder), mirror_html, interval, unit
            )
        )

        if not os.path.exists(local_folder):
            self.message_label.setText(
                "The local backup folder does not exist yet. It will be created when you run a sync."
            )
            self.btn_open_sync.setEnabled(False)
            return

        self._populate_cards(local_folder)

    def _clear_cards(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._cards.clear()

    def _populate_cards(self, sync_folder):
        mirror_states = {}
        if self.manager.get_mirror_folder():
            try:
                for entry in self.manager.compare_mirror():
                    mirror_states[entry.get('safe_title')] = entry
                    mirror_states[entry.get('title')] = entry
            except Exception as exc:
                print(f"[Savegames] Mirror compare failed: {exc}")

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
                    last_sync_str = _format_manifest_time(smeta.get('last_synced', '')) or "Never synced"
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

            state_entry = mirror_states.get(name) or mirror_states.get(game_title)
            if state_entry:
                card.setToolTip(self._mirror_tooltip(state_entry))

            self.flow_layout.addWidget(card)
            self._cards.append(card)

    @staticmethod
    def _mirror_tooltip(entry):
        label = MIRROR_STATE_LABELS.get(entry.get('state'), str(entry.get('state') or ''))
        lines = ["Mirror: {}".format(label)]
        if entry.get('local_version'):
            lines.append("Local: {}".format(game_version.describe_version(entry['local_version'])))
        if entry.get('mirror_version'):
            lines.append("Mirror copy: {}".format(game_version.describe_version(entry['mirror_version'])))
        return "\n".join(lines)

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
        summary = self.manager.sync_all_games()
        created = summary.get('synced_games', 0)
        unchanged = summary.get('unchanged_games', 0)
        mirror = summary.get('mirror') or {}
        mirror_text = ""
        if mirror.get('configured'):
            mirror_text = "\nMirror: {} file(s) copied, {} already up to date.".format(
                mirror.get('copied', 0), mirror.get('skipped', 0)
            )
        if summary.get('errors'):
            QMessageBox.warning(
                self, "Sync finished with warnings",
                "Some savegames could not be synced. Check the log or the messages below."
                + mirror_text
            )
        else:
            QMessageBox.information(
                self, "Sync complete",
                "{} new snapshot(s), {} game(s) already up to date.{}".format(
                    created, unchanged, mirror_text
                )
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
        """Resolve the live save folder for a game card.

        The installed game is the source of truth: its meta holds the portable
        path and the current Steam shortcut id, so a reinstall (or a different
        compatdata prefix) cannot leave us pointing at a stale location.
        """
        game_title = smeta.get('game_title', '')
        live_meta = self.installed_games.get(game_title) if game_title else None
        if live_meta:
            path = self.manager.get_source_save_path(live_meta)
            if path:
                return path

        path = self.manager.get_source_save_path(smeta)
        if path:
            return path

        # Last resort: the folder recorded when the snapshot was taken.
        recorded = smeta.get('source_path', '')
        if recorded and os.path.exists(recorded):
            return recorded
        return ""

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
        """Pick a local snapshot and restore it through BackupService."""
        smeta = self.manager.read_game_manifest(game_root)
        title = smeta.get('game_title') or os.path.basename(os.path.normpath(game_root))
        live_meta = self.installed_games.get(title)
        if not live_meta:
            QMessageBox.warning(
                self, "Game Not Installed",
                "<b>{}</b> is not installed right now.<br><br>"
                "Install the game first: the save folder depends on the installation "
                "path (and, on Linux, on the Proton prefix), so a backup can only be "
                "restored on a machine where the game is present.".format(html.escape(title))
            )
            return

        snapshots = self.backup_service.list_snapshots(title)
        if not snapshots:
            QMessageBox.information(self, "No Backups", "No backup snapshots found for this game.")
            return

        destination = self.manager.get_source_save_path(live_meta)
        if not destination:
            QMessageBox.warning(
                self, "No Save Path",
                "The save folder for <b>{}</b> could not be resolved.<br><br>"
                "Use <b>Point to Saves Folder</b> first.".format(html.escape(title))
            )
            return

        create_missing = False
        if not os.path.exists(destination):
            reply = QMessageBox.question(
                self, "Save Folder Missing",
                "The save folder does not exist yet:<br><code>{}</code><br><br>"
                "This usually means the game has not been launched since it was installed. "
                "Create it and restore the backup into it?".format(html.escape(destination)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            create_missing = True

        installed_version = game_version.version_info_from_meta(live_meta)
        # The staging folder is keyed by the manifest title; keep it even when
        # the live meta uses a slightly different display title.
        restore_meta = dict(live_meta)
        restore_meta['title'] = title

        dialog = QDialog(self)
        dialog.setWindowTitle("Select Backup to Restore")
        dialog.setMinimumWidth(480)
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
                text-align: left;
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

        target_label = QLabel("Destination: <code>{}</code>".format(html.escape(destination)))
        target_label.setWordWrap(True)
        target_label.setStyleSheet("font-size: 11px; color: #cba6d1;")
        dl.addWidget(target_label)

        for snap in snapshots:
            btn = QPushButton(self._snapshot_button_label(snap))
            btn.setToolTip(
                "Snapshot {} · {} · {} file state(s)".format(
                    snap.get('id', ''),
                    game_version.describe_version(snap.get('version') or {}),
                    "complete" if snap.get('complete', True) else "INCOMPLETE",
                )
            )
            btn.clicked.connect(
                lambda checked, s=snap: self._confirm_restore(
                    restore_meta, s, destination, installed_version, create_missing, dialog
                )
            )
            dl.addWidget(btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setStyleSheet("background-color: transparent; border: 1px solid #733d91;")
        cancel_btn.clicked.connect(dialog.reject)
        dl.addWidget(cancel_btn)

        dialog.exec()

    @staticmethod
    def _snapshot_button_label(snap):
        created = str(snap.get('created_at') or '')
        when = created or str(snap.get('id') or '')
        try:
            if created.endswith('Z'):
                parsed = datetime.strptime(created, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
                when = parsed.astimezone().strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass

        version = game_version.describe_version(snap.get('version') or {})
        notes = []
        reason = str(snap.get('reason') or '')
        if reason == 'pre_update':
            notes.append("pre-update")
        elif reason == 'pre_restore':
            notes.append("before restore")
        if not snap.get('complete', True):
            notes.append("INCOMPLETE")
        suffix = " · " + ", ".join(notes) if notes else ""
        return "{} · {}{}".format(when, version, suffix)

    def _confirm_restore(self, live_meta, snap, destination, installed_version,
                         create_missing, dialog):
        title = live_meta.get('title') or live_meta.get('game_title') or 'the game'
        version = snap.get('version') or {}
        comparison = game_version.compare_versions(version, installed_version)

        message = (
            "Restore the backup <b>{}</b> for <b>{}</b>?<br><br>"
            "Destination: <code>{}</code><br><br>"
        ).format(
            html.escape(self._snapshot_button_label(snap)),
            html.escape(str(title)),
            html.escape(destination),
        )

        if not snap.get('complete', True):
            message += (
                "<b>Warning:</b> this snapshot is marked as incomplete "
                "({} file(s) could not be copied).<br><br>".format(snap.get('errors', 0))
            )

        if comparison['status'] == game_version.STATUS_DIFFERENT:
            message += (
                "<b>Version mismatch</b><br>"
                "Backup: {}<br>Installed: {}<br><br>"
                "Savegames from a different build may not load or may corrupt your progress. "
                "Check the game's official changelog and the developer's notes before restoring."
                "<br><br>"
            ).format(
                html.escape(game_version.describe_version(version)),
                html.escape(game_version.describe_version(installed_version)),
            )
        elif comparison['status'] == game_version.STATUS_UNKNOWN:
            message += (
                "<b>Version not recorded</b><br>"
                "The build of this backup is unknown, so compatibility cannot be checked. "
                "Check the official changelog and the developer's notes before restoring."
                "<br><br>"
            )

        message += "The current saves will be snapshotted automatically before restoring."

        reply = QMessageBox.question(
            self, "Restore Backup", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        result = self.backup_service.restore_game(
            live_meta, snap.get('id', ''), create_missing=create_missing
        )
        if result.get('ok'):
            safety = result.get('safety_snapshot') or "none"
            QMessageBox.information(
                self, "Restore Complete",
                "Restored {} file(s) into:<br><code>{}</code><br><br>"
                "Safety snapshot: <b>{}</b>".format(
                    result.get('files', 0),
                    html.escape(result.get('destination', destination)),
                    html.escape(str(safety)),
                )
            )
            dialog.accept()
            self.refresh_view()
        else:
            QMessageBox.critical(
                self, "Restore Failed",
                self._restore_error_text(result)
            )

    @staticmethod
    def _restore_error_text(result):
        reason = str(result.get('reason') or '')
        detail = str(result.get('detail') or '')
        errors = result.get('errors') or []
        messages = {
            'not_installed': "The game is not installed, so its save folder cannot be resolved.",
            'snapshot_not_found': "The selected snapshot no longer exists.",
            'destination_unknown': "The save folder could not be resolved for this game.",
            'destination_missing': "The save folder does not exist yet. Confirm creating it first.",
            'destination_parent_missing': "The parent folder of the save path does not exist.",
            'destination_kind_mismatch': "The destination is a file, but this backup contains a folder.",
            'unsafe_target': "The save path is not a folder the launcher is allowed to write into"
                             " ({}).<br><br>Use <b>Point to Saves Folder</b> to choose it explicitly.".format(detail or "outside allowed roots"),
            'safety_snapshot_failed': "The current saves could not be snapshotted, so nothing was overwritten.",
            'copy_failed': "The backup content could not be read or written.",
            'swap_failed': "The save folder could not be replaced.",
            'snapshot_content_missing': "The snapshot is missing its data file.",
        }
        text = messages.get(reason, "Unexpected error while restoring the backup.")
        if errors:
            text += "<br><br>" + "<br>".join(html.escape(str(err)) for err in errors[:5])
        return text

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
