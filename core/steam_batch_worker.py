# steam_batch_worker.py
from PyQt6.QtCore import QThread, pyqtSignal


class SteamBatchWorker(QThread):
    """
    Aggiunge in blocco a Steam una lista di giochi usando un solo account.

    Steam viene chiuso dal chiamante UNA sola volta prima di avviare il worker
    (altrimenti sovrascriverebbe shortcuts.vdf alla chiusura) e riavviato UNA
    sola volta alla fine, se richiesto.
    """

    # (completati, totale, titolo in corso)
    progress = pyqtSignal(int, int, str)
    # (successi, errori)
    # successi: list[tuple(title, shortcut_id, grid_id, proton_ok, proton_ver, account_id)]
    # errori:   list[tuple(title, error_message)]
    finished_all = pyqtSignal(list, list)

    def __init__(self, jobs, selected_user_dir, restart_steam, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.selected_user_dir = selected_user_dir
        self.restart_steam = restart_steam
        self.restart_ok = None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        from core import steam_integration

        successes = []
        failures = []
        total = len(self.jobs)

        for idx, job in enumerate(self.jobs, start=1):
            if self._cancelled:
                break

            title, exe_path, install_dir = job
            self.progress.emit(idx - 1, total, title)

            try:
                result = steam_integration.add_game_to_steam(
                    game_title=title,
                    exe_path=exe_path,
                    install_dir=install_dir,
                    selected_user_dir=self.selected_user_dir,
                    restart_steam=False
                )
                # Il main ritorna 4 valori, il branch Discovery 5 (+ account_id):
                # prendiamo solo i primi 4 per compatibilità.
                shortcut_id, grid_id, proton_ok, proton_ver = result[:4]
                account_id = result[4] if len(result) > 4 else None
                successes.append((title, shortcut_id, grid_id, proton_ok, proton_ver, account_id))
            except Exception as e:
                failures.append((title, str(e)))

            self.progress.emit(idx, total, title)

        if self.restart_steam:
            self.restart_ok = steam_integration.restart_steam()

        self.finished_all.emit(successes, failures)
