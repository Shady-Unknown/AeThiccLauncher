"""Parser for the site's curated "Recently Updated" page."""
import unittest

from network.steamrip_api import parse_updated_games

PAGE = """
<div class="updated-list-date">11/16/2026</div>
<ul class="updated-list">
  <li class="updated-list-item">
    <a class="updated-card" href="/world-war-z-aftermath-free-download/">
      <span class="updated-card-title">World War Z: Aftermath Free Download (v2.51)</span>
      <span class="updated-list-build">Build 26291645</span>
    </a>
  </li>
  <li class="updated-list-item">
    <a class="updated-card" href="https://steamrip.com/castle-crashers-free-download/">
      <span class="updated-card-title">Castle Crashers Free Download (v3.3)</span>
      <span class="updated-list-build">Build 24095717</span>
    </a>
  </li>
</ul>
<div class="updated-list-date">11/15/2026</div>
<ul class="updated-list">
  <li class="updated-list-item">
    <a class="updated-card" href="/aviassembly-free-download/">
      <span class="updated-card-title">Aviassembly Free Download (Build 26619158)</span>
      <span class="updated-list-build">Build 26619158</span>
    </a>
  </li>
  <li class="updated-list-item">
    <a class="updated-card" href="/world-war-z-aftermath-free-download/">
      <span class="updated-card-title">World War Z: Aftermath Free Download (v2.51)</span>
    </a>
  </li>
</ul>
"""


class UpdatedPageParserTests(unittest.TestCase):
    def test_entries_follow_the_page_order(self):
        entries = parse_updated_games(PAGE)
        self.assertEqual([e["title"] for e in entries], [
            "World War Z: Aftermath",
            "Castle Crashers",
            "Aviassembly",
        ])

    def test_payload_fields(self):
        entries = parse_updated_games(PAGE)
        first = entries[0]
        self.assertEqual(first["url"],
                         "https://steamrip.com/world-war-z-aftermath-free-download")
        self.assertEqual(first["build"], "Build 26291645")
        self.assertEqual(first["date"], "11/16/2026")
        self.assertEqual(first["version"], "v2.51")
        self.assertTrue(entries[1]["url"].startswith("https://steamrip.com/"))
        self.assertEqual(entries[2]["date"], "11/15/2026")

    def test_duplicate_urls_are_skipped(self):
        entries = parse_updated_games(PAGE)
        urls = [e["url"] for e in entries]
        self.assertEqual(len(urls), len(set(urls)))

    def test_empty_or_unknown_markup_yields_nothing(self):
        self.assertEqual(parse_updated_games(""), [])
        self.assertEqual(parse_updated_games("<html><body>changed</body></html>"),
                         [])


if __name__ == "__main__":
    unittest.main()
