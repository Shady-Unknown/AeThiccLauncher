"""Shared QApplication for tests that need Qt signals or widgets.

Only one application instance can exist per process. Tests that need widgets
create the same QApplication as the signal-only ones; ``QT_QPA_PLATFORM`` is
forced to ``offscreen`` before creation so the suite runs headless.

``AA_ShareOpenGLContexts`` must be set before the first application instance so
later imports of QWebEngineWidgets do not fail, regardless of test order.
"""
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

# Keep a Python reference: PyQt destroys the application when the last
# reference goes away, and QWidget then aborts with "construct a QApplication".
_APP = None


def ensure_app():
    """Return the process-wide application, creating a headless one if needed."""
    global _APP
    instance = QApplication.instance()
    if instance is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        instance = QApplication(["aethicc-tests"])
    _APP = instance
    return instance


# Widget tests use the same instance; the alias documents the intent.
ensure_widgets_app = ensure_app
