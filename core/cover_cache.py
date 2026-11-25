import logging
import os
import time
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.http_utils import fetch_image_bytes
from core.paths import PathManager
from core.config import get_base_dir

log = logging.getLogger(__name__)


STEAM_ASSETS_DIR_NAME = "Steam_Assets"
COVERS_SUBDIR = "covers"
CACHE_TTL_HOURS = 48
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def to_file_url(path):
    """Converte un percorso locale in URL file:// valido su tutti gli OS.
    f"file://{path}" è corretto solo su Linux: su Windows produce
    'file://C:\\...' che Qt/Chromium considerano invalido."""
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return Path(os.path.abspath(path)).as_uri()


class CoverCache:

    @staticmethod
    def placeholder_url():
        path = os.path.join(get_base_dir(), ".media", "cover_placeholder.png")
        return to_file_url(path)

    @staticmethod
    def resolve_urls(urls, title=None, installed_games=None):
        if not urls:
            return [CoverCache.placeholder_url()] if isinstance(urls, list) else urls

        # --- Installed game path ---
        if title and installed_games:
            meta = installed_games.get(title)
            if meta and meta.get("install_dir"):
                install_dir = meta["install_dir"]
                assets_dir = os.path.join(install_dir, STEAM_ASSETS_DIR_NAME)
                for fname in ("Cover.jpg", "Cover.png"):
                    cover_path = os.path.join(assets_dir, fname)
                    if os.path.isfile(cover_path):
                        return [to_file_url(cover_path)]

                # Steam_Assets exists but no cover yet — download urls[0] (best quality) there
                if urls[0]:
                    local = CoverCache._download_to(urls[0], assets_dir, "Cover")
                    if local and os.path.isfile(local):
                        return [to_file_url(local)]

        # --- Not-installed / fallback path: cache each URL in parallel ---
        valid = [u for u in urls if u]
        if not valid:
            return [CoverCache.placeholder_url()]

        resolved = [None] * len(valid)
        with ThreadPoolExecutor(max_workers=4) as executor:
            fut_map = {executor.submit(CoverCache._cache_url, url): i for i, url in enumerate(valid)}
            for fut in as_completed(fut_map):
                idx = fut_map[fut]
                try:
                    resolved[idx] = fut.result()
                except Exception as e:
                    log.exception(f"[CoverCache] cache worker error: {e}")
                    resolved[idx] = CoverCache.placeholder_url()

        # Ensure every slot is a valid file:// path; replace any remote URL with placeholder
        for i, val in enumerate(resolved):
            if not val or not val.startswith("file://"):
                resolved[i] = CoverCache.placeholder_url()

        return resolved

    @staticmethod
    def resolve_single(url, title=None, installed_games=None):
        result = CoverCache.resolve_urls([url] if url else [], title, installed_games)
        return result[0] if result else CoverCache.placeholder_url()

    @staticmethod
    def _cache_path_for(url):
        ext = CoverCache._guess_ext(url)
        url_hash = hashlib.md5(url.encode()).hexdigest()
        cache_dir = PathManager.get_cache_dir() / COVERS_SUBDIR
        return os.path.join(str(cache_dir), f"{url_hash}{ext}")

    @staticmethod
    def resolve_urls_cached(urls, title=None, installed_games=None):
        """Non-blocking variant: only local files, never the network.

        Missing images become the placeholder; callers resolve the real list in
        a worker and update the UI when it is ready.
        """
        if not urls:
            return [CoverCache.placeholder_url()] if isinstance(urls, list) else urls

        if title and installed_games:
            meta = installed_games.get(title)
            if meta and meta.get("install_dir"):
                assets_dir = os.path.join(meta["install_dir"], STEAM_ASSETS_DIR_NAME)
                for fname in ("Cover.jpg", "Cover.png"):
                    cover_path = os.path.join(assets_dir, fname)
                    if os.path.isfile(cover_path):
                        return [to_file_url(cover_path)]

        resolved = []
        for url in urls:
            if not url:
                continue
            if url.startswith("file://"):
                path = CoverCache._local_path_from_url(url)
                resolved.append(to_file_url(path) if os.path.isfile(path)
                                else CoverCache.placeholder_url())
                continue
            cached = CoverCache._cache_path_for(url)
            if os.path.isfile(cached) and os.path.getsize(cached) > 0:
                resolved.append(to_file_url(cached))
            else:
                resolved.append(CoverCache.placeholder_url())
        return resolved or [CoverCache.placeholder_url()]

    @staticmethod
    def resolve_single_cached(url, title=None, installed_games=None):
        result = CoverCache.resolve_urls_cached(
            [url] if url else [], title, installed_games)
        return result[0] if result else CoverCache.placeholder_url()

    @staticmethod
    def cover_page_url(html, identifier):
        """Write cover HTML to a local file and return the file path.
        Loading via QUrl.fromLocalPath() gives a file:// origin, so
        file:// image URIs load without ERR_ACCESS_DENIED."""
        covers_dir = PathManager.get_cache_dir() / COVERS_SUBDIR
        os.makedirs(str(covers_dir), exist_ok=True)
        # Hash the content too: the old title-only URL kept the same file://
        # address across CSS/template changes, so QtWebEngine could serve a
        # stale page even after the file on disk was rewritten.
        digest = hashlib.md5()
        digest.update(str(identifier or "").encode("utf-8"))
        digest.update(b"\0")
        digest.update((html or "").encode("utf-8"))
        page_path = covers_dir / f"cover_{digest.hexdigest()}.html"
        with open(str(page_path), "w", encoding="utf-8") as f:
            f.write(html)

        # Auto-cleanup: delete HTML page files older than 24h (regenerated on demand)
        now = time.time()
        for fname in os.listdir(str(covers_dir)):
            if fname.endswith(".html"):
                fpath = os.path.join(str(covers_dir), fname)
                if now - os.path.getmtime(fpath) > 86400:
                    try: os.remove(fpath)
                    except OSError: pass

        return str(page_path)

    @staticmethod
    def _local_path_from_url(url):
        """file:// URL -> path locale (Windows e POSIX)."""
        from urllib.parse import unquote, urlparse

        path = unquote(urlparse(url).path)
        if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path

    @staticmethod
    def local_path_for(url, allow_placeholder=True):
        """Path locale dell'immagine (scarica e mette in cache se serve).

        Ritorna ``None`` se l'URL è vuoto o il download fallisce, così il
        chiamante può ricadere su un asset locale (es. splash screen). Con
        ``allow_placeholder=False`` un download fallito non restituisce il
        segnaposto verticale (inutile come sfondo panoramico).
        """
        if not url:
            return None
        if url.startswith("file://"):
            path = CoverCache._local_path_from_url(url)
            return path if os.path.isfile(path) else None
        local = CoverCache._cache_url(url, allow_placeholder=allow_placeholder)
        if not local or not local.startswith("file://"):
            return None
        path = CoverCache._local_path_from_url(local)
        return path if os.path.isfile(path) else None

    @staticmethod
    def _cache_url(url, allow_placeholder=True):
        if not url or url.startswith("file://"):
            return url

        cache_path = CoverCache._cache_path_for(url)
        cache_dir = os.path.dirname(cache_path)

        if os.path.isfile(cache_path) and os.path.getsize(cache_path) > 0:
            # Le immagini a un dato URL sono immutabili: cache permanente.
            # (Prima scadevano dopo 48h e venivano riscaricate a ogni apertura.)
            return to_file_url(cache_path)

        os.makedirs(str(cache_dir), exist_ok=True)
        try:
            data = fetch_image_bytes(url, user_agent=USER_AGENT, timeout=5)
            if data:
                with open(cache_path, "wb") as f:
                    f.write(data)
                return to_file_url(cache_path)
        except Exception as e:
            log.exception(f"[CoverCache] Download failed for {url}: {e}")

        if not allow_placeholder:
            return ""
        return CoverCache.placeholder_url()

    @staticmethod
    def _download_to(url, dest_dir, stem):
        os.makedirs(dest_dir, exist_ok=True)
        ext = CoverCache._guess_ext(url)
        dest_path = os.path.join(dest_dir, f"{stem}{ext}")
        try:
            data = fetch_image_bytes(url, user_agent=USER_AGENT, timeout=5)
            if data:
                with open(dest_path, "wb") as f:
                    f.write(data)
                return dest_path
        except Exception as e:
            log.exception(f"[CoverCache] Download to Steam_Assets failed for {url}: {e}")
        return None

    @staticmethod
    def _guess_ext(url):
        url = url.split("?")[0].split("#")[0]
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
            if url.lower().endswith(ext):
                return ext
        return ".jpg"
