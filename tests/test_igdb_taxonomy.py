"""IGDB taxonomy labels: JSON keys are strings, a fresh fetch is not."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from network.igdb_meta_worker import _fetch_taxonomy, taxonomy_names


class TaxonomyLookupTests(unittest.TestCase):
    def test_labels_resolve_from_string_keys(self):
        taxonomy = {"genres": {"12": "Role-playing (RPG)"}}

        self.assertEqual(
            taxonomy_names(taxonomy, "genres", [12]), ["Role-playing (RPG)"]
        )

    def test_labels_resolve_from_int_keys(self):
        # A taxonomy fetched in this same run keeps the ids as ints until it is
        # written and reloaded from JSON.
        taxonomy = {"themes": {31: "Drama"}}

        self.assertEqual(taxonomy_names(taxonomy, "themes", [31]), ["Drama"])

    def test_unknown_id_falls_back_to_the_number(self):
        self.assertEqual(taxonomy_names({"genres": {}}, "genres", [99]), ["99"])

    def test_missing_kind_falls_back_to_the_number(self):
        self.assertEqual(taxonomy_names({}, "modes", [5]), ["5"])
        self.assertEqual(taxonomy_names(None, "modes", [5]), ["5"])

    def test_mixed_ids_keep_their_order(self):
        taxonomy = {"genres": {"1": "Adventure", "2": "RPG"}}

        self.assertEqual(
            taxonomy_names(taxonomy, "genres", [2, 1, 3]), ["RPG", "Adventure", "3"]
        )


class FetchTaxonomyTests(unittest.TestCase):
    def test_fetch_stores_string_keys(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "igdb_taxonomy.json"
        response = mock.Mock(status_code=200)
        response.json.side_effect = lambda: [
            {"id": 12, "name": "Role-playing (RPG)"},
            {"id": 31, "name": "Drama"},
        ]

        with mock.patch(
            "network.igdb_meta_worker.requests.post", return_value=response
        ), mock.patch(
            "network.igdb_meta_worker._taxonomy_path", return_value=str(path)
        ):
            data = _fetch_taxonomy({"Client-ID": "test"})

        self.assertEqual(
            data["genres"], {"12": "Role-playing (RPG)", "31": "Drama"}
        )
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["genres"], data["genres"])
        self.assertEqual(
            taxonomy_names(saved, "themes", [31]), ["Drama"]
        )


if __name__ == "__main__":
    unittest.main()
