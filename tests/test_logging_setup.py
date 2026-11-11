import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import logging_setup


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.tmp.name) / "logs"
        self.patch = mock.patch.object(
            logging_setup.PathManager, "get_logs_dir", return_value=self.log_dir
        )
        self.patch.start()
        self.root = logging.getLogger()
        self._old_handlers = list(self.root.handlers)
        self._old_level = self.root.level

    def tearDown(self):
        for handler in list(self.root.handlers):
            if handler not in self._old_handlers:
                handler.close()
                self.root.removeHandler(handler)
        self.root.setLevel(self._old_level)
        self.patch.stop()
        self.tmp.cleanup()

    def test_creates_rotating_handler_once(self):
        path = logging_setup.setup_logging()
        self.assertTrue(Path(path).exists())
        first = len(self.root.handlers)
        logging_setup.setup_logging()
        self.assertEqual(len(self.root.handlers), first)

    def test_writes_messages(self):
        path = logging_setup.setup_logging()
        logging.getLogger("test").info("hello parity")
        for handler in self.root.handlers:
            handler.flush()
        self.assertIn("hello parity", Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
