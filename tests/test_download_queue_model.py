import os
import unittest

from core.download_queue_model import (
    KIND_DOWNLOAD,
    KIND_EXTRACT,
    SECTION_NEEDS_EXTRACTION,
    SECTION_UP_NEXT,
    STATE_DOWNLOADING,
    STATE_EXTRACTING,
    STATE_INTERRUPTED,
    STATE_PARTIAL,
    STATE_QUEUED,
    STATE_READY,
    build_layout,
    classify_download,
    classify_extraction,
    state_color,
    task_progress,
)


def make_task(task_id, title, downloaded=0, total=0):
    return {
        "_id": task_id,
        "title": title,
        "filename": title + ".rar",
        "dest_path": "",
        "downloaded_bytes": downloaded,
        "total_bytes": total,
        "provider": "GoFile",
    }


def make_archive(entry_id, title, state=STATE_READY, pct=None):
    return {
        "_id": entry_id,
        "title": title,
        "archive_path": "",
        "state": state,
        "pct": pct,
        "provider": "MegaDB",
    }


class LayoutTests(unittest.TestCase):
    def test_idle_prefers_pending_extraction(self):
        layout = build_layout(
            [make_archive("e1", "Extract me")],
            [make_task("d1", "Partial", downloaded=10, total=100)],
        )
        self.assertEqual(layout["top"].kind, KIND_EXTRACT)
        self.assertEqual(layout["top"].state, STATE_READY)
        self.assertEqual(
            [row.item["_id"] for row in layout["sections"][0]["rows"]],
            ["d1"],
        )

    def test_idle_prefers_partial_download_over_queued(self):
        queue = [
            make_task("d1", "Queued"),
            make_task("d2", "Partial", downloaded=50, total=100),
        ]
        layout = build_layout([], queue)
        self.assertEqual(layout["top"].kind, KIND_DOWNLOAD)
        self.assertEqual(layout["top"].row_id, "d2")
        self.assertEqual(layout["top"].state, STATE_PARTIAL)
        self.assertEqual(layout["top"].pct, 50)
        # The top row is not repeated in the section below.
        self.assertEqual(
            [row.row_id for row in layout["sections"][0]["rows"]],
            ["d1"],
        )

    def test_active_download_is_top_and_extractions_stay_below(self):
        current = make_task("d0", "Running", downloaded=20, total=100)
        layout = build_layout(
            [make_archive("e1", "Ready")],
            [make_task("d1", "Queued")],
            current,
            is_paused=False,
        )
        self.assertEqual(layout["top"].row_id, "d0")
        self.assertEqual(layout["top"].state, STATE_DOWNLOADING)
        self.assertEqual(layout["sections"][0]["label"], SECTION_NEEDS_EXTRACTION)
        self.assertEqual(layout["sections"][1]["label"], SECTION_UP_NEXT)

    def test_running_extraction_is_top(self):
        layout = build_layout(
            [make_archive("e1", "Extracting"), make_archive("e2", "Other")],
            [make_task("d1", "Partial", downloaded=5, total=10)],
            None,
            is_extracting=True,
            current_extraction_id="e1",
        )
        self.assertEqual(layout["top"].kind, KIND_EXTRACT)
        self.assertEqual(layout["top"].state, STATE_EXTRACTING)
        labels = [section["label"] for section in layout["sections"]]
        self.assertIn(SECTION_NEEDS_EXTRACTION, labels)
        self.assertIn(SECTION_UP_NEXT, labels)

    def test_empty_sections_are_omitted(self):
        layout = build_layout([], [make_task("d1", "Only one")])
        self.assertEqual(layout["top"].row_id, "d1")
        self.assertEqual(layout["sections"], [])

    def test_paused_current_row_is_partial(self):
        layout = build_layout(
            [],
            [],
            make_task("d1", "Paused", downloaded=42, total=100),
            is_paused=True,
        )
        self.assertEqual(layout["top"].state, STATE_PARTIAL)
        self.assertIn("42%", layout["top"].badge)


class RowTests(unittest.TestCase):
    def test_progress_uses_persisted_total(self):
        downloaded, total, pct = task_progress(
            {"downloaded_bytes": 25, "total_bytes": 100}
        )
        self.assertEqual((downloaded, total, pct), (25, 100, 25))

    def test_progress_without_total_has_no_percentage(self):
        downloaded, total, pct = task_progress(
            {"downloaded_bytes": 25, "total_bytes": 0}
        )
        self.assertEqual((downloaded, total, pct), (25, 0, None))

    def test_progress_falls_back_to_disk_size(self):
        sizes = {"/tmp/game.rar": 64}
        downloaded, _, _ = task_progress(
            {"dest_path": "/tmp/game.rar"}, size_provider=sizes.get
        )
        self.assertEqual(downloaded, 64)

    def test_queued_row_badge(self):
        row = classify_download(make_task("d1", "Queued"))
        self.assertEqual(row.state, STATE_QUEUED)
        self.assertEqual(row.badge, "QUEUED")

    def test_interrupted_extraction_badge(self):
        row = classify_extraction(
            make_archive("e1", "Broken", state=STATE_INTERRUPTED, pct=43)
        )
        self.assertEqual(row.state, STATE_INTERRUPTED)
        self.assertIn("43%", row.badge)

    def test_state_colors_come_from_theme(self):
        theme = {
            "queue_state_ready": "#00ff88",
            "queue_state_partial": "#ffcc00",
            "queue_state_interrupted": "#ff2d55",
        }
        self.assertEqual(state_color(theme, STATE_READY), "#00ff88")
        self.assertEqual(state_color(theme, STATE_PARTIAL), "#ffcc00")
        self.assertEqual(state_color(theme, STATE_INTERRUPTED), "#ff2d55")

    def test_state_colors_fall_back_to_legacy_keys(self):
        theme = {"status_green": "#00ff88", "status_yellow": "#ffcc00"}
        self.assertEqual(state_color(theme, STATE_READY), "#00ff88")
        self.assertEqual(state_color(theme, STATE_PARTIAL), "#ffcc00")


class ExtractionStateTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.archive = os.path.join(self._tmp.name, "game.rar")
        with open(self.archive, "wb") as handle:
            handle.write(b"x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_sidecar_roundtrip(self):
        from core.download_queue_model import (
            clear_extraction_state,
            extraction_state_path,
            read_extraction_state,
            write_extraction_state,
        )

        write_extraction_state(self.archive, {
            "install_dir": "/games/Game",
            "pct": 43,
        })
        self.assertTrue(os.path.isfile(extraction_state_path(self.archive)))
        data = read_extraction_state(self.archive)
        self.assertEqual(data["pct"], 43)
        self.assertEqual(data["install_dir"], "/games/Game")
        self.assertEqual(data["archive_path"], self.archive)

        clear_extraction_state(self.archive)
        self.assertFalse(os.path.isfile(extraction_state_path(self.archive)))
        self.assertEqual(read_extraction_state(self.archive), {})


if __name__ == "__main__":
    unittest.main()
