"""GameInstaller failure paths: password, 7-Zip errors and data preservation."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.qt_app import ensure_app

ensure_app()

from core.installer import GameInstaller


def _proc(returncode=0, stdout="", stderr=""):
    process = mock.Mock()
    process.returncode = returncode
    process.communicate.return_value = (stdout, stderr)
    process.stdout.read.return_value = ""
    process.poll.return_value = returncode
    return process


class InstallerFailureTests(unittest.TestCase):
    def _installer(self, archive, destination):
        installer = GameInstaller(
            str(archive), str(destination),
            {"title": "Test Game", "game_info": ""},
        )
        events = {"errors": [], "password": [], "complete": [], "started": []}
        installer.error_occurred.connect(events["errors"].append)
        installer.password_required.connect(
            lambda arc, dest: events["password"].append((arc, dest)))
        installer.installation_complete.connect(events["complete"].append)
        installer.extraction_started.connect(events["started"].append)
        return installer, events

    def _run(self, installer, processes):
        with mock.patch(
            "core.installer.archive_utils.seven_zip_path",
            return_value="/bin/true",
        ), mock.patch(
            "core.installer.subprocess.Popen", side_effect=processes
        ):
            installer.run()

    def test_success_removes_the_archive_and_writes_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.7z"
            archive.write_bytes(b"archive")
            destination = Path(tmp) / "dest"
            destination.mkdir()
            installer, events = self._installer(archive, destination)

            self._run(installer, [_proc(0, "100"), _proc(0)])

            self.assertEqual(len(events["complete"]), 1)
            self.assertEqual(events["errors"], [])
            self.assertFalse(archive.exists())
            install_dir = Path(events["complete"][0])
            self.assertTrue((install_dir / "steamrip_meta.json").is_file())

    def test_extraction_error_keeps_the_archive_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.7z"
            archive.write_bytes(b"archive")
            destination = Path(tmp) / "dest"
            destination.mkdir()
            installer, events = self._installer(archive, destination)

            self._run(installer, [_proc(0, "100"), _proc(1, stderr="boom")])

            self.assertEqual(len(events["errors"]), 1)
            self.assertIn("boom", events["errors"][0])
            self.assertEqual(events["complete"], [])
            self.assertTrue(archive.exists())
            self.assertTrue((destination / "Test Game").is_dir())

    def test_wrong_password_during_extraction_asks_for_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.7z"
            archive.write_bytes(b"archive")
            destination = Path(tmp) / "dest"
            destination.mkdir()
            installer, events = self._installer(archive, destination)

            self._run(
                installer,
                [_proc(0, "100"), _proc(1, stderr="Wrong password")],
            )

            self.assertEqual(len(events["password"]), 1)
            self.assertEqual(events["errors"], [])
            self.assertTrue(archive.exists())

    def test_wrong_password_during_list_skips_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "game.7z"
            archive.write_bytes(b"archive")
            destination = Path(tmp) / "dest"
            destination.mkdir()
            installer, events = self._installer(archive, destination)

            self._run(installer, [_proc(1, stdout="wrong password")])

            self.assertEqual(len(events["password"]), 1)
            self.assertEqual(events["errors"], [])
            self.assertTrue(archive.exists())


if __name__ == "__main__":
    unittest.main()
