"""HTTP helpers for remote images.

Cloudflare's bot protection rejects the TLS fingerprint of the plain
``requests``/OpenSSL stack in some frozen runtimes.  The Flatpak build is the
known case: SteamRIP images answer **403** there while the same URLs succeed
from the host, from the Windows build and through the impersonated
``curl_cffi`` client.  A 403 makes the Discovery cover worker fall back to the
rate-limited SteamGridDB name lookup (one cover every few seconds) and the
Explorer background fall back to the splash screen.

SteamRIP images therefore go through ``curl_cffi`` with Chrome impersonation.
Other hosts keep plain ``requests`` and only retry impersonated when they
answer 403, as a defensive fallback for other Cloudflare-protected CDNs.
"""
from __future__ import annotations

import io
import logging
from urllib.parse import urlparse

import requests
from curl_cffi import requests as curl_requests
from PIL import Image

log = logging.getLogger(__name__)

#: Browser profile used by curl_cffi; must stay a supported impersonation name.
IMPERSONATE = "chrome120"
#: Hosts that are always fetched with the impersonated client.
IMPERSONATED_HOSTS = ("steamrip.com",)


def _host(url):
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def is_impersonated_host(url):
    """True when ``url`` belongs to a host that requires impersonation."""
    host = _host(url)
    if not host:
        return False
    return any(host == name or host.endswith("." + name) for name in IMPERSONATED_HOSTS)


def _request(url, headers, timeout, impersonate):
    if impersonate:
        return curl_requests.get(
            url, headers=headers, timeout=timeout, impersonate=IMPERSONATE
        )
    return requests.get(url, headers=headers, timeout=timeout)


def is_image_bytes(data):
    """True when ``data`` is a decodable image, not an HTML challenge page."""
    if not data:
        return False
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.size  # forces the header parse
        return True
    except Exception:
        return False


def fetch_image_bytes(url, *, user_agent="", timeout=12, headers=None):
    """Download an image and return its bytes, or ``None`` on failure.

    ``user_agent`` is applied only when ``headers`` does not already carry
    one.  SteamRIP hosts always use the impersonated client; any other host
    that answers 403 is retried once with impersonation.
    """
    url = str(url or "").strip()
    if not url:
        return None

    request_headers = dict(headers or {})
    if user_agent:
        request_headers.setdefault("User-Agent", user_agent)

    impersonate = is_impersonated_host(url)
    try:
        response = _request(url, request_headers, timeout, impersonate)
    except Exception as exc:
        log.warning(f"[HTTP] image download failed for {url}: {exc}")
        return None

    if response.status_code == 403 and not impersonate:
        log.info(f"[HTTP] image answered 403, retrying impersonated: {url}")
        try:
            response = _request(url, request_headers, timeout, True)
        except Exception as exc:
            log.warning(f"[HTTP] impersonated retry failed for {url}: {exc}")
            return None

    if response.status_code != 200 or not response.content:
        log.warning(f"[HTTP] image download HTTP {response.status_code} for {url}")
        return None
    if not is_image_bytes(response.content):
        # A Cloudflare challenge or a login page must never reach the cache.
        log.warning(f"[HTTP] image download returned a non-image body for {url}")
        return None
    return response.content
