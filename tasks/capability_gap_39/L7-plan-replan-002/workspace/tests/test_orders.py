import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import place_order


def test_happy():
    result = place_order("OK-1", "SKU-1", 2, 5000, "somewhere street 12")
    assert result["ok"] is True


def test_out_of_stock():
    result = place_order("OK-2", "OOS-9", 1, 5000, "somewhere street 12")
    assert result["ok"] is False and result["code"] == "E-101"


def test_declined():
    result = place_order("DECLINE-3", "SKU-1", 1, 5000, "somewhere street 12")
    assert result["ok"] is False and result["code"] == "E-102"
