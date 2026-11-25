"""Pure SteamRIP parsers: happy path, malformed and missing markup."""
import unittest

from network.steamrip_parse import (
    parse_catalog_html, parse_cover_html, parse_game_page_html,
)

GAME_HTML = """
<html><body>
<h1 class="entry-title">DOOM Eternal Free Download (v1.2.3 + Multiplayer)</h1>
<div class="post-cat-wrap">
  <a class="tie-cat-12" href="/genre/action">Action</a>
  <a class="tie-cat-34" href="/genre/fps">FPS</a>
</div>
<figure class="single-featured-image"><img data-src="/featured.jpg"></figure>
<div class="entry-content">
  <p>Some description</p>
  <h2>System Requirements</h2>
  <ul><li>CPU: fast</li></ul>
  <h2>Game Info</h2>
  <p>Info text</p>
  <div class="toggle">
    <div class="toggle-head">DLC Included</div>
    <ul><li>DLC A</li><li>DLC B</li></ul>
  </div>
  <div><a class="shortc-button" href="https://mega.nz/file/x">Download</a></div>
  <div><a href="https://steamrip.com/doom-free-download">internal</a></div>
  <div><a href="https://gofile.io/d/x">Mirror</a></div>
  <div><p>Update v1.1</p><a href="https://1fichier.com/x">Download</a></div>
</div>
</body></html>
"""

CATALOG_HTML = """
<html><body>
<a href="/game/doom-eternal">DOOM Eternal Free Download (v1.2.3)</a>
<a href="//steamrip.com/game/quake">Quake Free Download</a>
<a href="https://steamrip.com/game/hades">Hades Build 123 Free Download</a>
<a href="/faq">FAQ</a>
<a href="?s=doom">Search</a>
<a href="https://steamrip.com/game/x">x</a>
<a href="https://example.test/game/other">Other</a>
</body></html>
"""


class GamePageParserTests(unittest.TestCase):
    def test_full_page(self):
        data = parse_game_page_html(GAME_HTML)

        self.assertEqual(data["title"], "DOOM Eternal")
        self.assertEqual(data["version"], "v1.2.3")
        self.assertEqual(data["mp_tag"], "Multiplayer")
        self.assertEqual(data["steamrip_categories"], [12, 34])
        self.assertEqual(data["img_url"], "https://steamrip.com/featured.jpg")
        self.assertEqual(data["cover_urls"], ["https://steamrip.com/featured.jpg"])
        self.assertIn("Some description", data["description"])
        self.assertIn("DLC INCLUSI (2 Pacchetti)", data["description"])
        self.assertIn("CPU: fast", data["requirements"])
        self.assertIn("Info text", data["game_info"])
        names = [link["name"] for link in data["links"]]
        self.assertIn("Mega", names)
        self.assertIn("GoFile", names)
        self.assertIn("1Fichier", names)
        self.assertNotIn("internal", [link["url"] for link in data["links"]])
        self.assertTrue(any(link["is_update"] for link in data["links"]))

    def test_malformed_markup_still_returns_data(self):
        data = parse_game_page_html("<h1>Broken page</h1><div><p>text")

        self.assertEqual(data["title"], "Broken page")
        self.assertEqual(data["links"], [])
        self.assertIn("Nessuna descrizione", data["description"])

    def test_empty_page_uses_defaults(self):
        data = parse_game_page_html("")

        self.assertEqual(data["title"], "Unknown Title")
        self.assertEqual(data["version"], "Release Base")
        self.assertEqual(data["links"], [])
        self.assertEqual(data["steamrip_categories"], [])
        self.assertEqual(data["steam_id"], "")

    def test_missing_fields_do_not_raise(self):
        data = parse_game_page_html("<html><body><p>only text</p></body></html>")

        self.assertEqual(data["title"], "Unknown Title")
        self.assertEqual(data["img_url"], "")


class CatalogParserTests(unittest.TestCase):
    def test_catalog_links(self):
        games = parse_catalog_html(CATALOG_HTML)

        self.assertEqual(
            set(games), {"DOOM Eternal", "Quake", "Hades"}
        )
        self.assertEqual(
            games["DOOM Eternal"]["url"],
            "https://steamrip.com/game/doom-eternal",
        )
        self.assertEqual(games["DOOM Eternal"]["version"], "v1.2.3")
        self.assertEqual(
            games["Quake"]["url"], "https://steamrip.com/game/quake"
        )
        self.assertEqual(games["Hades"]["version"], "Release Base")

    def test_forbidden_and_external_links_are_skipped(self):
        games = parse_catalog_html(CATALOG_HTML)

        urls = [entry["url"] for entry in games.values()]
        self.assertNotIn("https://steamrip.com/faq", urls)
        self.assertNotIn("https://example.test/game/other", urls)
        self.assertNotIn("https://steamrip.com/game/x", urls)

    def test_empty_catalog(self):
        self.assertEqual(parse_catalog_html(""), {})


class CoverParserTests(unittest.TestCase):
    def test_cover_url(self):
        self.assertEqual(
            parse_cover_html('<figure class="single-featured-image">'
                             '<img data-src="/x.jpg"></figure>'),
            "https://steamrip.com/x.jpg",
        )
        self.assertEqual(parse_cover_html(""), "")


if __name__ == "__main__":
    unittest.main()
