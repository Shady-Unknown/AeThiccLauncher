import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import archive_utils


class ArchiveUtilsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self._tmp.name, "game.zip")
        with open(self.archive, "wb") as handle:
            handle.write(b"PK\x03\x04")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, output):
        return SimpleNamespace(stdout=output)

    def test_complete_archive_is_valid(self):
        with patch(
            "core.archive_utils.subprocess.run",
            return_value=self._run("Listing archive: game.zip"),
        ):
            self.assertTrue(
                archive_utils.is_archive_complete(
                    self.archive, seven_z=self.archive
                )
            )

    def test_truncated_archive_is_detected(self):
        with patch(
            "core.archive_utils.subprocess.run",
            return_value=self._run("ERRORS:\nUnexpected end of archive"),
        ):
            self.assertFalse(
                archive_utils.is_archive_complete(
                    self.archive, seven_z=self.archive
                )
            )

    def test_encrypted_headers_are_not_treated_as_incomplete(self):
        with patch(
            "core.archive_utils.subprocess.run",
            return_value=self._run(
                "Cannot open encrypted archive. Wrong password?"
            ),
        ):
            self.assertTrue(
                archive_utils.is_archive_complete(
                    self.archive, seven_z=self.archive
                )
            )

    def test_missing_file_is_incomplete(self):
        self.assertFalse(
            archive_utils.is_archive_complete(
                os.path.join(self._tmp.name, "missing.zip")
            )
        )

    def test_incomplete_marker_lifecycle(self):
        self.assertFalse(archive_utils.is_marked_incomplete(self.archive))
        archive_utils.mark_incomplete(self.archive, "truncated")
        self.assertTrue(archive_utils.is_marked_incomplete(self.archive))
        archive_utils.clear_incomplete(self.archive)
        self.assertFalse(archive_utils.is_marked_incomplete(self.archive))

    def test_incomplete_marker_persists_metadata(self):
        archive_utils.mark_incomplete(
            self.archive,
            "truncated",
            meta={
                "title": "Game",
                "provider": "GoFile",
                "url": "https://gofile.io/d/abc",
                "total_bytes": 100,
            },
        )
        meta = archive_utils.read_incomplete_meta(self.archive)
        self.assertEqual(meta["reason"], "truncated")
        self.assertEqual(meta["title"], "Game")
        self.assertEqual(meta["provider"], "GoFile")
        self.assertEqual(meta["url"], "https://gofile.io/d/abc")
        self.assertEqual(meta["total_bytes"], 100)

    def test_legacy_text_marker_is_still_readable(self):
        with open(archive_utils.incomplete_marker(self.archive), "w",
                  encoding="utf-8") as handle:
            handle.write("truncated")
        self.assertEqual(
            archive_utils.read_incomplete_meta(self.archive),
            {"reason": "truncated"},
        )


if __name__ == "__main__":
    unittest.main()
