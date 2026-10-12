import unittest
from src.merge import deep_merge


class TestMerge(unittest.TestCase):
    def test_nested_not_replaced(self):
        base = {"db": {"host": "h1", "port": 5432}, "log": "info"}
        over = {"db": {"port": 6543}}
        self.assertEqual(deep_merge(base, over),
                         {"db": {"host": "h1", "port": 6543}, "log": "info"})

    def test_base_untouched(self):
        base = {"a": {"b": 1}}
        deep_merge(base, {"a": {"b": 2}})
        self.assertEqual(base, {"a": {"b": 1}})

    def test_scalar_over_dict(self):
        self.assertEqual(deep_merge({"a": {"b": 1}}, {"a": 5}), {"a": 5})
