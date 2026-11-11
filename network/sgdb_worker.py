import urllib.parse
import requests
from PyQt6.QtCore import QThread, pyqtSignal

from core import secrets


class SGDBWorker(QThread):
    sgdb_finished = pyqtSignal(str, dict)

    def __init__(self, game_title):
        super().__init__()
        self.game_title = game_title
        self._headers = secrets.sgdb_headers()

    def _steam_store_search(self):
        """Fallback: query Steam store search API for the real App ID."""
        try:
            url = f"https://store.steampowered.com/api/storesearch?term={urllib.parse.quote(self.game_title)}&cc=US&l=en"
            headers = {"User-Agent": "AeThiccLauncher/1.0"}
            print(f"[SGDB] Steam search fallback: {url}")
            r = requests.get(url, headers=headers, timeout=3)
            if r.status_code != 200:
                print(f"[SGDB] Steam search status: {r.status_code}")
                return ""
            data = r.json()
            items = data.get("items", [])
            if not items:
                print(f"[SGDB] Steam search: no items found")
                return ""
            first = items[0]
            found_id = first.get("id", "")
            found_name = first.get("name", "")
            print(f"[SGDB] Steam search first result: name='{found_name}', id={found_id}")
            # loose name match
            if (self.game_title.lower() not in found_name.lower()
                    and found_name.lower() not in self.game_title.lower()):
                print(f"[SGDB] Steam search name mismatch: '{self.game_title}' vs '{found_name}'")
                return ""
            print(f"[SGDB] Steam search matched: steam_app_id={found_id}")
            return str(found_id)
        except Exception as e:
            print(f"[SGDB] Steam search exception: {e}")
            return ""

    def run(self):
        sgdb_assets = {"steam_app_id": "", "grid": "", "hero": "", "logo": "", "wide": ""}
        if not self._headers:
            # No key: still resolve the Steam app id without SteamGridDB.
            steam_id = self._steam_store_search()
            if steam_id:
                sgdb_assets["steam_app_id"] = steam_id
            self.sgdb_finished.emit(self.game_title, sgdb_assets)
            return
        try:
            search_url = f"https://www.steamgriddb.com/api/v2/search/autocomplete/{urllib.parse.quote(self.game_title)}"
            print(f"[SGDB] Searching: {search_url}")
            r_search = requests.get(search_url, headers=self._headers, timeout=2)
            print(f"[SGDB] Search status: {r_search.status_code}")

            game_id = None
            if r_search.status_code == 200 and r_search.json().get("data"):
                game_data_item = r_search.json()["data"][0]
                result_name = game_data_item.get("name", "")
                app_id_raw = game_data_item.get("steam_app_id", "")
                print(f"[SGDB] First result: name='{result_name}', steam_app_id='{app_id_raw}'")
                if (self.game_title.lower() not in result_name.lower()
                        and result_name.lower() not in self.game_title.lower()):
                    print(f"[SGDB] Name match FAILED: '{self.game_title}' vs '{result_name}'")
                else:
                    game_id = game_data_item["id"]
                    sgdb_assets["steam_app_id"] = str(app_id_raw or "")
                    print(f"[SGDB] Name match OK, game_id={game_id}, steam_app_id='{sgdb_assets['steam_app_id']}'")

                # Fallback: if SGDB returned no steam_app_id, try Steam directly
                if not sgdb_assets["steam_app_id"]:
                    steam_id = self._steam_store_search()
                    if steam_id:
                        sgdb_assets["steam_app_id"] = steam_id

                if game_id:
                    print(f"[SGDB] Fetching assets for game_id={game_id}")
                    r_grid = requests.get(f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}?dimensions=600x900", headers=self._headers, timeout=2)
                    if r_grid.status_code == 200 and r_grid.json().get("data"):
                        sgdb_assets["grid"] = r_grid.json()["data"][0]["url"]
                        print(f"[SGDB] grid={sgdb_assets['grid']}")

                    r_hero = requests.get(f"https://www.steamgriddb.com/api/v2/heroes/game/{game_id}", headers=self._headers, timeout=2)
                    if r_hero.status_code == 200 and r_hero.json().get("data"):
                        sgdb_assets["hero"] = r_hero.json()["data"][0]["url"]
                        print(f"[SGDB] hero={sgdb_assets['hero']}")

                    r_logo = requests.get(f"https://www.steamgriddb.com/api/v2/logos/game/{game_id}", headers=self._headers, timeout=2)
                    if r_logo.status_code == 200 and r_logo.json().get("data"):
                        sgdb_assets["logo"] = r_logo.json()["data"][0]["url"]
                        print(f"[SGDB] logo={sgdb_assets['logo']}")

                    r_wide = requests.get(f"https://www.steamgriddb.com/api/v2/grids/game/{game_id}?dimensions=920x430", headers=self._headers, timeout=2)
                    if r_wide.status_code == 200 and r_wide.json().get("data"):
                        sgdb_assets["wide"] = r_wide.json()["data"][0]["url"]
                        print(f"[SGDB] wide={sgdb_assets['wide']}")
            else:
                body_preview = r_search.text[:200] if r_search.text else "(empty)"
                print(f"[SGDB] No data or bad status. body: {body_preview}")
                # Fallback even without SGDB result
                if not sgdb_assets["steam_app_id"]:
                    steam_id = self._steam_store_search()
                    if steam_id:
                        sgdb_assets["steam_app_id"] = steam_id
        except Exception as e:
            print(f"[SGDB] Exception: {e}")

        print(f"[SGDB] Final: steam_app_id='{sgdb_assets['steam_app_id']}', grid={'yes' if sgdb_assets['grid'] else 'no'}, hero={'yes' if sgdb_assets['hero'] else 'no'}, logo={'yes' if sgdb_assets['logo'] else 'no'}, wide={'yes' if sgdb_assets['wide'] else 'no'}")
        self.sgdb_finished.emit(self.game_title, sgdb_assets)
