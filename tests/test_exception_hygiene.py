"""Exception hygiene ratchet (P2-5).

Rules enforced by AST:
- no bare ``except:``;
- ``except Exception: pass`` requires a ``# best-effort: <reason>`` comment;
- the number of broad handlers per file may not grow. The baseline below is the
  count after the P2-5 review; lower it when handlers are narrowed or removed,
  never raise it to make a new catch pass review.

The baseline is deliberately per file: a new broad handler in a persistence or
install module is a deliberate decision, not a side effect of touching a file.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYERS = ("core", "managers", "network", "ui")

# Counts of ``except Exception`` per file after the P2-5 review.
BASELINE = {
    "core/config.py": 2,
    "core/cover_cache.py": 4,
    "core/events.py": 1,
    "core/gold_tier.py": 5,
    "core/http_utils.py": 3,
    "core/installer.py": 2,
    "core/logging_setup.py": 5,
    "core/master_catalog.py": 3,
    "core/meta_utils.py": 2,
    "core/mini_cover_cache.py": 5,
    "core/process_monitor.py": 8,
    "core/secrets.py": 9,
    "core/single_instance.py": 2,
    "core/spec_parser.py": 2,
    "core/state_store.py": 2,
    "core/steam_batch_worker.py": 1,
    "core/steam_integration.py": 29,
    "core/task_registry.py": 7,
    "core/trailer_cache.py": 2,
    "core/version_status.py": 2,
    "managers/achievement_engine.py": 1,
    "managers/achievements_manager.py": 2,
    "managers/backup_service.py": 1,
    "managers/discovery_manager.py": 11,
    "managers/igdb_batch_manager.py": 6,
    "managers/igdb_details_cache.py": 2,
    "managers/mirror_backend.py": 2,
    "managers/playtime_manager.py": 7,
    "managers/save_manager.py": 18,
    "managers/save_manifest.py": 4,
    "managers/save_locations.py": 4,
    "managers/stats_manager.py": 9,
    "network/cover_worker.py": 5,
    "network/downloader.py": 2,
    "network/featured_image_worker.py": 1,
    "network/igdb_batch_worker.py": 1,
    "network/igdb_details_worker.py": 1,
    "network/igdb_meta_worker.py": 4,
    "network/igdb_worker.py": 4,
    "network/page_freshness_worker.py": 3,
    "network/posts_index.py": 6,
    "network/resolver.py": 8,
    "network/sales_worker.py": 3,
    "network/scraper.py": 3,
    "network/sgdb_worker.py": 2,
    "network/steamrip_api.py": 5,
    "network/store_links_worker.py": 2,
    "network/trailer_downloader.py": 10,
    "network/trailer_worker.py": 3,
    "ui/background_scan_coordinator.py": 1,
    "ui/controllers/base.py": 2,
    "ui/controllers/catalog.py": 1,
    "ui/controllers/detail.py": 6,
    "ui/controllers/downloads.py": 2,
    "ui/controllers/game_launcher.py": 6,
    "ui/controllers/installer_flow.py": 2,
    "ui/controllers/library.py": 3,
    "ui/controllers/session.py": 2,
    "ui/controllers/settings.py": 5,
    "ui/controllers/version.py": 1,
    "ui/cover_worker.py": 1,
    "ui/dialogs/manual_save_path_dialog.py": 1,
    "ui/dialogs/onboarding_wizard.py": 2,
    "ui/library_manager.py": 1,
    "ui/library_scan_worker.py": 2,
    "ui/savegames_worker.py": 1,
    "ui/shell/app_window.py": 20,
    "ui/stats_worker.py": 1,
    "ui/steam_task_worker.py": 1,
    "ui/tabs/discovery_tab.py": 1,
    "ui/tabs/downloads_tab.py": 5,
    "ui/tabs/savegames_tab.py": 10,
    "ui/tabs/stats_tab.py": 5,
    "ui/widgets/blur_background.py": 1,
    "ui/widgets/game_detail_popup.py": 15,
    "ui/widgets/glass.py": 2,
    "ui/widgets/icons.py": 2,
}

# Files where broad handlers are structural, not laziness.
BASELINE_NOTES = {
    "core/logging_setup.py": "logging must never raise into its own handlers",
    "core/steam_integration.py": "legacy Steam files may be absent or changing",
    "core/process_monitor.py": "processes can exit while they are inspected",
    "managers/save_manager.py": "optional caches and probes around OS/HTTP",
    "managers/save_manifest.py": "optional manifest cache and HTTP probe (moved handlers)",
    "managers/save_locations.py": "OS probes and optional path providers (moved handlers)",
}


def _python_files():
    for layer in LAYERS:
        for path in (ROOT / layer).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def _handlers(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            yield node, lines


class ExceptionHygieneTests(unittest.TestCase):
    def test_no_bare_except(self):
        offenders = []
        for path in _python_files():
            for node, _lines in _handlers(path):
                if node.type is None:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [], "Bare except is forbidden:\n" + "\n".join(offenders))

    def test_silent_broad_catches_are_justified(self):
        offenders = []
        for path in _python_files():
            for node, lines in _handlers(path):
                if not (isinstance(node.type, ast.Name) and node.type.id == "Exception"):
                    continue
                if not (len(node.body) == 1 and isinstance(node.body[0], ast.Pass)):
                    continue
                start = node.lineno - 1
                end = node.body[0].end_lineno or node.body[0].lineno
                block = "\n".join(lines[start:end])
                marker = "# best-effort:"
                if marker not in block:
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} needs a "
                        f"'{marker} <reason>' comment")
                elif not block.split(marker, 1)[1].strip():
                    offenders.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} has an empty reason")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_broad_handler_baseline(self):
        offenders = []
        seen = {}
        for path in _python_files():
            rel = str(path.relative_to(ROOT))
            count = 0
            for node, _lines in _handlers(path):
                if isinstance(node.type, ast.Name) and node.type.id == "Exception":
                    count += 1
            if not count:
                continue
            seen[rel] = count
            allowed = BASELINE.get(rel)
            if allowed is None:
                offenders.append(
                    f"{rel}: {count} broad handler(s) with no baseline entry; "
                    "add it only after a review (and a note if structural)")
            elif count > allowed:
                offenders.append(
                    f"{rel}: {count} broad handler(s), baseline {allowed}; "
                    "narrow or remove the new one instead of raising the baseline")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_baseline_entries_still_exist(self):
        present = {
            str(path.relative_to(ROOT)) for path in _python_files()
        }
        missing = sorted(set(BASELINE) - present)
        self.assertEqual(
            missing, [],
            "Remove stale baseline entries for deleted files: " + ", ".join(missing),
        )


if __name__ == "__main__":
    unittest.main()
