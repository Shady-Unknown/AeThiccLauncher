# resolver.py
import re
import hashlib
import time
from curl_cffi import requests
from urllib.parse import urlparse

MAX_RESOLVE_RETRIES = 3

# Secret baked into gofile's wt.obf.js (decoded property 'YpTwx'). Rotated
# server-side over time; when it stops being accepted, re-extract it from a
# fresh wt.obf.js (see the deobfuscation notes).
GOFILE_WT_SECRET = "12af056dacea0b"


def generate_gofile_wt(user_agent, account_token, language="en-US", now_ts=None) -> str:
    """Replica di generateWT() di gofile: sha256 di UA::lang::token::interval::secret."""
    interval = str(int(now_ts if now_ts is not None else time.time()) // 14400)
    data = f"{user_agent}::{language}::{account_token}::{interval}::{GOFILE_WT_SECRET}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

_FRESHNESS_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/148.0.0.0 Safari/537.36'
)

def fetch_page_headers(url):
    """Lightweight HEAD request per ottenere ETag / Last-Modified di una pagina SteamRip."""
    try:
        r = requests.head(
            url, headers={'User-Agent': _FRESHNESS_UA, 'Accept': '*/*'},
            impersonate="chrome120", timeout=5, allow_redirects=True
        )
        return (r.headers.get('ETag', ''), r.headers.get('Last-Modified', ''))
    except Exception:
        return ('', '')

def check_page_freshness(url, etag=None, last_modified=None):
    """HEAD condizionale: True se la pagina non è cambiata (304 o headers identici)."""
    if not etag and not last_modified:
        return True  # niente da confrontare → trattiamo come fresca
    try:
        h = {'User-Agent': _FRESHNESS_UA, 'Accept': '*/*'}
        if etag:
            h['If-None-Match'] = etag
        if last_modified:
            h['If-Modified-Since'] = last_modified
        r = requests.head(
            url, headers=h, impersonate="chrome120",
            timeout=5, allow_redirects=True
        )
        if r.status_code == 304:
            return True
        new_etag = r.headers.get('ETag', '')
        new_lm = r.headers.get('Last-Modified', '')
        if etag and new_etag and etag == new_etag:
            return True
        if last_modified and new_lm and last_modified == new_lm:
            return True
        return False
    except Exception:
        return True  # errore di rete → trattiamo come fresca (graceful degradation)

HOSTER_RESOLVERS = {
    'buzzheavier.com':  '_resolve_buzzheavier',
    'bzzhr.to':         '_resolve_buzzheavier',
    'megadb.xyz':       '_resolve_megadb',
    'pixeldrain.com':   '_resolve_pixeldrain',
    'gofile.io':        '_resolve_gofile',
}

class GofileGateError(Exception):
    """La cartella GoFile è bloccata (premium/password). Forza il fallback Bowser."""
    def __init__(self, message, url):
        super().__init__(message)
        self.url = url

class LinkResolver:

    def __init__(self, user_agent=None, referer=None, cf_callback=None):
        self.ua = user_agent or (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/148.0.0.0 Safari/537.36'
        )
        self.base_headers = {
            'User-Agent': self.ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        if referer:
            self.base_headers['Referer'] = referer
            
        self.cf_callback = cf_callback
        self.session_cookies = {} # Mantiene i cookie estratti dal bypass per tutta la catena
        self._gofile_token = None # Guest token GoFile riusato per la sessione (evita il rate-limit)

    def resolve(self, url: str) -> tuple[str, dict]:
        host = urlparse(url).netloc.lower().replace('www.', '')
        for pattern, method_name in HOSTER_RESOLVERS.items():
            if pattern in host:
                return getattr(self, method_name)(url)
        return self._probe_direct(url)

    def _get(self, url, timeout=15, **kwargs):
        kwargs.setdefault('cookies', {}).update(self.session_cookies)
        return requests.get(
            url, headers=self.base_headers,
            impersonate="chrome120", timeout=timeout, **kwargs
        )

    def _get_with_cf(self, url, timeout=15, **kwargs):
        """Tenta il fetch HTTP puro. Se becca Cloudflare, fa intervenire il browser nascosto di PyQt."""
        r = self._get(url, timeout=timeout, **kwargs)
        
        is_cf = (r.status_code in (403, 503) and 
                 ('challenge-platform' in r.text or 'Just a moment' in r.text or 'cf-mitigated' in r.headers))
                 
        if is_cf:
            if self.cf_callback:
                print(f"[Resolver] Cloudflare bloccante su {url}. Invoco il browser fantasma PyQt...")
                html, cookies = self.cf_callback(url)
                self.session_cookies.update(cookies) # Salviamo i cookie di salvezza
                
                # Creiamo una falsa Response da dare in pasto alle regex degli hoster
                class DummyResponse:
                    def __init__(self, text, cookies):
                        self.text = text
                        self.cookies = cookies
                        self.status_code = 200
                return DummyResponse(html, cookies)
            else:
                raise Exception(f"Cloudflare bloccante su {url} e nessun bypass GUI configurato.")
        return r

    def _probe_direct(self, url: str) -> tuple[str, dict]:
        if '1fichier.com' in url and '/?' not in url:
            return url, self.session_cookies
            
        kwargs = {'cookies': self.session_cookies}
        r = requests.head(url, headers=self.base_headers, impersonate="chrome120", timeout=10, allow_redirects=True, **kwargs)
        ct = r.headers.get('content-type', '')
        if any(x in ct for x in ['octet-stream', 'rar', 'zip', 'x-7z', 'application/']):
            return url, self.session_cookies
            
        raise Exception(f"Hoster non riconosciuto e URL non diretto: {url}")

    def _resolve_megadb(self, url: str) -> tuple[str, dict]:
        print(f"[Resolver] Risoluzione MegaDB in corso per: {url}")
        if '/d/' in url:
            print("[MegaDB] L'URL è già un link CDN diretto. Lo passo al downloader!")
            return url, self.session_cookies
        
        page_r = self._get_with_cf(url, timeout=60)
        match = re.search(r'href="(https?://[a-zA-Z0-9-]+\.megadb\.[a-z]+(?::\d+)?/d/[^"]+)"', page_r.text)
        
        if match:
            direct_url = match.group(1)
            print(f"[MegaDB] Link CDN trovato: {direct_url}")
            return direct_url, self.session_cookies
        
        snippet = page_r.text[:200].replace('\n', ' ')
        raise Exception(f"MegaDB Resolver Error: Link CDN non trovato. Il server ha risposto con: {snippet}...")
    
    def _resolve_fileditch(self, url: str) -> tuple[str, dict]:
        r = self._get_with_cf(url)
        match = re.search(r'(https?://[^\s"\'<>]+\?md5=[^\s"\'<>&]+&expires=\d+)', r.text)
        if match:
            return match.group(1), self.session_cookies

        match2 = re.search(r'(https?://[^\s"\'<>]+\.(?:rar|zip|7z|iso)(?:\?[^\s"\'<>]*)?)', r.text, re.IGNORECASE)
        if match2:
            return match2.group(1), self.session_cookies

        raise Exception(f"FileDitch: link CDN non trovato nella pagina {url}")

    def _gofile_language(self) -> str:
        """Prima lingua dell'header Accept-Language, usata sia per wt che per X-BL."""
        accept_lang = self.base_headers.get('Accept-Language', 'en-US,en;q=0.9')
        return (accept_lang.split(',')[0].strip() or 'en-US')

    def _get_gofile_token(self) -> str:
        """Mint un guest token GoFile e lo riusa per l'intera sessione (rate-limit friendly)."""
        if self._gofile_token:
            return self._gofile_token
        token_r = requests.post(
            "https://api.gofile.io/accounts",
            headers=self.base_headers, cookies=self.session_cookies,
            impersonate="chrome120", timeout=15
        )
        try:
            token_data = token_r.json()
        except Exception:
            raise Exception(f"Gofile API Error: impossibile leggere il Token. HTTP {token_r.status_code}")

        if token_data.get('status') != 'ok' or not token_data.get('data', {}).get('token'):
            raise Exception(f"Gofile: impossibile ottenere token guest ({token_data.get('status')})")

        self._gofile_token = token_data['data']['token']
        return self._gofile_token

    def _get_gofile_wt(self, account_token: str) -> str:
        return generate_gofile_wt(self.ua, account_token, language=self._gofile_language())

    def _resolve_gofile(self, url: str) -> tuple[str, dict]:
        match = re.search(r'gofile\.io/d/([a-zA-Z0-9-]+)', url)
        if not match:
            raise Exception(f"Gofile: impossibile estrarre content_id da {url}")
        content_id = match.group(1)

        token = self._get_gofile_token()
        lang = self._gofile_language()
        api_url = (f"https://api.gofile.io/contents/{content_id}"
                   f"?page=1&pageSize=100&sortField=createTime&sortDirection=-1")

        def _fetch(account_token):
            wt = self._get_gofile_wt(account_token)
            api_headers = {
                **self.base_headers,
                'Authorization': f'Bearer {account_token}',
                'X-Website-Token': wt,
                'X-BL': lang,
            }
            return requests.get(
                api_url, headers=api_headers,
                cookies={**self.session_cookies, 'accountToken': account_token},
                impersonate="chrome120", timeout=15
            )

        api_r = _fetch(token)
        if api_r.status_code == 429:
            # Rate-limit: il token mintato è orfano. Reset cache e riprova una volta.
            print("[GoFile] Rate-limit sul token, minting di un nuovo account guest...")
            self._gofile_token = None
            token = self._get_gofile_token()
            api_r = _fetch(token)

        try:
            data = api_r.json()
        except Exception:
            raise Exception(f"Gofile API error: risposta non JSON. HTTP {api_r.status_code}")

        status = data.get('status')
        data_obj = data.get('data') or {}

        if status != 'ok':
            if status in ('error-token', 'error-wrongToken'):
                self._gofile_token = None
            raise GofileGateError(
                f"GoFile: accesso negato ({status}). La cartella richiede Premium o password.", url)

        if data_obj.get('canAccess') is False:
            raise GofileGateError(
                "GoFile: accesso negato alla cartella (richiede password o Premium).", url)

        children = data_obj.get('children', {})
        for child in children.values():
            if child.get('type') == 'file':
                link = child.get('link')
                if link:
                    self.session_cookies['accountToken'] = token
                    return link, self.session_cookies

        raise Exception("Gofile: nessun file trovato nella cartella")

    def _resolve_buzzheavier(self, url: str) -> tuple[str, dict]:
        page_r = self._get_with_cf(url)
        file_id = url.rstrip('/').split('/')[-1]
        domain = f"https://{urlparse(url).netloc}"

        # Nuovo flow (2025+): il bottone ha hx-get="/{file_id}/download" direttamente,
        # senza più il token ?t= nel query string.
        hx_match = re.search(r'hx-get=["\']([^"\']+)["\']', page_r.text)
        if hx_match:
            hx_path = hx_match.group(1).strip()
            if hx_path.startswith('/'):
                download_endpoint = domain + hx_path
            else:
                download_endpoint = f"{domain}/{file_id}/download"
        else:
            # Vecchio flow: cerca il token ?t= per compatibilità retroattiva
            token_match = re.search(r'[?&]t=([\w.\-]+)', page_r.text)
            if not token_match:
                token_match = re.search(r'hx-get=["\'][^"\']*[?&]t=([\w.\-]+)["\']', page_r.text)
            if token_match:
                download_endpoint = f"{domain}/{file_id}/download?t={token_match.group(1)}"
            else:
                download_endpoint = f"{domain}/{file_id}/download"

        htmx_headers = {
            **self.base_headers,
            'hx-current-url': url,
            'hx-request': 'true',
            'Referer': url,
            'Accept': '*/*',
        }

        dl_r = requests.get(
            download_endpoint, headers=htmx_headers, cookies=self.session_cookies,
            impersonate="chrome120", timeout=15,
            allow_redirects=False
        )

        if dl_r.status_code == 204:
            redirect_url = dl_r.headers.get('hx-redirect')
            if redirect_url:
                if not redirect_url.startswith('http'):
                    redirect_url = domain + redirect_url
                return redirect_url, self.session_cookies

        if dl_r.status_code in (301, 302, 303, 307, 308):
            location = dl_r.headers.get('Location', '')
            if location and not location.startswith('http'):
                location = domain + location
            return location, self.session_cookies

        raise Exception(f"Buzzheavier: risposta inattesa {dl_r.status_code}, hx-redirect assente")

    def _resolve_qiwi(self, url: str) -> tuple[str, dict]:
        print(f"[Resolver] Risoluzione Qiwi in corso per: {url}")
        page_r = self._get_with_cf(url, timeout=30)
        
        match = re.search(r'href=["\'](https?://[^"\']*\.qiwi\.gg/download/[^"\']+)["\']', page_r.text)
        if not match:
            match = re.search(r'href=["\']([^"\']+\.(?:rar|zip|7z|iso))["\']', page_r.text, re.IGNORECASE)

        if match:
            dl_link = match.group(1)
            if dl_link.startswith('/'):
                dl_link = "https://qiwi.gg" + dl_link
            print(f"[Qiwi] Link di redirect CDN trovato: {dl_link}")
            return dl_link, self.session_cookies
            
        raise Exception(f"Qiwi Resolver Error: Link non trovato.")

    def _resolve_pixeldrain(self, url: str) -> tuple[str, dict]:
        match = re.search(r'pixeldrain\.com/u/([a-zA-Z0-9]+)', url)
        if not match:
            raise Exception(f"Pixeldrain: impossibile estrarre file_id da {url}")
        return f"https://pixeldrain.com/api/file/{match.group(1)}?download", self.session_cookies


# --- Page freshness helpers (portati da main: usati da page_freshness_worker) ---
_FRESHNESS_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/148.0.0.0 Safari/537.36'
)


def fetch_page_headers(url):
    """Lightweight HEAD request per ottenere ETag / Last-Modified di una pagina SteamRip."""
    try:
        r = requests.head(
            url, headers={'User-Agent': _FRESHNESS_UA, 'Accept': '*/*'},
            impersonate="chrome120", timeout=5, allow_redirects=True
        )
        return (r.headers.get('ETag', ''), r.headers.get('Last-Modified', ''))
    except Exception:
        return ('', '')


def check_page_freshness(url, etag=None, last_modified=None):
    """HEAD condizionale: True se la pagina non è cambiata (304 o headers identici)."""
    if not etag and not last_modified:
        return True
    try:
        h = {'User-Agent': _FRESHNESS_UA, 'Accept': '*/*'}
        if etag:
            h['If-None-Match'] = etag
        if last_modified:
            h['If-Modified-Since'] = last_modified
        r = requests.head(
            url, headers=h, impersonate="chrome120",
            timeout=5, allow_redirects=True
        )
        if r.status_code == 304:
            return True
        new_etag = r.headers.get('ETag', '')
        new_lm = r.headers.get('Last-Modified', '')
        if etag and new_etag and etag == new_etag:
            return True
        if last_modified and new_lm and last_modified == new_lm:
            return True
        return False
    except Exception:
        return True