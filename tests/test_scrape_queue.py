import unittest

from network.scrape_queue import ScrapeQueue


class ScrapeQueueTests(unittest.TestCase):
    def test_fifo_order(self):
        queue = ScrapeQueue()
        queue.enqueue("extract", url="a")
        queue.enqueue("extract", url="b")
        self.assertEqual(queue.pop_next().url, "a")
        self.assertEqual(queue.pop_next().url, "b")
        self.assertIsNone(queue.pop_next())

    def test_priority_jumps_the_queue(self):
        queue = ScrapeQueue()
        queue.enqueue("extract", url="a")
        queue.enqueue("cf_bypass", url="cf", priority=10)
        self.assertEqual(queue.pop_next().mode, "cf_bypass")
        self.assertEqual(queue.pop_next().url, "a")

    def test_duplicates_are_dropped(self):
        queue = ScrapeQueue()
        self.assertIsNotNone(queue.enqueue("cover", url="a", title="Game"))
        self.assertIsNone(queue.enqueue("cover", url="a", title="Game"))
        self.assertEqual(len(queue), 1)

    def test_remove_and_clear(self):
        queue = ScrapeQueue()
        job = queue.enqueue("extract", url="a")
        queue.enqueue("extract", url="b")
        queue.remove(job)
        self.assertEqual([j.url for j in queue], ["b"])
        queue.clear()
        self.assertEqual(len(queue), 0)


if __name__ == "__main__":
    unittest.main()
