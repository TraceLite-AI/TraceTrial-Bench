from __future__ import annotations

import unittest

from tracetrial_bench.token_cost import calculate_token_cost


class TokenCostTests(unittest.TestCase):
    def test_prices_uncached_cached_cache_write_and_output_tokens(self) -> None:
        cost = calculate_token_cost(
            {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 800_000,
                "cache_write_input_tokens": 100_000,
                "output_tokens": 100_000,
            },
            "anthropic/claude-opus-4.8",
        )

        self.assertEqual(cost["status"], "available")
        self.assertEqual(cost["input_amount"], "1.525")
        self.assertEqual(cost["output_amount"], "2.5")
        self.assertEqual(cost["amount"], "4.025")

    def test_subscription_model_suffix_matches_catalog(self) -> None:
        cost = calculate_token_cost(
            {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 0,
            },
            "claude-opus-4.8",
        )

        self.assertEqual(cost["matched_model"], "anthropic/claude-opus-4.8")
        self.assertEqual(cost["amount"], "5")

    def test_claude_opus_4_7_api_id_matches_catalog(self) -> None:
        cost = calculate_token_cost(
            {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 1_000_000,
            },
            "claude-opus-4-7",
        )

        self.assertEqual(cost["status"], "available")
        self.assertEqual(cost["matched_model"], "anthropic/claude-opus-4-7")
        self.assertEqual(cost["amount"], "30")
        self.assertEqual(
            cost["pricing"]["source"],
            "https://www.imarouter.com/models",
        )

    def test_formal_provider_models_use_exact_prices_and_sources(self) -> None:
        usage = {
            "input_tokens": 1_000_000,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 1_000_000,
        }
        expectations = {
            "deepseek-v4-flash": (
                "0.42",
                "https://api-docs.deepseek.com/quick_start/pricing",
            ),
            "deepseek-v4-pro-0813": (
                "5.07",
                "https://intl.cloud.baidu.com/en/doc/qianfan/s/Jm8r1826a-intl-en",
            ),
            "google/gemini-3.7-flash": (
                "4.5",
                "https://zenmux.ai/models",
            ),
        }

        for model, (amount, source) in expectations.items():
            with self.subTest(model=model):
                cost = calculate_token_cost(usage, model)
                self.assertEqual(cost["status"], "available")
                self.assertEqual(cost["matched_model"], model)
                self.assertEqual(cost["amount"], amount)
                self.assertEqual(cost["pricing"]["source"], source)

    def test_missing_cache_counter_produces_a_tagged_cost_estimate(self) -> None:
        cost = calculate_token_cost(
            {"input_tokens": 1_000_000, "output_tokens": 100_000},
            "x-ai/grok-4.6",
        )

        self.assertEqual(cost["status"], "estimated")
        self.assertEqual(cost["reason"], "cached_input_tokens_unavailable")
        self.assertEqual(cost["amount"], "2.6")

    def test_unknown_model_keeps_cost_unavailable(self) -> None:
        cost = calculate_token_cost(
            {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
            },
            "unknown/model",
        )

        self.assertEqual(cost["status"], "unavailable")
        self.assertEqual(cost["amount"], "N/A")

    def test_cli_reported_cost_is_used_when_model_pricing_is_missing(self) -> None:
        cost = calculate_token_cost(
            {
                "input_tokens": 1,
                "cached_input_tokens": 0,
                "output_tokens": 1,
                "reported_cost_usd": 0.0429315,
            },
            "opus",
        )

        self.assertEqual(cost["status"], "available")
        self.assertEqual(cost["amount"], "0.0429315")
        self.assertEqual(
            cost["pricing"]["source"],
            "subscription_cli_reported_total_cost_usd",
        )


if __name__ == "__main__":
    unittest.main()
