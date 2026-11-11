import os
import sys
import unittest

from core import platform_info


class PlatformInfoTests(unittest.TestCase):
    def test_flags_match_sys_platform(self):
        self.assertEqual(platform_info.is_windows(), sys.platform == "win32")
        self.assertEqual(platform_info.is_linux(), sys.platform.startswith("linux"))

    def test_flatpak_check_matches_marker(self):
        platform_info.is_flatpak.cache_clear()
        self.assertEqual(platform_info.is_flatpak(), os.path.exists("/.flatpak-info"))


if __name__ == "__main__":
    unittest.main()
