import re
import requests
from urllib.parse import quote
from PyQt6.QtCore import QThread, pyqtSignal

from core.price_utils import detect_currency, parse_price_to_cents


class SalesWorker(QThread):
    sale_data_ready = pyqtSignal(dict, dict, str)

    def __init__(self, game_title, steam_id="", region="US"):
        super().__init__()
        self.game_title = game_title
        self.steam_app_id = steam_id
        self.region = (region or "US").upper()
        self._headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def _ultra_clean(self, text):
        text = re.sub(r'(?i)(game of the year|complete edition|ultimate edition|director\'s cut|deluxe edition|sunset edition|digital edition|gold edition|standard edition)', '', text)
        return re.sub(r'[^a-z0-9]', '', text.lower())

    def _get_match_score(self, search_title, result_title):
        s_clean = self._ultra_clean(search_title)
        r_clean = self._ultra_clean(result_title)
        if s_clean == r_clean:
            return 2
        if len(s_clean) >= 3 and len(r_clean) >= 3:
            if s_clean in r_clean or r_clean in s_clean:
                return 1
        return 0

    @staticmethod
    def _money_to_cents(money, fallback_text=""):
        if isinstance(money, dict):
            amount = money.get("amount")
            if amount is not None:
                parsed = parse_price_to_cents(amount)
                if parsed is not None:
                    return parsed
        return parse_price_to_cents(fallback_text)

    def _query_steam_by_id(self, app_id):
        try:
            url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc={self.region}&filters=price_overview"
            r = requests.get(url, headers=self._headers, timeout=4)
            if r.status_code != 200:
                return None
            data = r.json()
            app_data = data.get(str(app_id), {})
            if not app_data.get("success"):
                return None
            price = app_data.get("data", {}).get("price_overview", {})
            if not price:
                return {"url": f"https://store.steampowered.com/app/{app_id}", "on_sale": False, "discount_percent": 0, "final_price": ""}
            return {
                "url": f"https://store.steampowered.com/app/{app_id}",
                "on_sale": price.get("discount_percent", 0) > 0,
                "discount_percent": price.get("discount_percent", 0),
                "final_price": price.get("final_formatted", ""),
                "initial_price": price.get("initial_formatted", ""),
                "final_cents": int(price.get("final") or 0),
                "initial_cents": int(price.get("initial") or 0),
                "currency": price.get("currency") or "USD",
                "region": self.region,
            }
        except Exception as e:
            print(f"[SalesWorker] Steam API error: {e}")
            return None

    def _query_steam_by_search(self):
        try:
            clean_title = re.sub(r'\(.*?\)', '', self.game_title).strip()
            url_safe = quote(clean_title)
            search_url = f"https://store.steampowered.com/api/storesearch/?term={url_safe}&l=english&cc={self.region}"
            r = requests.get(search_url, headers=self._headers, timeout=4)
            if r.status_code != 200:
                return None
            data = r.json()
            best_score = 0
            best_id = None
            for item in data.get("items", [])[:10]:
                score = self._get_match_score(clean_title, item.get("name", ""))
                if score == 2:
                    best_id = item['id']
                    break
                if score == 1 and best_score < 1:
                    best_id = item['id']
                    best_score = 1
            if best_id:
                return self._query_steam_by_id(best_id)
            return {
                "url": f"https://store.steampowered.com/search/?term={url_safe}",
                "on_sale": False,
                "discount_percent": 0,
                "final_price": ""
            }
        except Exception as e:
            print(f"[SalesWorker] Steam search error: {e}")
            return None

    def _query_gog(self):
        try:
            clean_title = re.sub(r'\(.*?\)', '', self.game_title).strip()
            url_safe = quote(clean_title)

            api_url = f"https://catalog.gog.com/v1/catalog?limit=10&query=like:{url_safe}"
            r_api = requests.get(api_url, headers=self._headers, timeout=4)
            if r_api.status_code != 200:
                return None
            data = r_api.json()

            best_score = 0
            best_product = None

            for product in data.get("products", []):
                score = self._get_match_score(clean_title, product.get("title", ""))
                if score >= 1 and score > best_score:
                    best_score = score
                    best_product = product
                    if score == 2:
                        break

            if not best_product:
                return {
                    "url": f"https://www.gog.com/en/games?query={url_safe}",
                    "on_sale": False,
                    "discount_percent": 0,
                    "final_price": ""
                }

            slug = best_product.get("slug", "")
            url = f"https://www.gog.com/en/game/{slug}"

            price_data = best_product.get("price")
            if not price_data:
                return {
                    "url": url,
                    "on_sale": False,
                    "discount_percent": 0,
                    "final_price": ""
                }

            # GOG catalog price format:
            #   discount: "-25%"  (string with percent)
            #   final: "$26.25"   (formatted string)
            #   finalMoney: { "amount": "26.25", "currency": "USD" }
            discount_str = price_data.get("discount") or ""
            discount = 0
            if discount_str and isinstance(discount_str, str):
                m = re.search(r'(\d+)', discount_str.replace('-', ''))
                if m:
                    discount = int(m.group(1))

            final_str = price_data.get("final") or "$0.00"
            initial_str = price_data.get("initial") or final_str
            final_money = price_data.get("finalMoney")
            initial_money = price_data.get("initialMoney")
            currency = "USD"
            if isinstance(final_money, dict):
                currency = final_money.get("currency") or detect_currency(final_str)
            else:
                currency = detect_currency(final_str)

            final_cents = self._money_to_cents(final_money, final_str)
            initial_cents = self._money_to_cents(initial_money, initial_str)

            return {
                "url": url,
                "on_sale": discount > 0,
                "discount_percent": discount,
                "final_price": f"{(final_cents or 0) / 100:.2f} {currency}",
                "initial_price": f"{(initial_cents or 0) / 100:.2f} {currency}",
                "final_cents": final_cents,
                "initial_cents": initial_cents,
                "currency": currency,
                "region": self.region,
            }
        except Exception as e:
            print(f"[SalesWorker] GOG error: {e}")
            return None

    def run(self):
        steam_result = None
        if self.steam_app_id:
            steam_result = self._query_steam_by_id(self.steam_app_id)
        if steam_result is None:
            steam_result = self._query_steam_by_search()

        gog_result = self._query_gog()

        if steam_result is None:
            steam_result = {"url": "", "on_sale": False, "discount_percent": 0, "final_price": ""}
        if gog_result is None:
            gog_result = {"url": "", "on_sale": False, "discount_percent": 0, "final_price": ""}

        self.sale_data_ready.emit(steam_result, gog_result, self.game_title)
