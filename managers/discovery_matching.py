"""Pure matching rules for the Discovery rows.

This module owns the strategy Discovery has always used to map IGDB games and
SteamRip posts to catalog titles:

1. SteamRip posts match by page URL, then by exact normalized title, then with
   an appid when one is present;
2. IGDB games match by Steam appid, exact normalized title, then a fuzzy
   containment scan.

``build_index`` is pure. ``match_post`` and ``match_game`` read the shared
fuzzy cache: ``match_game`` stores misses there and ``match_post`` delegates to
it, so both mutate the ``cache`` they receive. ``build_cards`` is a
transformation with explicit effects: it reads the catalog and the cover
provider, and it writes fuzzy results into the ``cache`` dict it receives. The cache is deliberately shared with the caller
instead of copied, because it avoids rescanning the cleaned title list on every
page; it is cleared by the caller whenever the catalog token changes.

The strategy is intentionally unchanged. Fuzzy containment is permissive (for
example "DOOM" matches "DOOM 3"); that is current behaviour, recorded by the
characterization tests and not "fixed" here.
"""
from __future__ import annotations

from core import titles


def build_index(catalog) -> dict:
    """Build the appid/exact/cleaned/url lookup for one catalog snapshot.

    ``DiscoveryServices.catalog`` may legitimately return ``None`` before the
    catalog is ready: the empty index keeps matching controlled instead of
    raising on ``None.keys()``.
    """
    if catalog is None:
        return {"appid": {}, "exact": {}, "cleaned": [], "urls": {}}
    appid_index = {}
    exact = {}
    cleaned = []
    urls = {}
    for title in catalog.keys():
        entry = catalog.get_entry(title)
        clean = titles.normalize_title(title)
        if clean and clean not in exact:
            exact[clean] = title
        if isinstance(entry, dict):
            sid = str(entry.get("steam_id") or "").strip()
            if not sid:
                sgdb = entry.get("sgdb_assets")
                if isinstance(sgdb, dict):
                    sid = str(sgdb.get("steam_app_id") or "").strip()
            if sid.isdigit() and sid not in appid_index:
                appid_index[sid] = title
            raw = titles.normalize_title(str(entry.get("raw_title") or ""))
            if raw and raw not in exact:
                exact[raw] = title
            url = (entry.get("url") or "").rstrip("/")
            if url and url not in urls:
                urls[url] = title
        if clean:
            cleaned.append((clean, title))
    return {
        "appid": appid_index,
        "exact": exact,
        "cleaned": cleaned,
        "urls": urls,
    }


def match_post(post, index, cache) -> str | None:
    """Match a SteamRip post to a catalog title (URL before title)."""
    url = (post.get("url") or "").rstrip("/")
    if url and url in index["urls"]:
        return index["urls"][url]
    clean = titles.normalize_title(post.get("title") or "")
    if clean and clean in index["exact"]:
        return index["exact"][clean]
    return match_game({"name": post.get("title") or "", "steam_appid": ""},
                      index, cache)


def match_game(game, index, cache) -> str | None:
    """Match an IGDB game to a catalog title, memoizing fuzzy misses in cache."""
    sid = str(game.get("steam_appid") or "").strip()
    if sid and sid in index["appid"]:
        return index["appid"][sid]

    name = str(game.get("name") or "")
    clean = titles.normalize_title(name)
    if not clean:
        return None
    if clean in index["exact"]:
        return index["exact"][clean]

    cached = cache.get(clean, "__miss__")
    if cached != "__miss__":
        return cached or None

    best = None
    best_score = 0
    for candidate, title in index["cleaned"]:
        if len(candidate) < 3:
            continue
        if clean in candidate:
            score = len(clean)
        elif candidate in clean:
            score = len(candidate)
        else:
            continue
        if score > best_score:
            best = title
            best_score = score
    cache[clean] = best or ""
    return best


def build_cards(games, index, cache, catalog, cover_for,
                source: str = "igdb") -> list[dict]:
    """Turn raw posts/games into Discovery cards.

    Side effects: ``cache`` receives fuzzy results; ``catalog.get_entry`` and
    ``cover_for`` are called for every match; no input list is modified.
    """
    matched = []
    seen = set()
    for game in games:
        if source == "steamrip":
            title = match_post(game, index, cache)
            igdb_meta = {}
            steamrip_categories = list(game.get("categories") or [])
            post_url = (game.get("url") or "").rstrip("/")
        else:
            title = match_game(game, index, cache)
            igdb_meta = game
            steamrip_categories = []
            post_url = ""
        if not title or title in seen:
            continue
        seen.add(title)
        entry = catalog.get_entry(title) if catalog else None
        cover = cover_for(title)
        if isinstance(entry, dict) and entry.get("steamrip_categories"):
            steamrip_categories = sorted(
                set(steamrip_categories) | set(entry["steamrip_categories"]))
        matched.append({
            "title": title,
            "cover_path": cover,
            "meta": dict(entry) if isinstance(entry, dict) else {},
            "igdb": igdb_meta,
            "steamrip_categories": steamrip_categories,
            "url": post_url,
            "source": source,
        })
    return matched
