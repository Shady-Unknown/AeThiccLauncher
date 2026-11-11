"""Game detail sheet, covers, age badges, store links and sales.

Extracted from the historical AppWindow god object. The class is a
cohesive mixin: methods keep using ``self`` (the AppWindow instance),
so behaviour stays identical while the file is compartmentalised.
"""

import sys
import os
import base64
import tempfile
import json
import shutil
import subprocess
import time
import requests
#import traceback
#import random
#from datetime import datetime
import re
import html
from PyQt6.QtCore import Qt, QUrl, QTimer #QSize, pyqtSignal, QThread, QStringListModel
from PyQt6.QtGui import QColor, QDesktopServices, QPixmap, QIcon #QPainter, QPainterPath, QIcon
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QTabWidget, QGraphicsDropShadowEffect,
                             QPushButton,
                             QLabel, QMenuBar, QDialog, QComboBox, QMessageBox,
                             QMenu, QFileDialog, QInputDialog) #QHBoxLayout, QLineEdit, QTextEdit,
                             #QScrollArea, QColorDialog,
                             #QFormLayout, QListWidget, QStackedWidget, QFileDialog, 
                             #QListWidgetItem,  QSpinBox, 
                             #QSplitter, QSizeGrip, QCompleter, QProgressBar,  QFrame 
#from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from PyQt6.QtNetwork import QNetworkCookie

from ui.tabs.downloads_tab import DownloadManagerTab
from network.scraper import SteamRIPScraper
#from core.installer import GameInstaller
from managers.save_manager import SavegamesManager, safe_game_folder_name
from managers.playtime_manager import PlaytimeManager
from ui.tabs.savegames_tab import SavegamesTab
from core import config as style_settings
from core.config import get_base_dir
from core.mini_cover_cache import MiniCoverManager

from ui.dialogs.orphan_dialog import OrphanDialog
#from ui.dialogs.linux_wrapper_dialog import LinuxWrapperDialog
#from ui.widgets.profile_card import ProfileCard
#from ui.dialogs.steam_account_dialog import SteamAccountDialog
#from ui.dialogs.password_dialog import PasswordDialog
from network.store_links_worker import StoreLinksWorker
from network.page_freshness_worker import PageFreshnessWorker, PageHeadersWorker, VersionStatusWorker

_PAGE_TTL = 6 * 3600  # 6 ore: soglia dopo cui un link è considerato potenzialmente stale
from network.sales_worker import SalesWorker
from ui.styles.sale_style import SaleStyle, SALE_GREEN, SALE_GLOW_CSS
from ui.dialogs.settings_dialog import SettingsDialog
from managers.library_manager import LibraryManager, is_local_install
from managers.igdb_details_cache import IgdbDetailsCache
from network.igdb_details_worker import IGDBDetailsWorker
from managers.background_scanner import BackgroundScanner
from core.game_launcher import GameLauncher
from core.installer_flow import InstallerFlowCoordinator
from core import platform_info, steam_integration
from core.events import (
    EVENT_BACKUP_CREATED,
    EVENT_IGDB_DETAILS_READY,
    EVENT_LAUNCHER_START,
    EVENT_ORPHAN_RECOVERED,
    EVENT_PRICE_REFRESHED,
    EVENT_SESSION_END,
    EVENT_STORE_OPENED,
)
from core.gold_tier import GoldTierManager
from ui.styles.gold_tier_style import GoldTierStyle, GOLD_PRIMARY
from core.version_status import (
    compute_version_state,
    STATE_UPDATE_AVAILABLE,
    STATE_STEAMRIP_OK_STEAM_AHEAD,
    STATE_UPDATED,
    STATE_VERSION_ONLY,
    STATE_UNKNOWN,
)
from network.steam_build_worker import SteamBuildWorker

from ui.dialogs.steam_restart_dialog import SteamRestartDialog
from ui.styles.structural_theme import StructuralTheme
from core.cover_cache import CoverCache

# --- APP WINDOW PRINCIPALE ---

class GameDetailController:
    def _on_sgdb_assets_ready(self, title, sgdb_assets):
        print(f"[SGDB] _on_sgdb_assets_ready: title='{title}', steam_app_id='{sgdb_assets.get('steam_app_id', '')}', grid={'yes' if sgdb_assets.get('grid') else 'no'}")
        self.master_catalog.set_sgdb_assets(
            title, sgdb_assets.get("steam_app_id", ""),
            grid_url=sgdb_assets.get("grid", ""),
            hero_url=sgdb_assets.get("hero", ""),
            logo_url=sgdb_assets.get("logo", ""),
            wide_url=sgdb_assets.get("wide", ""),
            mark_checked=True,
        )

        if self.current_game_data.get("title") != title:
            return

        self.current_game_data["sgdb_assets"] = sgdb_assets
        self.current_game_data["steam_id"] = sgdb_assets.get("steam_app_id", "")
        img_url = self.current_game_data.get("img_url", "")
        igdb_cover = ""
        igdb_meta = self.current_game_data.get("igdb_meta")
        if isinstance(igdb_meta, dict):
            igdb_cover = igdb_meta.get("cover_url") or ""
        new_urls = [v for v in [
            sgdb_assets["grid"], igdb_cover, sgdb_assets["hero"],
            sgdb_assets["logo"], sgdb_assets["wide"], img_url,
        ] if v]
        new_urls = CoverCache.resolve_urls(new_urls, title=title, installed_games=self.installed_games)
        self.current_game_data["cover_urls"] = new_urls

        if not new_urls:
            return

        self.cover_image.setGraphicsEffect(None)
        first_url = html.escape(new_urls[0], quote=True)
        urls_array = [f"'{url}'" for url in new_urls]
        urls_array.append(f"'{CoverCache.placeholder_url()}'")
        urls_js = f"var urls = [{', '.join(urls_array)}];"
        neon_start = self.app_config['theme'].get('neon_gradient_start', '#8d0bb1')
        neon_end = self.app_config['theme'].get('neon_gradient_end', '#00d2ff')

        ribbon_html = ""
        if title in self.installed_games:
            installed_ver = self.installed_games[title].get('version', '')
            if installed_ver and installed_ver != self.current_game_data.get('version', ''):
                ribbon_html = "<div class='ribbon'>UPDATE!</div>"

        cached_prices = self.master_catalog.get_prices(title)
        is_on_sale = any(
            cached_prices.get(store, {}).get("on_sale", False)
            for store in ("steam", "gog")
        ) if isinstance(cached_prices, dict) else False
        sgdb_banner_text = None
        if isinstance(cached_prices, dict) and is_on_sale:
            plat_list = []
            disc = 0
            for name, key in [("Steam", "steam"), ("GOG", "gog")]:
                sd = cached_prices.get(key, {})
                if isinstance(sd, dict) and sd.get("on_sale"):
                    plat_list.append(name)
                    disc = max(disc, sd.get("discount_percent", 0))
            sgdb_banner_text = SaleStyle.sale_cover_banner_text(" & ".join(plat_list), disc)
        print(f"[SGDB] Cover re-render: _is_gold_tier={self._is_gold_tier}, is_on_sale={is_on_sale}, new_urls_count={len(new_urls)}")

        if self._is_gold_tier:
            html_cover = GoldTierStyle.gold_cover_html(
                new_urls,
                self.app_config['theme']['background_dark_purple'],
                ribbon_html,
                sale_active=is_on_sale,
            )
        else:
            if is_on_sale:
                card_bg = SALE_GREEN
                glow_bg = SALE_GREEN
                glow_ext = "-3px"
                glow_blur = "10px"
            else:
                card_bg = f"linear-gradient(135deg, {neon_start} 0%, #ff00a0 50%, {neon_end} 100%)"
                glow_bg = f"linear-gradient(135deg, {neon_start} 0%, #ff00a0 50%, {neon_end} 100%)"
                glow_ext = "-4px"
                glow_blur = "6px"

            html_cover = f"""
            <html>
            <head>
            <style>
                body {{ margin: 0; padding: 0; background-color: transparent; display: flex; justify-content: center; align-items: center; overflow: hidden; height: 100vh; }}
                .card-wrapper {{ width: 85%; height: 88%; max-width: 240px; max-height: 360px; box-sizing: border-box; position: relative; border-radius: 10px; background: {card_bg}; padding: 3px; }}
                .cover-glow {{ position: absolute; top: {glow_ext}; left: {glow_ext}; right: {glow_ext}; bottom: {glow_ext}; border-radius: 10px; background: {glow_bg}; filter: blur({glow_blur}); z-index: 0; }}
                .cover-art {{ position: absolute; top: 3px; left: 3px; right: 3px; bottom: 3px; border-radius: 7px; background-size: cover; background-position: center center; background-repeat: no-repeat; background-color: {self.app_config['theme']['background_dark_purple']}; z-index: 1; }}
                .cover-preload {{ display: none; }}
                .ribbon {{ position: absolute; top: 25px; left: -35px; background: {self.app_config['theme']['bright_neon_accent']}; color: #fff; padding: 5px 40px; transform: rotate(-45deg); font-weight: bold; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,0.8); z-index: 10; letter-spacing: 1px; text-align: center; }}
            </style>
            <script>
                {urls_js}
                var currentIdx = 0;
                function setCover(url) {{
                    var el = document.getElementById('coverArt');
                    if (el && url) el.style.backgroundImage = "url('" + String(url).replace(/'/g, "%27") + "')";
                }}
                function loadNext(img) {{ currentIdx++; if(currentIdx < urls.length) {{ img.src = urls[currentIdx]; }} }}
            </script>
            </head>
            <body>
                <div class="card-wrapper">
                    <div class="cover-glow"></div>
                    {ribbon_html}
                    <div class="cover-art" id="coverArt" style="background-image: url('{first_url}');"></div>
                    <img class="cover-preload" src="{first_url}" onload="setCover(this.src)" onerror="loadNext(this)" alt="">
                </div>
            </body>
            </html>
            """

        self._display_cover_html(html_cover, sgdb_banner_text)

        # Persist SGDB enrichment back to offline cache so next session loads with steam_id and cover URLs
        page_url = self.current_game_data.get("page_url", "")
        steam_app_id = sgdb_assets.get("steam_app_id", "")
        if page_url and page_url in self.session_cache:
            if steam_app_id:
                self.session_cache[page_url]["steam_id"] = steam_app_id
            self.session_cache[page_url]["cover_urls"] = self.current_game_data.get("cover_urls", [])
            self._save_offline_cache()

        # Re-fetch prices if we now have steam_app_id that wasn't available before
        if steam_app_id:
            steam_url = (cached_prices.get("steam", {}).get("url", "")
                         if isinstance(cached_prices, dict) else "")
            should_refetch = f"/app/{steam_app_id}" not in steam_url
            print(f"[SGDB] Refetch check: steam_app_id={steam_app_id}, steam_url='{steam_url}', should_refetch={should_refetch}, cached_prices_type={type(cached_prices).__name__}")
            if should_refetch:
                from network.sales_worker import SalesWorker
                sales_worker = SalesWorker(title, steam_id=steam_app_id, region=self._price_region())
                sales_worker.sale_data_ready.connect(self._on_sale_data_found)
                sales_worker.finished.connect(lambda w=sales_worker: self._cleanup_sales_worker(w))
                self._active_sales_workers.append(sales_worker)
                sales_worker.start()

    def _apply_age_badges(self, ratings):
        ratings = ratings if isinstance(ratings, dict) else {}
        for label, value, prefix in (
                (getattr(self, "lbl_pegi", None),
                 str(ratings.get("pegi") or "").strip(), "PEGI "),
                (getattr(self, "lbl_esrb", None),
                 str(ratings.get("esrb") or "").strip(), "ESRB ")):
            if label is None:
                continue
            label.setText(prefix + value if value else "")
            label.setVisible(bool(value))

    def _update_age_badges(self, title, data=None):
        details = IgdbDetailsCache.get(title) or {}
        ratings = details.get("age_ratings") or {}
        self._apply_age_badges(ratings)
        if not ratings:
            self._fetch_age_ratings(title, data)

    def _fetch_age_ratings(self, title, data=None):
        if not title:
            return
        if getattr(self, "_age_fetch_title", "") == title:
            return
        tried = getattr(self, "_age_tried", None)
        if tried is None:
            tried = set()
            self._age_tried = tried
        if title in tried:
            return
        tried.add(title)
        self._age_fetch_title = title

        entry = self.master_catalog.get_entry(title) or {}
        igdb = entry.get("igdb_meta") or (data or {}).get("igdb_meta") or {}
        appid = (igdb.get("steam_appid")
                 or entry.get("steam_id")
                 or (data or {}).get("steam_id")
                 or "")
        worker = IGDBDetailsWorker(
            title,
            igdb_id=igdb.get("igdb_id") or "",
            appid=appid,
            parent=self,
        )
        worker.details_ready.connect(self._on_age_details_ready)
        self._age_worker = worker
        worker.start()

    def _on_age_details_ready(self, title, details):
        if getattr(self, "_age_fetch_title", "") == title:
            self._age_fetch_title = ""
        if not details:
            return
        IgdbDetailsCache.set(title, details)
        self.events.emit_event(EVENT_IGDB_DETAILS_READY, title=title)
        current = str((getattr(self, "current_game_data", None) or {}).get("title") or "")
        if current and current != title:
            return
        self._apply_age_badges(details.get("age_ratings") or {})

    @staticmethod
    def _first_cover_field(dicts, key):
        for item in dicts:
            if isinstance(item, dict) and item.get(key):
                return item[key]
        return ""

    def _preferred_cover_urls(self, data):
        """Prefer vertical box art (SGDB grid / IGDB cover) over the
        horizontal SteamRIP featured image used as the initial fallback."""
        title = str(data.get("title") or "")
        entry = self.master_catalog.get_entry(title) or {}
        if not isinstance(entry, dict):
            entry = {}
        cover = self._first_cover_field(
            [data.get("igdb_meta"), entry.get("igdb_meta")], "cover_url")
        grid = self._first_cover_field(
            [data.get("sgdb_assets"), entry.get("sgdb_assets")], "grid")
        urls = []
        for url in (grid, cover):
            if url and url not in urls:
                urls.append(url)
        for url in data.get("cover_urls") or []:
            if url and url not in urls:
                urls.append(url)
        return urls

    def display_game_data(self, data):
        # Se la pagina e' stata estratta su richiesta del popup Discovery,
        # non ricostruire anche la scheda Explorer (render costoso, ~1s):
        # verra' mostrata quando l'utente preme Install.
        popup_flag = getattr(self.scraper, "_popup_extract", None)
        if popup_flag:
            self.scraper._popup_extract = None
            page_url = str(data.get("page_url") or "").rstrip("/")
            if str(popup_flag).rstrip("/") == page_url:
                print("[App] extract richiesto dal popup: Explorer non aggiornato")
                return

        _perf_t0 = time.perf_counter()
        _perf_marks = []

        def _perf(label):
            if os.environ.get("SRP_PROFILE"):
                _perf_marks.append((label, (time.perf_counter() - _perf_t0) * 1000))

        self.db_progress.hide()
        self.clear_links()
        self.current_game_data = data  
        title = data.get('title', 'Unknown')
        is_local = bool(data.get('local_only'))

        print(f"[Display] title='{title}', steam_id='{data.get('steam_id', '')}', has_cover_urls={'cover_urls' in data and bool(data['cover_urls'])}")

        # Merge enrichment from master_catalog (SGDB assets override cached data)
        mc_sgdb = self.master_catalog.get_sgdb_assets(title)
        if isinstance(mc_sgdb, dict) and mc_sgdb.get("steam_app_id"):
            data["steam_id"] = mc_sgdb["steam_app_id"]
            mc_urls = [mc_sgdb[k] for k in ("grid", "hero", "logo", "wide") if mc_sgdb.get(k)]
            if mc_urls:
                img_url = data.get("img_url", "")
                combined = mc_urls + ([img_url] if img_url else [])
                data["cover_urls"] = combined

        # Ensure cover_urls exists (fallback to img_url for cache hits when SGDB has no data)
        if not data.get("cover_urls"):
            img_url = data.get("img_url", "")
            if img_url:
                data["cover_urls"] = [img_url]

        # Prefer vertical box art BEFORE resolving: resolve_urls caches the
        # first URL locally, so prepending IGDB/SGDB here makes the Explorer
        # use the same box art as the detail popup.
        preferred_cover_urls = self._preferred_cover_urls(data)
        if preferred_cover_urls:
            data["cover_urls"] = preferred_cover_urls

        # Resolve cover URLs to local file:// paths (installed → Steam_Assets, else → cache)
        data["cover_urls"] = CoverCache.resolve_urls(
            data.get("cover_urls", []),
            title=title,
            installed_games=self.installed_games,
        )
        _perf("cover_resolve")

        # Trigger SGDB fetch solo se manca davvero e non l'abbiamo già verificato.
        if self.needs_sgdb_fetch(title):
            self.request_sgdb_assets(title)
        _perf("sgdb_queue")

        self._is_gold_tier = False
        self._gold_reason = None

        # Tier 1: Steam App ID from scraped data (most reliable)
        sid = str(data.get("steam_id", "") or "")
        if sid:
            self._is_gold_tier = GoldTierManager.is_gold_by_steam_id(sid)
            if self._is_gold_tier:
                self._gold_reason = GoldTierManager.get_reason_by_steam_id(sid)
                print(f"[GoldTier] Matched via steam_app_id={sid}")

        # Tier 2: Steam App ID from installed_games metadata
        if not self._is_gold_tier and title in self.installed_games:
            meta = self.installed_games[title]
            sid2 = str(meta.get("steam_id", "") or "")
            if sid2 and sid2 != sid:
                self._is_gold_tier = GoldTierManager.is_gold_by_steam_id(sid2)
                if self._is_gold_tier:
                    self._gold_reason = GoldTierManager.get_reason_by_steam_id(sid2)
                    print(f"[GoldTier] Matched via installed meta steam_app_id={sid2}")

        # Tier 3: Title-based matching (handles old caches, non-Steam, fallback)
        if not self._is_gold_tier:
            self._is_gold_tier = GoldTierManager.is_gold_tier(title)
            if self._is_gold_tier:
                self._gold_reason = GoldTierManager.get_gold_reason(title)
                print(f"[GoldTier] Matched via title='{title}'")

        # Tier 4: Installed games alternate title as last resort
        if not self._is_gold_tier and title in self.installed_games:
            alt = self.installed_games[title].get('title', '')
            if alt and alt != title:
                self._is_gold_tier = GoldTierManager.is_gold_tier(alt)
                if self._is_gold_tier:
                    self._gold_reason = GoldTierManager.get_gold_reason(alt)
                    print(f"[GoldTier] Matched via alt title='{alt}'")

        # --- VERIFICA INTEGRITÀ STEAM ---
        if title in self.installed_games:
            self.game_launcher.verify_steam_integrity(title)
        _perf("steam_verify")
            
        # FIX 2: Usiamo il VERO URL della pagina web per la cache, è infallibile.
        actual_url = data.get('page_url', self.search_input.text()).rstrip('/')
        self.current_game_data['source_url'] = actual_url
        
        # SALVIAMO IN CACHE SOLO I DATI ESSENZIALI (senza duplicati da master_catalog)
        # I giochi locali non hanno URL SteamRip: non entrano nella cache offline.
        if not is_local:
            # Riusa i validatori già presenti: niente HEAD sincrono a ogni click.
            etag = data.get("etag", "")
            last_modified = data.get("last_modified", "")
            cached_at = data.get("cached_at") or time.time()
            self.session_cache[actual_url] = {
                "title": data["title"],
                "version": data.get("version", ""),
                "mp_tag": data.get("mp_tag", ""),
                "description": data.get("description", ""),
                "requirements": data.get("requirements", ""),
                "game_info": data.get("game_info", ""),
                "links": data.get("links", []),
                "page_url": actual_url,
                "img_url": data.get("img_url", ""),
                "steam_id": data.get("steam_id", ""),
                "cover_urls": data.get("cover_urls", []),
                "source_url": data.get("source_url", actual_url),
                "etag": etag,
                "last_modified": last_modified,
                "cached_at": cached_at,
            }
            # Persistiamo il Version Status SOLO se lo scrape lo ha prodotto:
            # così le entry legacy restano riconoscibili e possono fare backfill.
            if "version_status" in data:
                self.session_cache[actual_url]["version_status"] = data.get("version_status") or {}
            if data.get("steamrip_categories"):
                self.session_cache[actual_url]["steamrip_categories"] = data.get("steamrip_categories")
            self._save_offline_cache()
            if not data.get("cached_at"):
                # Scrape fresco: recupera ETag/Last-Modified in background
                self._fetch_page_headers_async(actual_url)
        _perf("page_headers_cache")
        
        web_version = data['version']
        # Cross-reference con master_catalog per versione autoritativa
        mc_entry = self.master_catalog.get_entry(title) if self.master_catalog else None
        if isinstance(mc_entry, dict):
            mc_ver = mc_entry.get('version', '')
            if mc_ver and mc_ver != 'Release Base' and mc_ver != web_version:
                print(f"[App] Master catalog has newer version '{mc_ver}' vs cached '{web_version}' for '{title}'")
                web_version = mc_ver
        
        # --- 1. GESTIONE AUTO-POPULATE JSON ---
        if self._pending_auto_populate_folder:
            recovered_dir = self._pending_auto_populate_folder
            self.installer_flow.generate_local_metadata(recovered_dir, data)
            self.events.emit_event(
                EVENT_ORPHAN_RECOVERED,
                title=title or '',
                install_dir=recovered_dir,
            )
            QMessageBox.information(self, "Auto-Populate", f"Metadata for {title} successfully generated in:\n{recovered_dir}")
            self._pending_auto_populate_folder = None
            self.lib_manager.scan_library() # Rescan per mostrarlo in libreria
            if hasattr(self, 'tab_discovery'):
                self.tab_discovery.refresh_library()
            
        # --- 2. LOGICA DI UPDATE COMPARATIVO E TASTO PLAY ---
        is_installed = False
        update_available = False
        installed_version = ""
        meta_for_update = self.installed_games.get(title)
        self._current_web_version = web_version

        # Version Status del sito (build SteamRIP vs build Steam)
        site_status = data.get("version_status") or {}
        if site_status:
            self.master_catalog.set_version_status(title, site_status)

        site_categories = data.get("steamrip_categories")
        if site_categories:
            self.master_catalog.set_game_field(title, "steamrip_categories", site_categories)
        version_status = self.master_catalog.get_version_status(title)

        # Entry in cache vecchia (scrapata prima del widget Version Status):
        # recuperiamo il blocco dal sito una sola volta, in background.
        if (not version_status
                and "version_status" not in data
                and not is_local
                and actual_url
                and actual_url not in self._version_status_backfill):
            self._version_status_backfill.add(actual_url)
            print(f"[VersionStatus] cached entry senza status per '{title}' — backfill in background")
            QTimer.singleShot(200, lambda t=title, u=actual_url: self._backfill_version_status_async(t, u))

        if meta_for_update:
            is_installed = True
            installed_version = meta_for_update.get('version', '')

            # Backfill della build per installazioni precedenti al tracking
            if not meta_for_update.get('build') and version_status.get('posted_build'):
                if installed_version and installed_version == web_version:
                    meta_for_update['build'] = version_status.get('posted_build')
                    meta_for_update['build_date'] = version_status.get('posted_build_date', '')
                    meta_for_update['build_inferred'] = True
                    self.game_launcher._save_meta(meta_for_update)

        self._version_status_current_title = title
        self._version_status_state = compute_version_state(
            installed_build=meta_for_update.get('build') if meta_for_update else None,
            posted_build=version_status.get('posted_build'),
            steam_build=version_status.get('steam_build'),
            installed_version=installed_version,
            posted_version_text=web_version,
            posted_version_only=version_status.get('posted_version', ''),
            is_installed=is_installed,
            has_status=bool(version_status),
        )
        update_available = self._version_status_state.get('state') == STATE_UPDATE_AVAILABLE

        if update_available and meta_for_update:
            meta_for_update['update_available'] = True
            meta_for_update['web_version'] = web_version

        # Se eravamo in "Update Check Mode", mostriamo il pop-up
        if self._update_check_game == title:
            state_now = self._version_status_state.get('state')
            installed_build = (meta_for_update or {}).get('build')
            posted_build = version_status.get('posted_build')
            if state_now == STATE_UPDATE_AVAILABLE:
                detail = ""
                if installed_build and posted_build:
                    detail = f"\n\nYour build: {installed_build}\nSteamRIP build: {posted_build}"
                else:
                    detail = f"\n\nYour version: {installed_version or '—'}\nWeb version: {web_version}"
                QMessageBox.information(self, "Update Available", f"Found an update for {title}!{detail}")
            elif state_now == STATE_STEAMRIP_OK_STEAM_AHEAD:
                QMessageBox.information(
                    self, "Up to date (SteamRIP)",
                    f"{title} is on the latest SteamRIP build"
                    + (f" (build {posted_build})." if posted_build else ".")
                    + "\n\nSteam has a newer build that SteamRIP hasn't released yet."
                )
            elif state_now == STATE_UPDATED:
                QMessageBox.information(
                    self, "Up to date",
                    f"{title} is up to date"
                    + (f" (build {posted_build})." if posted_build else ".")
                )
            else:
                QMessageBox.information(self, "Up to date", f"{title} is already up to date ({installed_version}).")
            self._update_check_game = None

        # Stampiamo il titolo in alto a sinistra
        self.lbl_game_title.setText(title)

        # --- INIZIO FIX COMPORTAMENTO TASTO PLAY MULTI-OS ---
        if is_installed:
            self.btn_play_main.show()
            
            meta = self.installed_games.get(title, {})
            
            # Disconnettiamo i vecchi slot per evitare esecuzioni multiple/doppie sui click
            try: self.btn_play_main.clicked.disconnect()
            except Exception: pass
            try: self.btn_steam_optional.clicked.disconnect()
            except Exception: pass

            # --- COMPORTAMENTO DIVISIONE OS ---
            if sys.platform == 'linux':
                # Su Linux l'integrazione Steam è considerata vitale (niente tasto opzionale separato)
                self.btn_steam_optional.hide()
                
                steam_id = meta.get('steam_shortcut_id', '')
                if steam_id and str(steam_id).strip():
                    self.btn_play_main.setText("▶ PLAY")
                    self.btn_play_main.setStyleSheet("") # Usa il verde globale dello stylesheet
                else:
                    self.btn_play_main.setText("➕ ADD TO STEAM")
                    neon_blue = self.app_config['theme'].get('neon_gradient_end', '#00d2ff')
                    self.btn_play_main.setStyleSheet(f"""
                        QPushButton#BtnPlayMain {{
                            background-color: {neon_blue};
                            color: #1a0f2b;
                            border: none;
                            border-radius: 6px;
                            font-size: 14px;
                            font-weight: bold;
                            padding: 8px 16px;   /* <-- Aggiunto padding */
                            min-width: 180px;    /* <-- FORZATURA LARGHEZZA MINIMA */
                        }}
                        QPushButton#BtnPlayMain:hover {{ background-color: white; }}
                    """)
                self.btn_play_main.clicked.connect(lambda checked, t=title: self.game_launcher.launch_game(t))
                
            else:
                # Su Windows il comportamento è responsive rispetto al file .exe impostato
                exe_path = meta.get('executable_path', '')
                steam_id = meta.get('steam_shortcut_id', '')
                
                # 1. Configurazione del Tasto Principale (Play / Set Executable)
                if exe_path and str(exe_path).strip():
                    self.btn_play_main.setText("▶ PLAY")
                    self.btn_play_main.setStyleSheet("") # Ripristina il verde standard
                else:
                    self.btn_play_main.setText("⚙ SET EXECUTABLE")
                    neon_pink = self.app_config['theme'].get('bright_neon_accent', '#ff00a0')
                    self.btn_play_main.setStyleSheet(f"""
                        QPushButton#BtnPlayMain {{
                            background-color: {neon_pink};
                            color: white;
                            border: none;
                            border-radius: 6px;
                            font-size: 13px;
                            font-weight: bold;
                            padding: 8px 16px;   /* <-- Aggiunto padding */
                            min-width: 180px;    /* <-- FORZATURA LARGHEZZA MINIMA */
                        }}
                        QPushButton#BtnPlayMain:hover {{ background-color: white; color: #1a0f2b; }}
                    """)
                # In entrambi i casi, launch_game gestisce nativamente sia la scelta del file che il lancio
                self.btn_play_main.clicked.connect(lambda checked, t=title: self.game_launcher.launch_game(t))
                
                # 2. Configurazione del Tasto Secondario Discreto (Add to Steam Opzionale)
                if steam_id and str(steam_id).strip():
                    # Se è già integrato in Steam, nascondiamo il tasto opzionale
                    self.btn_steam_optional.hide()
                else:
                    self.btn_steam_optional.setText("➕ Add to Steam (Optional)")
                    text_muted = self.app_config['theme'].get('text_muted', '#8a7fa4')
                    self.btn_steam_optional.setStyleSheet(f"""
                        QPushButton#BtnSteamOptional {{
                            background-color: transparent;
                            color: {text_muted};
                            border: 1px dashed {text_muted};
                            border-radius: 4px;
                            padding: 4px 10px;
                            font-size: 11px;
                            font-weight: normal;
                            margin-top: 4px;
                        }}
                        QPushButton#BtnSteamOptional:hover {{
                            color: white;
                            border: 1px solid white;
                            background-color: rgba(255, 255, 255, 0.05);
                        }}
                    """)
                    self.btn_steam_optional.show()
                    self.btn_steam_optional.clicked.connect(lambda checked, t=title: self.game_launcher._add_windows_game_to_steam_optional(t))
        else:
            self.btn_play_main.hide()
            if hasattr(self, 'btn_steam_optional'):
                self.btn_steam_optional.hide()
        # --- HOUR COUNTER: mostra il playtime se il gioco è installato ---
        if hasattr(self, 'lbl_playtime'):
            if is_installed:
                seconds = int(self.installed_games.get(title, {}).get('playtime_seconds', 0) or 0)
                mode = self.app_config.get('playtime', {}).get('display', 'hours')
                self.lbl_playtime.setText(f"⏱ {PlaytimeManager.format_playtime(seconds, mode)}")
                self.lbl_playtime.show()
            else:
                self.lbl_playtime.hide()
        # --- FINE FIX COMPORTAMENTO TASTO PLAY MULTI-OS ---
        
        # --- FIX STRUTTURA VERSIONE, MULTIPLAYER E STATUS ---
        desat_pink = "#cba6d1" # Rosa desaturato, elegante e non aggressivo
        
        mp_label_html = ""
        if data.get('mp_tag'):
            mp_label_html = f"<br><span style='color:{desat_pink};'>➕ {data['mp_tag']} Patch Included</span>"

        ribbon_html = ""
        if update_available:
            ribbon_html = f"<div class='ribbon'>UPDATE!</div>"
            item = self.lib_manager._find_item_by_title(title)
            if item:
                self.lib_manager._update_library_item(item, f"❗ {title}", "(update avail.)", title_color="#00ff88", sub_color="#00cc6a")

        # Assembliamo tutto nella label: riga 1 versione web, riga 2 (se c'è) la patch coop.
        # Lo stato (Updated / Update Pending / Update available) vive nella pill accanto.
        if is_local:
            # Giochi locali: niente versione web / aggiornamenti, mostriamo il badge "Local Game"
            local_color = self.app_config['theme'].get('text_secondary_pink', '#f3c6ff')
            self.lbl_game_version.setText(
                f"<span style='color:{local_color};'>Local Game — not on SteamRip</span>"
            )
        else:
            self.lbl_game_version.setText(f"<span style='color:{desat_pink};'>Web Version: {web_version}</span>{mp_label_html}")

        self._render_version_status(title, is_installed, is_local, web_version)
        self._update_age_badges(title, data)

        raw_desc = data['description']
        clean_desc = re.sub(r'<h[234][^>]*>.*?</h[234]>', '<h2>Game Description</h2>', raw_desc, count=1, flags=re.IGNORECASE)

        if self._is_gold_tier and self._gold_reason:
            gold_html = GoldTierStyle.why_gold_html(self._gold_reason)
            self.desc_display.setHtml(gold_html + clean_desc)
        else:
            self.desc_display.setHtml(clean_desc)
        self.info_display.setHtml(data['game_info'])
        self.req_display.setHtml(data['requirements'])
        _perf("html_render")

        if title in self.installed_games:
            self._update_desc_custom_save_path(self.installed_games[title])
        
        # --- CHECK CACHED SALE STATUS FOR COVER (avoids async JS race) ---
        cached_sale_active = False
        default_banner_text = None
        cached_prices_cover = self.master_catalog.get_prices(title)
        if isinstance(cached_prices_cover, dict):
            sale_platforms = []
            sale_discount = 0
            for store_name, store_key in [("Steam", "steam"), ("GOG", "gog")]:
                sd = cached_prices_cover.get(store_key, {})
                if isinstance(sd, dict) and sd.get("on_sale"):
                    cached_sale_active = True
                    sale_platforms.append(store_name)
                    sale_discount = max(sale_discount, sd.get("discount_percent", 0))
            if sale_platforms:
                default_banner_text = SaleStyle.sale_cover_banner_text(" & ".join(sale_platforms), sale_discount)

        # --- FIX: PRENDIAMO GLI URL DIRETTAMENTE DALLO SCRAPER ---
        if data.get('cover_urls'):
            self.cover_image.setGraphicsEffect(None)  # clear any previous sale glow
            if self._is_gold_tier:
                html_cover = GoldTierStyle.gold_cover_html(
                    data['cover_urls'],
                    self.app_config['theme']['background_dark_purple'],
                    ribbon_html,
                    sale_active=cached_sale_active,
                )
            else:
                def _js_quote(u):
                    return "'" + u.replace("\\", "\\\\").replace("'", "\\'") + "'"
                first_url = html.escape(data['cover_urls'][0], quote=True) if data['cover_urls'] else ""
                urls_array = [_js_quote(url) for url in data['cover_urls']]
                urls_array.append(_js_quote(CoverCache.placeholder_url()))
                urls_js = f"var urls = [{', '.join(urls_array)}];"

                # --- INIZIO FIX NEON DEFINITIVO E CONTENUTO ---
                neon_start = self.app_config['theme'].get('neon_gradient_start', '#8d0bb1')
                neon_end = self.app_config['theme'].get('neon_gradient_end', '#00d2ff')
                if cached_sale_active:
                    card_bg = SALE_GREEN
                    glow_bg = SALE_GREEN
                    glow_ext = "-3px"
                    glow_blur = "10px"
                else:
                    card_bg = f"linear-gradient(135deg, {neon_start} 0%, #ff00a0 50%, {neon_end} 100%)"
                    glow_bg = f"linear-gradient(135deg, {neon_start} 0%, #ff00a0 50%, {neon_end} 100%)"
                    glow_ext = "-4px"
                    glow_blur = "6px"

                html_cover = f"""
                <html>
                <head>
                <style>
                    body {{ 
                        margin: 0; padding: 0; 
                        background-color: transparent; 
                        display: flex; justify-content: center; align-items: center; 
                        overflow: hidden; height: 100vh;
                    }}
                    .card-wrapper {{
                        width: 85%;
                        height: 88%;
                        max-width: 240px;
                        max-height: 360px;
                        box-sizing: border-box;
                        position: relative;
                        border-radius: 10px;
                        /* Bordo Gradiente Fluido */
                        background: {card_bg};
                        padding: 3px; 
                    }}

                    /* Glow multicolore: div reale (il ::before sfocato faceva crashare il renderer) */
                    .cover-glow {{
                        position: absolute;
                        top: {glow_ext}; left: {glow_ext}; right: {glow_ext}; bottom: {glow_ext};
                        border-radius: 10px;
                        background: {glow_bg};
                        filter: blur({glow_blur});
                        z-index: 0;
                    }}

                    .cover-art {{
                        position: absolute;
                        top: 3px; left: 3px; right: 3px; bottom: 3px;
                        border-radius: 7px;
                        background-size: cover;
                        background-position: center center;
                        background-repeat: no-repeat;
                        background-color: {self.app_config['theme']['background_dark_purple']};
                        z-index: 1; /* Sopra il clone luminoso */
                    }}
                    .cover-preload {{ display: none; }}
                    
                    .ribbon {{ 
                        position: absolute; top: 25px; left: -35px; 
                        background: {self.app_config['theme']['bright_neon_accent']}; 
                        color: #fff; padding: 5px 40px; 
                        transform: rotate(-45deg); 
                        font-weight: bold; font-size: 14px; 
                        box-shadow: 0 2px 8px rgba(0,0,0,0.8); 
                        z-index: 10; letter-spacing: 1px; text-align: center; 
                    }}
                </style>
                <script>
                    {urls_js}
                    var currentIdx = 0;
                    function setCover(url) {{
                        var el = document.getElementById('coverArt');
                        if (el && url) el.style.backgroundImage = "url('" + String(url).replace(/'/g, "%27") + "')";
                    }}
                    function loadNext(img) {{
                        currentIdx++;
                        console.log('COVER: onerror fired for', img.src, '-> fallback', currentIdx);
                        if(currentIdx < urls.length) {{
                            console.log('COVER: trying fallback URL:', urls[currentIdx]);
                            img.src = urls[currentIdx];
                        }} else {{
                            console.log('COVER: no more fallback URLs');
                        }}
                    }}
                </script>
                </head>
                <body>
                    <div class="card-wrapper">
                        <div class="cover-glow"></div>
                        {ribbon_html}
                        <div class="cover-art" id="coverArt" style="background-image: url('{first_url}');"></div>
                        <img class="cover-preload" src="{first_url}" onload="setCover(this.src)" onerror="loadNext(this)" alt="">
                    </div>
                </body>
                </html>
                """
                # --- FINE FIX NEON DEFINITIVO E CONTENUTO ---
            
            self._display_cover_html(html_cover, default_banner_text)
            
        # --- DYNAMIC DIGITAL STORE LINKS HANDLING ---
        self.btn_steam_store.hide()
        self.btn_gog_store.hide()
        try:
            self.btn_steam_store.clicked.disconnect()
            self.btn_gog_store.clicked.disconnect()
        except:
            pass

        # 1. Controlliamo se il Master Catalog ha già store URLs
        store_urls = self.master_catalog.get_store_urls(title)
        if store_urls.get("steam") or store_urls.get("gog"):
            self._on_store_links_found(store_urls.get("steam", ""), store_urls.get("gog", ""), title)
        else:
            # 2. FIX CRASH: Nessun terminate()! Lasciamo che i vecchi worker finiscano in background.
            if not hasattr(self, '_active_store_workers'):
                self._active_store_workers = []
            
            # Pulizia automatica dei worker passati che hanno finito il loro lavoro
            self._active_store_workers = [w for w in self._active_store_workers if w.isRunning()]
                
            new_worker = StoreLinksWorker(title)
            new_worker.links_ready.connect(self._on_store_links_found)
            self._active_store_workers.append(new_worker) # Lo salviamo dalla Garbage Collection
            new_worker.start()

        # --- SALES WORKER (skip if prices are <24h fresh) ---
        self._active_sales_workers = [w for w in self._active_sales_workers if w.isRunning()]
        if self.master_catalog.is_prices_stale(title, region=self._price_region()):
            sales_worker = SalesWorker(
                title,
                steam_id=data.get("steam_id", ""),
                region=self._price_region(),
            )
            sales_worker.sale_data_ready.connect(self._on_sale_data_found)
            sales_worker.finished.connect(lambda w=sales_worker: self._cleanup_sales_worker(w))
            self._active_sales_workers.append(sales_worker)
            sales_worker.start()

        # Stash SGDB enrichment into Master Catalog — solo se abbiamo davvero
        # asset nuovi, altrimenti NON sovrascrivere quelli già in cache.
        sid = data.get("steam_id", "")
        sgdb = data.get("sgdb_assets", {}) or {}
        if any(sgdb.get(k) for k in ("grid", "hero", "logo", "wide")):
            self.master_catalog.set_sgdb_assets(
                title, sid or sgdb.get("steam_app_id", ""),
                grid_url=sgdb.get("grid", ""),
                hero_url=sgdb.get("hero", ""),
                logo_url=sgdb.get("logo", ""),
                wide_url=sgdb.get("wide", ""),
            )

        neon_start = self.app_config['theme'].get('neon_gradient_start', '#8d0bb1')
        neon_end = self.app_config['theme'].get('neon_gradient_end', '#00d2ff')
        
        main_accent = self.app_config['theme'].get('main_accent_color', '#733d91')
        bright_neon = self.app_config['theme'].get('bright_neon_accent', '#ff00a0')
        has_shown_update_divider = False
        
        for link in data['links']:
            if link.get('is_update') and not has_shown_update_divider:
                has_shown_update_divider = True
                div_lbl = QLabel("📦 UPDATES & PATCHES")
                div_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                div_lbl.setStyleSheet(f"color: {main_accent}; font-weight: bold; font-size: 14px; margin-top: 15px; margin-bottom: 5px; border-bottom: 1px solid {bright_neon}; padding-bottom: 4px;")
                self.links_container.addWidget(div_lbl)

            if link.get('is_update') and link.get('update_version'):
                ver_lbl = QLabel(link['update_version'])
                ver_lbl.setWordWrap(True)
                ver_lbl.setStyleSheet(f"color: {self.app_config['theme'].get('text_secondary_pink', '#f3c6ff')}; font-size: 12px; margin-top: 5px; margin-left: 8px;")
                self.links_container.addWidget(ver_lbl)

            btn = QPushButton(f"  🔗  {link['name']}")
            self._theme.apply_download_button(btn, self._is_gold_tier, cached_sale_active)
            btn.clicked.connect(lambda checked, l=link: self.open_download_gate(l))
            self.links_container.addWidget(btn)

        # --- STRUCTURAL THEME ROUTING ---
        if self._is_gold_tier and cached_sale_active:
            self._theme.apply_gold_sale()
        elif self._is_gold_tier:
            self._theme.apply_gold()
        elif cached_sale_active:
            self._theme.apply_sale()
        else:
            self._theme.apply_default()
        _perf("theme_routing")

        # --- RE-APPLY CACHED SALE VISUALS (no re-fetch needed) ---
        cached_prices = self.master_catalog.get_prices(title)
        if isinstance(cached_prices, dict):
            cached_steam = cached_prices.get("steam", {})
            cached_gog = cached_prices.get("gog", {})
            cached_platforms = []
            cached_discount = 0
            if isinstance(cached_steam, dict) and cached_steam.get("on_sale"):
                cached_platforms.append("Steam")
                cached_discount = max(cached_discount, cached_steam.get("discount_percent", 0))
            if isinstance(cached_gog, dict) and cached_gog.get("on_sale"):
                cached_platforms.append("GOG")
                cached_discount = max(cached_discount, cached_gog.get("discount_percent", 0))
            if cached_platforms:
                platform_str = " & ".join(cached_platforms)
                sale_badge = SaleStyle.sale_badge_html(platform_str, cached_discount)
                current_ver = self.lbl_game_version.text()
                self.lbl_game_version.setText(f"{current_ver}  |  {sale_badge}")
                if not self._is_gold_tier:
                    glow = QGraphicsDropShadowEffect(self)
                    glow.setBlurRadius(20)
                    glow.setColor(QColor(SALE_GREEN))
                    glow.setOffset(0, 0)
                    self.cover_image.setGraphicsEffect(glow)
                if isinstance(cached_steam, dict) and cached_steam.get("on_sale"):
                    self._theme.apply_store_sale_glow(self.btn_steam_store)
                if isinstance(cached_gog, dict) and cached_gog.get("on_sale"):
                    self._theme.apply_store_sale_glow(self.btn_gog_store)

        _perf("end")
        if os.environ.get("SRP_PROFILE") and _perf_marks:
            prev = 0.0
            parts = []
            for label, ms in _perf_marks:
                parts.append(f"{label}={ms - prev:.0f}ms")
                prev = ms
            print("[Perf] display_game_data " + " ".join(parts) + f" total={prev:.0f}ms")

    def _on_store_links_found(self, steam_url, gog_url, game_title):
        """Asynchronously updates store shortcut buttons and persists to Master Catalog."""
        self.master_catalog.set_store_urls(game_title, steam_url, gog_url)
        # Aggiorniamo i bottoni SOLO SE l'utente sta ancora guardando la scheda di questo specifico gioco
        current_title = self.current_game_data.get('title')
        if current_title == game_title:
            # Only show Steam button for a real /app/{id} page, not a search URL
            has_real_steam = bool(re.search(r'/app/\d+', steam_url)) if steam_url else False
            if has_real_steam:
                self.btn_steam_store.show()
                self.btn_steam_store.clicked.connect(
                    lambda: self._open_store_link(steam_url, "steam", game_title)
                )
            if gog_url:
                self.btn_gog_store.show()
                self.btn_gog_store.clicked.connect(
                    lambda: self._open_store_link(gog_url, "gog", game_title)
                )

    def _open_store_link(self, url, store, title):
        self.events.emit_event(
            EVENT_STORE_OPENED,
            title=title or '',
            store=store,
            url=url or '',
        )
        QDesktopServices.openUrl(QUrl(url))

    def _cleanup_sales_worker(self, worker):
        if worker in self._active_sales_workers:
            self._active_sales_workers.remove(worker)

    def _cleanup_sgdb_worker(self, worker):
        title = getattr(worker, "game_title", None)
        if title:
            self._sgdb_inflight_titles.discard(title)
        if worker in self._active_sgdb_workers:
            self._active_sgdb_workers.remove(worker)

    def needs_sgdb_fetch(self, title):
        """True se conviene interrogare SteamGridDB per questo titolo."""
        assets = self.master_catalog.get_sgdb_assets(title)
        if isinstance(assets, dict) and assets.get("grid"):
            return False
        if self.master_catalog.is_sgdb_checked_recently(title):
            return False
        if title in self._sgdb_inflight_titles:
            return False
        return True

    def request_sgdb_assets(self, title):
        if not self.needs_sgdb_fetch(title):
            return
        from network.sgdb_worker import SGDBWorker
        print(f"[SGDB] Fetching assets for '{title}'")
        self._sgdb_inflight_titles.add(title)
        worker = SGDBWorker(title)
        worker.sgdb_finished.connect(self._on_sgdb_assets_ready)
        worker.finished.connect(lambda w=worker: self._cleanup_sgdb_worker(w))
        self._active_sgdb_workers.append(worker)
        worker.start()

    def _price_region(self):
        return str((self.app_config.get("stats", {}) or {}).get("region") or "US").upper()

    def _on_sale_data_found(self, steam_data, gog_data, game_title):
        print(f"[SaleData] called for '{game_title}', steam_on_sale={steam_data.get('on_sale')}, gog_on_sale={gog_data.get('on_sale')}, steam_has_url={'url' in steam_data}, gog_has_url={'url' in gog_data}")
        region = self._price_region()
        self.master_catalog.set_prices(game_title, steam_data, gog_data, region=region)
        self.events.emit_event(
            EVENT_PRICE_REFRESHED,
            title=game_title,
            region=region,
            steam=steam_data,
            gog=gog_data,
            steam_on_sale=bool(steam_data.get("on_sale")),
            gog_on_sale=bool(gog_data.get("on_sale")),
            discount_percent=max(
                int(steam_data.get("discount_percent") or 0),
                int(gog_data.get("discount_percent") or 0),
            ),
        )
        current_title = self.current_game_data.get('title')
        if current_title != game_title:
            print(f"[SaleData] SKIP: current_title='{current_title}' != game_title='{game_title}'")
            return

        platforms = []
        discount = 0
        if steam_data.get("on_sale"):
            platforms.append("Steam")
            discount = max(discount, steam_data.get("discount_percent", 0))
        if gog_data.get("on_sale"):
            platforms.append("GOG")
            discount = max(discount, gog_data.get("discount_percent", 0))

        if not platforms:
            print(f"[SaleData] platforms empty for '{game_title}' — no sale badge shown")
            self._hide_sale_banner()
            if self._is_gold_tier:
                self._theme.apply_gold()
            else:
                self._theme.apply_default()
            return

        platform_str = " & ".join(platforms)
        print(f"[SaleData] platforms={platforms}, discount={discount}")

        # --- STRUCTURAL THEME (live sale data) ---
        if self._is_gold_tier:
            self._theme.apply_gold_sale()
        else:
            self._theme.apply_sale()

        # Per-platform store button glow (on top of base theme)
        if steam_data.get("on_sale"):
            print(f"[SaleData] Applying STEAM sale glow/style for '{game_title}'")
            self._theme.apply_store_sale_glow(self.btn_steam_store)
        if gog_data.get("on_sale"):
            print(f"[SaleData] Applying GOG sale glow/style for '{game_title}'")
            self._theme.apply_store_sale_glow(self.btn_gog_store)

        banner_text = SaleStyle.sale_cover_banner_text(platform_str, discount)
        self._show_sale_banner(banner_text)

        if not self._is_gold_tier:
            self.lbl_game_title.setStyleSheet(SaleStyle.sale_title_style())

        current_ver = self.lbl_game_version.text()
        sale_badge = SaleStyle.sale_badge_html(platform_str, discount)
        print(f"[SaleData] setting version label: '{current_ver}' + badge '{sale_badge}'")
        self.lbl_game_version.setText(f"{current_ver}  |  {sale_badge}")

        desc_html = self.desc_display.toHtml()
        green_desc = SaleStyle.greenify_headings(desc_html)
        self.desc_display.setHtml(green_desc)

        # --- COVER CARD SALE VISUAL ---
        if self._is_gold_tier:
            self.cover_image.page().runJavaScript(
                "document.querySelector('.card-wrapper').classList.add('sale-active');"
            )
        else:
            glow = QGraphicsDropShadowEffect(self)
            glow.setBlurRadius(20)
            glow.setColor(QColor(SALE_GREEN))
            glow.setOffset(0, 0)
            self.cover_image.setGraphicsEffect(glow)

    def _display_cover_html(self, html, sale_banner_text=None):
        self._pending_banner = sale_banner_text
        title = self.current_game_data.get("title", "cover")
        self._cover_fallback_title = title
        self._cover_fallback_html = self._build_cover_fallback_html(html)
        self._cover_fallback_used = False
        page_path = CoverCache.cover_page_url(html, title)
        self.cover_image.setUrl(QUrl.fromLocalFile(page_path))

    def _build_cover_fallback_html(self, html):
        """
        Variante senza filter:blur (glow multicolore via box-shadow multiplo).
        Usata solo se il renderer rifiuta la pagina con il blur.
        """
        try:
            fallback = re.sub(r"\s*\.cover-glow\s*\{.*?\}", "", html, flags=re.S)
            fallback = re.sub(r'\s*<div class="cover-glow"></div>', "", fallback)
            if getattr(self, "_is_gold_tier", False):
                shadows = "box-shadow: 0 0 18px 3px #FFD700, 0 0 28px 6px #22c55e;"
            else:
                shadows = ("box-shadow: -8px 0 22px 2px #ff00a0, 8px 0 22px 2px #00d2ff, "
                           "0 0 18px 2px #8d0bb1;")
            if ".card-wrapper {" in fallback:
                fallback = fallback.replace(".card-wrapper {", ".card-wrapper { " + shadows, 1)
            return fallback
        except Exception as e:
            print(f"[Cover] fallback build failed: {e}")
            return ""

    def _on_cover_page_loaded(self, ok):
        if not ok and not self._cover_fallback_used and self._cover_fallback_html:
            self._cover_fallback_used = True
            print("[Cover] renderer load failed — retrying without blur glow")
            try:
                page_path = CoverCache.cover_page_url(
                    self._cover_fallback_html,
                    self._cover_fallback_title + "_noblur",
                )
                self.cover_image.setUrl(QUrl.fromLocalFile(page_path))
            except Exception as e:
                print(f"[Cover] fallback load failed: {e}")
            return
        if self._pending_banner:
            self._show_sale_banner(self._pending_banner)
        else:
            self._hide_sale_banner()
        self._pending_banner = None

    def _show_sale_banner(self, text):
        escaped = text.replace("\\", "\\\\").replace("'", "\\'")
        js = (
            "(function(){"
            "var e=document.querySelector('.card-wrapper > .sale-banner');"
            "if(e)e.remove();"
            "var b=document.createElement('div');"
            "b.className='sale-banner';"
            "b.textContent='" + escaped + "';"
            "b.style.cssText='position:absolute; left:4px; right:4px; bottom:64px; background:rgba(0,255,136,0.85); color:#000; padding:4px 8px; font-weight:bold; font-size:12px; text-align:center; z-index:12; border-radius:4px; pointer-events:none;';"
            "var cw=document.querySelector('.card-wrapper');"
            "if(cw)cw.appendChild(b);"
            "})()"
        )
        self.cover_image.page().runJavaScript(js)

    def _hide_sale_banner(self):
        js = "(function(){var e=document.querySelector('.card-wrapper > .sale-banner');if(e)e.remove();})()"
        self.cover_image.page().runJavaScript(js)

    def clear_links(self):
        while self.links_container.count():
            child = self.links_container.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        self._hide_sale_banner()
