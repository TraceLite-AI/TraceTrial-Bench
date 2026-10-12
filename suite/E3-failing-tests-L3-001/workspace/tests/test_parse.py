import unittest
from src.parse import field, required


class TestParse(unittest.TestCase):
    def test_missing_vs_empty(self):
        self.assertIsNone(field({}, "a"))
        self.assertEqual(field({"a": ""}, "a"), "")
        self.assertEqual(field({"a": "  x "}, "a"), "x")

    def test_none_value(self):
        self.assertIsNone(field({"a": None}, "a"))

    def test_required_rejects_both(self):
        with self.assertRaises(KeyError):
            required({}, "a")
        with self.assertRaises(KeyError):
            required({"a": "   "}, "a")

    def test_non_string_passthrough(self):
        self.assertEqual(field({"a": 5}, "a"), 5)
