import os
import tempfile
import unittest

from core.meta_utils import EXTRACTING_MARKER, STEAMRIP_META_FILENAME
from ui.controllers.downloads import DownloadsController


class PendingExtractionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.install_dir = os.path.join(
            self.root, "Hollow Knight  Silksong download"
        )
        os.makedirs(self.install_dir)
        self.controller = object.__new__(DownloadsController)
        self.controller.installed_games = {
            "Hollow Knight: Silksong": {
                "install_dir": self.install_dir,
            },
            "Grounded 2": {
                "install_dir": os.path.join(self.root, "Grounded 2"),
            },
        }

    def tearDown(self):
        self._tmp.cleanup()

    def _create_meta(self):
        with open(
            os.path.join(self.install_dir, STEAMRIP_META_FILENAME),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write('{"title": "Hollow Knight: Silksong"}')

    def _archive(self):
        return os.path.join(
            self.root, "Hollow_Knight__Silksong_download.zip"
        )

    def test_already_installed_archive_is_skipped(self):
        self._create_meta()
        self.assertTrue(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_interrupted_extraction_is_not_skipped(self):
        self._create_meta()
        with open(
            os.path.join(self.install_dir, EXTRACTING_MARKER),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("in progress")
        self.assertFalse(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_folder_without_metadata_is_not_skipped(self):
        self.assertFalse(
            self.controller._archive_matches_installed(
                self._archive(), "Hollow Knight  Silksong download"
            )
        )

    def test_unrelated_archive_is_not_skipped(self):
        self._create_meta()
        self.assertFalse(
            self.controller._archive_matches_installed(
                os.path.join(self.root, "inZOI-SteamRIP.com.rar"),
                "inZOI-SteamRIP.com",
            )
        )

    def test_explorer_search_text_strips_download_noise(self):
        self.assertEqual(
            DownloadsController._explorer_search_text(
                {"title": "inZOI-SteamRIP.com"}
            ),
            "inZOI",
        )
        self.assertEqual(
            DownloadsController._explorer_search_text(
                {"title": "Hollow Knight  Silksong download"}
            ),
            "Hollow Knight Silksong",
        )


if __name__ == "__main__":
    unittest.main()
