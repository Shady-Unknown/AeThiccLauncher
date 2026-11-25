"""SavePathResolver state lifecycle: providers are read live, never copied."""
import unittest
from unittest import mock

from managers import save_locations
from managers.save_manager import SavegamesManager


def make_manager():
    manager = object.__new__(SavegamesManager)
    manager.parent = None
    manager.app_config = {}
    manager.installed_games = {}
    manager._prompt_save_path = None
    manager._notify_callback = None
    return manager


class ProviderLifecycleTests(unittest.TestCase):
    def test_preview_reads_the_current_installed_meta(self):
        manager = make_manager()
        manager.installed_games = {"Game": {"steam_grid_id": "1"}}
        manager._get_compatdata_base = mock.Mock(return_value="/base/one")
        with mock.patch.object(save_locations.platform_info, "is_linux",
                               return_value=True), mock.patch.object(
                save_locations.save_paths, "translate_win_path_details",
                return_value={"path": "/translated", "kind": "prefix",
                              "reason": "", "token": ""}) as translate:
            manager.preview_linux_path(r"%appdata%\Game\Saves", "Game")
            self.assertEqual(translate.call_args.args[1], "/base/one")

            manager.installed_games = {"Game": {"steam_grid_id": "2"}}
            manager._get_compatdata_base.return_value = "/base/two"
            manager.preview_linux_path(r"%appdata%\Game\Saves", "Game")
            self.assertEqual(translate.call_args.args[1], "/base/two")

    def test_get_source_reads_the_current_meta_each_time(self):
        manager = make_manager()
        meta = {"savegame_path_linux": "~/.local/share/Game/one"}
        first = manager.get_source_save_path(meta)
        meta["savegame_path_linux"] = "~/.local/share/Game/two"
        second = manager.get_source_save_path(meta)
        self.assertIn("one", first)
        self.assertIn("two", second)

    def test_installed_games_replacement_is_visible_to_rescan(self):
        manager = make_manager()
        manager.installed_games = {"Game": {"steam_grid_id": "1"}}
        seen = []
        manager.find_manifest_entry = lambda title: {
            "files": {r"%appdata%\Game": {"tags": ["save"]}}}
        manager.save_meta = lambda meta: seen.append(dict(meta))
        manager._get_compatdata_base = mock.Mock(return_value="")
        with mock.patch.object(save_locations.platform_info, "is_linux",
                               return_value=True):
            manager.rescan_save_folder({
                "title": "Game",
                "install_dir": "/tmp/Game",
                "steam_grid_id": "1",
            })
        self.assertTrue(seen)


if __name__ == "__main__":
    unittest.main()
