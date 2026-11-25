import tempfile
import unittest
from pathlib import Path


from core.single_instance import SingleInstanceLock

from tests.qt_app import ensure_app

_app = ensure_app()


class SingleInstanceLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "launcher.lock"

    def test_second_instance_is_rejected(self):
        first = SingleInstanceLock(self.path)
        second = SingleInstanceLock(self.path)
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            second.release()
            first.release()

    def test_release_allows_acquire_again(self):
        first = SingleInstanceLock(self.path)
        self.assertTrue(first.acquire())
        first.release()

        second = SingleInstanceLock(self.path)
        try:
            self.assertTrue(second.acquire())
        finally:
            second.release()

    def test_lock_path_is_inside_the_data_root(self):
        self.assertEqual(
            SingleInstanceLock.path_for_data_root("/tmp/data"),
            Path("/tmp/data/launcher.lock"),
        )


if __name__ == "__main__":
    unittest.main()
