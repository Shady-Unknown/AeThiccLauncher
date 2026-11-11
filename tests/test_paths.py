import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import paths


class DataRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.app_dir = self.base / "App"
        self.app_dir.mkdir()
        self.legacy = self.base / "legacy"
        self._old_env = os.environ.pop(paths.ENV_DATA_DIR, None)
        self.patches = [
            mock.patch.object(paths, "_executable_dir", return_value=self.app_dir),
            mock.patch.object(paths, "_legacy_root", return_value=self.legacy),
        ]
        for patch in self.patches:
            patch.start()
        paths.PathManager.reset_cache()

    def tearDown(self):
        paths.PathManager.reset_cache()
        for patch in reversed(self.patches):
            patch.stop()
        if self._old_env is not None:
            os.environ[paths.ENV_DATA_DIR] = self._old_env
        else:
            os.environ.pop(paths.ENV_DATA_DIR, None)
        self.tmp.cleanup()

    def test_default_is_legacy(self):
        self.assertEqual(paths.PathManager.get_data_root(), self.legacy)
        self.assertFalse(paths.PathManager.has_custom_root())

    def test_environment_override_wins(self):
        env_root = self.base / "envdata"
        os.environ[paths.ENV_DATA_DIR] = str(env_root)
        paths.PathManager.reset_cache()
        self.assertEqual(paths.PathManager.get_data_root(), env_root.resolve())
        self.assertTrue(paths.PathManager.has_custom_root())

    def test_bootstrap_relative_to_bootstrap_file(self):
        bootstrap = self.app_dir / paths.BOOTSTRAP_FILENAME
        bootstrap.write_text(json.dumps({"data_root": "../Data"}), encoding="utf-8")
        paths.PathManager.reset_cache()
        self.assertEqual(
            paths.PathManager.get_data_root(), (self.base / "Data").resolve()
        )
        self.assertEqual(paths.PathManager.get_bootstrap_path(), bootstrap)

    def test_set_data_root_writes_bootstrap_and_dirs(self):
        target = self.base / "MovedData"
        written = paths.PathManager.set_data_root(target)
        self.assertEqual(written, self.app_dir / paths.BOOTSTRAP_FILENAME)
        payload = json.loads(written.read_text(encoding="utf-8"))
        self.assertEqual(Path(payload["data_root"]), target.resolve())
        for name in ("Cache", "local_dbs", "config", "stats", "state", "logs"):
            self.assertTrue((target / name).is_dir(), name)

    @unittest.skipIf(sys.platform == "win32", "Linux/XDG cache layout")
    def test_legacy_linux_cache_is_preserved(self):
        cache_home = self.base / "xdg-cache"
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(cache_home)}):
            self.assertEqual(
                paths.PathManager.get_cache_dir(),
                cache_home / "AeThiccLauncher" / "downloads",
            )

    def test_custom_root_moves_cache(self):
        target = self.base / "MovedData"
        paths.PathManager.set_data_root(target)
        if sys.platform == "win32":
            self.assertEqual(paths.PathManager.get_cache_dir(), target / "Cache")
        else:
            self.assertEqual(paths.PathManager.get_cache_dir(), target / "downloads")


class GoldTierNonBlockingTests(unittest.TestCase):
    def test_ensure_fetched_returns_immediately_without_network(self):
        # Use a temporary data root: ensure_fetched must load local data only,
        # never perform synchronous HTTP (the refresh is a background thread).
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {paths.ENV_DATA_DIR: tmp}):
                paths.PathManager.reset_cache()
                from core import gold_tier

                gold_tier.GoldTierManager._initialized = False
                with mock.patch.object(gold_tier.GoldTierManager, "_schedule_refresh"):
                    import time as _time

                    started = _time.perf_counter()
                    gold_tier.GoldTierManager.ensure_fetched()
                    elapsed = _time.perf_counter() - started
                    self.assertLess(elapsed, 0.5)
                paths.PathManager.reset_cache()


if __name__ == "__main__":
    unittest.main()
