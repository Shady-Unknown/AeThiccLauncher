# downloader.py
import os
from curl_cffi import requests
from PyQt6.QtCore import QThread, pyqtSignal, QEventLoop
from network.resolver import GofileGateError
from core.archive_utils import is_archive_complete, is_archive_path

MAX_RESOLVE_RETRIES = 3

class SmartDownloaderWorker(QThread):
    progress_update = pyqtSignal(float, float)
    finished_success = pyqtSignal(str)
    finished_error = pyqtSignal(str)
    cf_bypass_needed = pyqtSignal(str) # NUOVO SEGNALE
    gate_fallback_needed = pyqtSignal(str)
    resume_detected = pyqtSignal(float, float)  # (bytes already on disk, total bytes)

    def __init__(self, url, dest_path, cookies=None, user_agent=None, referer=None):
        super().__init__()
        from urllib.parse import urlparse
        from network.resolver import HOSTER_RESOLVERS
        
        self.page_url = url            
        self.dest_path = dest_path
        self.cookies = cookies or {}
        self.user_agent = user_agent or 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        self.referer = referer or ''   
        self._is_cancelled = False
        
        # Variabili ponte per il bypass Cloudflare GUI
        self.cf_loop = None
        self.cf_cookies = {}
        self.cf_html = ""
        
        domain = urlparse(self.page_url).netloc.lower().replace('www.', '')
        if any(pattern in domain for pattern in HOSTER_RESOLVERS.keys()):
            self.resolved_url = None
        else:
            self.resolved_url = url    

    def cancel(self):
        self._is_cancelled = True
        # Se eravamo bloccati ad aspettare la GUI per risolvere Cloudflare, uccidiamo il blocco in sicurezza
        if self.cf_loop and self.cf_loop.isRunning():
            self.cf_loop.quit()

    def cf_bypass_callback(self, url):
        """Mette in pausa il worker delegando l'invisibilità alla UI."""
        self.cf_bypass_needed.emit(url)
        self.cf_loop = QEventLoop()
        self.cf_loop.exec()
        # Una volta risolto, il metodo cf_bypass_finished sbloccherà questo loop
        return self.cf_html, self.cf_cookies

    def cf_bypass_finished(self, html, cookies):
        """Metodo chiamato dalla GUI una volta che il browser fantasma ha finito."""
        self.cf_html = html
        self.cf_cookies = cookies
        if self.cf_loop and self.cf_loop.isRunning():
            self.cf_loop.quit()

    def _resolve_direct_url(self):
        from network.resolver import LinkResolver
        # Passiamo la nostra callback al resolver
        resolver = LinkResolver(user_agent=self.user_agent, referer=self.referer, cf_callback=self.cf_bypass_callback)
        direct_url, extra_cookies = resolver.resolve(self.page_url)
        self.cookies = {**self.cookies, **extra_cookies}
        self.referer = self.page_url
        return direct_url

    def run(self):
        retry_count = 0
        while retry_count < MAX_RESOLVE_RETRIES:
            if self._is_cancelled: return
            try:
                if self.resolved_url is None:
                    self.resolved_url = self._resolve_direct_url()

                result = self._attempt_download(self.resolved_url)
                
                if result == "LINK_EXPIRED":
                    print(f"[SmartDL] Link scaduto. Re-resolve tentativo {retry_count + 1}/{MAX_RESOLVE_RETRIES}...")
                    self.resolved_url = None  
                    retry_count += 1
                    continue
                
                if result == "CANCELLED":
                    print("[SmartDL] Download interrotto (Pausa o Stop).")
                    return 
                
                self.finished_success.emit(self.dest_path)
                return
            except GofileGateError as ge:
                if not self._is_cancelled:
                    print(f"[SmartDL] GoFile gate: {ge}. Apro il browser di fallback...")
                    self.gate_fallback_needed.emit(ge.url)
                    self.finished_error.emit(str(ge))
                return
            except Exception as e:
                if not self._is_cancelled:
                    self.finished_error.emit(str(e))
                return

        if not self._is_cancelled:
            self.finished_error.emit(f"Link scaduto {MAX_RESOLVE_RETRIES} volte di seguito. Impossibile completare il download.")

    def _attempt_download(self, url: str) -> str:
        import time
        os.makedirs(os.path.dirname(self.dest_path), exist_ok=True)

        headers = {
            'User-Agent': self.user_agent,
            'Accept': '*/*',
            'Connection': 'keep-alive'
        }
        if self.referer: headers['Referer'] = self.referer

        max_network_drops = 10
        drop_count = 0

        while drop_count < max_network_drops:
            if self._is_cancelled: return "CANCELLED"

            downloaded_bytes = 0
            if os.path.exists(self.dest_path):
                downloaded_bytes = os.path.getsize(self.dest_path)

            if downloaded_bytes > 0:
                headers['Range'] = f'bytes={downloaded_bytes}-'
                print(f"[SmartDL] File parziale. Resume da {downloaded_bytes} bytes...")
            else:
                headers.pop('Range', None)

            try:
                r = requests.get(
                    url, headers=headers, cookies=self.cookies, 
                    stream=True, impersonate="chrome120",
                    timeout=0 
                )

                if r.status_code in (403, 410, 401, 404): 
                    return "LINK_EXPIRED"
                
                if r.status_code == 416: 
                    self.progress_update.emit(float(downloaded_bytes), float(downloaded_bytes))
                    return "OK"

                content_type = r.headers.get('content-type', '').lower()
                if 'text/html' in content_type:
                    if r.status_code in (200,) and downloaded_bytes > 0:
                        return "LINK_EXPIRED"
                    raise Exception("Il server ha bloccato la richiesta (pagina HTML).")

                r.raise_for_status()

                total_bytes_header = int(r.headers.get('content-length') or 0)
                is_resume_supported = (r.status_code == 206)

                if downloaded_bytes > 0 and not is_resume_supported:
                    downloaded_bytes = 0
                    file_mode = 'wb'
                    total_bytes = total_bytes_header
                else:
                    file_mode = 'ab' if downloaded_bytes > 0 else 'wb'
                    cr = r.headers.get('Content-Range')
                    if cr:
                        total_bytes = int(cr.split('/')[-1])
                    else:
                        total_bytes = downloaded_bytes + total_bytes_header

                if downloaded_bytes > 0 and is_resume_supported:
                    self.resume_detected.emit(float(downloaded_bytes), float(total_bytes))

                if total_bytes > 0 and total_bytes < 1000000 and downloaded_bytes == 0:
                    raise Exception(f"File troppo piccolo ({total_bytes} bytes). Possibile blocco.")

                last_emit = time.time()
                
                with open(self.dest_path, file_mode) as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256): 
                        if self._is_cancelled:
                            r.close() 
                            return "CANCELLED"
                            
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            
                            now = time.time()
                            if now - last_emit > 0.2:
                                self.progress_update.emit(float(downloaded_bytes), float(total_bytes))
                                last_emit = now
                
                if total_bytes > 0 and downloaded_bytes < total_bytes:
                    raise Exception("Stream interrotto prematuramente dal server.")

                # Some hosts omit Content-Length or close the stream cleanly
                # before the archive is complete. Ask 7-Zip to read the
                # headers and resume the transfer when the file is truncated.
                if is_archive_path(self.dest_path) and not is_archive_complete(
                    self.dest_path
                ):
                    raise Exception(
                        "Archivio incompleto: il download non è terminato."
                    )

                self.progress_update.emit(float(total_bytes), float(total_bytes))
                return "OK"

            except Exception as e:
                if self._is_cancelled: return "CANCELLED"
                err_str = str(e).lower()
                print(f"[SmartDL] Rete instabile ({err_str}). Auto-Resume in corso {drop_count+1}/{max_network_drops}...")
                drop_count += 1
                time.sleep(2)
                
        raise Exception("Troppe disconnessioni. Impossibile terminare il download.")