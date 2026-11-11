# Changelog

All notable changes to **Ae Thicc Launcher** are documented here.

## v1.9.1 — 2026-11-11

### Fixed
- On Windows, a game that already has a Steam shortcut is now launched **through Steam** (`steam://rungameid/<id>`) instead of starting the executable directly, so the Steam overlay, Steam Game Recording and playtime tracking work again. Games without a Steam shortcut still launch directly.
- If `steam_shortcut_id` is missing but the shortcut still exists in `shortcuts.vdf`, the launcher now restores the ID from Steam before launching, instead of silently starting the game outside Steam.

## v1.9.0 — 2026-11-11

### Open source & API credentials (BYOK)
- The launcher is now **open source**.
- No API keys are bundled. SteamGridDB and IGDB are **BYOK** (Bring Your Own Key), configurable in the first-run wizard and in **Settings → API Keys**, with Test/Remove actions and optional Skip.
- Credentials are stored in the OS keychain (Windows Credential Manager, Secret Service on Linux) with an encrypted fallback file. They are never written to `config.json` or logs. `AETHICC_SGDB_KEY` / `AETHICC_IGDB_*` environment variables override them for development.
- The app works without keys: IGDB rows are hidden with a one-time banner and SteamGridDB cover lookups are skipped.
- `keyring` added to the requirements; the Flatpak manifest gets the Secret Service permission.

### First-run wizard & configurable data root
- New first-run setup wizard: choose the data/cache folder, one or more game libraries and a default library, plus temp and savegame folders. It can be re-run from **Settings → Run setup wizard**.
- The whole runtime footprint (config, catalog, covers, stats, logs, temp) follows a configurable **data root**. Defaults remain the historical per-user AppData/XDG folders; a bootstrap `aethicc.paths.json` records a non-default root. When the install folder is read-only (Flatpak `/app`, `Program Files`) the bootstrap is written to the user data directory.
- Optional, non-destructive import of existing data from the legacy platform folder.
- "Watched Folders" is now presented as **Game Libraries**; the install destination dialog preselects the default library, shows free space and can create a new library on another drive.
- Windows build switched to **PyInstaller `--onedir`** with an onedir zip and an **Inno Setup** installer (`Ae_Thicc_Launcher_Setup.exe`). The installer puts the app in the standard Programs folder (`C:\Program Files\AeThiccLauncher`, UAC) and writes only the application folder; data and games stay in AppData/`C:\SteamRip` (or wherever the wizard points them), so uninstalling removes the app only.
- Onboarding defaults: data/cache in `%LOCALAPPDATA%\AeThiccLauncher`, game library in `C:\SteamRip` (Windows) / `~/SteamRip` (Linux).
- The executable is now **`Ae_Thicc_Launcher.exe`**; the Windows AppUserModelID and the SteamGridDB user agent no longer carry the old project name.

### Data seed and hidden support folders
- `data/`, `bin/`, `.media/`, `_internal/` and `lists/` are hidden in the installer and treated as seeds only. At startup their contents are copied into the data root chosen by the wizard; runtime reads use only the data root (`data/` for definitions/icons/achievement packs, `local_dbs/` for the posts index and gold-tier list).
- Seed updates use a hash state: unmodified shipped files are refreshed, user-edited files are preserved; achievement packs are add-only.
- The launcher no longer reads runtime data from the application folder.

### Performance
- The Gold Tier list no longer performs a synchronous network HEAD on the GUI thread at startup (could block ~10s when GitHub was unreachable). It loads the local/seed list immediately and refreshes in a background thread.
- Heavy startup work (library scan, stats refresh, discovery, catalog fetch) now runs after the window is visible.
- Changing theme no longer re-applies the whole app once per colour editor (25s → ~1-2s): the settings dialog blocks editor signals and coalesces previews.
- Startup phase timings are logged to `launcher.log` (`[STARTUP]`).

### Network resilience
- Background network failures now show a dismissible banner in Discovery instead of a misleading "task failed" panel; the message mentions antivirus/firewall exceptions.
- README documents the Malwarebytes website block on `steamrip.com` (website reputation, not a file detection).

## v1.8.0 — 2026-11-10

### Stats foundations (S0)
- Session, install, usage and price history are recorded locally as JSONL, deduplicated by id and safe across crashes.
- Game launches write a recovery marker; a session interrupted by a launcher crash is closed on the next start.
- Steam `LastPlayed` and `Playtime2wks` are imported from `localconfig.vdf` and cached for Stats.
- Prices now carry integer cents, currency and region; history keeps at most one variation per title/store/region/day.
- Usage events cover hoster choice, download resume, in-place updates, trailers, store pages and orphan recovery.
- New `StateBackupManager` skeleton merges launcher state (sessions, history, achievements, prefs) from the save sync folder.

### Stats engine (S1)
- New `StatsManager` snapshot: tracked/imported hours, session stats, streaks, value per hour (official price ÷ played hours), library value, per-game, developers, publishers, genres, timeline and finance breakdowns.
- Background IGDB batch backfill for the installed library, prioritised by playtime (one batch per launch), with negative caching only for titles actually queried.
- Steam `LastPlayed`/`Playtime2wks` and price history feed the snapshot; region changes select the matching price data.

### Stats tab (S2)
- New **Stats** tab: hero cards, hours timeline and `30d / 90d / 365d / All` + region selectors.
- Interactive **per-game browser**: scrollable clickable bar list, selected-game cover + metrics on the right and a selected-game hours chart filling the lower info panel.
- The bar list primary value follows the active ordering: playtime, value per hour, sessions or last-played recency. Secondary values move to the line above the bar.
- Library toolbar with persisted ordering (`Installed first`, playtime, value per hour, sessions, last played, title) and a `Hide non-installed games` filter.
- Selected rows use a subtle left accent and tint instead of a full highlight box; bar labels sit at the bar end or immediately after short bars, never outside the widget.
- A top accent-coloured advice banner rotates automatically every 20 seconds and regenerates on `New line`. It follows the selected game.
- `Value per hour` replaces the old "cost" wording: it is the official price divided by played hours, not an amount the user actually paid.
- Cover thumbnails reuse `MiniCoverManager`; when nothing is cached the panel shows a neutral `NO IMAGE` stub.
- A subtle `PER GAME` divider separates global stats from the per-game section; the ethical `BUY THE GAME` footer closes the page like the Explorer.
- `Overview | Badges` segmented control; the Badges page is a placeholder for S4/S5.
- Phrase engine reads `data/stats_phrases.json`, rotates lines, resolves safe placeholders and follows the selected game.
- Nerdy phrase pack: hours-per-buck and cinema-multiple value lines, Portal/LOTR/Skyrim/Elden Ring time comparisons, backlog/copies/hardware math and library-wide lines even without a selected game. Reference numbers and approximate USD conversion live in the file's `refs` block.
- Stats runs on real launcher data only: no demo/stub dataset is shipped or loaded.

### Charts (S3)
- New separate **Charts** view (`Overview | Charts | Badges`) so the Overview and per-game browser stay uncluttered.
- 52-week activity heatmap with date/hours tooltips.
- Hours by time of day and by weekday; session-length histogram.
- Genre donut with legend and percentages; top developers by hours; best value-per-hour ranking.
- Temporal charts follow `30d / 90d / 365d / All`; the heatmap stays at the last 52 weeks, genre/developer/value rankings are all-time.
- All custom `QPainter`; no new runtime dependencies.

### Launcher achievements (S4/S5)
- 40 generic launcher achievements, data-driven from `data/launcher_achievements.json` with sliced icons in `data/achievements/`.
- Themed packs load automatically from `data/achievement_packs/*.json`; adding a normal achievement means adding a JSON entry, not engine code.
- Curated title/franchise lists in `data/achievement_titles.json` (Half-Life, Portal, Quake, DOOM, Elder Scrolls, Fallout, Civilization and more).
- Trigger support: stat thresholds, event + `where` conditions, `all`/`any`, title/franchise/list matching.
- Badges view with `All / Unlocked / Locked` filters, graphs of progress for numeric goals, hidden `???` entries, categories and pack labels.
- Clicking a badge opens a full-window dimmed overlay with a large glowing icon, status/date and progress; click outside the card or press Esc to close.
- Completed achievements automatically move above locked ones; fixed-size cards keep the grid aligned.
- Retroactive evaluation on startup, idempotent unlocks and state persisted in `achievements.json`; the Reset action was removed to avoid accidental progress loss.
- Toast notifications: individual unlocks, plus one summary card instead of a toast storm when many historical achievements unlock together.
- The achievement check intentionally ignores the future wallet blacklist, so themed content can still exist for blacklisted publishers.
- Badges are now a top-level **Achievements** tab next to **Stats**; Stats keeps the `Overview | Charts` segmented control.
- The main tab bar uses the same segmented pill style as the Stats sub-tabs instead of the old folder-like tabs.

### Downloads & Steam
- Pending-extraction recovery no longer prompts for an archive whose game is already extracted and installed; partial/interrupted extractions stay recoverable thanks to an in-progress marker.
- Incomplete downloads are detected by checking the archive headers: truncated files are not offered for extraction, are marked so the check is not repeated, and remain on disk so a new download can resume them. Completed downloads are validated before extraction too.
- The incomplete-download dialog shows which provider/hoster produced the partial file and offers to open the game page directly in Explorer (or a clean title search when the source URL is missing).
- Download queue entries are deduplicated by archive path: re-adding the same game refreshes the existing row instead of creating a duplicate, and the provider is shown in the queue row.
- Provider changes are handled safely: a partial file from a different provider is restarted from scratch instead of being resumed and corrupted.
- Failed downloads with a partial file stay paused in the queue and can be resumed with Play, instead of being cleared and rediscovered as orphan archives.
- Steam restart/shutdown now resolves the real `steam.exe` on Windows and detects `steam.exe` as a running Steam process; the batch result says when Steam could not be restarted automatically.
- New `Library → Add All to Steam Library` action (Windows) to create shortcuts for every installed game in one batch.

### Notes
- Launcher-state backup/restore wiring and its Settings toggle are deferred to 1.8.1; the tested `StateBackupManager` core is present but not yet connected to the app.

## v1.7.0 — 2026-11-09

### Architecture
- Split the `AppWindow` god object (3,000+ lines) into a 9-line facade, a shell and eight cohesive controllers. New features no longer have a single file to fall back into.
- New `AppContext`, typed event bus, JSONL store, bundled/user data resolver and price utilities.
- Deep-merged configuration, centralized Flatpak detection and a rotating `launcher.log`.
- 29 unit tests with the standard library; no new runtime dependencies.

### Explorer
- Cover art now fits its panel: content-hashed cover pages (no stale QtWebEngine page), viewport-relative card sizing and background-based rendering.
- Explorer prefers vertical SGDB/IGDB box art over the horizontal SteamRIP featured image.
- Savegame thumbnails: fixed Windows `file://` handling; missing minicovers are fetched lazily from catalog/SGDB.
- Detail-sheet covers are promoted to the mini cover cache; the scaled-cover cache is invalidated when a cover is upgraded.

### Discovery
- Search Results now trigger cover downloads. The on-the-fly search cards are no longer looked up in the loaded rows, where they never existed.
- Horizontal fallback covers are upgraded to vertical IGDB/SGDB art and the visible card updates immediately.

### Trailers
- Fixed the player reusing the previous game's local MP4 when opening another title.
- Thin determinate progress line while a trailer is prepared.
- English UI strings; a failed download no longer leaves the player stuck on "Preparing…".

### Build
- Local Flatpak builds now copy `data/` (definitions, indexes) like the CI build.
- Saving state and history is prepared for the upcoming Stats/Achievements work.

## v1.6.1 — 2026-11-09

### Discovery
- New **Recently Added** row: the newest SteamRip arrivals, in the same order as the site.
- **Search the whole catalog:** from two characters the search row now matches every title in the master catalog (5,000+), not only the cards already loaded.
- New **All Games** row: the entire catalog in one endless row, newest SteamRip releases first and the rest alphabetical.
- **Covers load while you scroll:** rows fetch artwork for the cards that come into view, use the SteamRip page image as a fallback and fall back to a SteamGridDB title lookup for games without a Steam app id.
- **Sort where it makes sense:** a dropdown orders Trending and My Library by title, SteamRip added date, release date or rating. Date-based rows (New Releases, Recently Added/Updated, the SteamRIP categories and All Games) always keep their own order.
- **Calmer scrolling:** a 6 px dead zone before a drag starts, a cap on the flick speed, and touchpads no longer jump a row with tiny scrolls.
- Hover feedback returns as soon as a drag or scroll settles.

## v1.6.0 — 2026-11-08

### Discovery
- Rebuilt the Discovery tab as a single-row shelf: the category name sits in the lead-in next to the first card, four cards are visible at rest and the row scrolls infinitely to the right (the carousel wrap is gone).
- New search experience: from two characters the categories are replaced by a single aggregated **Search Results** row, deduplicated across every category and sorted by title; clearing the field restores the category you were on.
- Compact header with a large search field and a labelled **Go to** category selector.
- Hover feedback replaced: the old purple glow is gone (it was intermittent by design); hovering now lifts the card, zooms the cover ~8% and brightens the title.

### Game detail popup
- Click a card to open a detail sheet at 60% of the window: clean cover, IGDB metadata (year, rating, genres, themes, game modes, developer/publisher, platforms), IGDB summary or SteamRIP description, SteamRIP version vs Steam build status and the SteamRIP category tags.
- **Install (Open in Games Explorer)** hands the game over to the Explorer with the download links, reusing the same session cache.
- Data is lazy and cached: IGDB details in `igdb_details.json`, big covers in `detail_covers/`, SteamRIP page in the existing page cache. IGDB lookups now fall back to a strict name match when a title has no Steam appid.

### Trailer player
- In-app playback with immersive fullscreen (panel fills the window; ESC exits), volume slider and mute, persisted across restarts.
- The trailer button is always available: Steam stream → IGDB YouTube video (opens the default browser) → YouTube search.
- Seeking finally works: QtMultimedia cannot resume Steam's HLS stream after a seek, so the trailer is remuxed once to a local MP4 and played from disk (`bin/ffmpeg.zip` on Windows, extracted on first use; system `ffmpeg` on Linux). Files are cached under `<cache>/trailers` with an 800 MB cap.
- Closing the trailer only pauses and rewinds (the old synchronous teardown of the HLS pipeline could freeze the app).

### Performance
- Cache-first Discovery startup: ~6x less CPU after launch, no more 10-second freeze.
- Discovery cache now stores only reconstructible data (5.4 MB → ~0.5 MB); the debounced flush went from ~150 ms to negligible.
- Rows are built progressively and stop at 40 cards, growing 10 at a time while scrolling; rebuilding a scrolled row only creates the cards needed to cover the visible area.
- Drag updates are throttled (~120 Hz) and the hover glow no longer re-renders cards offscreen.
- Cover window and IGDB enrichment limited to the visible row and its first cards; cover jobs retry up to three times instead of giving up forever.
- Metadata batches use a title→card index instead of scanning every card, and `master_catalog.json` is saved compact.
- The background update scanner now starts 60 s after launch (it uses QtWebEngine on the GUI thread).

### Fixes
- Fixed a startup crash caused by `print()` of emoji titles/badges on a non-UTF-8 console (PyQt aborts on slot exceptions). `stdout`/`stderr` are now UTF-8 with `errors="replace"` and unexpected exceptions are written to `crash.log`.
- Fixed IGDB genre/theme/game-mode names being stored as raw ids (`31 · 32 · 1`) because the taxonomy lookup used int keys for a JSON with string keys.
- Fixed missing covers on a fresh install (cover jobs now resolve the catalog entry even when the slim cache has no per-card metadata).
- Stable per-row matching: the whole row is no longer re-matched on every page; empty pages stop after five attempts.
- Fixed the "seek or close the player freezes the video" behaviour and the click-through that could open a card behind the popup.
