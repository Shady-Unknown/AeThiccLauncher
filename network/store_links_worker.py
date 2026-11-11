import re
import requests
from PyQt6.QtCore import QThread, pyqtSignal

class StoreLinksWorker(QThread):
    links_ready = pyqtSignal(str, str, str) 

    def __init__(self, game_title):
        super().__init__()
        self.game_title = game_title

    def _get_match_score(self, search_title, result_title):
        """Assegna 2 per match esatto, 1 per match parziale, 0 per nessun match."""
        def ultra_clean(text):
            text = re.sub(r'(?i)(game of the year|complete edition|ultimate edition|director\'s cut|deluxe edition)', '', text)
            return re.sub(r'[^a-z0-9]', '', text.lower())
            
        s_clean = ultra_clean(search_title)
        r_clean = ultra_clean(result_title)
        
        # Vittoria assoluta: i titoli puliti sono letteralmente identici
        if s_clean == r_clean:
            return 2
            
        # Fallback parziale di emergenza
        if len(s_clean) >= 3 and len(r_clean) >= 3:
            if s_clean in r_clean or r_clean in s_clean:
                return 1
                
        return 0

    def run(self):
        steam_url = ""
        gog_url = ""
        
        clean_title = re.sub(r'\(.*?\)', '', self.game_title).strip()
        url_safe_title = clean_title.replace(' ', '%20')
        
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}

        # --- 1. STEAM SEARCH (Best Match Logic) ---
        try:
            steam_api = f"https://store.steampowered.com/api/storesearch/?term={url_safe_title}&l=english&cc=US"
            r = requests.get(steam_api, headers=headers, timeout=4)
            if r.status_code == 200:
                data = r.json()
                best_score = 0
                for item in data.get("items", [])[:10]:
                    score = self._get_match_score(clean_title, item.get("name", ""))
                    if score == 2:
                        steam_url = f"https://store.steampowered.com/app/{item['id']}"
                        break # Match perfetto, fermiamo il ciclo!
                    elif score == 1 and best_score < 1:
                        steam_url = f"https://store.steampowered.com/app/{item['id']}"
                        best_score = 1 # Salviamo come riserva e continuiamo a cercare
        except Exception as e:
            print(f"[Store Worker] Steam Error: {e}")

        if not steam_url:
            steam_url = f"https://store.steampowered.com/search/?term={url_safe_title}"

        # --- 2. GOG SEARCH (Best Match Logic, API-only) ---
        try:
            gog_api = f"https://catalog.gog.com/v1/catalog?limit=10&query=like:{url_safe_title}"
            r_api = requests.get(gog_api, headers=headers, timeout=4)
            if r_api.status_code == 200:
                data = r_api.json()
                best_score = 0
                for product in data.get("products", []):
                    score = self._get_match_score(clean_title, product.get("title", ""))
                    if score == 2:
                        slug = product.get("slug")
                        if slug:
                            gog_url = f"https://www.gog.com/en/game/{slug}"
                            break
                    elif score == 1 and best_score < 1:
                        slug = product.get("slug")
                        if slug:
                            gog_url = f"https://www.gog.com/en/game/{slug}"
                            best_score = 1
        except Exception as e:
            print(f"[Store Worker] GOG API Error: {e}")

        self.links_ready.emit(steam_url, gog_url, self.game_title)

# --- CONFIGURATION PANEL ---
