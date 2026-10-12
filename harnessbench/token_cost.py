from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping


UNKNOWN = "unknown"
NOT_AVAILABLE = "N/A"
TOKENS_PER_MILLION = Decimal(1_000_000)
DEFAULT_COST_PATH = Path(__file__).resolve().parents[1] / "cost.json"
COST_SCHEMA = "harnessbench_token_cost_v1"


def attach_token_cost(
    trace_summary: dict[str, Any],
    model: str,
    *,
    cost_path: Path = DEFAULT_COST_PATH,
) -> dict[str, Any]:
    result = calculate_token_cost(
        trace_summary.get("token_usage"),
        model,
        cost_path=cost_path,
    )
    trace_summary["total_cost"] = result
    return result


def calculate_token_cost(
    token_usage: object,
    model: str,
    *,
    cost_path: Path = DEFAULT_COST_PATH,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": COST_SCHEMA,
        "status": "unavailable",
        "reason": None,
        "model": model,
        "matched_model": None,
        "currency": "USD",
        "amount": NOT_AVAILABLE,
        "input_amount": NOT_AVAILABLE,
        "cache_write_amount": NOT_AVAILABLE,
        "output_amount": NOT_AVAILABLE,
        "pricing": None,
        "cost_file": cost_path.name,
    }
    usage = token_usage if isinstance(token_usage, Mapping) else {}
    input_tokens = _nonnegative_integer(usage.get("input_tokens"))
    output_tokens = _nonnegative_integer(usage.get("output_tokens"))
    reported_cost = _nonnegative_decimal(usage.get("reported_cost_usd"))
    if input_tokens is None or output_tokens is None:
        if reported_cost is not None:
            return _reported_cost_result(base, reported_cost)
        base["reason"] = "input_or_output_tokens_unavailable"
        return base

    try:
        catalog = _load_catalog(cost_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        if reported_cost is not None:
            return _reported_cost_result(base, reported_cost)
        base["reason"] = f"cost_catalog_unavailable:{type(exc).__name__}"
        return base

    matched_model, raw_pricing = _resolve_model(catalog["models"], model)
    if matched_model is None or raw_pricing is None:
        if reported_cost is not None:
            return _reported_cost_result(base, reported_cost)
        base["reason"] = "model_pricing_unavailable"
        return base

    try:
        input_rate = _decimal(raw_pricing.get("input"))
        cached_rate = _decimal(raw_pricing.get("cached_input"))
        cache_write_rate = _decimal(
            raw_pricing.get("cache_write_input", raw_pricing.get("input"))
        )
        output_rate = _decimal(raw_pricing.get("output"))
    except ValueError:
        base["reason"] = "model_pricing_invalid"
        return base

    cached_tokens = _nonnegative_integer(usage.get("cached_input_tokens"))
    cache_write_tokens = _nonnegative_integer(
        usage.get("cache_write_input_tokens")
    )
    cache_available = cached_tokens is not None and cached_tokens <= input_tokens
    if cache_available:
        assert cached_tokens is not None
        cache_write_required = cache_write_rate != input_rate
        cache_write_available = (
            cache_write_tokens is not None
            and cached_tokens + cache_write_tokens <= input_tokens
        )
        effective_cache_write_tokens = (
            cache_write_tokens if cache_write_available else 0
        )
        uncached_tokens = (
            input_tokens - cached_tokens - effective_cache_write_tokens
        )
        cache_write_amount = (
            Decimal(effective_cache_write_tokens) * cache_write_rate
            / TOKENS_PER_MILLION
        )
        input_amount = (
            Decimal(uncached_tokens) * input_rate
            + Decimal(cached_tokens) * cached_rate
        ) / TOKENS_PER_MILLION + cache_write_amount
        if cache_write_required and not cache_write_available:
            status = "estimated"
            reason = "cache_write_input_tokens_unavailable"
        else:
            status = "available"
            reason = None
    else:
        # A missing cache counter must not block cost reporting.  Charge all
        # input at the ordinary rate and mark the result as a conservative
        # estimate rather than silently assuming a cache hit count of zero.
        input_amount = Decimal(input_tokens) * input_rate / TOKENS_PER_MILLION
        cache_write_amount = Decimal(0)
        status = "estimated"
        reason = "cached_input_tokens_unavailable"

    output_amount = Decimal(output_tokens) * output_rate / TOKENS_PER_MILLION
    amount = input_amount + output_amount
    model_effective_at = raw_pricing.get("effective_at")
    model_source = raw_pricing.get("source")
    pricing_snapshot = {
        "unit": catalog["unit"],
        "input": _decimal_text(input_rate),
        "cached_input": _decimal_text(cached_rate),
        "cache_write_input": _decimal_text(cache_write_rate),
        "output": _decimal_text(output_rate),
        "effective_at": (
            model_effective_at
            if isinstance(model_effective_at, str) and model_effective_at
            else catalog.get("effective_at")
        ),
        "source": (
            model_source
            if isinstance(model_source, str) and model_source
            else catalog.get("source")
        ),
    }
    if isinstance(raw_pricing.get("note"), str):
        pricing_snapshot["note"] = raw_pricing["note"]
    return {
        **base,
        "status": status,
        "reason": reason,
        "matched_model": matched_model,
        "currency": catalog["currency"],
        "amount": _decimal_text(amount),
        "input_amount": _decimal_text(input_amount),
        "cache_write_amount": _decimal_text(cache_write_amount),
        "output_amount": _decimal_text(output_amount),
        "pricing": pricing_snapshot,
    }


def _load_catalog(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != "harnessbench_model_cost_v1"
        or value.get("currency") != "USD"
        or value.get("unit") != "per_million_tokens"
        or not isinstance(value.get("models"), dict)
    ):
        raise ValueError("invalid cost catalog")
    return value


def _resolve_model(
    models: Mapping[str, object], model: str
) -> tuple[str | None, Mapping[str, object] | None]:
    candidates = [model]
    if ":" in model:
        candidates.append(model.split(":", 1)[1])
    for candidate in candidates:
        direct = models.get(candidate)
        if isinstance(direct, Mapping):
            return candidate, direct
        suffix_matches = [
            (name, pricing)
            for name, pricing in models.items()
            if name.endswith("/" + candidate) and isinstance(pricing, Mapping)
        ]
        if len(suffix_matches) == 1:
            return suffix_matches[0]
    return None, None


def _decimal(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid price") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid price")
    return parsed


def _nonnegative_decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _reported_cost_result(base: dict[str, Any], amount: Decimal) -> dict[str, Any]:
    return {
        **base,
        "status": "available",
        "reason": None,
        "amount": _decimal_text(amount),
        "pricing": {
            "unit": "reported_total",
            "source": "subscription_cli_reported_total_cost_usd",
        },
    }


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
