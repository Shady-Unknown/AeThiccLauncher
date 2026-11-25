"""P2-8A: one shared QApplication and the first widget-level checks."""
import unittest

from PyQt6.QtWidgets import QApplication, QWidget

from tests.qt_app import ensure_app, ensure_widgets_app
from ui.dialogs.manual_save_path_dialog import ManualSavePathDialog


class ApplicationFixtureTests(unittest.TestCase):
    def setUp(self):
        ensure_app()

    def test_one_application_is_shared(self):
        first = ensure_app()
        second = ensure_widgets_app()

        self.assertIs(first, second)
        self.assertIsInstance(first, QApplication)
        self.assertIs(QApplication.instance(), first)

    def test_widget_can_be_created_headless(self):
        widget = QWidget()
        widget.resize(120, 40)
        widget.show()
        QApplication.processEvents()
        self.assertTrue(widget.isVisible())
        widget.close()
        widget.deleteLater()


class ManualSavePathDialogTests(unittest.TestCase):
    def setUp(self):
        ensure_app()

    def _dialog(self, details):
        return ManualSavePathDialog("Game", preview=lambda _value: details)

    def test_preview_shows_the_translated_path(self):
        dialog = self._dialog({
            "path": "/tmp/compatdata/1/pfx/drive_c/users/steamuser/Saves",
            "kind": "prefix",
            "reason": "",
            "exists": True,
        })
        self.addCleanup(dialog.deleteLater)

        dialog.path_input.setText(r"%userprofile%\Saves")
        dialog._update_preview()

        text = dialog.preview_label.text()
        self.assertIn("/tmp/compatdata/1", text)
        self.assertIn("#00ff88", dialog.preview_label.styleSheet())

    def test_preview_explains_an_unknown_token(self):
        dialog = self._dialog({
            "path": "", "kind": "", "reason": "unknown_placeholder",
            "token": "<foo>",
        })
        self.addCleanup(dialog.deleteLater)

        dialog.path_input.setText(r"<foo>\Saves")
        dialog._update_preview()

        text = dialog.preview_label.text()
        self.assertIn("&lt;foo&gt;", text)
        self.assertIn("#ff6b6b", dialog.preview_label.styleSheet())

    def test_empty_preview_is_cleared(self):
        dialog = self._dialog({"path": "", "kind": "", "reason": "empty"})
        self.addCleanup(dialog.deleteLater)

        dialog.path_input.setText("")
        dialog._update_preview()

        self.assertEqual(dialog.preview_label.text(), "")


if __name__ == "__main__":
    unittest.main()
