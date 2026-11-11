import sys
import os
import time
import logging
import traceback

# stdout/stderr robusti: i titoli/le badge contengono emoji e un print su una
# console cp1252 sollevava UnicodeEncodeError dentro uno slot Qt -> qFatal
# (l'app si chiudeva senza preavviso). Con errors="replace" non puo' piu' fallire.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_T0 = time.monotonic()


def _phase(label):
    elapsed = (time.monotonic() - _T0) * 1000.0
    message = f"[STARTUP] {label}: {elapsed:.0f}ms"
    print(message)
    try:
        logging.getLogger("startup").info(message)
    except Exception:
        pass


def _log_unhandled_exception(exc_type, exc, tb):
    """Salva il traceback su disco prima che Qt abortisca."""
    try:
        import datetime

        from core.paths import PathManager

        folder = PathManager.get_app_data_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        path = os.path.join(str(folder), "crash.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== {datetime.datetime.now().isoformat()} ===\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_unhandled_exception

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDialog
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

# Assicuriamoci che la root del progetto sia nel sys.path per le importazioni assolute
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import get_base_dir, load_config
from core.logging_setup import setup_logging
from core import data_files
from core.version import __version__

setup_logging()
logging.getLogger("startup").info("[STARTUP] Ae Thicc Launcher %s", __version__)
_phase("imports + logging ready")

from ui.main_window import AppWindow


def _on_fuse_fs(path: str) -> bool:
    try:
        real = os.path.realpath(path)
        with open("/proc/mounts", "r", encoding="utf-8", errors="replace") as fh:
            best_len, best_type = -1, None
            for line in fh:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mountpoint, fstype = parts[1], parts[2]
                if real.startswith(mountpoint) and len(mountpoint) > best_len:
                    best_len, best_type = len(mountpoint), fstype
        return best_type is not None and best_type.startswith("fuse")
    except OSError:
        return False


def _needs_onboarding(config) -> bool:
    """First run only: no completed onboarding and no library configured yet."""
    # Installer/upgrade pointed us at a new data root that has no config yet:
    # run the wizard even if the legacy config says onboarding was completed.
    try:
        from core.config import CONFIG_FILE
        from core.paths import PathManager

        if PathManager.has_custom_root():
            new_cfg = PathManager.get_config_dir() / CONFIG_FILE
            old_cfg = PathManager.get_legacy_root() / "config" / CONFIG_FILE
            if not new_cfg.exists() and (old_cfg.exists() or not config.get("watched_folders")):
                return True
    except Exception:
        pass

    if config.get("onboarding", {}).get("completed"):
        return False
    if config.get("watched_folders"):
        return False
    return True


def _run_onboarding(config) -> bool:
    """Show the first-run wizard. Returns True when it was presented."""
    if not _needs_onboarding(config):
        return False
    try:
        from ui.dialogs.onboarding_wizard import OnboardingWizard

        wizard = OnboardingWizard(config)
        if wizard.exec() == QDialog.DialogCode.Accepted:
            # The data root may have moved: re-attach logging to the new folder.
            setup_logging()
        return True
    except Exception as exc:
        print(f"[Startup] onboarding wizard failed: {exc}")
        return False


class SplashWidget(QWidget):
    def __init__(self, pixmap):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(620, 300)

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(geo.center() - self.rect().center())

        scaled = pixmap.scaled(620, 300, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(self)
        label.setPixmap(scaled)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)

    def fadeOut(self, duration=600, on_finished=None):
        self.anim = QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(duration)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.anim.finished.connect(self.close)
        if on_finished is not None:
            self.anim.finished.connect(on_finished)
        self.anim.start()


if __name__ == "__main__":
    # IMPORTANTE: Su Windows, l'AppUserModelID deve essere impostato PRIMA di istanziare QApplication
    # altrimenti la taskbar userà comunque l'icona di default di python.exe o dell'eseguibile compilato.
    if sys.platform == "win32":
        try:
            import ctypes
            myappid = 'aethicc.launcher.1'  # Stringa arbitraria univoca
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        except Exception:
            pass

    if sys.platform == "linux" and _on_fuse_fs(get_base_dir()):
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    app = QApplication(sys.argv)
    app.setApplicationName("AeThiccLauncher")
    _phase("QApplication ready")

    # Usiamo get_base_dir() per avere un percorso affidabile sia da script che da exe compilato
    icon_path = os.path.join(get_base_dir(), ".media", "icon.png")

    # Fallback sicuro: se non c'è il png tenta con l'ico
    if not os.path.exists(icon_path):
        icon_path = os.path.join(get_base_dir(), ".media", "icon.ico")

    app_icon = None
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    # --- FIRST RUN SETUP (prima della finestra: i percorsi devono essere già corretti) ---
    onboarded = _run_onboarding(load_config())
    _phase("onboarding check done")

    # --- DATA SEED (data/ nell'install e' solo una sorgente) ---
    try:
        copied, kept = data_files.ensure_user_data()
        if copied:
            print(f"[DataFiles] Seeded {copied} file(s) into {data_files.user_root()}")
        _phase(f"data seed done (copied={copied}, kept={kept})")
    except Exception as exc:
        print(f"[DataFiles] seed failed: {exc}")

    # --- SPLASH SCREEN ---
    splash_path = os.path.join(get_base_dir(), ".media", "SplashScreen.png")
    splash = None
    if os.path.exists(splash_path):
        splash_pixmap = QPixmap(splash_path)
        if not splash_pixmap.isNull():
            splash = SplashWidget(splash_pixmap)
            splash.show()
            splash.raise_()
            splash.activateWindow()
            app.processEvents()

    splash_start = time.monotonic()
    window = AppWindow()
    # Evita che il check a 3s riproponga subito il wizard già mostrato.
    window._onboarding_prompted = onboarded
    _phase("AppWindow built")
    if app_icon is not None:
        window.setWindowIcon(app_icon)

    def _reveal():
        window.showMaximized()
        _phase("window shown")
        # Lavoro pesante (scan libreria, stats, discovery, catalogo) DOPO
        # la finestra visibile, così l'apertura non e' bloccata dall'I/O/HTTP.
        QTimer.singleShot(0, window.start_background_init)

    if splash is not None:
        MIN_SPLASH_MS = 1000
        elapsed_ms = (time.monotonic() - splash_start) * 1000
        remain_ms = max(0, MIN_SPLASH_MS - elapsed_ms)

        def show_and_fade():
            splash.fadeOut(450, on_finished=_reveal)

        if remain_ms > 0:
            QTimer.singleShot(int(remain_ms), show_and_fade)
        else:
            show_and_fade()
    else:
        _reveal()

    sys.exit(app.exec())
