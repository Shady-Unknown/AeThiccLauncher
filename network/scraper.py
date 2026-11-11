import re
import random
from bs4 import BeautifulSoup
from core.meta_utils import strip_title
from core.version_status import parse_version_status_html
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineHttpRequest
from network.browser_stealth import PersistentWebView, StealthWebPage, AdBlockInterceptor

class SteamRIPScraper(QObject):
    extract_started = pyqtSignal(str)  
    extract_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    download_requested_signal = pyqtSignal(object)
    catalog_ready = pyqtSignal(dict) 
    silent_update_ready = pyqtSignal(dict, str)
    sgdb_assets_ready = pyqtSignal(str, dict)  # title, sgdb_assets dict
    cf_bypassed = pyqtSignal(str, dict) # NUOVO SEGNALE PER IL BYPASS CLOUDFLARE INVISIBILE
    cover_fetched = pyqtSignal(str, str)  # title, featured image url

    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.profile = QWebEngineProfile.defaultProfile()
        
        # --- NIENTE PIÙ SCRIPT EVASION O USER-AGENT FALSI ---
        # Lasciamo che il browser PyQt comunichi con i suoi veri dati (Client Hints genuini).
        
        self.profile.downloadRequested.connect(self._handle_download_request)
        self.browser = PersistentWebView()
        
        # Aggancio assoluto dell'AdBlocker al profilo principale
        self.adblocker = AdBlockInterceptor(self)
        self.profile.setUrlRequestInterceptor(self.adblocker)
        self.browser.setWindowTitle("SteamRIP - Background Engine")
        self.browser.resize(1024, 768)
        
        self.page = StealthWebPage(self.profile, self.browser)
        self.browser.setPage(self.page)
        self.browser.move(-10000, -10000)
        
        self.browser.urlChanged.connect(self._handle_url_change)
        self.browser.loadFinished.connect(self._on_load_finished)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._check_dom_state)
        
        self.current_mode = None
        self.timeout_counter = 0
        self._active_sgdb_workers = []

    def solve_cloudflare(self, url):
        """Metodo chiamato dalla GUI per risolvere URL ostici usando il browser nativo."""
        self.timer.stop()
        self.current_mode = "cf_bypass"
        self.timeout_counter = 0
        self.target_url = url
        self.browser.move(-10000, -10000)
        self.browser.hide()
        self.browser.load(QUrl(url))
        self.timer.start(500)

    def fetch_master_catalog(self):
        print("[Scraper] Fetching Master Catalog...")
        delay = random.randint(500, 2000)
        QTimer.singleShot(delay, lambda: self._start_fetch_sequence())

    def _start_fetch_sequence(self):
        """Metodo privato che esegue effettivamente il fetch."""    
        self.timer.stop()
        self.current_mode = "catalog"
        self.timeout_counter = 0
        self.target_url = "https://steamrip.com/games-list-page"
        self.browser.move(-10000, -10000)
        self.browser.hide()
        self.browser.load(QUrl("https://steamrip.com/games-list-page/"))
        self.timer.start(500)

    def check_update_silently(self, url_string, game_title):
        print(f"[Scraper] Silent check: {game_title}")
        self.timer.stop()
        self.current_mode = "silent_update"
        self.current_silent_title = game_title
        self.timeout_counter = 0
        self.target_url = url_string
        self.browser.move(-10000, -10000)
        self.browser.hide()
        self.browser.load(QUrl(url_string))
        self.timer.start(500)

    def search_game(self, query):
        self.timer.stop()
        self.current_mode = "search"
        self.browser.setWindowTitle("SteamRIP - Search")
        self.browser.move(100, 100) 
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()
        url = QUrl(f"https://steamrip.com/?s={query}")
        self.browser.load(url)

    def extract_game(self, url_string):
        self.timer.stop()
        self.current_mode = "extract"
        self.timeout_counter = 0
        self.target_url = url_string
        self.browser.move(-10000, -10000)
        self.browser.hide()
        self.browser.load(QUrl(url_string))
        self.timer.start(500)

    def fetch_cover(self, game_title, url_string):
        """Lightweight mode: load a game page and extract only the featured cover image."""
        self.timer.stop()
        self.current_mode = "cover"
        self.current_cover_title = game_title
        self.timeout_counter = 0
        self.target_url = url_string
        self.browser.move(-10000, -10000)
        self.browser.hide()
        self.browser.load(QUrl(url_string))
        self.timer.start(500)

    def open_download_link(self, url_string):
        self.timer.stop()
        self.current_mode = "download" 
        self.browser.setWindowTitle("Bowser the Browser - Bypass Gate")
        self.browser.move(100, 100) 
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()
        
        req = QWebEngineHttpRequest(QUrl(url_string))
        req.setHeader(b"Referer", b"https://steamrip.com/")
        self.browser.load(req)

    def _handle_url_change(self, qurl):
        url_str = qurl.toString()
        if self.current_mode == "search" and url_str != "about:blank":
            if "?s=" not in url_str and url_str != "https://steamrip.com/":
                self.current_mode = "extract"
                self.timeout_counter = 0
                self.target_url = url_str
                self.browser.hide()
                self.browser.move(-10000, -10000) 
                self.extract_started.emit(url_str)
                self.timer.start(1000)
    
    def _handle_download_request(self, download_item):
        self.download_requested_signal.emit(download_item)

    def _on_load_finished(self, ok):
        if ok and self.current_mode == "download":
            js = "document.querySelectorAll('a[target=\"_blank\"]').forEach(a => a.removeAttribute('target'));"
            self.page.runJavaScript(js)
            
            js_gofile = """
            if (window.location.hostname.includes('gofile.io')) {
                let clicker = setInterval(() => {
                    let bodyText = document.body.innerText || "";
                    if (bodyText.includes("High Traffic Alert") || bodyText.includes("Bulk download is a Premium feature")) {
                        clearInterval(clicker);
                        return;
                    }
                    let btns = Array.from(document.querySelectorAll('button, a')).filter(el => {
                        return el.innerText && el.innerText.trim().toLowerCase() === 'download';
                    });
                    if (btns.length > 0) {
                        btns[btns.length - 1].click();
                        clearInterval(clicker);
                    }
                }, 500);
            }
            """
            self.page.runJavaScript(js_gofile)

    def _check_dom_state(self):
        if self.current_mode not in ["extract", "catalog", "silent_update", "cf_bypass", "cover"]:
            return
            
        self.timeout_counter += 1
        
        # === 1. GESTIONE DEDICATA AL BYPASS CLOUDFLARE INVISIBILE ===
        if self.current_mode == "cf_bypass":
            if self.timeout_counter >= 16 and not self.browser.isVisible():
                print("[Scraper] ⚠️ Turnstile necessita di interazione manuale. Mostro il browser...")
                self.browser.setWindowTitle("Risoluzione Cloudflare in corso...")
                self.browser.resize(800, 600)
                self.browser.showNormal()
                self.browser.raise_()
                self.browser.activateWindow()
            
            js = """
            (function() {
                if (document.title.includes('Just a moment') || document.title.includes('Attention Required')) return false;
                if (document.querySelector('.cf-turnstile-wrapper') || document.querySelector('#challenge-stage')) return false;
                return document.readyState === 'complete';
            })();
            """
            self.page.runJavaScript(js, self._handle_cf_solved_check)
            return # Ferma il resto per evitare i timeout normali

        # === TIMEOUT NORMALI PER ESTRAZIONE E CATALOGO ===
        if self.timeout_counter == 12:
            print("[Scraper] ⚠️ Possible Cloudflare block! Showing browser for manual bypass...")
            self.browser.setWindowTitle("SteamRIP - Cloudflare Bypass")
            self.browser.resize(800, 600)
            self.browser.move(100, 100) 
            self.browser.showNormal() 
            self.browser.raise_()
            self.browser.activateWindow()

        if self.timeout_counter > 60: 
            self.timer.stop()
            self.browser.hide()
            print("[Scraper] ❌ Timeout! Operation cancelled.")
            mode = self.current_mode
            self.current_mode = None
            if mode == "silent_update":
                self.silent_update_ready.emit({}, getattr(self, 'current_silent_title', ''))
            elif mode == "cover":
                self.cover_fetched.emit(getattr(self, 'current_cover_title', ''), '')
            else:
                self.error_occurred.emit("Timeout during data processing.")
            return

        if self.timeout_counter <= 3:
            return

        target = getattr(self, 'target_url', '')
        target_path = "/" + target.strip('/').split('/')[-1] if target else ""
        
        if self.current_mode in ["extract", "silent_update"]:
            js = f"""
            (function() {{
                var isComplete = document.readyState === 'complete';
                var currentPath = decodeURIComponent(window.location.pathname).toLowerCase();
                var expectedPath = decodeURIComponent('{target_path}').toLowerCase();
                var hasTarget = currentPath.includes(expectedPath);
                var hasContent = document.querySelector('.entry-content') !== null;
                return isComplete && hasTarget && hasContent;
            }})();
            """
            self.page.runJavaScript(js, self._handle_extract_check)
        
        elif self.current_mode == "catalog":
            js = """
            (function() {
                if (document.title.includes('Just a moment')) return false;
                return document.readyState === 'complete' && window.location.pathname.includes('games-list-page') && document.querySelectorAll('a').length > 500;
            })();
            """
            self.page.runJavaScript(js, self._handle_catalog_check)
        
        elif self.current_mode == "cover":
            js = """
            (function() {
                if (document.title.includes('Just a moment')) return false;
                var hasImg = document.querySelector('figure.single-featured-image img') !== null
                    || document.querySelector('img.wp-post-image') !== null
                    || document.querySelectorAll('article img').length > 0;
                return document.readyState === 'complete' && hasImg;
            })();
            """
            self.page.runJavaScript(js, self._handle_cover_check)

    def _handle_cf_solved_check(self, is_solved):
        if is_solved:
            self.timer.stop()
            self.browser.hide()
            self.page.toHtml(self._extract_cf_data)

    def _extract_cf_data(self, html):
        self.current_mode = None
        self.cf_bypassed.emit(html, {})

    def _handle_extract_check(self, element_found):
        if element_found:
            self.timer.stop()
            self.browser.hide()
            self.page.toHtml(self._parse_extract_html)

    def _handle_catalog_check(self, element_found):
        if element_found:
            self.timer.stop()
            self.browser.hide()
            self.page.toHtml(self._parse_catalog_html)

    def _handle_cover_check(self, element_found):
        if element_found:
            self.timer.stop()
            self.browser.hide()
            self.page.toHtml(self._parse_cover_html)

    def _parse_cover_html(self, html):
        self.current_mode = None
        try:
            soup = BeautifulSoup(html, 'lxml')
            img_tag = soup.find('figure', class_='single-featured-image')
            if img_tag and img_tag.find('img'):
                img_tag = img_tag.find('img')
            else:
                img_tag = (soup.find('img', class_='wp-post-image')
                           or soup.find('article img')
                           or soup.find('img'))
            img_url = ""
            if img_tag:
                img_url = (img_tag.get('data-src') or img_tag.get('data-lazy-src')
                           or img_tag.get('src', ''))
                if img_url.startswith('//'):
                    img_url = "https:" + img_url
                elif img_url.startswith('/'):
                    img_url = "https://steamrip.com" + img_url
            self.cover_fetched.emit(getattr(self, 'current_cover_title', ''), img_url)
        except Exception as e:
            print(f"[Scraper] ❌ Cover Parsing Error: {str(e)}")
            self.cover_fetched.emit(getattr(self, 'current_cover_title', ''), '')

    def _parse_catalog_html(self, html):
        try:
            soup = BeautifulSoup(html, 'lxml')
            games_dict = {}
            for a in soup.find_all('a'):
                title = a.get_text(strip=True)
                href = a.get('href', '').strip()
                if title and href:
                    if href.startswith('/'):
                        href = "https://steamrip.com" + href
                    elif href.startswith('//'):
                        href = "https:" + href
                        
                    if "steamrip.com" in href:
                        forbidden = ['/games-list-page', '/faq', '/category', '/genre', '/update-request', '?s=', 'wp-admin', 'wp-login']
                        if not any(f in href.lower() for f in forbidden) and len(title) > 2:
                            clean, version, mp_tag = strip_title(title)
                            games_dict[clean] = {
                                "url": href,
                                "raw_title": title,
                                "version": version,
                                "mp_tag": mp_tag,
                            }
                            
            self.current_mode = None
            print(f"[Scraper] Catalog parsing completed: {len(games_dict)} games indexed.")
            self.catalog_ready.emit(games_dict)
        except Exception as e:
            self.current_mode = None
            print(f"[Scraper] ❌ Catalog Parsing Error: {str(e)}")
            self.error_occurred.emit(f"Catalog Parsing Error: {str(e)}")


    def _parse_extract_html(self, html):
        print(f"[Python DEBUG] _parse_extract_html INIZIATO! Lunghezza HTML: {len(html) if html else 0}")
        try:
            print("[Python DEBUG] Step 1: Inizio BeautifulSoup parsing...")
            soup = BeautifulSoup(html, 'lxml')
            
            print("[Python DEBUG] Step 2: Estrazione Immagine e Titolo...")
            img_tag = soup.find('figure', class_='single-featured-image')
            if img_tag and img_tag.find('img'):
                img_tag = img_tag.find('img')
            else:
                img_tag = soup.find('img', class_='wp-post-image')
            
            img_url = ""
            if img_tag:
                img_url = img_tag.get('data-src') or img_tag.get('data-lazy-src') or img_tag.get('src', '')
                if img_url.startswith('//'): img_url = "https:" + img_url
                elif img_url.startswith('/'): img_url = "https://steamrip.com" + img_url

            title_tag = soup.find('h1', class_='entry-title') or soup.find('h1')
            raw_title = title_tag.text.strip() if title_tag else "Unknown Title"

            title, version, mp_tag = strip_title(raw_title)

            # Categorie SteamRip reali (link rossi sotto il titolo)
            steamrip_categories = []
            cat_wrap = soup.find(class_='post-cat-wrap')
            if cat_wrap:
                for a in cat_wrap.find_all('a'):
                    for cls in a.get('class', []) or []:
                        m = re.match(r'tie-cat-(\d+)', cls)
                        if m:
                            steamrip_categories.append(int(m.group(1)))
                            break

            content_div = soup.find(class_='entry-content')
            
            links = []
            desc_html, req_html, info_html, dlcs_html = "", "", "", ""
            current_sec = "desc"

            if content_div:
                for el in content_div.find_all(recursive=False):
                    text_lower = el.get_text(strip=True).lower()
                    if el.name == 'hr' or 'clearfix' in el.get('class', []) or 'sharedaddy' in el.get('class', []): continue
                    if el.name in ['h2', 'h3', 'h4']:
                        if 'screenshot' in text_lower:
                            current_sec = "skip"
                            continue
                        elif 'requirement' in text_lower or 'minimum:' in text_lower:
                            current_sec = "req"
                            continue
                        elif 'game info' in text_lower or 'game detail' in text_lower:
                            current_sec = "info"
                            continue
                    
                    if el.name == 'div' and 'toggle' in el.get('class', []):
                        head = el.find(class_='toggle-head')
                        if head:
                            head_text = head.get_text(strip=True).lower()
                            if 'dlc' in head_text:
                                ul = el.find('ul')
                                if ul: dlcs_html += str(ul)
                                continue
                            elif 'step' in head_text or 'how to' in head_text:
                                continue 
                    
                    dl_found = False
                    for a in el.find_all('a'):
                        href = a.get('href', '').strip()
                        if not href or href.startswith('#'): continue
                        if href.startswith('//'): href = "https:" + href
                        elif href.startswith('/'): href = "https://steamrip.com" + href

                        href_lower = href.lower()
                        if 'steamrip.com' in href_lower and '-free-download' in href_lower: continue
                        is_dl = False
                        classes = a.get('class', [])
                        if 'shortc-button' in classes or 'wp-block-button__link' in classes: is_dl = True
                        
                        hosters_map = {
                            'mega.nz': 'Mega', 'megadb': 'MegaDB', 'gofile.io': 'GoFile',
                            '1fichier.com': '1Fichier', 'buzzheavier.com': 'Buzzheavier',
                            'datanodes': 'DataNodes', 'qiwi.gg': 'Qiwi', 'pixeldrain.com': 'PixelDrain',
                            'filecrypt.cc': 'FileCrypt', 'bowfile.com': 'Bowfile',
                            'clicknupload': 'ClicknUpload', 'ddownload.com': 'DDownload',
                            'hexupload.net': 'HexUpload', 'rapidgator.net': 'Rapidgator',
                            'gamedrive.org': 'GameDrive', 'drive.google': 'Google Drive', 'mediafire.com': 'MediaFire'
                        }
                        
                        if any(d in href_lower for d in hosters_map.keys()) or 'magnet:' in href_lower or is_dl:
                            dl_found = True
                            full_text = el.get_text(strip=True).lower()
                            btn_text = a.get_text(strip=True)
                            name = "Download"
                            
                            is_update = False
                            update_version = ""
                            if len(full_text) < 150 and re.search(r'\b(update|patch|dlc)\b', full_text):
                                is_update = True
                                strong_tag = el.find('strong')
                                if strong_tag:
                                    update_version = strong_tag.get_text(" ", strip=True)
                                else:
                                    text_nodes = [t for t in el.stripped_strings if t != btn_text]
                                    update_version = " ".join(text_nodes)[:50]
                                
                            if 'magnet:' in href_lower or 'torrent' in full_text:
                                name = 'Torrent (Magnet)'
                            else:
                                matched = False
                                for domain, host_name in hosters_map.items():
                                    if domain in href_lower:
                                        name = host_name
                                        matched = True
                                        break
                                if not matched:
                                    if btn_text and len(btn_text) < 20 and 'download' not in btn_text.lower():
                                        name = btn_text
                                    else:
                                        match = re.search(r'https?://(?:www\.)?([^/]+)', href)
                                        if match: name = match.group(1).capitalize() 

                            if not any(l['url'] == href for l in links):
                                links.append({"name": name, "url": href, "is_update": is_update, "update_version": update_version})
                                
                    if dl_found: continue 
                    if el.find('img'): continue
                    if 'free download' in text_lower and len(text_lower) < 50: continue
                    if current_sec == "skip": continue
                    
                    html_chunk = str(el)
                    if current_sec == "req": req_html += html_chunk
                    elif current_sec == "info": info_html += html_chunk
                    elif current_sec == "desc": desc_html += html_chunk

            if dlcs_html:
                desc_html += f"<details style='background-color:rgba(115, 61, 145, 0.15); padding: 10px; border: 1px solid #733d91; border-radius: 5px; margin-top: 15px;'><summary style='color:#ff00a0; font-weight:bold; cursor:pointer; font-size:14px; outline: none;'>📦 MOSTRA DLC INCLUSI ({dlcs_html.count('<li>')} Pacchetti)</summary>{dlcs_html}</details>"

            base_css = "<style>body { color: #e0e0e0; font-family: 'Segoe UI', Arial; font-size: 13px; margin:0; } a { color: #ff00a0; text-decoration: underline; } ul { padding-left: 20px; margin-top: 5px; } li { margin-bottom: 4px; } h2, h3, h4 { color: #f3c6ff; margin-top: 10px; margin-bottom: 5px; font-size: 14px; text-transform: uppercase; } p { margin-top: 5px; margin-bottom: 5px; line-height: 1.5; } strong, b { color: #ffffff; }</style>"

            cover_urls = [img_url] if img_url else []

            # Widget "Version Status" del sito: build SteamRIP vs build Steam
            version_status = parse_version_status_html(html) or {}

            parsed_data = {
                "title": title, "version": version, "mp_tag": mp_tag, "img_url": img_url,
                "sgdb_assets": {"steam_app_id": "", "grid": "", "hero": "", "logo": "", "wide": ""},
                "cover_urls": cover_urls,
                "description": base_css + desc_html if desc_html else "Nessuna descrizione trovata.",
                "requirements": base_css + req_html if req_html else "Dati requisiti non disponibili.",
                "game_info": base_css + info_html if info_html else "Nessuna info tecnica aggiuntiva.",
                "links": links,
                "page_url": self.browser.url().toString(),
                "steam_id": str(version_status.get("app_id", "") or ""),
                "version_status": version_status,
                "steamrip_categories": steamrip_categories,
            }
            
            if self.current_mode == "silent_update":
                self.silent_update_ready.emit(parsed_data, getattr(self, 'current_silent_title', ''))
            else:
                self.extract_ready.emit(parsed_data)
                # Le asset SteamGridDB vengono richieste da MainWindow.display_game_data()
                # solo se non sono già in cache (vedi needs_sgdb_fetch), evitando
                # una chiamata API a ogni caricamento pagina.

            self.current_mode = None
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            mode = self.current_mode
            self.current_mode = None
            if mode == "silent_update":
                self.silent_update_ready.emit({}, getattr(self, 'current_silent_title', ''))
            else:
                self.error_occurred.emit(str(e))
