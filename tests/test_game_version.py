import unittest

from core import game_version as gv


class LabelTests(unittest.TestCase):
    def test_build_prefix_and_v_prefix_are_ignored(self):
        self.assertEqual(gv.normalize_label("Build 25922370"), "25922370")
        self.assertEqual(gv.normalize_label("v1.2.3"), "1.2.3")
        self.assertEqual(gv.normalize_label("V1.2.3"), "1.2.3")
        self.assertEqual(gv.normalize_label("  v1.2.3  "), "1.2.3")

    def test_meaningless_labels_become_empty(self):
        for value in ("", None, "Release Base", "release", "unknown", "N/A"):
            self.assertEqual(gv.normalize_label(value), "", value)

    def test_build_parsing(self):
        self.assertEqual(gv.to_build(25922370), 25922370)
        self.assertEqual(gv.to_build("25922370"), 25922370)
        self.assertEqual(gv.to_build("Build 25922370"), 25922370)
        self.assertIsNone(gv.to_build("Release Base"))
        self.assertIsNone(gv.to_build(None))
        self.assertIsNone(gv.to_build(True))


class VersionInfoTests(unittest.TestCase):
    def test_steamrip_meta_with_build(self):
        meta = {
            "version": "Build 25922370",
            "build": 25922370,
            "build_date": "16 October 2026",
            "web_version": "v20261013.16166.W",
            "source_url": "https://example.test/game",
        }
        info = gv.version_info_from_meta(meta, now="2026-11-13T00:00:00Z")
        self.assertEqual(info["build"], 25922370)
        self.assertEqual(info["label"], "Build 25922370")
        self.assertEqual(info["source"], gv.SOURCE_STEAMRIP)
        self.assertFalse(info["inferred"])
        self.assertEqual(info["recorded_at"], "2026-11-13T00:00:00Z")

    def test_build_wins_over_missing_source_url(self):
        info = gv.version_info_from_meta({"build": 123, "version": "Build 123"})
        self.assertEqual(info["source"], gv.SOURCE_STEAMRIP)

    def test_local_install_without_version(self):
        info = gv.version_info_from_meta({"is_local": True})
        self.assertEqual(info["source"], gv.SOURCE_LOCAL)
        self.assertIsNone(info["build"])
        self.assertEqual(gv.describe_version(info), "unknown (local install)")

    def test_empty_meta_is_unknown(self):
        info = gv.version_info_from_meta({})
        self.assertEqual(info["source"], gv.SOURCE_UNKNOWN)
        self.assertEqual(gv.describe_version(info), "unknown")

    def test_inferred_build_is_marked(self):
        info = gv.version_info_from_meta(
            {"build": 42, "build_inferred": True, "version": "Build 42"}
        )
        self.assertTrue(info["inferred"])
        self.assertIn("inferred", gv.describe_version(info))


class CompareTests(unittest.TestCase):
    def test_same_build(self):
        result = gv.compare_versions({"build": 10}, {"build": 10})
        self.assertEqual(result["status"], gv.STATUS_SAME)
        self.assertEqual(result["reason"], "build")

    def test_different_build(self):
        result = gv.compare_versions({"build": 10}, {"build": 11})
        self.assertEqual(result["status"], gv.STATUS_DIFFERENT)

    def test_same_label_when_build_missing(self):
        result = gv.compare_versions(
            {"label": "v1.2.3"}, {"label": "Build 1.2.3"}
        )
        self.assertEqual(result["status"], gv.STATUS_SAME)
        self.assertEqual(result["reason"], "label")

    def test_different_label(self):
        result = gv.compare_versions({"label": "v1.2.3"}, {"label": "v1.3.0"})
        self.assertEqual(result["status"], gv.STATUS_DIFFERENT)

    def test_mixed_identity_matches_when_strings_are_equal(self):
        result = gv.compare_versions({"build": 25922370}, {"label": "Build 25922370"})
        self.assertEqual(result["status"], gv.STATUS_SAME)
        self.assertEqual(result["reason"], "mixed_match")

    def test_mixed_identity_does_not_guess(self):
        result = gv.compare_versions({"build": 25922370}, {"label": "v1.2.3"})
        self.assertEqual(result["status"], gv.STATUS_UNKNOWN)
        self.assertEqual(result["reason"], "mixed_incomparable")

    def test_missing_side_is_unknown(self):
        self.assertEqual(gv.compare_versions({}, {"build": 1})["reason"], "backup_missing")
        self.assertEqual(gv.compare_versions({"build": 1}, {})["reason"], "installed_missing")
        self.assertEqual(gv.compare_versions({}, {})["reason"], "no_version_info")


if __name__ == "__main__":
    unittest.main()
