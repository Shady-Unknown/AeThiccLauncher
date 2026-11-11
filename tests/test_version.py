import re
import unittest
from pathlib import Path

from core.version import __version__, __version_info__

ROOT = Path(__file__).resolve().parent.parent


class VersionTests(unittest.TestCase):
    def test_version_format(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        self.assertEqual(
            __version_info__, tuple(int(part) for part in __version__.split("."))
        )

    def test_installer_fallback_matches_version(self):
        text = (ROOT / "installer.iss").read_text(encoding="utf-8")
        match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
        self.assertIsNotNone(match, "installer.iss has no AppVersion define")
        self.assertEqual(match.group(1), __version__)

    def test_installer_uses_versioned_output_name(self):
        text = (ROOT / "installer.iss").read_text(encoding="utf-8")
        self.assertIn(
            "OutputBaseFilename=Ae_Thicc_Launcher_Setup_{#AppVersion}", text
        )
        # La versione deve arrivare dal build script, non essere duplicata a mano.
        self.assertIn("/DAppVersion=", text + (ROOT / "build_win.ps1").read_text(encoding="utf-8"))

    def test_changelog_top_section_matches_version(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        headings = re.findall(r"^##\s+(.+)$", text, re.MULTILINE)
        self.assertTrue(headings, "CHANGELOG.md has no '## ' section")
        top = headings[0].strip()
        if top.lower().startswith("unreleased"):
            # Lavoro in corso: il commit di release rinomina questa sezione.
            return
        match = re.match(r"v?(\d+\.\d+\.\d+)", top)
        self.assertIsNotNone(match, f"unexpected CHANGELOG heading: {top!r}")
        self.assertEqual(
            match.group(1),
            __version__,
            "CHANGELOG section and core/version.py are out of sync",
        )


if __name__ == "__main__":
    unittest.main()
