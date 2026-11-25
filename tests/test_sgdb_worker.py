"""SteamGridDB worker: a wrong first hit must cache nothing."""
import unittest
from unittest import mock

from tests.qt_app import ensure_app

ensure_app()

from network.sgdb_worker import SGDBWorker

EMPTY_ASSETS = {
    "steam_app_id": "", "grid": "", "hero": "", "logo": "", "wide": "",
}


def _worker(title, headers=None):
    with mock.patch(
        "network.sgdb_worker.secrets.sgdb_headers",
        return_value=headers if headers is not None else {"Authorization": "Bearer test"},
    ):
        return SGDBWorker(title)


def _response(payload):
    response = mock.Mock(status_code=200)
    response.json.return_value = payload
    return response


class SgdbNameMatchTests(unittest.TestCase):
    def test_mismatch_emits_empty_assets_and_skips_the_steam_fallback(self):
        worker = _worker("DOOM")
        emitted = []
        worker.sgdb_finished.connect(
            lambda title, assets: emitted.append((title, assets))
        )
        search = _response(
            {"data": [{"id": 9, "name": "Quake Champions", "steam_app_id": "555"}]}
        )

        with mock.patch(
            "network.sgdb_worker.requests.get", return_value=search
        ) as get, mock.patch.object(
            worker, "_steam_store_search", return_value="777"
        ) as steam_search:
            worker.run()

        self.assertEqual(emitted, [("DOOM", dict(EMPTY_ASSETS))])
        steam_search.assert_not_called()
        self.assertEqual(get.call_count, 1)

    def test_sequel_result_is_rejected(self):
        worker = _worker("DOOM")
        emitted = []
        worker.sgdb_finished.connect(
            lambda title, assets: emitted.append((title, assets))
        )
        search = _response(
            {"data": [{"id": 9, "name": "DOOM 3", "steam_app_id": "555"}]}
        )

        with mock.patch(
            "network.sgdb_worker.requests.get", return_value=search
        ), mock.patch.object(
            worker, "_steam_store_search", return_value="777"
        ) as steam_search:
            worker.run()

        self.assertEqual(emitted, [("DOOM", dict(EMPTY_ASSETS))])
        steam_search.assert_not_called()

    def test_matching_title_fetches_every_asset(self):
        worker = _worker("DOOM")
        emitted = []
        worker.sgdb_finished.connect(
            lambda title, assets: emitted.append((title, assets))
        )

        def fake_get(url, **_kwargs):
            if "autocomplete" in url:
                return _response(
                    {"data": [{"id": 42, "name": "DOOM", "steam_app_id": "782330"}]}
                )
            if "dimensions=920x430" in url:
                return _response({"data": [{"url": "wide.jpg"}]})
            if "grids/game/42" in url:
                return _response({"data": [{"url": "grid.jpg"}]})
            if "heroes/game/42" in url:
                return _response({"data": [{"url": "hero.jpg"}]})
            if "logos/game/42" in url:
                return _response({"data": [{"url": "logo.jpg"}]})
            raise AssertionError(url)

        with mock.patch("network.sgdb_worker.requests.get", side_effect=fake_get):
            worker.run()

        self.assertEqual(len(emitted), 1)
        _title, assets = emitted[0]
        self.assertEqual(assets["steam_app_id"], "782330")
        self.assertEqual(assets["grid"], "grid.jpg")
        self.assertEqual(assets["hero"], "hero.jpg")
        self.assertEqual(assets["logo"], "logo.jpg")
        self.assertEqual(assets["wide"], "wide.jpg")

    def test_without_a_key_only_the_steam_app_id_is_resolved(self):
        worker = _worker("DOOM", headers={})
        emitted = []
        worker.sgdb_finished.connect(
            lambda title, assets: emitted.append((title, assets))
        )

        with mock.patch.object(
            worker, "_steam_store_search", return_value="782330"
        ), mock.patch("network.sgdb_worker.requests.get") as get:
            worker.run()

        self.assertEqual(emitted[0][1]["steam_app_id"], "782330")
        self.assertEqual(emitted[0][1]["grid"], "")
        get.assert_not_called()

    def test_empty_search_result_emits_empty_assets(self):
        worker = _worker("DOOM")
        emitted = []
        worker.sgdb_finished.connect(
            lambda title, assets: emitted.append((title, assets))
        )
        search = _response({"data": []})
        search.text = ""

        with mock.patch(
            "network.sgdb_worker.requests.get", return_value=search
        ), mock.patch.object(
            worker, "_steam_store_search", return_value=""
        ):
            worker.run()

        self.assertEqual(emitted, [("DOOM", dict(EMPTY_ASSETS))])


if __name__ == "__main__":
    unittest.main()
