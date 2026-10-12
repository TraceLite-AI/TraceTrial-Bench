"""分页。页码从 1 开始。"""
import math


def page_count(total_items, page_size):
    if page_size <= 0:
        raise ValueError("page_size must be positive")
    return total_items // page_size


def slice_page(items, page, page_size):
    if page < 1:
        raise ValueError("page starts at 1")
    start = page * page_size
    return items[start:start + page_size]
