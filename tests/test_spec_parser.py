"""Test del parser delle sezioni requisiti / game info di SteamRip."""

import unittest

from core.spec_parser import icon_for_label, looks_structured, parse_spec_html

STANDARD_REQUIREMENTS = """
<style>body { color: #e0e0e0; }</style>
<ul>
  <li><strong>OS:</strong> Windows 10/11 with updates</li>
  <li><strong>Processor:</strong> AMD Ryzen 5 2600 / Intel i5-8400</li>
  <li><strong>Memory:</strong> 16&nbsp;GB RAM</li>
  <li><strong>Graphics:</strong> NVIDIA GTX 1070</li>
  <li><strong>DirectX:</strong> Version 12</li>
  <li><strong>Storage:</strong> 40 GB available space</li>
</ul>
"""


class SpecParserTests(unittest.TestCase):

    def test_standard_list_is_structured(self):
        items = parse_spec_html(STANDARD_REQUIREMENTS)
        self.assertEqual(len(items), 6)
        self.assertTrue(looks_structured(items))
        self.assertEqual(items[0]["label"], "OS")
        self.assertEqual(items[0]["value"], "Windows 10/11 with updates")
        self.assertEqual(items[0]["icon"], "spec_os")
        self.assertEqual(items[1]["icon"], "spec_cpu")
        self.assertEqual(items[2]["icon"], "spec_ram")
        self.assertEqual(items[3]["icon"], "spec_gpu")
        self.assertEqual(items[4]["icon"], "spec_dx")
        self.assertEqual(items[5]["icon"], "spec_storage")

    def test_style_block_is_ignored(self):
        items = parse_spec_html(STANDARD_REQUIREMENTS)
        joined = " ".join(item["value"] for item in items)
        self.assertNotIn("color:", joined)

    def test_nbsp_is_normalized(self):
        items = parse_spec_html(STANDARD_REQUIREMENTS)
        memory = next(item for item in items if item["label"] == "Memory")
        self.assertEqual(memory["value"], "16 GB RAM")

    def test_unknown_labels_get_no_icon(self):
        html = "<ul><li><strong>Something Custom:</strong> value</li></ul>"
        items = parse_spec_html(html)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["icon"])
        self.assertEqual(items[0]["value"], "value")

    def test_list_without_bold_labels(self):
        html = "<ul><li>Just a line of text</li><li>Another line</li></ul>"
        items = parse_spec_html(html)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["label"], "")
        self.assertEqual(items[0]["value"], "Just a line of text")
        self.assertIsNone(items[0]["icon"])

    def test_paragraph_fallback(self):
        html = "<p><strong>Genre:</strong> Action, Adventure</p><p><strong>Developer:</strong> Obsidian</p>"
        items = parse_spec_html(html)
        self.assertTrue(looks_structured(items))
        self.assertEqual(items[0]["icon"], "info_genre")
        self.assertEqual(items[1]["icon"], "info_dev")

    def test_plain_text_is_not_structured(self):
        self.assertEqual(parse_spec_html("Nessuna info tecnica aggiuntiva."), [])
        self.assertFalse(looks_structured(parse_spec_html("<p>Dati requisiti non disponibili.</p>")))

    def test_empty_input(self):
        self.assertEqual(parse_spec_html(""), [])
        self.assertEqual(parse_spec_html(None), [])

    def test_released_by_uses_publisher_icon(self):
        html = "<ul><li><strong>Released by:</strong> SteamRip</li><li><strong>Genre:</strong> Action</li></ul>"
        items = parse_spec_html(html)
        self.assertEqual(items[0]["icon"], "info_pub")
        self.assertEqual(items[0]["value"], "SteamRip")

    def test_coop_by_variant_has_icon(self):
        for label in ("Co-op By", "Co-op by:", "Coop By", "Co-Op:", "Co-op"):
            self.assertEqual(icon_for_label(label), "info_coop", label)

    def test_label_prefix_variants(self):
        self.assertEqual(icon_for_label("OS Version"), "spec_os")
        self.assertEqual(icon_for_label("Game Size (GB)"), "info_size")
        self.assertEqual(icon_for_label("Released by"), "info_pub")

    def test_icon_for_label_normalization(self):
        self.assertEqual(icon_for_label("OS:"), "spec_os")
        self.assertEqual(icon_for_label("  Processor  "), "spec_cpu")
        self.assertEqual(icon_for_label("Game Size"), "info_size")
        self.assertIsNone(icon_for_label("Unmapped label"))


if __name__ == "__main__":
    unittest.main()
