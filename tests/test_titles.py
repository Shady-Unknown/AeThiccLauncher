"""Shared title keys and the title matching rule."""
import unittest

from core import titles


class NormalizeTests(unittest.TestCase):
    def test_editions_accents_and_punctuation(self):
        self.assertEqual(
            titles.normalize_title("DOOM: Eternal — Deluxe Edition"),
            "doometernal",
        )
        self.assertEqual(
            titles.normalize_title("Pokémon™ & Friends"),
            "pokemonandfriends",
        )

    def test_spaced_key_keeps_editions(self):
        self.assertEqual(
            titles.normalize_spaced("  Control: Ultimate Edition "),
            "control ultimate edition",
        )


class MatchesTests(unittest.TestCase):
    def test_identical_titles(self):
        self.assertTrue(titles.matches("Control Ultimate Edition", "Control Ultimate Edition"))
        self.assertTrue(titles.matches("DOOM", "Doom"))

    def test_edition_suffix_is_allowed(self):
        self.assertTrue(titles.matches("DOOM Eternal", "DOOM Eternal: Deluxe Edition"))
        self.assertTrue(titles.matches("Cyberpunk 2077", "Cyberpunk 2077 Ultimate Edition"))
        self.assertTrue(titles.matches("The Alters", "Alters"))

    def test_sequels_are_rejected(self):
        self.assertFalse(titles.matches("DOOM", "DOOM 3"))
        self.assertFalse(titles.matches("Control", "Control 2"))
        self.assertFalse(titles.matches("Grand Theft Auto", "Grand Theft Auto V"))

    def test_unrelated_titles(self):
        self.assertFalse(titles.matches("DOOM", "Quake Champions"))
        self.assertFalse(titles.matches("", "DOOM"))
        self.assertFalse(titles.matches(None, "DOOM"))


class SequelTests(unittest.TestCase):
    def test_detects_numeric_and_roman_sequels(self):
        self.assertTrue(titles.looks_like_sequel("DOOM", "DOOM 3"))
        self.assertTrue(titles.looks_like_sequel("Grand Theft Auto", "Grand Theft Auto V"))
        self.assertFalse(titles.looks_like_sequel("DOOM", "DOOM Eternal"))
        self.assertFalse(titles.looks_like_sequel("DOOM", "DOOM"))


if __name__ == "__main__":
    unittest.main()
