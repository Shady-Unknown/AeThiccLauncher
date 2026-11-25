"""UI text for restore recovery outcomes (no widget needed)."""
import unittest

from ui.tabs.savegames_tab import SavegamesTab


class RestoreErrorTextTests(unittest.TestCase):
    def test_recovery_ambiguous_tells_the_user_to_keep_both_copies(self):
        text = SavegamesTab._restore_error_text({
            'reason': 'recovery_ambiguous',
            'detail': 'destination and restore_old both exist',
        })
        self.assertIn('Both the live save folder', text)
        self.assertIn('preserved copy', text)
        self.assertIn('Do not delete', text)
        self.assertIn('.restore_old', text)

    def test_recovery_failed_preserves_the_original_copy_message(self):
        text = SavegamesTab._restore_error_text({
            'reason': 'recovery_failed',
            'detail': 'cannot restore the original',
        })
        self.assertIn('could not be put back', text)
        self.assertIn('.restore_old', text)
        self.assertNotIn('Unexpected error', text)

    def test_restore_journal_failure_is_explained(self):
        text = SavegamesTab._restore_error_text({
            'reason': 'restore_journal_failed',
            'detail': 'disk full',
        })
        self.assertIn('no destructive step', text)
        self.assertNotIn('Unexpected error', text)


if __name__ == "__main__":
    unittest.main()
