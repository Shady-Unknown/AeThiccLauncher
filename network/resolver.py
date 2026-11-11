# resolver.py
import re
import hashlib
import time
from curl_cffi import requests
from urllib.parse import urlparse, urljoin

MAX_RESOLVE_RETRIES = 3

# Secret baked into gofile's wt.obf.js (decoded property 'YpTwx'). Rotated
# server-side over time; when it stops being accepted, re-extract it from a
# fresh https://gofile.io/js/wt.obf.js (the bundle used to live under
# /dist/js/, it moved in the 2026 redesign). Loading that file in Node and
# printing generateWT(<token>) gives the matching sha256 for the current bucket.
GOFILE_WT_SECRET = "12af056dacea0b"

# Shared cache for the GoFile guest token (each download builds a new
# LinkResolver, so without it we minted a fresh guest account per file and hit
# the per-IP rate limit).
GOFILE_TOKEN_TTL = 30 * 60
_GOFILE_TOKEN = {'value': None, 'expires': 0.0}


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
    'megadb.net':       '_resolve_megadb',
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
        self.referer = referer or ''
            
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

    def _get_page_text(self, url, timeout=30, max_bytes=2 * 1024 * 1024, extra_headers=None):
        """GET with a bounded read: when the server actually serves a file this
        returns None instead of pulling gigabytes into RAM (the old cause of the
        curl timeouts on CDN links)."""
        headers = {**self.base_headers, **(extra_headers or {})}
        r = requests.get(
            url, headers=headers, cookies=self.session_cookies,
            impersonate="chrome120", timeout=timeout, stream=True
        )
        try:
            self.session_cookies.update(r.cookies.get_dict())
        except Exception:
            pass

        ct = (r.headers.get('content-type') or '').lower()
        if ct and not any(t in ct for t in ('html', 'text', 'json', 'xml')):
            r.close()
            return None

        buf = bytearray()
        try:
            for chunk in r.iter_content():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    break
        finally:
            r.close()
        # Without a content-type the server may still be serving an archive:
        # binary bytes (NUL) give it away, so treat it as a direct link instead
        # of decoding it as HTML.
        if b'\x00' in bytes(buf[:512]):
            return None
        return bytes(buf).decode('utf-8', 'replace')

    def _resolve_megadb(self, url: str) -> tuple[str, dict]:
        print(f"[Resolver] Risoluzione MegaDB in corso per: {url}")

        # Already a CDN link: never GET a multi-GB file into memory.
        path_lower = urlparse(url).path.lower()
        if (
            '/d/' in url
            or 'download_token=' in url
            or re.search(r'\.(?:rar|zip|7z|iso|part\d+)$', path_lower)
        ):
            print("[MegaDB] L'URL è già un link CDN diretto. Lo passo al downloader!")
            return url, self.session_cookies

        # MegaDB enforces a Referer allowlist: SteamRip content accepts steamrip.com.
        referer = self.referer if 'steamrip.com' in (self.referer or '') else 'https://steamrip.com/'

        text = self._get_page_text(url, timeout=30, extra_headers={'Referer': referer})
        if text is None:
            print("[MegaDB] La pagina ha risposto con un file: uso l'URL così com'è.")
            return url, self.session_cookies

        # Legacy flow: direct href to /d/.
        match = re.search(r'href="(https?://[a-zA-Z0-9-]+\.megadb\.[a-z]+(?::\d+)?/d/[^"]+)"', text)
        if match:
            direct_url = match.group(1)
            print(f"[MegaDB] Link CDN trovato: {direct_url}")
            return direct_url, self.session_cookies

        # New 2026 flow: finalDownloadUrl + countdown, then the CDN redirect.
        token_url = self._extract_megadb_token_url(text)
        if token_url:
            last_error = None
            for attempt in range(2):
                if attempt:
                    # The previous token has been burnt: reload the page to get
                    # a fresh one (and the updated session cookie).
                    print("[MegaDB] Token non armato, ricarico la pagina e riprovo...")
                    text = self._get_page_text(url, timeout=30, extra_headers={'Referer': referer})
                    token_url = self._extract_megadb_token_url(text or '')
                    if not token_url:
                        break
                try:
                    direct_url = self._follow_megadb_token(token_url, referer)
                    print(f"[MegaDB] Link CDN risolto: {direct_url}")
                    return direct_url, self.session_cookies
                except Exception as exc:
                    last_error = exc
            if last_error:
                raise last_error

        if 'error?e=' in text:
            err = re.search(r'error\?e=([^"\'&<]+)', text)
            detail = err.group(1).replace('+', ' ') if err else 'unknown error'
            raise Exception(f"MegaDB: the server rejected the request ({detail}).")

        snippet = text[:200].replace('\n', ' ')
        raise Exception(f"MegaDB Resolver Error: CDN link not found. Server response: {snippet}...")

    @staticmethod
    def _extract_megadb_token_url(html: str):
        if not html:
            return None
        m = re.search(r"""finalDownloadUrl\s*=\s*['"]([^'"]+)['"]""", html)
        if not m:
            m = re.search(r"""['"]([^'"]+\?pt=[A-Za-z0-9%._~-]+)['"]""", html)
        if not m:
            return None
        return m.group(1).replace('\\/', '/').replace('&amp;', '&')

    def _follow_megadb_token(self, token_url: str, referer: str) -> str:
        """The ?pt= token only arms itself after the countdown (10 s) and needs
        the session cookie set by the file page. It must be used exactly once
        after the wait: using it earlier burns it."""
        time.sleep(12)
        headers = {
            **self.base_headers,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Referer': referer,
        }
        r = requests.get(
            token_url, headers=headers, cookies=self.session_cookies,
            impersonate="chrome120", timeout=30, allow_redirects=False
        )
        try:
            self.session_cookies.update(r.cookies.get_dict())
        except Exception:
            pass

        if r.status_code in (301, 302, 303, 307, 308):
            location = r.headers.get('Location') or ''
            if location:
                return urljoin(token_url, location)

        if r.status_code == 200:
            body = r.text[:2 * 1024 * 1024]
            meta = re.search(
                r"""http-equiv=["']refresh["'][^>]*?content=["'][^"']*?url=['"]?([^"'>]+)""",
                body, re.IGNORECASE
            )
            if meta:
                return meta.group(1).strip().strip("'\"")

            if 'error?e=' in body:
                err = re.search(r'error\?e=([^"\'&<]+)', body)
                detail = err.group(1).replace('+', ' ') if err else 'unknown error'
                raise Exception(f"MegaDB: the server rejected the download ({detail}).")

        raise Exception(
            f"MegaDB: the server replied {r.status_code} without a CDN link "
            "(countdown not completed or missing session cookie)."
        )
    
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
        """Mint a GoFile guest token and reuse the module-level cache.

        Every download builds a new LinkResolver: without a shared cache we
        minted a guest account per file and hit the per-IP rate limit.
        """
        if self._gofile_token:
            return self._gofile_token

        now = time.time()
        if _GOFILE_TOKEN['value'] and now < _GOFILE_TOKEN['expires']:
            self._gofile_token = _GOFILE_TOKEN['value']
            return self._gofile_token

        token_r = requests.post(
            "https://api.gofile.io/accounts",
            headers=self.base_headers, cookies=self.session_cookies,
            impersonate="chrome120", timeout=15
        )
        try:
            token_data = token_r.json()
        except Exception:
            raise Exception(f"Gofile API error: could not read the account token. HTTP {token_r.status_code}")

        if token_data.get('status') != 'ok' or not token_data.get('data', {}).get('token'):
            raise Exception(f"Gofile: could not obtain a guest token ({token_data.get('status')})")

        self._gofile_token = token_data['data']['token']
        _GOFILE_TOKEN['value'] = self._gofile_token
        _GOFILE_TOKEN['expires'] = now + GOFILE_TOKEN_TTL
        return self._gofile_token

    def _reset_gofile_token(self) -> str:
        """Drop the current guest token (rate-limited/expired) and mint a new one."""
        self._gofile_token = None
        _GOFILE_TOKEN['value'] = None
        _GOFILE_TOKEN['expires'] = 0.0
        self.session_cookies.pop('accountToken', None)
        return self._get_gofile_token()

    def _get_gofile_wt(self, account_token: str) -> str:
        return generate_gofile_wt(self.ua, account_token, language=self._gofile_language())

    def _fetch_gofile_contents(self, content_id: str, account_token: str, page: int = 1):
        """GET /contents/{id} with the wt computed on our own UA/language."""
        api_url = (
            f"https://api.gofile.io/contents/{content_id}"
            f"?page={page}&pageSize=1000&sortField=createTime&sortDirection=-1&maxdepth=16"
        )
        api_headers = {
            **self.base_headers,
            'Accept': 'application/json, text/plain, */*',
            'Authorization': f'Bearer {account_token}',
            'X-Website-Token': self._get_gofile_wt(account_token),
            'X-BL': self._gofile_language(),
        }
        return requests.get(
            api_url, headers=api_headers,
            cookies={**self.session_cookies, 'accountToken': account_token},
            impersonate="chrome120", timeout=20
        )

    @staticmethod
    def _iter_gofile_files(node):
        """Walk the recursive children tree (API maxdepth) and yield file links."""
        stack = [node]
        while stack:
            obj = stack.pop()
            if not isinstance(obj, dict):
                continue
            if obj.get('type') == 'file':
                link = obj.get('link')
                if link:
                    yield link
                continue
            for child in (obj.get('children') or {}).values():
                if isinstance(child, dict):
                    stack.append(child)

    def _collect_gofile_links(self, content_id: str, token: str, payload: dict) -> list[str]:
        """File links contained in a /contents response, following pagination."""
        node = payload.get('data') or {}
        if node.get('type') == 'file' and node.get('link'):
            return [node['link']]

        links = list(self._iter_gofile_files(node))
        meta = payload.get('metadata') or {}
        page = int(meta.get('page') or 1)
        while meta.get('hasNextPage') and page < 10:
            page += 1
            r = self._fetch_gofile_contents(content_id, token, page=page)
            try:
                nxt = r.json()
            except Exception:
                break
            if nxt.get('status') != 'ok':
                break
            links.extend(self._iter_gofile_files(nxt.get('data') or {}))
            meta = nxt.get('metadata') or {}
        return links

    def _resolve_gofile(self, url: str) -> tuple[str, dict]:
        match = re.search(r'gofile\.io/d/([a-zA-Z0-9-]+)', url)
        if not match:
            raise Exception(f"Gofile: could not extract content_id from {url}")
        content_id = match.group(1)

        token = self._get_gofile_token()
        data = None
        status = None
        last_http = None

        # error-rateLimit comes back with HTTP 200, so it must be handled from
        # the payload, not only from the status code. A "dirty" guest token is
        # recovered by minting a new one + backoff.
        for attempt in range(3):
            if attempt:
                time.sleep(1.5 * attempt)
            api_r = self._fetch_gofile_contents(content_id, token)
            last_http = api_r.status_code
            if api_r.status_code == 429:
                token = self._reset_gofile_token()
                continue
            try:
                data = api_r.json()
            except Exception:
                raise Exception(f"Gofile API error: non-JSON response. HTTP {api_r.status_code}")
            status = data.get('status')
            if status in ('error-rateLimit', 'error-token', 'error-wrongToken'):
                print(f"[GoFile] {status}: rinnovo token guest e riprovo...")
                token = self._reset_gofile_token()
                continue
            break

        data_obj = (data or {}).get('data') or {}

        if status != 'ok':
            if status == 'error-notFound':
                raise Exception("GoFile: content not found (link expired or removed from the server).")
            if status == 'error-rateLimit':
                raise Exception(
                    "GoFile: server rate-limit even after refreshing the guest token. "
                    "Please try again in a few minutes."
                )
            raise GofileGateError(
                f"GoFile: access denied ({status or 'HTTP ' + str(last_http)}). "
                "The folder requires Premium or a password.", url)

        if data_obj.get('canAccess') is False:
            raise GofileGateError(
                "GoFile: access to the folder denied (password or Premium required).", url)

        links = self._collect_gofile_links(content_id, token, data or {})

        if links:
            print(f"[GoFile] Link diretto trovato ({len(links)} file).")
            self.session_cookies['accountToken'] = token
            return links[0], self.session_cookies

        raise Exception(
            "Gofile: no files found in the folder (it may be empty or contain only subfolders)."
        )

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