import unittest
from src.retry import call_with_retry


class TestRetry(unittest.TestCase):
    def test_attempt_count(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("no")

        with self.assertRaises(RuntimeError):
            call_with_retry(boom, max_retries=3)
        # max_retries=3 → 首次 + 3 次重试 = 4 次
        self.assertEqual(calls["n"], 4)

    def test_backoff_capped(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise RuntimeError("no")

        try:
            call_with_retry(boom, max_retries=6, base_delay=1.0, max_delay=8.0)
        except RuntimeError:
            pass

        def ok():
            raise RuntimeError("x")

        seen = []
        try:
            call_with_retry(lambda: (_ for _ in ()).throw(RuntimeError()),
                            max_retries=6, base_delay=1.0, max_delay=8.0)
        except RuntimeError:
            pass
        # 通过成功路径拿 delays
        def succeed_after(k):
            state = {"n": 0}
            def f():
                state["n"] += 1
                if state["n"] <= k:
                    raise RuntimeError("retry")
                return "ok"
            return f
        val, attempts, delays = call_with_retry(succeed_after(6), max_retries=8,
                                                base_delay=1.0, max_delay=8.0)
        self.assertEqual(val, "ok")
        self.assertEqual(attempts, 7)
        self.assertTrue(all(d <= 8.0 for d in delays), delays)
        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 8.0, 8.0])
