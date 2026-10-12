import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import run_probe


def test_happy():
    result = run_probe('OK-1', 'sda1', 2, 5000, 'edge-01.internal')
    assert result["ok"] is True


def test_mountunavailable():
    result = run_probe('OK-2', 'BADDEV-9', 1, 5000, 'edge-01.internal')
    assert result["ok"] is False and result["code"] == "E-101"


def test_serviceunreachable():
    result = run_probe('UNREACH-3', 'sda1', 1, 5000, 'edge-01.internal')
    assert result["ok"] is False and result["code"] == "E-102"
