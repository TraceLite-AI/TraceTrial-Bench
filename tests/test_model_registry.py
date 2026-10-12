from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from harnessbench.model_registry import ModelRegistry


class ModelRegistryTests(unittest.TestCase):
    def _load(
        self,
        *,
        provider: str = "zenmux",
        model: str,
        deepseek_reasoning_effort: str | None = None,
        litellm_disable_web_search: bool | None = None,
        pricing: dict[str, str] | None = None,
    ) -> ModelRegistry:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "models.toml"
        lines = [
                    "[models.test]",
                    f'provider = "{provider}"',
                    f'model = "{model}"',
                    'base_url = "https://model.example/v1"',
                    'api_mode = "chat_completions"',
                    'api_key = "test-secret"',
        ]
        if deepseek_reasoning_effort is not None:
            lines.append(
                f'deepseek_reasoning_effort = "{deepseek_reasoning_effort}"'
            )
        if litellm_disable_web_search is not None:
            lines.append(
                "litellm_disable_web_search = "
                + ("true" if litellm_disable_web_search else "false")
            )
        if pricing is not None:
            lines.append("[models.test.pricing]")
            lines.extend(f'{key} = "{value}"' for key, value in pricing.items())
        lines.append("")
        path.write_text(
            "\n".join(lines),
            encoding="utf-8",
        )
        path.chmod(0o600)
        return ModelRegistry.load(path)

    def test_vendor_qualified_model_id_is_supported(self) -> None:
        registry = self._load(
            model="google/gemini-3.7-flash",
            deepseek_reasoning_effort="high",
        )

        resolved = registry.resolve("test", "deepseek")

        self.assertEqual(resolved.model, "google/gemini-3.7-flash")
        self.assertEqual(resolved.harness_model, "google/gemini-3.7-flash")
        self.assertEqual(resolved.deepseek_reasoning_effort, "high")
        self.assertEqual(
            resolved.public_summary()["deepseek_reasoning_effort"],
            "high",
        )

    def test_deepseek_reasoning_effort_defaults_to_max(self) -> None:
        registry = self._load(model="deepseek-v4-flash")

        resolved = registry.resolve("test", "deepseek")

        self.assertEqual(resolved.deepseek_reasoning_effort, "max")
        self.assertNotIn("deepseek_reasoning_effort", resolved.public_summary())

    def test_litellm_web_search_opt_out_is_explicit_and_model_scoped(self) -> None:
        enabled = self._load(
            model="z-ai/glm-5.2",
            litellm_disable_web_search=True,
        ).resolve("test", "codex")
        default = self._load(model="x-ai/grok-4.6").resolve("test", "codex")

        self.assertTrue(enabled.litellm_disable_web_search)
        self.assertTrue(enabled.public_summary()["litellm_disable_web_search"])
        self.assertFalse(default.litellm_disable_web_search)
        self.assertNotIn(
            "litellm_disable_web_search",
            default.public_summary(),
        )

    def test_unsafe_model_path_segments_are_rejected(self) -> None:
        for model in (
            "/google/gemini",
            "google/gemini/",
            "google//gemini",
            "google/../gemini",
            "google/gemini flash",
            "google/gemini;echo",
        ):
            with self.subTest(model=model):
                with self.assertRaisesRegex(ValueError, "unsafe model id"):
                    self._load(model=model)

    def test_provider_id_still_rejects_slashes(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsafe provider id"):
            self._load(provider="proxy/zenmux", model="google/gemini")

    def test_model_pricing_is_parsed_and_cache_write_defaults_to_input(self) -> None:
        registry = self._load(
            model="google/gemini-3.7-flash",
            pricing={
                "currency": "usd",
                "uncached_input_per_million": "1.25",
                "cache_read_input_per_million": "0.125",
                "effective_at": "2026-08-15",
            },
        )

        resolved = registry.resolve("test", "codex")

        self.assertIsNotNone(resolved.pricing)
        self.assertEqual(
            resolved.public_summary()["pricing"],
            {
                "currency": "USD",
                "uncached_input_per_million": "1.25",
                "cache_read_input_per_million": "0.125",
                "cache_write_input_per_million": "1.25",
                "effective_at": "2026-08-15",
            },
        )

    def test_model_pricing_rejects_negative_or_unknown_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid uncached_input"):
            self._load(
                model="model",
                pricing={
                    "currency": "USD",
                    "uncached_input_per_million": "-1",
                    "cache_read_input_per_million": "0.1",
                },
            )
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            self._load(
                model="model",
                pricing={
                    "currency": "USD",
                    "uncached_input_per_million": "1",
                    "cache_read_input_per_million": "0.1",
                    "cached_input_per_million": "typo",
                },
            )


if __name__ == "__main__":
    unittest.main()
