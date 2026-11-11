import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import secrets
from core.paths import PathManager


class SecretsFileTests(unittest.TestCase):
    def setUp(self):
        secrets.clear_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._env = {k: os.environ.pop(k, None) for k in secrets.ENV_VARS.values()}
        self.patches = [
            mock.patch.dict(os.environ, {secrets.ENV_VARS[secrets.SGDB_API_KEY]: ""}),
            mock.patch.object(secrets, "_keyring", return_value=None),
            mock.patch.object(PathManager, "get_data_root", return_value=self.root),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        for key, value in self._env.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        self.tmp.cleanup()
        secrets.clear_cache()

    def test_file_fallback_round_trip(self):
        self.assertFalse(secrets.has_secret(secrets.SGDB_API_KEY))
        self.assertTrue(secrets.set_secret(secrets.SGDB_API_KEY, "abc123"))
        self.assertEqual(secrets.get_secret(secrets.SGDB_API_KEY), "abc123")
        self.assertEqual(secrets.secret_source(secrets.SGDB_API_KEY), "file")
        path = self.root / secrets.SECRETS_FILENAME
        self.assertTrue(path.is_file())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        secrets.delete_secret(secrets.SGDB_API_KEY)
        self.assertFalse(secrets.has_secret(secrets.SGDB_API_KEY))

    def test_env_has_priority(self):
        with mock.patch.dict(
            os.environ, {secrets.ENV_VARS[secrets.SGDB_API_KEY]: "from-env"}
        ):
            self.assertTrue(secrets.set_secret(secrets.SGDB_API_KEY, "from-file"))
            self.assertEqual(secrets.get_secret(secrets.SGDB_API_KEY), "from-env")
            self.assertEqual(secrets.secret_source(secrets.SGDB_API_KEY), "env")

    def test_igdb_helpers(self):
        self.assertFalse(secrets.has_igdb_credentials())
        secrets.set_secret(secrets.IGDB_CLIENT_ID, "cid")
        self.assertFalse(secrets.has_igdb_credentials())
        secrets.set_secret(secrets.IGDB_CLIENT_SECRET, "secret")
        self.assertTrue(secrets.has_igdb_credentials())
        self.assertEqual(secrets.get_igdb_credentials(), ("cid", "secret"))


class ValidationTests(unittest.TestCase):
    def test_sgdb_valid(self):
        response = mock.Mock(status_code=200)
        with mock.patch("core.secrets.requests.get", return_value=response):
            ok, _ = secrets.validate_sgdb_key("key")
        self.assertTrue(ok)

    def test_sgdb_invalid(self):
        response = mock.Mock(status_code=401)
        with mock.patch("core.secrets.requests.get", return_value=response):
            ok, _ = secrets.validate_sgdb_key("key")
        self.assertFalse(ok)

    def test_igdb_valid(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {"access_token": "token"}
        with mock.patch("core.secrets.requests.post", return_value=response):
            ok, _ = secrets.validate_igdb_credentials("cid", "secret")
        self.assertTrue(ok)

    def test_igdb_missing(self):
        ok, _ = secrets.validate_igdb_credentials("", "")
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
