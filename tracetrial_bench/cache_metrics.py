from __future__ import annotations

from decimal import Decimal
from typing import Mapping

from tracetrial_bench.model_registry import ModelPricing


CACHE_SAVINGS_SCHEMA = "harnessbench_cache_savings_v1"
UNKNOWN = "unknown"
_TOKENS_PER_MILLION = Decimal(1_000_000)
_CACHE_WRITE_KEYS = (
    "cache_write_input_tokens",
    "cache_write_tokens",
    "cache_creation_input_tokens",
)


def calculate_cache_savings(
    token_usage: object,
    pricing: ModelPricing | None,
) -> dict[str, object]:
    usage = token_usage if isinstance(token_usage, Mapping) else {}
    reused_tokens = _nonnegative_integer(usage.get("cached_input_tokens"))
    cache_write_tokens = _first_nonnegative_integer(usage, _CACHE_WRITE_KEYS)
    result: dict[str, object] = {
        "schema_version": CACHE_SAVINGS_SCHEMA,
        "status": "unavailable",
        "reason": None,
        "method": "counterfactual_uncached_input_pricing_v1",
        "reused_input_tokens": (
            reused_tokens if reused_tokens is not None else UNKNOWN
        ),
        "cache_write_input_tokens": (
            cache_write_tokens if cache_write_tokens is not None else UNKNOWN
        ),
        "currency": pricing.currency if pricing is not None else UNKNOWN,
        "gross_savings_amount": UNKNOWN,
        "cache_write_cost_delta_amount": UNKNOWN,
        "net_savings_amount": UNKNOWN,
        "pricing": pricing.public_summary() if pricing is not None else None,
    }
    if reused_tokens is None:
        result["reason"] = "cached_input_tokens_unavailable"
        return result
    if pricing is None:
        result["status"] = "token_only"
        result["reason"] = "pricing_unavailable"
        return result

    gross_savings = (
        Decimal(reused_tokens)
        * (
            pricing.uncached_input_per_million
            - pricing.cache_read_input_per_million
        )
        / _TOKENS_PER_MILLION
    )
    result["gross_savings_amount"] = _decimal_text(gross_savings)

    write_price_delta = (
        pricing.cache_write_input_per_million
        - pricing.uncached_input_per_million
    )
    if write_price_delta == 0:
        write_cost_delta = Decimal(0)
    elif cache_write_tokens is None:
        result["status"] = "partial"
        result["reason"] = "cache_write_input_tokens_unavailable"
        return result
    else:
        write_cost_delta = (
            Decimal(cache_write_tokens)
            * write_price_delta
            / _TOKENS_PER_MILLION
        )

    result["cache_write_cost_delta_amount"] = _decimal_text(write_cost_delta)
    result["net_savings_amount"] = _decimal_text(
        gross_savings - write_cost_delta
    )
    result["status"] = "available"
    return result


def attach_cache_savings(
    trace_summary: dict[str, object],
    pricing: ModelPricing | None,
) -> dict[str, object]:
    savings = calculate_cache_savings(trace_summary.get("token_usage"), pricing)
    trace_summary["cache_savings"] = savings
    return savings


def _first_nonnegative_integer(
    usage: Mapping[object, object], keys: tuple[str, ...]
) -> int | None:
    for key in keys:
        value = _nonnegative_integer(usage.get(key))
        if value is not None:
            return value
    return None


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
