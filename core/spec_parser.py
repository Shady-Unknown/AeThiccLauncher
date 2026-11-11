"""Parser delle sezioni "requirements" / "game info" di SteamRip.

L'HTML arriva dal sito come ``<style>…</style>`` + una lista di voci::

    <ul>
      <li><strong>OS:</strong> Windows 10/11 with updates</li>
      <li><strong>Processor:</strong> AMD Ryzen 5 2600 / Intel i5-8400</li>
    </ul>

Qui lo si trasforma in righe ``{"icon", "label", "value"}`` per la griglia
della UI. Se il formato non è riconosciuto si ritorna una lista vuota e la
view mostra l'HTML originale (nessuna regressione sui formati strani).
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

#: label normalizzata -> chiave icona (``.media/icons/<key>.png``).
LABEL_ICONS = {
    "os": "spec_os",
    "operative system": "spec_os",
    "operating system": "spec_os",
    "system": "spec_os",
    "processor": "spec_cpu",
    "cpu": "spec_cpu",
    "memory": "spec_ram",
    "ram": "spec_ram",
    "graphics": "spec_gpu",
    "video card": "spec_gpu",
    "gpu": "spec_gpu",
    "directx": "spec_dx",
    "direct x": "spec_dx",
    "storage": "spec_storage",
    "hard drive": "spec_storage",
    "hard disk": "spec_storage",
    "disk space": "spec_storage",
    "space": "spec_storage",
    "genre": "info_genre",
    "genres": "info_genre",
    "developer": "info_dev",
    "developers": "info_dev",
    "studio": "info_dev",
    "publisher": "info_dev",
    "released by": "info_pub",
    "uploaded by": "info_pub",
    "platform": "info_platform",
    "platforms": "info_platform",
    "co-op": "info_coop",
    "coop": "info_coop",
    "co op": "info_coop",
    "co-op by": "info_coop",
    "coop by": "info_coop",
    "co op by": "info_coop",
    "multiplayer": "info_coop",
    "version": "info_version",
    "build": "info_version",
    "game size": "info_size",
    "size": "info_size",
    "download size": "info_size",
}

_WHITESPACE = re.compile(r"\s+")


def _clean(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _normalize_label(label: str) -> str:
    label = _clean(label).rstrip(":").strip().lower()
    label = label.replace("/", " ")
    label = _WHITESPACE.sub(" ", label)
    return label.strip(" :.-")


#: Chiavi ordinate dalla più lunga: serve per il match "prefisso + parola".
_PREFIX_KEYS = tuple(sorted(LABEL_ICONS, key=len, reverse=True))


def icon_for_label(label: str) -> str | None:
    """Icona per una label, tollerante alle varianti ("Co-op By", "OS Version")."""
    normalized = _normalize_label(label)
    if not normalized:
        return None
    icon = LABEL_ICONS.get(normalized)
    if icon:
        return icon
    # Varianti tipo "Co-op By", "Game Size (GB)", "Released by (team)".
    for key in _PREFIX_KEYS:
        if normalized.startswith(key + " "):
            return LABEL_ICONS[key]
    return None


def _split_entry(bold_text: str, full_text: str) -> tuple[str, str]:
    label = _clean(bold_text).rstrip(":").strip()
    value = _clean(full_text)
    if label and value.startswith(bold_text.strip()):
        value = value[len(bold_text.strip()):]
    value = value.lstrip(": ").strip()
    return label, value


def parse_spec_html(html_content: str) -> list[dict]:
    """Righe strutturate dall'HTML SteamRip; ``[]`` se non riconosciuto."""
    if not html_content or not isinstance(html_content, str):
        return []
    cleaned = re.sub(r"<style[^>]*>.*?</style>", " ", html_content, flags=re.S | re.I)
    try:
        soup = BeautifulSoup(cleaned, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(cleaned, "html.parser")
        except Exception:
            return []

    items: list[dict] = []
    nodes = soup.find_all("li")
    if not nodes:
        # Alcuni giochi usano paragrafi invece di liste.
        nodes = [p for p in soup.find_all("p") if p.find(["strong", "b"])]

    for node in nodes:
        bold = node.find(["strong", "b"])
        full_text = _clean(node.get_text(" ", strip=True))
        if not full_text:
            continue
        if bold is not None:
            label, value = _split_entry(bold.get_text(" ", strip=True), full_text)
        else:
            label, value = "", full_text
        if not value and not label:
            continue
        if not value:
            value, label = label, ""
        items.append({
            "icon": icon_for_label(label) if label else None,
            "label": label,
            "value": value,
        })

    # Scarta righe inutili tipo "Minimum:" da sole.
    items = [item for item in items if item["value"]]
    return items


def looks_structured(items: list[dict]) -> bool:
    """Almeno due righe: sotto questa soglia vale l'HTML originale."""
    return len(items) >= 2
