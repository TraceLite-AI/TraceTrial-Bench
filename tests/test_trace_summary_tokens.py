from __future__ import annotations

import unittest

from harness_tracer.summary import _cli_stream_timing, _merge_usage


class TraceTokenSummaryTests(unittest.TestCase):
    def test_cli_stream_timing_uses_observed_stdout_offsets(self) -> None:
        timing = _cli_stream_timing(
            [
                {
                    "seq": 1,
                    "event_type": "model_input",
                    "stream_observed_offset_ms": 100.0,
                },
                {
                    "seq": 2,
                    "event_type": "assistant_message",
                    "content": "hello",
                    "stream_observed_offset_ms": 600.0,
                },
                {
                    "seq": 3,
                    "event_type": "token_usage",
                    "stream_observed_offset_ms": 2600.0,
                },
            ]
        )

        assert timing is not None
        self.assertEqual(timing["ttft_mean_ms"], 500.0)
        self.assertEqual(timing["decode_duration_ms"], 2000.0)
        self.assertEqual(timing["request_count"], 1)

    def test_cli_stream_timing_uses_last_token_usage_as_decode_end(self) -> None:
        timing = _cli_stream_timing(
            [
                {
                    "seq": 1,
                    "event_type": "model_input",
                    "stream_observed_offset_ms": 100.0,
                },
                {
                    "seq": 2,
                    "event_type": "assistant_message",
                    "content": "first",
                    "stream_observed_offset_ms": 600.0,
                },
                {
                    "seq": 3,
                    "event_type": "token_usage",
                    "stream_observed_offset_ms": 700.0,
                },
                {
                    "seq": 4,
                    "event_type": "token_usage",
                    "usage_aggregate": True,
                    "stream_observed_offset_ms": 2600.0,
                },
            ]
        )

        assert timing is not None
        self.assertEqual(timing["decode_duration_ms"], 2000.0)

    def test_cache_write_aliases_are_aggregated_once(self) -> None:
        usage = _merge_usage(
            [
                {
                    "event_type": "token_usage",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 1,
                        "cache_write_tokens": 2,
                        "output_tokens": 1,
                    },
                },
                {
                    "event_type": "token_usage",
                    "usage": {
                        "input_tokens": 20,
                        "cached_input_tokens": 2,
                        "cache_creation_input_tokens": 3,
                        "output_tokens": 2,
                    },
                },
                {
                    "event_type": "token_usage",
                    "usage": {
                        "input_tokens": 30,
                        "cached_input_tokens": 3,
                        "cache_write_input_tokens": 4,
                        "cache_write_tokens": 4,
                        "output_tokens": 3,
                    },
                },
            ]
        )

        self.assertEqual(usage["input_tokens"], 60)
        self.assertEqual(usage["cached_input_tokens"], 6)
        self.assertEqual(usage["cache_write_input_tokens"], 9)
        self.assertEqual(usage["output_tokens"], 6)
        self.assertEqual(usage["total_tokens"], 66)
        self.assertEqual(usage["cache_hit_rate"], 0.1)
        self.assertEqual(usage["call_count"], 3)
        self.assertEqual(
            [call["total_tokens"] for call in usage["calls"]],
            [11, 22, 33],
        )

    def test_aggregate_usage_supplies_totals_without_creating_a_call(self) -> None:
        usage = _merge_usage(
            [
                {
                    "event_type": "token_usage",
                    "source": "claudecode.assistant",
                    "usage_scope": "call",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 4,
                        "output_tokens": 1,
                    },
                },
                {
                    "event_type": "token_usage",
                    "source": "claudecode.result",
                    "usage_scope": "run",
                    "usage_aggregate": True,
                    "reported_cost_usd": 0.25,
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 80,
                        "cache_write_input_tokens": 5,
                        "output_tokens": 20,
                    },
                },
            ]
        )

        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["cached_input_tokens"], 80)
        self.assertEqual(usage["cache_write_input_tokens"], 5)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(usage["cache_hit_rate"], 0.8)
        self.assertEqual(usage["call_count"], 1)
        self.assertEqual(usage["calls"][0]["source"], "claudecode.assistant")
        self.assertEqual(usage["reported_cost_usd"], 0.25)

    def test_missing_cache_write_data_stays_unknown(self) -> None:
        usage = _merge_usage(
            [
                {
                    "event_type": "token_usage",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 0,
                        "output_tokens": 1,
                    },
                }
            ]
        )

        self.assertEqual(usage["cache_write_input_tokens"], "unknown")

    def test_missing_cache_data_is_explicitly_not_available(self) -> None:
        usage = _merge_usage(
            [
                {
                    "event_type": "token_usage",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            ]
        )

        self.assertEqual(usage["cached_input_tokens"], "N/A")
        self.assertEqual(usage["cache_hit_rate"], "N/A")
        self.assertEqual(usage["total_tokens"], 12)
        self.assertEqual(usage["calls"][0]["cached_input_tokens"], "N/A")

    def test_zero_token_call_has_a_real_zero_total(self) -> None:
        usage = _merge_usage(
            [
                {
                    "event_type": "token_usage",
                    "usage": {
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                    },
                }
            ]
        )

        self.assertEqual(usage["total_tokens"], 0)
        self.assertEqual(usage["cache_hit_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
