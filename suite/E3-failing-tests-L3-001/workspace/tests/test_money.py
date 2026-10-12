import unittest
from src.money import total, split_evenly, to_cents


class TestMoney(unittest.TestCase):
    def test_total_no_float_drift(self):
        self.assertEqual(total([0.1] * 10), 1.0)
        self.assertEqual(total([19.99, 0.01]), 20.0)

    def test_split_sums_back(self):
        for amount, n in [(10.0, 3), (0.05, 4), (100.01, 7)]:
            parts = split_evenly(amount, n)
            self.assertEqual(len(parts), n)
            self.assertEqual(round(sum(parts), 2), amount)

    def test_split_remainder_to_front(self):
        self.assertEqual(split_evenly(1.00, 3), [0.34, 0.33, 0.33])

    def test_to_cents(self):
        self.assertEqual(to_cents(1.005), 101)
        self.assertEqual(to_cents(-2.5), -250)
