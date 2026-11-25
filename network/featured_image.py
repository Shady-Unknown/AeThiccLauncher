"""SteamRIP featured image extraction, shared by the scraper and the API client.

The cover used as the Explorer background lives in a WordPress
``figure.single-featured-image``; lazy-loading plugins move the real URL to
``data-src``/``data-lazy-src`` and leave a ``data:`` placeholder in ``src``.
Three copies of this logic used to drift apart.
"""
from __future__ import annotations

STEAMRIP_BASE = "https://steamrip.com"


def image_url(img_tag) -> str:
    """Absolute URL for an ``<img>`` tag, following the lazy-load attributes."""
    if img_tag is None:
        return ""
    url = (img_tag.get("data-src")
           or img_tag.get("data-lazy-src")
           or img_tag.get("src")
           or "").strip()
    if url.startswith("data:"):
        url = (img_tag.get("data-src") or img_tag.get("data-lazy-src") or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return STEAMRIP_BASE + url
    return url


def featured_image_url(soup) -> str:
    """URL of the page's featured image, or ``""`` when the page has none."""
    if soup is None:
        return ""
    figure = soup.find("figure", class_="single-featured-image")
    img_tag = figure.find("img") if figure else None
    if img_tag is None:
        img_tag = soup.find("img", class_="wp-post-image")
    if img_tag is None:
        img_tag = soup.find("article img")
    if img_tag is None:
        img_tag = soup.find("img")
    return image_url(img_tag)
