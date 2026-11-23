"""The bundled emoji font must win over the COLRv1 system font on Linux.

Qt 6.11 cannot rasterize ``Noto Color Emoji`` (COLRv1), but its font fallback
prefers that font for emoji-presentation characters, so the UI icons render as
blank glyphs. ``.media/fonts/fonts.conf`` rejects that family; ``main.py``
points ``FONTCONFIG_FILE`` at it before creating the QApplication.
"""
import os
import unittest
import xml.etree.ElementTree as ET

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTCONFIG_PATH = os.path.join(REPO_ROOT, ".media", "fonts", "fonts.conf")


class FontConfigTests(unittest.TestCase):
    def test_fontconfig_exists_and_parses(self):
        self.assertTrue(os.path.isfile(FONTCONFIG_PATH))
        root = ET.parse(FONTCONFIG_PATH).getroot()
        self.assertEqual(root.tag, "fontconfig")

    def test_rejects_colour_emoji_family(self):
        with open(FONTCONFIG_PATH, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("rejectfont", text)
        self.assertIn("Noto Color Emoji", text)

    def test_bundled_emoji_font_is_shipped(self):
        font = os.path.join(REPO_ROOT, ".media", "fonts",
                            "NotoEmoji-Regular.ttf")
        self.assertTrue(os.path.isfile(font))
        self.assertGreater(os.path.getsize(font), 0)

    def test_colour_emoji_font_is_shipped(self):
        font = os.path.join(REPO_ROOT, ".media", "fonts",
                            "Twemoji.Mozilla.ttf")
        self.assertTrue(os.path.isfile(font))
        self.assertGreater(os.path.getsize(font), 0)
        license_file = os.path.join(REPO_ROOT, ".media", "fonts",
                                    "Twemoji-LICENSE.txt")
        self.assertTrue(os.path.isfile(license_file))

    def test_linux_stylesheet_prefers_the_colour_font(self):
        import sys
        from core.config import DEFAULT_PURPLE_RIP_THEME, get_stylesheet

        stylesheet = get_stylesheet(DEFAULT_PURPLE_RIP_THEME)
        if sys.platform.startswith("linux"):
            self.assertIn("Twemoji Mozilla", stylesheet)
        self.assertIn("Noto Emoji", stylesheet)


if __name__ == "__main__":
    unittest.main()
