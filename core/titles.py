"""Title normalisation shared by manifest, Discovery, IGDB and gold tier.

The launcher used to keep four near-copies of the same key (accent folding,
edition words, punctuation). A single implementation means a title matches the
same way in every feature, and a matching rule that refuses sequels.
"""
from __future__ import annotations

import re
import unicodedata

_EDITION_RE = re.compile(
    r"(?i)(game of the year|complete edition|ultimate edition|"
    r"director's cut|deluxe edition|sunset edition|digital edition|"
    r"gold edition|standard edition|enhanced edition|definitive edition|"
    r"collector's edition|anniversary edition|legacy edition|"
    r"remastered|remake|edition)"
)

_ROMAN_SEQUELS = {"ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}


def normalize_title(text) -> str:
    """Lowercase alphanumeric key: accent-, punctuation- and edition-insensitive."""
    text = re.sub(r"[™®©]", "", str(text or ""))
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("&", "and").lower()
    text = _EDITION_RE.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text)


def normalize_spaced(text) -> str:
    """Lowercase words separated by single spaces, editions kept."""
    text = re.sub(r"[^a-z0-9]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _words(text):
    words = normalize_spaced(text).split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return words


def matches(first, second) -> bool:
    """True when the two titles name the same game.

    Edition suffixes ("Deluxe Edition", "The Ancient Gods") are allowed, sequels
    ("DOOM" vs "DOOM 3", "Control" vs "Control 2") are not: a wrong match is
    worse than no match.
    """
    left = _words(first)
    right = _words(second)
    if not left or not right:
        return False
    if left == right:
        return True

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if longer[:len(shorter)] != shorter:
        return False

    extra = longer[len(shorter):]
    if not extra:
        return True
    head = re.sub(r"[^a-z0-9]", "", extra[0])
    if not head:
        return True
    if head.isdigit() or head in _ROMAN_SEQUELS:
        return False
    return True


def looks_like_sequel(first, second) -> bool:
    """True when the only difference is a sequel number."""
    left = _words(first)
    right = _words(second)
    if not left or not right:
        return False
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if longer[:len(shorter)] != shorter or len(longer) == len(shorter):
        return False
    head = re.sub(r"[^a-z0-9]", "", longer[len(shorter)])
    return bool(head) and (head.isdigit() or head in _ROMAN_SEQUELS)
