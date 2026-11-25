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
    try:
        logging.getLogger("startup").info("[STARTUP] %s: %.0fms", label, elapsed)
    except Exception:
        pass


def _want_dev_console() -> bool:
    return ("--console" in sys.argv
            or os.environ.get("AETHICC_CONSOLE", "").strip().lower()
            in ("1", "true", "yes", "on"))


def _strip_console_flag() -> None:
    while "--console" in sys.argv:
        sys.argv.remove("--console")


def _attach_windows_console() -> None:
    """Open a console on demand for a --noconsole Windows build."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8", errors="replace")
        except OSError:
            pass
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
    try:
        logging.getLogger("crash").critical(
            "Uncaught exception", exc_info=(exc_type, exc, tb))
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


sys.excepthook = _log_unhandled_exception

# Console mode must be decided before logging_setup captures the real streams.
_want_console = _want_dev_console()
_strip_console_flag()
if _want_console:
    _attach_windows_console()

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QDialog, QMessageBox,
    QProxyStyle, QStyle,
)
from PyQt6.QtGui import QIcon, QPixmap, QFontDatabase
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve

# Assicuriamoci che la root del progetto sia nel sys.path per le importazioni assolute
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import get_base_dir, load_config
from core.logging_setup import (
    install_print_bridge,
    install_qt_message_handler,
    setup_logging,
)
from core import data_files
from core.paths import PathManager
from core.single_instance import SingleInstanceLock
from core.version import __version__

_console = _want_console or not getattr(sys, "frozen", False)
setup_logging(console=_console)
install_print_bridge()
install_qt_message_handler()
logging.getLogger("startup").info(
    "[STARTUP] Ae Thicc Launcher %s (console=%s)", __version__, _console)
_phase("imports + logging ready")

from core.master_catalog import CatalogCorruptionError
from ui.main_window import AppWindow

log = logging.getLogger(__name__)


class _AbsoluteScrollBarStyle(QProxyStyle):
    """Left-clicking a scrollbar groove jumps to the clicked position.

    Qt's default pages by a screen; most users expect the handle to teleport
    under the cursor (the usual way to scroll long lists). ``SH_ScrollBar_
    LeftClickAbsolutePosition`` turns that on for every scrollbar in the app,
    including the ones themed by the app stylesheet.
    """

    def styleHint(self, hint, option=None, widget=None, return_data=None):
        if hint == QStyle.StyleHint.SH_ScrollBar_LeftClickAbsolutePosition:
            return 1
        return super().styleHint(hint, option, widget, return_data)


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
        log.exception(f"[Startup] onboarding wizard failed: {exc}")
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

    if sys.platform.startswith("linux"):
        # Qt 6.11 cannot rasterize the COLRv1 "Noto Color Emoji" font and
        # prefers it over the bundled monochrome emoji font, leaving the UI
        # icons blank. Point fontconfig at the shipped config that rejects it.
        fontconfig_conf = os.path.join(
            get_base_dir(), ".media", "fonts", "fonts.conf")
        if os.path.isfile(fontconfig_conf):
            os.environ.setdefault("FONTCONFIG_FILE", fontconfig_conf)

    app = QApplication(sys.argv)
    app.setStyle(_AbsoluteScrollBarStyle(app.style()))
    app.setApplicationName("AeThiccLauncher")
    # Teardown hides the window while workers drain, so the window closing
    # must not end the process by itself: the final shutdown stage calls
    # QApplication.quit() once no worker is running.
    app.setQuitOnLastWindowClosed(False)
    if sys.platform.startswith("linux"):
        # Wayland takes the window/taskbar icon from the .desktop entry that
        # matches this app id, so setWindowIcon() alone is not enough.
        app.setDesktopFileName("com.MJK.AeThiccLauncher")
    _phase("QApplication ready")

    # Emoji are UI icons here: Windows always has Segoe UI Emoji, Linux and
    # Flatpak may ship no emoji font at all (blank glyphs). Bundle one.
    emoji_font = os.path.join(get_base_dir(), ".media", "fonts", "NotoEmoji-Regular.ttf")
    if os.path.exists(emoji_font):
        font_id = QFontDatabase.addApplicationFont(emoji_font)
        if font_id < 0:
            log.info(f"[Fonts] Could not load the emoji font: {emoji_font}")
    else:
        log.info(f"[Fonts] Emoji font missing: {emoji_font}")
    if sys.platform.startswith("linux"):
        # COLRv1 colour emoji (the Noto Color Emoji packaged by Fedora/Nobara)
        # is not rasterised by Qt 6.11, so ship a COLRv0 colour font it can
        # draw. Qt prefers it over the monochrome fallback automatically.
        colour_font = os.path.join(
            get_base_dir(), ".media", "fonts", "Twemoji.Mozilla.ttf")
        if os.path.exists(colour_font):
            font_id = QFontDatabase.addApplicationFont(colour_font)
            if font_id < 0:
                log.info(f"[Fonts] Could not load the colour emoji font: {colour_font}")
        else:
            log.info(f"[Fonts] Colour emoji font missing: {colour_font}")
    _phase("fonts loaded")

    # Usiamo get_base_dir() per avere un percorso affidabile sia da script che da exe compilato
    icon_path = os.path.join(get_base_dir(), ".media", "icon.png")

    # Fallback sicuro: se non c'è il png tenta con l'ico
    if not os.path.exists(icon_path):
        icon_path = os.path.join(get_base_dir(), ".media", "icon.ico")

    app_icon = None
    if os.path.exists(icon_path):
        app_icon = QIcon(icon_path)
        app.setWindowIcon(app_icon)

    # --- SINGLE INSTANCE (same data root -> one launcher only) ---
    instance_lock = SingleInstanceLock(
        SingleInstanceLock.path_for_data_root(PathManager.get_data_root()))
    if not instance_lock.acquire():
        QMessageBox.warning(
            None, "Ae Thicc Launcher",
            "Another Ae Thicc Launcher instance is already running.\n\n"
            "Close it before starting a new one.",
        )
        sys.exit(1)

    # --- FIRST RUN SETUP (prima della finestra: i percorsi devono essere già corretti) ---
    onboarded = _run_onboarding(load_config())
    _phase("onboarding check done")

    # --- DATA SEED (data/ nell'install e' solo una sorgente) ---
    try:
        copied, kept = data_files.ensure_user_data()
        if copied:
            log.info(f"[DataFiles] Seeded {copied} file(s) into {data_files.user_root()}")
        _phase(f"data seed done (copied={copied}, kept={kept})")
    except Exception as exc:
        log.exception(f"[DataFiles] seed failed: {exc}")

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
    try:
        window = AppWindow()
    except CatalogCorruptionError as exc:
        if splash is not None:
            splash.close()
        message = str(exc)
        try:
            from core.paths import PathManager

            message += f"\n\nLogs: {PathManager.get_logs_dir()}"
        except Exception:
            pass
        QMessageBox.critical(None, "Ae Thicc Launcher", message)
        sys.exit(1)
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

    app.aboutToQuit.connect(window.on_about_to_quit)

    # Test-only hook used by the shutdown smoke test: close the window after N
    # milliseconds. No effect unless the environment variable is set.
    smoke_ms = os.environ.get("AETHICC_SMOKE_QUIT_MS", "")
    if smoke_ms.isdigit():
        QTimer.singleShot(int(smoke_ms), window.close)

    sys.exit(app.exec())
