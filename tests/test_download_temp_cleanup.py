import os
import tempfile
import unittest

from core.download_queue_model import temp_cleanup_candidates


class TempCleanupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _file(self, name, data=b"x"):
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def test_cleanup_keeps_queue_archives_and_state(self):
        active = self._file("Active.rar")
        active_marker = self._file("Active.rar.incomplete")
        ready = self._file("Ready.rar")
        ready_sidecar = self._file("Ready.rar.extracting.json")
        orphan = self._file("Orphan.rar")
        state = self._file(".dl_state.json")
        legacy_state = self._file("dl_state.json")

        candidates = temp_cleanup_candidates(
            self.root, [active, ready]
        )
        names = {os.path.basename(path) for path, _ in candidates}

        self.assertEqual(names, {"Orphan.rar"})
        for path in (active, active_marker, ready, ready_sidecar, state, legacy_state):
            self.assertTrue(os.path.exists(path), path)

    def test_cleanup_with_nothing_to_remove(self):
        self._file(".dl_state.json")
        self.assertEqual(temp_cleanup_candidates(self.root, []), [])

    def test_cleanup_ignores_directories(self):
        os.makedirs(os.path.join(self.root, "patches"))
        self.assertEqual(temp_cleanup_candidates(self.root, []), [])


if __name__ == "__main__":
    unittest.main()
