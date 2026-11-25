"""WebEngine profile helpers (P2-6).

The default profile is used by the hidden scraper because Cloudflare clearance
lives in its cookie store. Cover views get a separate off-the-record profile with
a memory cache, so they never share cookies with the scraper and never write
their cache to disk. The properties are logged at startup instead of assuming
Qt's defaults, because they differ between Qt versions and platforms.
"""
from __future__ import annotations

import logging

from PyQt6.QtWebEngineCore import QWebEngineProfile

log = logging.getLogger(__name__)


def configure_media_profile(profile):
    """Memory cache and no persistent cookies for cover/trailer views."""
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
    profile.setPersistentCookiesPolicy(
        QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
    )
    return profile


def describe_profile(profile) -> dict:
    """Runtime facts about a profile, for logs and diagnostics."""
    if profile is None:
        return {}
    return {
        "off_the_record": bool(profile.isOffTheRecord()),
        "storage_name": str(profile.storageName() or ""),
        "persistent_storage": str(profile.persistentStoragePath() or ""),
        "cookies": profile.persistentCookiesPolicy().name,
    }
