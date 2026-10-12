import unittest
from core.envelope import Envelope


class TestEnvelope(unittest.TestCase):
    def test_frozen(self):
        e = Envelope("EV-0011AABB", None, "order", 100, "2026-07-28T00:00:00Z")
        with self.assertRaises(Exception):
            e.event_id = "x"
