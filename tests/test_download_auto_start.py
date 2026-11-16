"""Auto-start rules of the download queue.

The queue used to keep ``is_paused`` sticky: ``load_state`` forced it to True
and ``_process_queue`` returned before clearing it, so every later
``add_to_queue`` was ignored until a restart. ``is_paused`` must describe a
paused *current* task only.
"""
import unittest

from ui.tabs.downloads_tab import can_auto_start


class CanAutoStartTests(unittest.TestCase):
    def test_idle_queue_with_stale_pause_still_starts(self):
        self.assertTrue(
            can_auto_start(
                active_worker=None,
                is_extracting=False,
                current_task=None,
                is_paused=True,
            )
        )

    def test_idle_queue_starts(self):
        self.assertTrue(can_auto_start(None, False, None, False))

    def test_paused_current_task_blocks(self):
        self.assertFalse(can_auto_start(None, False, {"title": "A"}, True))

    def test_active_download_blocks(self):
        self.assertFalse(
            can_auto_start(object(), False, {"title": "A"}, False)
        )

    def test_running_extraction_blocks(self):
        self.assertFalse(can_auto_start(None, True, None, False))

    def test_current_task_downloading_keeps_blocking(self):
        # A current task that is not paused means a worker is about to exist.
        self.assertFalse(
            can_auto_start(object(), False, {"title": "A"}, False)
        )


if __name__ == "__main__":
    unittest.main()
