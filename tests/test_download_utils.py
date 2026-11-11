import os
import tempfile
import unittest

from core.download_utils import (
    choose_provider_task,
    dedupe_tasks,
    merge_task_data,
    normalize_dest_path,
    provider_key,
)


class DownloadUtilsTests(unittest.TestCase):
    def test_normalize_dest_path_is_case_insensitive_on_windows(self):
        first = normalize_dest_path(r"C:\Cache\Game.RAR")
        second = normalize_dest_path(r"c:\cache\game.rar")
        self.assertEqual(first, second)

    def test_provider_key_prefers_domain_then_url(self):
        self.assertEqual(
            provider_key({"provider_domain": "GoFile.io"}), "gofile.io"
        )
        self.assertEqual(
            provider_key({"url": "https://store.gofile.io/download/x"}),
            "store.gofile.io",
        )

    def test_choose_newer_task(self):
        old = {"created_at": 10, "provider": "A"}
        new = {"created_at": 20, "provider": "B"}
        winner, loser = choose_provider_task(old, new)
        self.assertIs(winner, new)
        self.assertIs(loser, old)

    def test_merge_can_keep_existing_credentials(self):
        target = {
            "url": "https://old",
            "cookies": {"a": "1"},
            "provider": "Old",
            "title": "Old title",
        }
        merge_task_data(
            target,
            {
                "url": "https://new",
                "cookies": {"b": "2"},
                "provider": "New",
                "title": "New title",
            },
            keep_download_credentials=True,
        )
        self.assertEqual(target["url"], "https://old")
        self.assertEqual(target["cookies"], {"a": "1"})
        self.assertEqual(target["provider"], "Old")
        self.assertEqual(target["title"], "New title")


class LoadedQueueDedupeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = os.path.join(self._tmp.name, "game.rar")
        with open(self.dest, "wb") as handle:
            handle.write(b"partial")

    def tearDown(self):
        self._tmp.cleanup()

    def _task(self, provider, url, created_at):
        return {
            "dest_path": self.dest,
            "title": "inZOI",
            "filename": "inZOI-SteamRIP.com.rar",
            "url": url,
            "provider": provider,
            "provider_domain": f"{provider.lower()}.example",
            "created_at": created_at,
        }

    def test_duplicate_with_different_provider_restarts_partial(self):
        old = self._task("ProviderA", "https://a.example/file", 10)
        new = self._task("ProviderB", "https://b.example/file", 20)

        deduped = dedupe_tasks([old, new])

        self.assertEqual(len(deduped), 1)
        winner = deduped[0]
        self.assertEqual(winner["provider"], "ProviderB")
        self.assertEqual(winner["url"], "https://b.example/file")
        self.assertTrue(winner["_restart_partial"])
        self.assertEqual(winner["_provider_conflict"], "ProviderA")

    def test_duplicate_same_provider_is_merged_without_restart(self):
        old = self._task("ProviderA", "https://a.example/old", 10)
        new = self._task("ProviderA", "https://a.example/new", 20)

        deduped = dedupe_tasks([old, new])

        self.assertEqual(len(deduped), 1)
        winner = deduped[0]
        self.assertNotIn("_restart_partial", winner)
        self.assertEqual(winner["url"], "https://a.example/new")

    def test_legacy_duplicate_infers_provider_from_url(self):
        old = {
            "dest_path": self.dest,
            "filename": "inZOI-SteamRIP.com.rar",
            "title": "inZOI",
            "url": "https://lumisleepsby10pm.xyz/old-token",
        }
        new = {
            "dest_path": self.dest,
            "filename": "inZOI-SteamRIP.com.rar",
            "title": "inZOI",
            "url": "https://lumisleepsby10pm.xyz/new-token",
            "provider": "Lumisleepsby10pm",
            "provider_domain": "lumisleepsby10pm.xyz",
        }

        deduped = dedupe_tasks([old, new])

        self.assertEqual(len(deduped), 1)
        winner = deduped[0]
        self.assertNotIn("_restart_partial", winner)
        self.assertEqual(winner["url"], "https://lumisleepsby10pm.xyz/new-token")


if __name__ == "__main__":
    unittest.main()
