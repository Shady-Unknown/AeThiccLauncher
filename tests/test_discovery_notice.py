"""The IGDB config banner follows the saved credentials.

The banner was evaluated once when the Discovery tab was built, so adding the
keys later left a yellow "IGDB not configured" notice on screen until the app
restarted.
"""
import unittest
from unittest.mock import patch

from ui.tabs.discovery_tab import DiscoveryTab


class _StubTab:
    """Minimal stand-in for the parts refresh_config_notice touches."""

    CONFIG_NOTICE_TITLE = DiscoveryTab.CONFIG_NOTICE_TITLE
    CONFIG_NOTICE_BODY = DiscoveryTab.CONFIG_NOTICE_BODY

    def __init__(self, dismissed=(), manager=object()):
        self._manager = manager
        self._dismissed_notices = set(dismissed)
        self.shown = []
        self.hidden = []

    def _discovery(self):
        return self._manager

    def show_config_notice(self, title, body=""):
        self.shown.append((title, body))

    def hide_notice(self, kind=None):
        self.hidden.append(kind)


class ConfigNoticeTests(unittest.TestCase):
    def _refresh(self, tab, has_keys):
        with patch(
            "ui.tabs.discovery_tab.secrets.has_igdb_credentials",
            return_value=has_keys,
        ):
            DiscoveryTab.refresh_config_notice(tab)
        return tab

    def test_missing_keys_show_the_banner(self):
        tab = self._refresh(_StubTab(), has_keys=False)
        self.assertEqual(len(tab.shown), 1)
        self.assertEqual(tab.shown[0][0], DiscoveryTab.CONFIG_NOTICE_TITLE)
        self.assertEqual(tab.hidden, [])

    def test_saved_keys_hide_the_banner(self):
        tab = self._refresh(_StubTab(), has_keys=True)
        self.assertEqual(tab.shown, [])
        self.assertEqual(tab.hidden, ["config"])

    def test_dismissed_banner_is_not_reshown(self):
        tab = self._refresh(_StubTab(dismissed=("config",)), has_keys=False)
        self.assertEqual(tab.shown, [])

    def test_without_a_discovery_manager_nothing_happens(self):
        tab = self._refresh(_StubTab(manager=None), has_keys=False)
        self.assertEqual(tab.shown, [])
        self.assertEqual(tab.hidden, [])


if __name__ == "__main__":
    unittest.main()
