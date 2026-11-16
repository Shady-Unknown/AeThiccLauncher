# version_status.py
"""
Lettura del widget "Version Status" di SteamRIP + calcolo dello stato di
aggiornamento e refresh del solo lato Steam (build pubblicata su Steam).

Il sito espone i dati in attributi e classi stabili:

    <div class="svchk__wrapper" data-svchk-app="2661300" data-svchk-build="25063598">
        <label class="svchk__trigger svchk__trigger--amber">
            <span class="svchk__trigger-label">Update Pending</span>
        ...
        Posted Build / 25063598 / 20 September 2026
        Steam Latest / 26399478 / 4 November 2026
        [data-svchk-alert] "A newer build is available on Steam."
        Last checked: [data-svchk-lastchecked] 7 November 2026
        <a class="svchk__btn" href="//steamdb.info/app/2661300/patchnotes/">

Per il "Steam Latest" in refresh usiamo il mirror pubblico SteamCMD
(api.steamcmd.net), che restituisce lo stesso buildid del sito.
"""
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from core.game_version import normalize_label


MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)

# Stati possibili
STATE_UPDATE_AVAILABLE = "update_available"              # SteamRIP avanti: aggiorna
STATE_STEAMRIP_OK_STEAM_AHEAD = "steamrip_ok_steam_ahead"  # sei ok su SteamRIP, Steam è avanti
STATE_UPDATED = "updated"                                # tutto allineato
STATE_VERSION_ONLY = "version_only"                      # niente build tracking
STATE_UNKNOWN = "unknown"                                # dati assenti

#: Un build id plausibile è solo un numero: 4-10 cifre, nessun punto o lettera.
_BUILD_ID_RE = re.compile(r"^\d{4,10}$")
_BUILD_ID_MIN = 1000
_BUILD_ID_MAX = 9_999_999_999


def _clean_text(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def classify_build(value):
    """``"buildid"`` for a plain numeric build, ``"label"`` for a version text.

    SteamRIP sometimes puts a version such as ``v0.1.0.0.14530`` in the same
    field that elsewhere holds a build id. Turning those digits into an integer
    produces numbers that can never be compared with a real build.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return "buildid" if _BUILD_ID_MIN <= value <= _BUILD_ID_MAX else None
    text = str(value).strip()
    if not text:
        return None
    if _BUILD_ID_RE.match(text):
        return "buildid"
    return "label"


def as_build(value):
    """The int build id when ``value`` is one, ``None`` otherwise."""
    if classify_build(value) != "buildid":
        return None
    return int(str(value).strip())


def _to_int(value):
    if value is None:
        return None
    digits = re.sub(r"[^\d]", "", str(value))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def format_timestamp(ts):
    """Epoch -> '4 November 2026' (inglese, come il sito)."""
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    return f"{dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def parse_version_status_html(html):
    """
    Estrae il blocco Version Status da una pagina SteamRIP.
    Ritorna un dict (o None se il widget non è presente).
    """
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        return None

    wrapper = soup.find(class_="svchk__wrapper")
    if not wrapper:
        return None

    trigger = wrapper.find(class_="svchk__trigger")
    label_el = wrapper.find(class_="svchk__trigger-label")
    title_el = wrapper.find(class_="svchk__title")

    color = ""
    if trigger:
        for cls in trigger.get("class", []) or []:
            match = re.match(r"svchk__trigger--(\w+)", cls)
            if match:
                color = match.group(1)
                break

    data = {
        "variant": "build",
        "app_id": str(wrapper.get("data-svchk-app", "") or "").strip(),
        "label": _clean_text(label_el.get_text(strip=True)) if label_el else "",
        "color": color or "grey",
        "header": _clean_text(title_el.get_text(strip=True)) if title_el else "",
        "posted_build": as_build(wrapper.get("data-svchk-build")),
        "posted_build_date": "",
        "steam_build": None,
        "steam_build_date": "",
        "posted_version": "",
        "alert": "",
        "alert_color": "",
        "subtext": "",
        "site_last_checked": "",
        "steamdb_url": "",
    }

    # A version string in the build attribute (or row) must not become a build.
    widget_build = wrapper.get("data-svchk-build")
    if data["posted_build"] is None and classify_build(widget_build) == "label":
        data["posted_version"] = _clean_text(str(widget_build))

    for row in wrapper.find_all(class_="svchk__row"):
        key_el = row.find(class_="svchk__key")
        key = _clean_text(key_el.get_text(strip=True)).lower() if key_el else ""
        val_el = row.find(class_="svchk__val")
        date_el = row.find(class_="svchk__date")
        value = _clean_text(val_el.get_text(" ", strip=True)) if val_el else ""
        date = _clean_text(date_el.get_text(" ", strip=True)) if date_el else ""

        if "posted build" in key:
            build = as_build(value)
            if build is not None:
                data["posted_build"] = build
                data["posted_build_date"] = date
            elif value and not data["posted_version"]:
                data["posted_version"] = value
        elif "steam latest" in key:
            data["steam_build"] = as_build(value)
            data["steam_build_date"] = date
        elif "posted version" in key:
            data["posted_version"] = value

    alert_box = wrapper.find(attrs={"data-svchk-alert": True})
    alert_el = alert_box.find(class_="svchk__alert") if alert_box else wrapper.find(class_="svchk__alert")
    if alert_el:
        classes = " ".join(alert_el.get("class", []) or [])
        match = re.search(r"svchk__alert--(\w+)", classes)
        data["alert_color"] = match.group(1) if match else ""
        data["alert"] = _clean_text(alert_el.get_text(" ", strip=True))

    sub_el = wrapper.find(class_="svchk__subtext")
    if sub_el:
        data["subtext"] = _clean_text(sub_el.get_text(" ", strip=True))

    last_el = wrapper.find(attrs={"data-svchk-lastchecked": True})
    if last_el:
        data["site_last_checked"] = _clean_text(last_el.get_text(" ", strip=True))

    link = wrapper.find("a", class_="svchk__btn") or wrapper.find("a", href=re.compile("steamdb"))
    if link and link.get("href"):
        href = link["href"].strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = "https://steamdb.info" + href
        data["steamdb_url"] = href

    if not data["app_id"] and data["steamdb_url"]:
        match = re.search(r"/app/(\d+)", data["steamdb_url"])
        if match:
            data["app_id"] = match.group(1)

    if not data["posted_build"]:
        data["variant"] = "version"

    return data


def fetch_steam_latest_build(app_id, timeout=5):
    """
    Legge l'ultima build pubblica su Steam dal mirror SteamCMD.
    Ritorna {'build': int, 'date': str, 'timestamp': int} oppure None.
    """
    app_id = str(app_id or "").strip()
    if not app_id.isdigit():
        return None

    try:
        from curl_cffi import requests
        response = requests.get(
            f"https://api.steamcmd.net/v1/info/{app_id}",
            impersonate="chrome120",
            timeout=timeout,
        )
        if response.status_code != 200:
            return None

        payload = response.json()
        app_data = (payload.get("data") or {}).get(app_id) or {}
        public = ((app_data.get("depots") or {}).get("branches") or {}).get("public") or {}
        build = as_build(public.get("buildid"))
        timestamp = _to_int(public.get("timeupdated")) or 0
        if not build:
            return None

        return {
            "build": build,
            "timestamp": timestamp,
            "date": format_timestamp(timestamp) if timestamp else "",
        }
    except Exception as e:
        print(f"[VersionStatus] SteamCMD refresh failed for {app_id}: {e}")
        return None


def compute_version_state(
    installed_build=None,
    posted_build=None,
    steam_build=None,
    installed_version="",
    posted_version_text="",
    posted_version_only="",
    is_installed=False,
    has_status=False,
):
    """
    Calcola lo stato di aggiornamento combinando i tre build.

    Ritorna {'state': str, 'ok': bool|None, 'reason': str}.

    I build vengono normalizzati qui: un valore che non è un build id (per
    esempio una versione come ``v0.1.0.0.14530``) non partecipa ai confronti
    numerici e non può produrre un falso "Steam ahead".
    """
    def result(state, ok=None, reason=""):
        return {"state": state, "ok": ok, "reason": reason}

    if not has_status:
        return result(STATE_UNKNOWN, None, "no_status")

    installed_build = as_build(installed_build)
    posted_build = as_build(posted_build)
    steam_build = as_build(steam_build)

    if posted_build:
        if is_installed:
            if installed_build:
                if installed_build < posted_build:
                    return result(STATE_UPDATE_AVAILABLE, False, "installed_below_site")
                if steam_build and posted_build < steam_build:
                    return result(STATE_STEAMRIP_OK_STEAM_AHEAD, True, "site_below_steam")
                return result(STATE_UPDATED, True, "all_aligned")

            # Build installata sconosciuta: fallback al confronto versione
            installed_label = normalize_label(installed_version)
            site_label = normalize_label(posted_version_text)
            if installed_label and site_label:
                if installed_label != site_label:
                    return result(STATE_UPDATE_AVAILABLE, False, "version_mismatch")
                return result(STATE_VERSION_ONLY, True, "version_aligned")
            return result(STATE_UNKNOWN, None, "installed_build_unknown")

        # Non installato: si mostra solo lo stato lato sito/Steam
        if steam_build and posted_build < steam_build:
            return result(STATE_STEAMRIP_OK_STEAM_AHEAD, True, "site_below_steam")
        return result(STATE_UPDATED, True, "all_aligned")

    # Variante senza tracking build (purple "Version: vX")
    site_version = posted_version_only or posted_version_text
    if site_version:
        site_label = normalize_label(site_version)
        installed_label = normalize_label(installed_version)
        if is_installed and site_label and installed_label and site_label != installed_label:
            return result(STATE_UPDATE_AVAILABLE, False, "version_mismatch")
        return result(STATE_VERSION_ONLY, True, "version_only")
    return result(STATE_UNKNOWN, None, "no_build_info")
