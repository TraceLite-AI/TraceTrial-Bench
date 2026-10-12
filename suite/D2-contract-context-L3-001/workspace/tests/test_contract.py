import unittest, inspect
from ops.errors import OpsError


class TestErrorType(unittest.TestCase):
    def test_fields(self):
        sig = inspect.signature(OpsError)
        self.assertEqual(list(sig.parameters), ["code", "retryable"])

    def test_str(self):
        self.assertIn("retryable", str(OpsError("X-SMOKE", True)))
