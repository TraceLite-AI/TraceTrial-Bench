import unittest
from src.paging import page_count, slice_page


class TestPaging(unittest.TestCase):
    def test_page_count_exact(self):
        self.assertEqual(page_count(100, 10), 10)

    def test_page_count_partial_last(self):
        self.assertEqual(page_count(101, 10), 11)
        self.assertEqual(page_count(1, 10), 1)

    def test_page_count_empty(self):
        self.assertEqual(page_count(0, 10), 0)

    def test_slice(self):
        items = list(range(25))
        self.assertEqual(slice_page(items, 1, 10), list(range(0, 10)))
        self.assertEqual(slice_page(items, 3, 10), list(range(20, 25)))
