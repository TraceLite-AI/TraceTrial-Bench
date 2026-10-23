import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.orchestrator import dispatch_notice


def test_happy():
    result = dispatch_notice('OK-1', 'TPL-1', 2, 5000, 'user@example.org')
    assert result["ok"] is True


def test_templatemissing():
    result = dispatch_notice('OK-2', 'MISSING-9', 1, 5000, 'user@example.org')
    assert result["ok"] is False and result["code"] == "E-101"


def test_channelrefused():
    result = dispatch_notice('REFUSE-3', 'TPL-1', 1, 5000, 'user@example.org')
    assert result["ok"] is False and result["code"] == "E-102"
