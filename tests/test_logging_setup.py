import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest import mock

from core.logging_setup import (
    _RedactionFilter,
    _redact,
    install_print_bridge,
    remove_print_bridge,
    setup_logging,
)


def _reset_root():
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.setLevel(logging.WARNING)


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        _reset_root()
        self.addCleanup(_reset_root)
        self.addCleanup(remove_print_bridge)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_dir = Path(self.tmp.name)

    def _read(self, path):
        logging.getLogger().handlers[0].flush()
        return Path(path).read_text(encoding="utf-8")

    def test_creates_file_and_writes_records(self):
        path = setup_logging(level="DEBUG", log_dir=self.log_dir, console=False)
        logging.getLogger("test.sample").info("hello 42")
        content = self._read(path)
        self.assertIn("hello 42", content)
        self.assertIn("test.sample", content)

    def test_same_directory_does_not_duplicate_handlers(self):
        setup_logging(level="INFO", log_dir=self.log_dir)
        setup_logging(level="INFO", log_dir=self.log_dir)
        handlers = [h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(handlers), 1)

    def test_new_directory_replaces_the_old_handler(self):
        first = Path(setup_logging(level="INFO", log_dir=self.log_dir))
        other = self.log_dir / "other"
        second = Path(setup_logging(level="INFO", log_dir=other))
        handlers = [h for h in logging.getLogger().handlers
                    if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(handlers), 1)
        self.assertEqual(Path(handlers[0].baseFilename), second)
        self.assertNotEqual(first, second)

    def test_console_handler_only_when_requested(self):
        setup_logging(level="INFO", log_dir=self.log_dir, console=False)
        self.assertFalse(any(getattr(h, "_aethicc_console", False)
                             for h in logging.getLogger().handlers))
        setup_logging(level="INFO", log_dir=self.log_dir, console=True)
        console = [h for h in logging.getLogger().handlers
                   if getattr(h, "_aethicc_console", False)]
        self.assertEqual(len(console), 1)

    def test_level_from_environment(self):
        with mock.patch.dict(os.environ, {"AETHICC_LOG_LEVEL": "DEBUG"}):
            setup_logging(level=None, log_dir=self.log_dir)
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_print_bridge_writes_to_the_log(self):
        path = setup_logging(level="INFO", log_dir=self.log_dir)
        install_print_bridge()
        print("bridge visible")
        remove_print_bridge()
        self.assertIn("bridge visible", self._read(path))

    def test_redact_masks_secret_values(self):
        text = 'client_secret="supersecret" token=abcdef Authorization: Bearer abc.def'
        redacted = _redact(text)
        self.assertNotIn("supersecret", redacted)
        self.assertNotIn("abcdef", redacted)
        self.assertNotIn("abc.def", redacted)
        self.assertIn("***", redacted)

    def test_secret_values_never_reach_the_file(self):
        path = setup_logging(level="INFO", log_dir=self.log_dir)
        logging.getLogger("test.secrets").warning(
            'request failed api_key="LEAKME"')
        content = self._read(path)
        self.assertNotIn("LEAKME", content)
        self.assertIn("***", content)


class RedactionFilterTests(unittest.TestCase):
    def test_filter_rewrites_the_record(self):
        record = logging.LogRecord(
            "x", logging.INFO, __file__, 1,
            'client_secret="topsecret"', (), None)
        _RedactionFilter().filter(record)
        self.assertNotIn("topsecret", record.getMessage())
        self.assertIn("***", record.getMessage())


if __name__ == "__main__":
    unittest.main()
