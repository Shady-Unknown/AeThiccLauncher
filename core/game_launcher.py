import sys
import os
import subprocess
import json
import time
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QMessageBox, QFileDialog, QDialog, QApplication, QProgressDialog
from ui.dialogs.linux_wrapper_dialog import LinuxWrapperDialog
from ui.dialogs.steam_account_dialog import SteamAccountDialog
from ui.dialogs.steam_restart_dialog import SteamRestartDialog
from core import platform_info, steam_integration
from core.events import EVENT_GAME_LAUNCHED
from core.steam_batch_worker import SteamBatchWorker
#from core.config import get_base_dir
from core.process_monitor import GameMonitorThread
from core.meta_utils import save_steamrip_meta


class GameLauncher:
    def __init__(self, app_window):
        self.app_window = app_window
        self.active_monitors = []
        
    def _update_library_item(self, *args, **kwargs):
        self.app_window.lib_manager._update_library_item(*args, **kwargs)
        
    def _find_item_by_title(self, *args, **kwargs):
        return self.app_window.lib_manager._find_item_by_title(*args, **kwargs)

    def verify_steam_integrity(self, game_title):
        """Verifica se un gioco con ID Steam è ancora presente nella libreria Steam reale."""
        meta = self.app_window.installed_games.get(game_title)
        if not meta or meta.get("is_orphan"): return False
        
        steam_id = meta.get('steam_shortcut_id')
        if steam_id and str(steam_id).strip():
            # Controllo reale nel file shortcuts.vdf di Steam
            in_steam = steam_integration.is_game_in_steam(game_title)
            print(f"[Steam Check] '{game_title}' is_game_in_steam={in_steam}")
            if not in_steam:
                print(f"[Steam Check] '{game_title}' not found in Steam. Cleaning metadata...")
                meta['steam_shortcut_id'] = ''
                meta['steam_grid_id'] = ''
                if 'linux_wrapper' in meta: meta['linux_wrapper'] = ''
                
                self._save_meta(meta)
                
                # Aggiorna il pallino nella sidebar
                item = self._find_item_by_title(game_title)
                if item:
                    self._update_library_item(item, game_title, "Installed", meta=meta, game_title=game_title)
                return True
        return False

    def launch_game(self, game_title):
        # Verifica integrità Steam prima di ogni altra cosa
        self.verify_steam_integrity(game_title)
        
        meta = self.app_window.installed_games.get(game_title)
        if not meta or meta.get("is_orphan"): return
        
        exe_path = meta.get('executable_path', '')
        
        # --- FLUSSO LINUX ---
        if sys.platform == 'linux':
            wrapper = meta.get('linux_wrapper', '')
            steam_id = meta.get('steam_shortcut_id', '')
            
            # 2. SELEZIONE WRAPPER (Se è la prima volta o se è stato resettato)
            if not wrapper:
                dialog = LinuxWrapperDialog(game_title, self.app_window)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    wrapper = dialog.choice
                    meta['linux_wrapper'] = wrapper
                    self._save_meta(meta)
                else:
                    return # L'utente ha annullato
            
            # 3. Flusso STEAM
            if wrapper == 'steam':
                from core import steam_integration
                if not steam_id:
                    if not exe_path or not os.path.exists(exe_path):
                        QMessageBox.information(self.app_window, "Executable Selection", f"Select the startup executable (.exe) for {game_title}.\nThis operation needs to be done only once!")
                        file_path, _ = QFileDialog.getOpenFileName(self.app_window, "Select Executable", meta['install_dir'], "Executables (*.exe)")
                        if not file_path: return
                        exe_path = file_path
                        meta['executable_path'] = exe_path

                    # --- GESTIONE ACCOUNT MULTIPLI ---
                    accounts = steam_integration.get_steam_accounts()
                    if not accounts:
                        QMessageBox.critical(self.app_window, "Error", "No local Steam accounts found on this PC.")
                        return
                    
                    selected_account_path = ""
                    if len(accounts) == 1:
                        selected_account_path = accounts[0]['path']
                    else:
                        acc_dialog = SteamAccountDialog(accounts, self.app_window)
                        if acc_dialog.exec() == QDialog.DialogCode.Accepted:
                            selected_account_path = acc_dialog.selected_path
                        else:
                            return # Utente ha annullato

                    # Scelta riavvio Steam
                    restart_dialog = SteamRestartDialog(self.app_window)
                    restart_dialog.exec()
                    restart_steam = restart_dialog.restart_now

                    # Avviamo l'integrazione
                    try:
                        self.app_window.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Ongoing...</h3><p>Waiting for Steam to process data.</p>")
                        QApplication.processEvents()
                        
                        steam_id, grid_id, proton_ok, proton_ver, account_id = (
                            steam_integration.add_game_to_steam(
                                game_title, exe_path, meta['install_dir'], selected_account_path,
                                restart_steam=restart_steam
                            )
                        )
                        
                        meta['steam_shortcut_id'] = steam_id
                        meta['steam_grid_id'] = grid_id # Salviamo anche questo per il futuro
                        if account_id:
                            meta['steam_account_id'] = account_id
                        self._save_meta(meta)

                        if game_title not in self.app_window.bg_update_queue:
                            self.app_window.bg_update_queue.append(game_title)
                        if not self.app_window.is_bg_checking:
                            self.app_window.bg_scanner._process_next_bg_check()

                        # Refresh UI istantaneo: Semaforo Sidebar e Pulsante Play
                        item = self._find_item_by_title(game_title)
                        if item:
                            self._update_library_item(item, game_title, "Installed", meta=meta, game_title=game_title)
                        self.app_window.display_game_data(self.app_window.current_game_data)
                            
                        msg = "Integration completed!\n\n"
                        if restart_steam:
                            msg += "Click OK to launch Steam. It will take you directly to your new game page in the library!\n\n"
                        else:
                            msg += "The shortcut has been added. Restart Steam manually to see it in your library.\n\n"
                        
                        if proton_ok:
                            msg += f"✅ Compatibility Tool set to: {proton_ver}"
                        else:
                            msg += f"⚠️ Impossible to set Proton automatically. Please set it manually."
                            
                        QMessageBox.information(self.app_window, "Success", msg)
                        self.app_window.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Setup completed for {game_title}.</h3>")
                        
                        if restart_steam:
                            self._open_steam_game_page_nav(grid_id)
                        return 
                        
                    except Exception as e:
                        QMessageBox.critical(self.app_window, "Steam Error", f"Impossible to integrate the game with Steam:\n{e}")
                        return
                
                # 4. AVVIO NATIVO
                try:
                    self.app_window.task_label.setText(f"🎮 Running on Steam: {game_title}")
                    self.app_window.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
                    try:
                        # FIX: Usiamo direttamente 'steam' invece di 'xdg-open' e sganciamo il processo!
                        if platform_info.is_flatpak():
                            subprocess.Popen(['flatpak-spawn', '--host', 'steam', f"steam://rungameid/{steam_id}"],
                                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        else:
                            subprocess.Popen(['steam', f"steam://rungameid/{steam_id}"],
                                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception as e:
                        print(f"[Launch] Comando non trovato nel sistema: {e}")
                    # Avviamo il monitoraggio passando None come PID ma indicando l'exe da cercare
                    self._start_process_monitor(None, meta)
                except Exception as e:
                    QMessageBox.critical(self.app_window, "Launch Error", f"Impossible to start from Steam:\n{str(e)}")
            return # Usciamo dal launch_game per Linux
            
        # --- FLUSSO WINDOWS CLASSICO ---
        if not exe_path or not os.path.exists(exe_path):
            QMessageBox.information(self.app_window, "Launch Setup", f"Select the executable file (.exe) to start {game_title}.\n\nYou only have to do this once!")
            
            file_path, _ = QFileDialog.getOpenFileName(self.app_window, "Select Game Executable", meta['install_dir'], "Executables (*.exe)")
            if file_path:
                meta['executable_path'] = file_path
                self._save_meta(meta)
                self.app_window.installed_games[game_title] = meta
                self.app_window.display_game_data(self.app_window.current_game_data)
                return
            else:
                return 
                
        try:
            work_dir = os.path.dirname(meta['executable_path'])
            proc = subprocess.Popen([meta['executable_path']], cwd=work_dir)
            self.app_window.task_label.setText(f"🎮 Running: {game_title}")
            self.app_window.task_label.setStyleSheet("color: #00ff88; font-weight: bold; border: none; background: transparent;")
        
            self._start_process_monitor(proc.pid, meta)

        except Exception as e:
            QMessageBox.critical(self.app_window, "Launch Error", f"Impossible to start the game:\n{str(e)}")
    
    def _start_process_monitor(self, pid, meta):
        """Inizializza il thread di monitoraggio per il backup automatico."""
        # Pulizia thread terminati
        self.active_monitors = [m for m in self.active_monitors if m.isRunning()]
        
        monitor = GameMonitorThread(pid, meta, exe_path=meta.get('executable_path'))
        monitor.game_exited_signal.connect(self.app_window.on_game_exited_backup)
        events = getattr(self.app_window, 'events', None)
        if events is not None:
            events.emit_event(
                EVENT_GAME_LAUNCHED,
                title=meta.get('title') or '',
                install_dir=meta.get('install_dir') or '',
                pid=pid,
                started_at=int(time.time()),
            )
        monitor.start()
        self.active_monitors.append(monitor)

    def _open_steam_game_page_nav(self, app_id):
        """Open Steam library game page after a delay to ensure UI is ready."""
        QTimer.singleShot(3000, lambda: self._do_open_steam_url(app_id))

    def _do_open_steam_url(self, app_id):
        try:
            url = f"steam://nav/games/details/{app_id}"
            if platform_info.is_flatpak():
                subprocess.Popen(["flatpak-spawn", "--host", "xdg-open", url],
                                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", url],
                                 start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"[Launch] Comando non trovato nel sistema: {e}")

    def rescan_executable(self, game_title, meta):
        """Forces the selection of a new Executable and prompts wrapper re-integration."""
        QMessageBox.information(self.app_window, "Rescan Executable", f"Select the new startup executable (.exe) for {game_title}.")
        
        file_path, _ = QFileDialog.getOpenFileName(self.app_window, "Select Executable", meta['install_dir'], "Executables (*.exe)")
        if not file_path:
            return # Annullato
            
        meta['executable_path'] = file_path

        # --- FLUSSO LINUX: Mostra Wrapper Dialog post-selezione ---
        if sys.platform == 'linux':
            dialog = LinuxWrapperDialog(game_title, self.app_window)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                meta['linux_wrapper'] = dialog.choice
            else:
                # Se annulla il wrapper, salviamo solo l'exe e usciamo
                self._save_meta(meta)
                return

            # RE-INTEGRAZIONE FORZATA STEAM
            if meta['linux_wrapper'] == 'steam':
                from core import steam_integration
                
                # GESTIONE ACCOUNT
                accounts = steam_integration.get_steam_accounts()
                if not accounts: return
                
                selected_account_path = accounts[0]['path']
                if len(accounts) > 1:
                    acc_dialog = SteamAccountDialog(accounts, self.app_window)
                    if acc_dialog.exec() == QDialog.DialogCode.Accepted:
                        selected_account_path = acc_dialog.selected_path
                    else: return

                # Scelta riavvio Steam
                restart_dialog = SteamRestartDialog(self.app_window)
                restart_dialog.exec()
                restart_steam = restart_dialog.restart_now

                try:
                    self.app_window.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Re-integration in progress...</h3><p>Processing VDF files.</p>")
                    QApplication.processEvents()
                    
                    steam_id, grid_id, proton_ok, proton_ver, account_id = (
                        steam_integration.add_game_to_steam(
                            game_title, file_path, meta['install_dir'], selected_account_path,
                            restart_steam=restart_steam
                        )
                    )
                    
                    meta['steam_shortcut_id'] = steam_id
                    meta['steam_grid_id'] = grid_id
                    if account_id:
                        meta['steam_account_id'] = account_id
                    self._save_meta(meta)
                    
                    # --- AGGIORNAMENTO SEMAFORI ---
                    if game_title not in self.app_window.bg_update_queue:
                        self.app_window.bg_update_queue.append(game_title)
                    if not self.app_window.is_bg_checking:
                        self.app_window.bg_scanner._process_next_bg_check()

                    # Refresh UI istantaneo: Semaforo Sidebar e Pulsante Play
                    item = self._find_item_by_title(game_title)
                    if item:
                        self._update_library_item(item, game_title, "Installed", meta=meta, game_title=game_title)
                    self.app_window.display_game_data(self.app_window.current_game_data)

                    msg = "Re-integration completed!\n\n"
                    if restart_steam:
                        msg += "Click OK to open Steam directly on your updated game."
                    else:
                        msg += "The shortcut has been updated. Restart Steam manually to see the changes."
                    QMessageBox.information(self.app_window, "Success", msg)
                    self.app_window.desc_display.setHtml(f"<h3 style='color:#00ff88;'>Setup updated for {game_title}.</h3>")
                    
                    if restart_steam:
                        self._open_steam_game_page_nav(grid_id)
                    return 
                except Exception as e:
                    QMessageBox.critical(self.app_window, "Steam Error", f"Impossible to integrate the game with Steam:\n{e}")
                    return
        else:
            # --- FLUSSO WINDOWS ---
            self._save_meta(meta)
            QMessageBox.information(self.app_window, "Success", "Executable path updated successfully!")

    def _is_steam_running(self):
        """True se il processo Steam è attivo (usato dal flusso Windows)."""
        try:
            import psutil
            for p in psutil.process_iter(['name']):
                try:
                    if (p.info.get('name') or '').lower() in ('steam', 'steam.exe'):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
        except Exception:
            pass
        return False

    def _add_windows_game_to_steam_optional(self, title):
        meta = self.app_window.installed_games.get(title, {})
        exe_path = meta.get('executable_path', '')
        install_dir = meta.get('install_dir', '')
        
        # Blocco di sicurezza: Steam ha bisogno di un eseguibile valido per creare uno shortcut
        if not exe_path or not str(exe_path).strip():
            QMessageBox.warning(
                self.app_window, 
                "Executable Required", 
                "Please map the game executable first by clicking the 'SET EXECUTABLE' button, so we know what to link to Steam!"
            )
            return
            
        from core import steam_integration
        try:
            accounts = steam_integration.get_steam_accounts()
            if not accounts:
                QMessageBox.critical(self.app_window, "Steam Error", "No local Steam accounts found. Make sure Steam is installed and you've logged in at least once.")
                return
                
            # Mostriamo il dialog di selezione dell'account Steam già presente nella codebase
            dialog = SteamAccountDialog(accounts, self.app_window)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_path:
                self.app_window.task_label.setText("🔗 Adding shortcut to Steam...")
                self.app_window.task_progress.setRange(0, 0)
                self.app_window.task_progress.show()
                QApplication.processEvents()
                
                # Scelta riavvio Steam
                restart_dialog = SteamRestartDialog(self.app_window)
                restart_dialog.exec()
                restart_steam = restart_dialog.restart_now

                # Windows: se Steam resta aperto sovrascrive shortcuts.vdf alla chiusura,
                # facendo sparire lo shortcut appena aggiunto. Chiediamo di chiuderlo.
                if not restart_steam and self._is_steam_running():
                    answer = QMessageBox.question(
                        self.app_window,
                        "Steam Is Running",
                        "Steam is currently running.\n\n"
                        "If the shortcut is written while Steam is open, Steam will overwrite "
                        "it when it closes and the game will disappear from your library.\n\n"
                        "Close Steam now and add the shortcut? (You can restart Steam manually later.)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.Yes
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        self.app_window.task_progress.hide()
                        self.app_window.task_label.setText("Ready")
                        return
                    steam_integration.kill_steam_safely()

                # Invochiamo il modulo cross-platform con la firma aggiornata
                shortcut_id, grid_id, proton_ok, proton_ver, account_id = (
                    steam_integration.add_game_to_steam(
                        game_title=title,
                        exe_path=exe_path,
                        install_dir=install_dir,
                        selected_user_dir=dialog.selected_path,
                        restart_steam=restart_steam
                    )
                )
                
                if shortcut_id and grid_id:
                    meta['steam_shortcut_id'] = shortcut_id
                    meta['steam_grid_id'] = grid_id
                    if account_id:
                        meta['steam_account_id'] = account_id
                    self._save_meta(meta)
                    
                    self.app_window.installed_games[title] = meta

                    msg = f"'{title}' successfully integrated into Steam!\n\n"
                    if restart_steam:
                        msg += "Steam has been restarted to apply the changes."
                    else:
                        msg += "Please restart Steam manually to see it in your library."
                    QMessageBox.information(self.app_window, "Success", msg)
                    
                    # --- AGGIORNAMENTO SEMAFORI ---
                    item = self._find_item_by_title(title)
                    if item:
                        self._update_library_item(item, title, "Installed", meta=meta, game_title=title)

                    if title not in self.app_window.bg_update_queue:
                        self.app_window.bg_update_queue.append(title)
                    if not self.app_window.is_bg_checking:
                        self.app_window.bg_scanner._process_next_bg_check()

                    # Rinfresca istantaneamente la tab per nascondere il bottone opzionale appena completato
                    self.app_window.display_game_data(self.app_window.current_game_data)
                else:
                    QMessageBox.critical(self.app_window, "Error", "Failed to write shortcut into Steam's shortcuts.vdf file.")
                    
                self.app_window.task_progress.hide()
                self.app_window.task_label.setText("Ready")
        except Exception as e:
            self.app_window.task_progress.hide()
            self.app_window.task_label.setText("Ready")
            QMessageBox.critical(self.app_window, "Integration Failed", f"An unexpected error occurred:\n{str(e)}")

    def _collect_steam_candidates(self):
        """Giochi installati senza shortcut Steam ma con un eseguibile valido."""
        candidates = []
        for title, meta in self.app_window.installed_games.items():
            if not meta or meta.get("is_orphan"):
                continue
            if str(meta.get("steam_shortcut_id", "") or "").strip():
                continue
            exe_path = meta.get("executable_path", "")
            if not exe_path or not str(exe_path).strip() or not os.path.exists(exe_path):
                continue
            candidates.append((title, meta))
        candidates.sort(key=lambda item: item[0].lower())
        return candidates

    def _is_steam_batch_running(self):
        worker = getattr(self, "_steam_batch_worker", None)
        return worker is not None and worker.isRunning()

    def sync_all_games_to_steam(self):
        """
        Aggiunge in blocco a Steam tutti i giochi installati che non hanno ancora
        uno shortcut. Steam viene chiuso UNA sola volta e riavviato UNA sola volta.
        """
        if sys.platform != 'win32':
            return

        if self._is_steam_batch_running():
            QMessageBox.information(self.app_window, "Sync In Progress", "A Steam sync is already running.")
            return

        candidates = self._collect_steam_candidates()
        if not candidates:
            QMessageBox.information(
                self.app_window,
                "Nothing to Add",
                "Every installed game is already in Steam.\n\n"
                "Games without an executable are skipped: use 'SET EXECUTABLE' first."
            )
            return

        accounts = steam_integration.get_steam_accounts()
        if not accounts:
            QMessageBox.critical(
                self.app_window,
                "Steam Error",
                "No local Steam accounts found. Make sure Steam is installed and you've logged in at least once."
            )
            return

        selected_account_path = accounts[0]['path']
        account_name = accounts[0].get('name', '')
        if len(accounts) > 1:
            acc_dialog = SteamAccountDialog(accounts, self.app_window)
            if acc_dialog.exec() != QDialog.DialogCode.Accepted or not acc_dialog.selected_path:
                return
            selected_account_path = acc_dialog.selected_path
            account_name = next(
                (a.get('name', '') for a in accounts if a.get('path') == selected_account_path),
                account_name
            )

        titles_preview = "\n".join(f"• {t}" for t, _ in candidates)
        reply = QMessageBox.question(
            self.app_window,
            "Add All to Steam",
            f"{len(candidates)} game(s) will be added to Steam under '{account_name}':\n\n"
            f"{titles_preview}\n\n"
            "Steam will be closed once, the shortcuts written, then Steam will be restarted once.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        restart_dialog = SteamRestartDialog(self.app_window)
        restart_dialog.exec()
        restart_steam = restart_dialog.restart_now

        # Con Steam aperto il VDF verrebbe sovrascritto alla chiusura.
        if not restart_steam and self._is_steam_running():
            answer = QMessageBox.question(
                self.app_window,
                "Steam Is Running",
                "Steam is currently running.\n\n"
                "If the shortcuts are written while Steam is open, Steam will overwrite "
                "them when it closes.\n\n"
                "Close Steam now and add the shortcuts? (You can restart Steam manually later.)",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        self.app_window.task_label.setText(f"🔗 Adding {len(candidates)} game(s) to Steam...")
        self.app_window.task_progress.setRange(0, 0)
        self.app_window.task_progress.show()
        QApplication.processEvents()

        # Chiudiamo Steam una sola volta, prima di scrivere i VDF.
        steam_integration.kill_steam_safely()

        jobs = [(t, m.get('executable_path', ''), m.get('install_dir', '')) for t, m in candidates]

        self._steam_batch_restart = restart_steam
        self._steam_batch_progress = QProgressDialog(
            f"Adding {len(jobs)} game(s) to Steam...", "Cancel", 0, len(jobs), self.app_window
        )
        self._steam_batch_progress.setWindowTitle("Adding Games to Steam")
        self._steam_batch_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._steam_batch_progress.setMinimumDuration(0)
        self._steam_batch_progress.setAutoClose(False)
        self._steam_batch_progress.setAutoReset(False)
        self._steam_batch_progress.setValue(0)

        self._steam_batch_worker = SteamBatchWorker(
            jobs,
            selected_account_path,
            restart_steam,
            parent=self.app_window
        )
        self._steam_batch_worker.progress.connect(self._on_steam_batch_progress)
        self._steam_batch_worker.finished_all.connect(self._on_steam_batch_finished)
        self._steam_batch_progress.canceled.connect(self._steam_batch_worker.cancel)
        self._steam_batch_worker.start()

    def _on_steam_batch_progress(self, done, total, title):
        progress = getattr(self, "_steam_batch_progress", None)
        if progress is not None:
            progress.setMaximum(total)
            progress.setValue(done)
            progress.setLabelText(f"Adding to Steam ({done}/{total}):\n{title}")

        self.app_window.task_progress.setRange(0, max(total, 1))
        self.app_window.task_progress.setValue(done)
        self.app_window.task_label.setText(f"🔗 Adding to Steam ({done}/{total}): {title}")

    def _on_steam_batch_finished(self, successes, failures):
        progress = getattr(self, "_steam_batch_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
            self._steam_batch_progress = None

        restart_steam = getattr(self, "_steam_batch_restart", False)
        restart_ok = getattr(
            getattr(self, "_steam_batch_worker", None), "restart_ok", None
        )

        for title, shortcut_id, grid_id, proton_ok, proton_ver, account_id in successes:
            meta = self.app_window.installed_games.get(title)
            if not meta:
                continue
            meta['steam_shortcut_id'] = shortcut_id
            meta['steam_grid_id'] = grid_id
            if account_id:
                meta['steam_account_id'] = account_id
            self._save_meta(meta)

            item = self._find_item_by_title(title)
            if item:
                self._update_library_item(item, title, "Installed", meta=meta, game_title=title)

            if title not in self.app_window.bg_update_queue:
                self.app_window.bg_update_queue.append(title)

        if successes and not self.app_window.is_bg_checking:
            self.app_window.bg_scanner._process_next_bg_check()

        self.app_window.task_progress.hide()
        self.app_window.task_progress.setRange(0, 0)
        self.app_window.task_label.setText("Ready")
        self.app_window.display_game_data(self.app_window.current_game_data)

        msg = f"{len(successes)} game(s) added to Steam."
        if restart_steam:
            if restart_ok is False:
                msg += (
                    "\n\nSteam could not be restarted automatically. "
                    "Please start it manually to see them in your library."
                )
            else:
                msg += "\n\nSteam is restarting to show them in your library."
        else:
            msg += "\n\nRestart Steam manually to see them."
        if failures:
            msg += "\n\nCould not add:\n" + "\n".join(f"• {t}: {e}" for t, e in failures)

        if successes:
            QMessageBox.information(self.app_window, "Steam Sync Complete", msg)
        else:
            QMessageBox.warning(self.app_window, "Steam Sync", msg or "No game could be added.")

    def _save_meta(self, meta):
        if not meta or 'install_dir' not in meta:
            return
        save_steamrip_meta(meta['install_dir'], meta)
