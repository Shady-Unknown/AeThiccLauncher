"""Test package setup.

QtWebEngine requires this attribute before the first QCoreApplication
instance. Several test modules create a QCoreApplication at import time, so set
it once here, before discovery imports them.
"""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
