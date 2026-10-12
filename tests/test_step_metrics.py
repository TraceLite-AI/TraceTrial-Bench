from __future__ import annotations

import unittest

from harnessbench.step_metrics import NOT_AVAILABLE, calculate_step_count


class StepMetricsTests(unittest.TestCase):
    def test_provider_request_evidence_is_exact_and_has_priority(self) -> None:
        count, details = calculate_step_count(
            {
                "provider_request_evidence": {"request_count": 4},
                "token_usage": {
                    "calls": [{"usage_scope": "turn"}],
                },
            }
        )

        self.assertEqual(count, 4)
        self.assertEqual(details["status"], "exact")
        self.assertEqual(details["source"], "provider_request_evidence")

    def test_per_call_usage_excludes_known_internal_calls(self) -> None:
        count, details = calculate_step_count(
            {
                "token_usage": {
                    "calls": [
                        {"usage_scope": "call"},
                        {"usage_scope": "internal"},
                        {"usage_scope": "call"},
                    ]
                }
            }
        )

        self.assertEqual(count, 2)
        self.assertEqual(details["status"], "derived")
        self.assertEqual(details["excluded_internal_events"], 1)

    def test_turn_usage_counts_as_one_subscription_cli_step(self) -> None:
        count, details = calculate_step_count(
            {"token_usage": {"calls": [{"usage_scope": "turn"}]}}
        )

        self.assertEqual(count, 1)
        self.assertEqual(details["status"], "derived")
        self.assertEqual(details["source"], "per_call_token_usage")

    def test_aggregate_or_unknown_usage_is_not_reported_as_one_step(self) -> None:
        for scope in ("run", "unknown"):
            with self.subTest(scope=scope):
                count, details = calculate_step_count(
                    {"token_usage": {"calls": [{"usage_scope": scope}]}}
                )

                self.assertEqual(count, NOT_AVAILABLE)
                self.assertEqual(details["status"], "unavailable")
                self.assertEqual(
                    details["reason"],
                    "aggregate_or_unknown_usage_scope",
                )

    def test_zero_provider_requests_is_a_real_zero(self) -> None:
        count, details = calculate_step_count(
            {"provider_request_evidence": {"request_count": 0}}
        )

        self.assertEqual(count, 0)
        self.assertEqual(details["status"], "exact")


if __name__ == "__main__":
    unittest.main()
