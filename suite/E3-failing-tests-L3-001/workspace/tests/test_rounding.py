import unittest
from src.rounding import round_half_up, round_cents


class TestRounding(unittest.TestCase):
    def test_half_up_positive(self):
        self.assertEqual(round_half_up(2.5), 3.0)
        self.assertEqual(round_half_up(3.5), 4.0)
        self.assertEqual(round_half_up(0.5), 1.0)

    def test_half_up_negative(self):
        # 负数向绝对值大的方向：-2.5 → -3
        self.assertEqual(round_half_up(-2.5), -3.0)
        self.assertEqual(round_half_up(-0.5), -1.0)

    def test_cents(self):
        self.assertEqual(round_cents(1.005), 1.01)
        self.assertEqual(round_cents(2.344), 2.34)
