import unittest

from PyQt6.QtCore import QCoreApplication

from core.events import EVENT_SESSION_END, EventBus

_app = QCoreApplication.instance() or QCoreApplication([])


class EventBusTests(unittest.TestCase):
    def test_emit_adds_id_and_timestamp(self):
        bus = EventBus()
        payload = bus.emit_event(EVENT_SESSION_END, title="DOOM", seconds=60)
        self.assertTrue(payload["id"])
        self.assertIsInstance(payload["at"], int)
        self.assertEqual(payload["title"], "DOOM")

    def test_subscriber_receives_only_its_event(self):
        bus = EventBus()
        received = []
        bus.subscribe(EVENT_SESSION_END, received.append)
        bus.emit_event("other_event", value=1)
        bus.emit_event(EVENT_SESSION_END, value=2)
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["value"], 2)

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        callback = bus.subscribe(EVENT_SESSION_END, received.append)
        bus.unsubscribe(EVENT_SESSION_END, callback)
        bus.emit_event(EVENT_SESSION_END)
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
