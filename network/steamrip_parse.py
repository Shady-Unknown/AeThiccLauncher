"""Pure SteamRIP HTML parsers (no Qt, no signals).

The scraper used to mix BeautifulSoup logic with page state and signal
emission, which made the most fragile part of the network layer untestable
without WebEngine. These functions return plain data; the scraper only adds
the page URL and emits.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from core.meta_utils import strip_title
from core.version_status import parse_version_status_html
from network.featured_image import featured_image_url

_HOSTERS = {
    'mega.nz': 'Mega', 'megadb': 'MegaDB', 'gofile.io': 'GoFile',
    '1fichier.com': '1Fichier', 'buzzheavier.com': 'Buzzheavier',
    'datanodes': 'DataNodes', 'qiwi.gg': 'Qiwi', 'pixeldrain.com': 'PixelDrain',
    'filecrypt.cc': 'FileCrypt', 'bowfile.com': 'Bowfile',
    'clicknupload': 'ClicknUpload', 'ddownload.com': 'DDownload',
    'hexupload.net': 'HexUpload', 'rapidgator.net': 'Rapidgator',
    'gamedrive.org': 'GameDrive', 'drive.google': 'Google Drive',
    'mediafire.com': 'MediaFire',
}

_BASE_CSS = (
    "<style>body { color: #e0e0e0; font-family: 'Segoe UI', Arial; "
    "font-size: 13px; margin:0; } a { color: #ff00a0; text-decoration: underline; } "
    "ul { padding-left: 20px; margin-top: 5px; } li { margin-bottom: 4px; } "
    "h2, h3, h4 { color: #f3c6ff; margin-top: 10px; margin-bottom: 5px; "
    "font-size: 14px; text-transform: uppercase; } p { margin-top: 5px; "
    "margin-bottom: 5px; line-height: 1.5; } strong, b { color: #ffffff; }</style>"
)

_CATALOG_FORBIDDEN = (
    '/games-list-page', '/faq', '/category', '/genre', '/update-request',
    '?s=', 'wp-admin', 'wp-login',
)


def parse_cover_html(html):
    return featured_image_url(BeautifulSoup(html or "", "lxml"))


def _absolute(href):
    href = (href or "").strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://steamrip.com" + href
    return href


def parse_catalog_html(html):
    """Catalog page -> ``{title: {url, raw_title, version, mp_tag}}``."""
    soup = BeautifulSoup(html or "", "lxml")
    games = {}
    for anchor in soup.find_all('a'):
        title = anchor.get_text(strip=True)
        href = _absolute(anchor.get('href', ''))
        if not title or not href:
            continue
        if "steamrip.com" not in href:
            continue
        if any(part in href.lower() for part in _CATALOG_FORBIDDEN):
            continue
        if len(title) <= 2:
            continue
        clean, version, mp_tag = strip_title(title)
        games[clean] = {
            "url": href,
            "raw_title": title,
            "version": version,
            "mp_tag": mp_tag,
        }
    return games


def _categories(soup):
    categories = []
    wrap = soup.find(class_='post-cat-wrap')
    if not wrap:
        return categories
    for anchor in wrap.find_all('a'):
        for cls in anchor.get('class', []) or []:
            match = re.match(r'tie-cat-(\d+)', cls)
            if match:
                categories.append(int(match.group(1)))
                break
    return categories


def _section_for(html_chunk, section, req_html, info_html, desc_html):
    if section == "req":
        return req_html + html_chunk, info_html, desc_html
    if section == "info":
        return req_html, info_html + html_chunk, desc_html
    if section == "desc":
        return req_html, info_html, desc_html + html_chunk
    return req_html, info_html, desc_html


def _link_name(href, button_text):
    lower = href.lower()
    if 'magnet:' in lower or 'torrent' in button_text.lower():
        return 'Torrent (Magnet)'
    for domain, host_name in _HOSTERS.items():
        if domain in lower:
            return host_name
    if button_text and len(button_text) < 20 and 'download' not in button_text.lower():
        return button_text
    match = re.search(r'https?://(?:www\.)?([^/]+)', href)
    return match.group(1).capitalize() if match else "Download"


def _parse_content(soup):
    content = soup.find(class_='entry-content')
    links = []
    desc_html = req_html = info_html = dlcs_html = ""
    section = "desc"
    if not content:
        return links, desc_html, req_html, info_html, dlcs_html

    for element in content.find_all(recursive=False):
        text_lower = element.get_text(strip=True).lower()
        classes = element.get('class', []) or []
        if element.name == 'hr' or 'clearfix' in classes or 'sharedaddy' in classes:
            continue
        if element.name in ('h2', 'h3', 'h4'):
            if 'screenshot' in text_lower:
                section = "skip"
            elif 'requirement' in text_lower or 'minimum:' in text_lower:
                section = "req"
            elif 'game info' in text_lower or 'game detail' in text_lower:
                section = "info"
            continue

        if element.name == 'div' and 'toggle' in classes:
            head = element.find(class_='toggle-head')
            head_text = head.get_text(strip=True).lower() if head else ""
            if 'dlc' in head_text:
                ul = element.find('ul')
                if ul:
                    dlcs_html += str(ul)
                continue
            if 'step' in head_text or 'how to' in head_text:
                continue

        download_found = False
        for anchor in element.find_all('a'):
            href = _absolute(anchor.get('href', ''))
            if not href or href.startswith('#'):
                continue
            href_lower = href.lower()
            if 'steamrip.com' in href_lower and '-free-download' in href_lower:
                continue
            is_download = (
                'shortc-button' in (anchor.get('class', []) or [])
                or 'wp-block-button__link' in (anchor.get('class', []) or [])
                or any(domain in href_lower for domain in _HOSTERS)
                or 'magnet:' in href_lower
            )
            if not is_download:
                continue
            download_found = True
            button_text = anchor.get_text(strip=True)
            is_update = False
            update_version = ""
            full_text = element.get_text(strip=True).lower()
            if len(full_text) < 150 and re.search(r'\b(update|patch|dlc)\b', full_text):
                is_update = True
                strong = element.find('strong')
                if strong:
                    update_version = strong.get_text(" ", strip=True)
                else:
                    nodes = [t for t in element.stripped_strings if t != button_text]
                    update_version = " ".join(nodes)[:50]
            if not any(item['url'] == href for item in links):
                links.append({
                    "name": _link_name(href, button_text),
                    "url": href,
                    "is_update": is_update,
                    "update_version": update_version,
                })

        if download_found:
            continue
        if element.find('img'):
            continue
        if 'free download' in text_lower and len(text_lower) < 50:
            continue
        if section == "skip":
            continue

        req_html, info_html, desc_html = _section_for(
            str(element), section, req_html, info_html, desc_html)

    return links, desc_html, req_html, info_html, dlcs_html


def parse_game_page_html(html):
    """Game page -> the data dictionary used by the Explorer/discovery."""
    soup = BeautifulSoup(html or "", "lxml")
    image = featured_image_url(soup)

    title_tag = soup.find('h1', class_='entry-title') or soup.find('h1')
    raw_title = title_tag.text.strip() if title_tag else "Unknown Title"
    title, version, mp_tag = strip_title(raw_title)

    links, desc_html, req_html, info_html, dlcs_html = _parse_content(soup)
    if dlcs_html:
        desc_html += (
            "<details style='background-color:rgba(115, 61, 145, 0.15); "
            "padding: 10px; border: 1px solid #733d91; border-radius: 5px; "
            "margin-top: 15px;'><summary style='color:#ff00a0; font-weight:bold; "
            f"cursor:pointer; font-size:14px; outline: none;'>📦 MOSTRA DLC INCLUSI "
            f"({dlcs_html.count('<li>')} Pacchetti)</summary>{dlcs_html}</details>"
        )

    version_status = parse_version_status_html(html or "") or {}
    return {
        "title": title,
        "version": version,
        "mp_tag": mp_tag,
        "img_url": image,
        "sgdb_assets": {"steam_app_id": "", "grid": "", "hero": "",
                        "logo": "", "wide": ""},
        "cover_urls": [image] if image else [],
        "description": _BASE_CSS + desc_html if desc_html else "Nessuna descrizione trovata.",
        "requirements": _BASE_CSS + req_html if req_html else "Dati requisiti non disponibili.",
        "game_info": _BASE_CSS + info_html if info_html else "Nessuna info tecnica aggiuntiva.",
        "links": links,
        "steam_id": str(version_status.get("app_id", "") or ""),
        "version_status": version_status,
        "steamrip_categories": _categories(soup),
    }
