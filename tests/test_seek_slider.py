import unittest

from ui.widgets.discovery_card import seek_value_from_position


class SeekValueTests(unittest.TestCase):
    def test_middle_click_maps_to_the_middle(self):
        self.assertEqual(seek_value_from_position(0, 100, 50, 100), 50)

    def test_edges_map_to_min_and_max(self):
        self.assertEqual(seek_value_from_position(0, 100, 0, 100), 0)
        self.assertEqual(seek_value_from_position(0, 100, 100, 100), 100)

    def test_out_of_range_clamps(self):
        self.assertEqual(seek_value_from_position(0, 100, -20, 100), 0)
        self.assertEqual(seek_value_from_position(0, 100, 250, 100), 100)

    def test_duration_mapping(self):
        # 10-minute video, click at 25% -> 2:30.
        self.assertEqual(seek_value_from_position(0, 600000, 25, 100), 150000)


if __name__ == "__main__":
    unittest.main()
