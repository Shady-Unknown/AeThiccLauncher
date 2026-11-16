"""Version status: build ids are compared only with build ids.

SteamRIP sometimes fills the same field with a version label
(``v0.1.0.0.14530``); turning those digits into an int produced fake build
ids that could never be compared with Steam's real ones.
"""
import unittest

from core.version_status import (
    as_build,
    classify_build,
    compute_version_state,
    parse_version_status_html,
    STATE_STEAMRIP_OK_STEAM_AHEAD,
    STATE_UNKNOWN,
    STATE_UPDATE_AVAILABLE,
    STATE_UPDATED,
    STATE_VERSION_ONLY,
)

BUILD_HTML = """
<div class="svchk__wrapper" data-svchk-app="2661300" data-svchk-build="25063598">
  <label class="svchk__trigger svchk__trigger--amber">
    <span class="svchk__trigger-label">Update Pending</span>
  </label>
  <div class="svchk__row">
    <span class="svchk__key">Posted Build</span>
    <span class="svchk__val">25063598</span>
    <span class="svchk__date">20 September 2026</span>
  </div>
  <div class="svchk__row">
    <span class="svchk__key">Steam Latest</span>
    <span class="svchk__val">26399478</span>
    <span class="svchk__date">4 November 2026</span>
  </div>
  <div data-svchk-alert>
    <span class="svchk__alert svchk__alert--amber">A newer build is available on Steam.</span>
  </div>
</div>
"""

VERSION_HTML = """
<div class="svchk__wrapper" data-svchk-app="2661300" data-svchk-build="v0.1.0.0.14530">
  <label class="svchk__trigger svchk__trigger--purple">
    <span class="svchk__trigger-label">Version only</span>
  </label>
  <div class="svchk__row">
    <span class="svchk__key">Posted Version</span>
    <span class="svchk__val">v0.1.0.0.14530</span>
  </div>
</div>
"""


class ClassifyBuildTests(unittest.TestCase):
    def test_plain_ids_are_builds(self):
        self.assertEqual(classify_build("25063598"), "buildid")
        self.assertEqual(classify_build(25063598), "buildid")
        self.assertEqual(classify_build("29928"), "buildid")

    def test_version_labels_are_not_builds(self):
        for value in ("v0.1.0.0.14530", "v68322", "v20261013.16166.W", "Build 24424450"):
            self.assertEqual(classify_build(value), "label", value)

    def test_as_build_never_fabricates_a_number(self):
        self.assertEqual(as_build("24424450"), 24424450)
        self.assertIsNone(as_build("v0.1.0.0.14530"))
        self.assertIsNone(as_build(""))
        self.assertIsNone(as_build(None))


class ComputeVersionStateTests(unittest.TestCase):
    def test_all_aligned(self):
        state = compute_version_state(
            installed_build=24424450, posted_build=24424450,
            is_installed=True, has_status=True,
        )
        self.assertEqual(state["state"], STATE_UPDATED)
        self.assertTrue(state["ok"])

    def test_installed_below_the_site(self):
        state = compute_version_state(
            installed_build=24000000, posted_build=24424450,
            is_installed=True, has_status=True,
        )
        self.assertEqual(state["state"], STATE_UPDATE_AVAILABLE)
        self.assertFalse(state["ok"])

    def test_steam_ahead_keeps_the_split_state(self):
        state = compute_version_state(
            installed_build=24424450, posted_build=24424450,
            steam_build=26651831, is_installed=True, has_status=True,
        )
        self.assertEqual(state["state"], STATE_STEAMRIP_OK_STEAM_AHEAD)
        self.assertTrue(state["ok"])

    def test_not_installed_steam_ahead(self):
        state = compute_version_state(
            posted_build=24424450, steam_build=26651831, has_status=True,
        )
        self.assertEqual(state["state"], STATE_STEAMRIP_OK_STEAM_AHEAD)

    def test_a_version_in_the_build_slot_is_never_steam_ahead(self):
        state = compute_version_state(
            installed_build="v0.1.0.0.14530",
            posted_build="v0.1.0.0.14530",
            steam_build=26651831,
            installed_version="v0.1.0.0.14530",
            posted_version_text="v0.1.0.0.14530",
            is_installed=True,
            has_status=True,
        )
        self.assertEqual(state["state"], STATE_VERSION_ONLY)

    def test_string_builds_do_not_raise(self):
        state = compute_version_state(
            installed_build="24424450", posted_build=24424450,
            is_installed=True, has_status=True,
        )
        self.assertEqual(state["state"], STATE_UPDATED)

    def test_version_labels_match_ignoring_prefix_and_case(self):
        state = compute_version_state(
            posted_build=24424450,
            installed_version="V1.0",
            posted_version_text="v1.0",
            is_installed=True,
            has_status=True,
        )
        self.assertEqual(state["state"], STATE_VERSION_ONLY)

    def test_version_mismatch_still_warns(self):
        state = compute_version_state(
            posted_build=24424450,
            installed_version="v1.0",
            posted_version_text="v1.1",
            is_installed=True,
            has_status=True,
        )
        self.assertEqual(state["state"], STATE_UPDATE_AVAILABLE)

    def test_missing_status(self):
        self.assertEqual(
            compute_version_state(has_status=False)["state"], STATE_UNKNOWN
        )


class ParseVersionStatusTests(unittest.TestCase):
    def test_build_widget_is_parsed_as_builds(self):
        data = parse_version_status_html(BUILD_HTML)
        self.assertIsNotNone(data)
        self.assertEqual(data["variant"], "build")
        self.assertEqual(data["posted_build"], 25063598)
        self.assertEqual(data["steam_build"], 26399478)
        self.assertEqual(data["posted_build_date"], "20 September 2026")
        self.assertEqual(data["label"], "Update Pending")
        self.assertEqual(data["alert_color"], "amber")
        self.assertEqual(data["alert"], "A newer build is available on Steam.")

    def test_version_widget_does_not_produce_a_build(self):
        data = parse_version_status_html(VERSION_HTML)
        self.assertIsNotNone(data)
        self.assertEqual(data["variant"], "version")
        self.assertIsNone(data["posted_build"])
        self.assertEqual(data["posted_version"], "v0.1.0.0.14530")

    def test_missing_widget(self):
        self.assertIsNone(parse_version_status_html("<html><body>nope</body></html>"))


class CachedVersionStatusTests(unittest.TestCase):
    """A stale build must not survive a refresh that no longer reports it."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from core.master_catalog import MasterCatalog

        self._tmp = tempfile.TemporaryDirectory()
        db = Path(self._tmp.name) / "catalog.json"
        self.catalog = MasterCatalog(str(db))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_values_clear_previous_keys(self):
        self.catalog.set_version_status("Game", {
            "posted_build": 25063598,
            "steam_build": 26399478,
            "variant": "build",
        })
        self.catalog.set_version_status("Game", {
            "posted_build": None,
            "variant": "version",
        })

        status = self.catalog.get_version_status("Game")
        self.assertNotIn("posted_build", status)
        self.assertEqual(status["variant"], "version")
        self.assertEqual(status["steam_build"], 26399478)

    def test_clear_sentinel_removes_a_key(self):
        from core.master_catalog import CLEAR

        self.catalog.set_version_status("Game", {"posted_build": 25063598})
        self.catalog.set_version_status("Game", {
            "posted_build": CLEAR,
            "variant": "none",
        })
        self.assertNotIn("posted_build", self.catalog.get_version_status("Game"))


if __name__ == "__main__":
    unittest.main()
