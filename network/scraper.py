import logging
import re
import random
from PyQt6.QtCore import QObject, pyqtSignal, QTimer, QUrl
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineHttpRequest
from network.browser_stealth import PersistentWebView, StealthWebPage, AdBlockInterceptor
from network.scrape_queue import ScrapeQueue
from network.steamrip_parse import (
    parse_catalog_html, parse_cover_html, parse_game_page_html,
)

log = logging.getLogger(__name__)

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
        self.current_job = None
        self._interactive = False
        self.queue = ScrapeQueue()
        self.timeout_counter = 0

    def shutdown(self):
        """Stop the hidden WebEngine view before the application quits.

        The view is a top-level widget (it has no Qt parent), so closing the
        main window does not destroy it on its own.
        """
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        timer = getattr(self, "timer", None)
        if timer is not None:
            timer.stop()
        self._shutting_down = True
        queue = getattr(self, "queue", None)
        if queue is not None:
            queue.clear()
        self.current_job = None
        self.current_mode = None
        self._interactive = False
        try:
            self.profile.downloadRequested.disconnect(self._handle_download_request)
        except (TypeError, RuntimeError):
            pass
        clear_interceptor = getattr(self.profile, "setUrlRequestInterceptor", None)
        if clear_interceptor is not None:
            try:
                clear_interceptor(None)
            except (TypeError, RuntimeError):
                pass
        browser = getattr(self, "browser", None)
        page = getattr(self, "page", None)
        self.browser = None
        self.page = None
        if browser is not None:
            try:
                browser.stop()
                browser.setPage(None)
                browser.force_close = True
                browser.close()
                browser.deleteLater()
            except RuntimeError:
                pass
        if page is not None:
            try:
                page.deleteLater()
            except RuntimeError:
                pass

    def _enqueue_job(self, mode, url="", title="", priority=0):
        if getattr(self, "_shutting_down", False):
            return None
        current = self.current_job
        if current is not None and (
                current.mode, current.url, current.title) == (
                str(mode), str(url or ""), str(title or "")):
            return None
        job = self.queue.enqueue(mode, url=url, title=title, priority=priority)
        if job is not None:
            self._start_next_job()
        return job

    def _start_next_job(self):
        if getattr(self, "_shutting_down", False):
            return
        if self.current_job is not None or getattr(self, "_interactive", False):
            return
        job = self.queue.pop_next()
        if job is None:
            return
        self.current_job = job
        self.current_mode = job.mode
        self.target_url = job.url
        self.timeout_counter = 0
        self.browser.move(-10000, -10000)
        self.browser.hide()
        if job.mode == "catalog":
            self.browser.load(QUrl("https://steamrip.com/games-list-page/"))
        else:
            self.browser.load(QUrl(job.url))
        self.timer.start(500)
        if job.mode == "extract":
            self.extract_started.emit(job.url)

    def _finish_job(self, emit_timeout=False):
        job = self.current_job
        if job is None:
            return
        self.timer.stop()
        self.browser.hide()
        self.current_job = None
        self.current_mode = None
        self.target_url = ""
        if emit_timeout:
            if job.mode == "silent_update":
                self.silent_update_ready.emit({}, job.title)
            elif job.mode == "cover":
                self.cover_fetched.emit(job.title, "")
            elif job.mode == "catalog":
                self.error_occurred.emit("Timeout while loading the catalog.")
            else:
                self.error_occurred.emit("Timeout during data processing.")
        self._start_next_job()

    def _complete_job(self):
        self._finish_job()

    def _interactive_preempt(self, mode):
        """User-visible browser action: background jobs yield immediately.

        Search resumes the queue when a result is opened; the download browser
        has no reliable close signal, so it drops pending work instead of
        leaving the queue stuck forever.
        """
        self._interactive = True
        if mode == "download":
            self.queue.clear()
        job = self.current_job
        if job is not None:
            self.timer.stop()
            self.current_job = None
            self.current_mode = None
            self.target_url = ""
        self.current_mode = mode

    def resume_background(self):
        """Leave an interactive browser mode and resume the queue."""
        self._interactive = False
        self.current_mode = None
        self._start_next_job()

    def solve_cloudflare(self, url):
        """Metodo chiamato dalla GUI per risolvere URL ostici usando il browser nativo."""
        self._enqueue_job("cf_bypass", url=url, priority=10)

    def fetch_master_catalog(self):
        log.info("[Scraper] Fetching Master Catalog...")
        delay = random.randint(500, 2000)
        QTimer.singleShot(
            delay, lambda: self._enqueue_job("catalog", url="https://steamrip.com/games-list-page/"))

    def check_update_silently(self, url_string, game_title):
        log.info(f"[Scraper] Silent check: {game_title}")
        self._enqueue_job("silent_update", url=url_string, title=game_title)

    def search_game(self, query):
        self._interactive_preempt("search")
        self.browser.setWindowTitle("SteamRIP - Search")
        self.browser.move(100, 100)
        self.browser.show()
        self.browser.raise_()
        self.browser.activateWindow()
        url = QUrl(f"https://steamrip.com/?s={query}")
        self.browser.load(url)

    def extract_game(self, url_string):
        self._enqueue_job("extract", url=url_string)

    def fetch_cover(self, game_title, url_string):
        """Lightweight mode: load a game page and extract only the featured cover image."""
        self._enqueue_job("cover", url=url_string, title=game_title)

    def open_download_link(self, url_string):
        self._interactive_preempt("download")
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
                self.browser.hide()
                self.browser.move(-10000, -10000)
                self._interactive = False
                self.current_mode = None
                self._enqueue_job("extract", url=url_str, priority=5)
    
    def _handle_download_request(self, download_item):
        self.download_requested_signal.emit(download_item)
        if self.current_mode == "download":
            self.browser.hide()
            self.resume_background()

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
                log.info("[Scraper] ⚠️ Turnstile necessita di interazione manuale. Mostro il browser...")
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
            log.info("[Scraper] ⚠️ Possible Cloudflare block! Showing browser for manual bypass...")
            self.browser.setWindowTitle("SteamRIP - Cloudflare Bypass")
            self.browser.resize(800, 600)
            self.browser.move(100, 100) 
            self.browser.showNormal() 
            self.browser.raise_()
            self.browser.activateWindow()

        if self.timeout_counter > 60:
            log.info("[Scraper] ❌ Timeout! Operation cancelled.")
            self._finish_job(emit_timeout=True)
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
        self.cf_bypassed.emit(html, {})
        self._complete_job()

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
        try:
            img_url = parse_cover_html(html)
            self.cover_fetched.emit(
                self.current_job.title if self.current_job else '', img_url)
        except Exception as e:
            log.exception(f"[Scraper] ❌ Cover Parsing Error: {str(e)}")
            self.cover_fetched.emit(
                self.current_job.title if self.current_job else '', '')
        finally:
            self._complete_job()

    def _parse_catalog_html(self, html):
        try:
            games_dict = parse_catalog_html(html)
            log.info(f"[Scraper] Catalog parsing completed: {len(games_dict)} games indexed.")
            self.catalog_ready.emit(games_dict)
        except Exception as e:
            log.exception(f"[Scraper] ❌ Catalog Parsing Error: {str(e)}")
            self.error_occurred.emit(f"Catalog Parsing Error: {str(e)}")
        finally:
            self._complete_job()

    def _parse_extract_html(self, html):
        try:
            parsed_data = parse_game_page_html(html)
            parsed_data["page_url"] = self.browser.url().toString()
            if self.current_mode == "silent_update":
                self.silent_update_ready.emit(
                    parsed_data, self.current_job.title if self.current_job else '')
            else:
                self.extract_ready.emit(parsed_data)
        except Exception as e:
            log.exception("[Scraper] Unexpected error while handling the page")
            mode = self.current_mode
            if mode == "silent_update":
                self.silent_update_ready.emit(
                    {}, self.current_job.title if self.current_job else '')
            else:
                self.error_occurred.emit(str(e))
        finally:
            self._complete_job()
