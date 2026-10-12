from __future__ import annotations

import unittest
from decimal import Decimal

from harnessbench.cache_metrics import attach_cache_savings, calculate_cache_savings
from harnessbench.model_registry import ModelPricing


def _pricing(*, cache_write: str = "1.25") -> ModelPricing:
    return ModelPricing(
        currency="USD",
        uncached_input_per_million=Decimal("1"),
        cache_read_input_per_million=Decimal("0.1"),
        cache_write_input_per_million=Decimal(cache_write),
        effective_at="2026-08-15",
    )


class CacheSavingsTests(unittest.TestCase):
    def test_calculates_gross_and_net_savings_with_cache_write_surcharge(self) -> None:
        savings = calculate_cache_savings(
            {
                "cached_input_tokens": 800_000,
                "cache_write_input_tokens": 100_000,
            },
            _pricing(),
        )

        self.assertEqual(savings["status"], "available")
        self.assertEqual(savings["reused_input_tokens"], 800_000)
        self.assertEqual(savings["gross_savings_amount"], "0.72")
        self.assertEqual(savings["cache_write_cost_delta_amount"], "0.025")
        self.assertEqual(savings["net_savings_amount"], "0.695")
        self.assertEqual(savings["currency"], "USD")

    def test_reports_token_only_when_pricing_is_not_configured(self) -> None:
        savings = calculate_cache_savings(
            {"cached_input_tokens": 4096},
            None,
        )

        self.assertEqual(savings["status"], "token_only")
        self.assertEqual(savings["reused_input_tokens"], 4096)
        self.assertEqual(savings["net_savings_amount"], "unknown")
        self.assertEqual(savings["reason"], "pricing_unavailable")

    def test_write_tokens_are_not_required_without_a_write_surcharge(self) -> None:
        savings = calculate_cache_savings(
            {"cached_input_tokens": 500_000},
            _pricing(cache_write="1"),
        )

        self.assertEqual(savings["status"], "available")
        self.assertEqual(savings["cache_write_input_tokens"], "unknown")
        self.assertEqual(savings["net_savings_amount"], "0.45")

    def test_missing_write_tokens_make_surcharged_net_savings_partial(self) -> None:
        savings = calculate_cache_savings(
            {"cached_input_tokens": 500_000},
            _pricing(),
        )

        self.assertEqual(savings["status"], "partial")
        self.assertEqual(savings["gross_savings_amount"], "0.45")
        self.assertEqual(savings["net_savings_amount"], "unknown")
        self.assertEqual(
            savings["reason"],
            "cache_write_input_tokens_unavailable",
        )

    def test_unknown_cache_reads_remain_unavailable(self) -> None:
        savings = calculate_cache_savings(
            {"cached_input_tokens": "unknown"},
            _pricing(),
        )

        self.assertEqual(savings["status"], "unavailable")
        self.assertEqual(savings["reused_input_tokens"], "unknown")
        self.assertEqual(savings["reason"], "cached_input_tokens_unavailable")

    def test_attach_writes_the_metric_to_the_trace_summary(self) -> None:
        summary: dict[str, object] = {
            "token_usage": {
                "cached_input_tokens": 100_000,
                "cache_creation_input_tokens": 20_000,
            }
        }

        attached = attach_cache_savings(summary, _pricing())

        self.assertIs(summary["cache_savings"], attached)
        self.assertEqual(attached["cache_write_input_tokens"], 20_000)
        self.assertEqual(attached["net_savings_amount"], "0.085")


if __name__ == "__main__":
    unittest.main()
