from __future__ import annotations

import copy
from typing import Any, Mapping

from tracetrial_bench.provider_request_recorder import PROVIDER_USAGE_SCHEMA


NOT_AVAILABLE = "N/A"
PROVIDER_TOKEN_USAGE_SOURCE = "provider_response_usage"
CACHE_MISSING_POLICY = "missing_as_zero_when_any_call_reports_cache"
_OPTIONAL_TOKEN_FIELDS = (
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)


def attach_provider_token_usage(
    trace_summary: dict[str, Any],
) -> dict[str, object] | None:
    """Prefer complete per-request provider usage over harness aggregates.

    Provider response usage is observed at the same runner-owned boundary used
    for exact model-call counting. It is only promoted when every request has
    both input and output counts; partial evidence remains available under
    provider_request_evidence without weakening an existing native aggregate.
    """

    evidence = trace_summary.get("provider_request_evidence")
    usage = evidence.get("usage") if isinstance(evidence, Mapping) else None
    if (
        not isinstance(usage, Mapping)
        or usage.get("schema_version") != PROVIDER_USAGE_SCHEMA
        or usage.get("status") != "available"
    ):
        return None
    request_count = _nonnegative_integer(usage.get("request_count"))
    complete_count = _nonnegative_integer(usage.get("complete_count"))
    calls = usage.get("calls")
    if (
        request_count is None
        or request_count <= 0
        or complete_count != request_count
        or not isinstance(calls, list)
        or len(calls) != request_count
    ):
        return None

    cache_observed_count = sum(
        1
        for call in calls
        if isinstance(call, Mapping)
        and _nonnegative_integer(call.get("cached_input_tokens")) is not None
    )
    cache_field_supported = cache_observed_count > 0
    cache_inferred_zero_count = 0

    normalized_calls: list[dict[str, object]] = []
    for index, call in enumerate(calls, start=1):
        if not isinstance(call, Mapping):
            return None
        input_tokens = _nonnegative_integer(call.get("input_tokens"))
        output_tokens = _nonnegative_integer(call.get("output_tokens"))
        if (
            call.get("request_seq") != index
            or input_tokens is None
            or output_tokens is None
        ):
            return None
        cached_input_tokens = _nonnegative_integer(call.get("cached_input_tokens"))
        if cached_input_tokens is not None:
            cached_input_tokens_source = "provider_response"
        elif cache_field_supported:
            cached_input_tokens = 0
            cached_input_tokens_source = "inferred_zero"
            cache_inferred_zero_count += 1
        else:
            cached_input_tokens = NOT_AVAILABLE
            cached_input_tokens_source = "unavailable"
        normalized_calls.append(
            {
                "call_index": index,
                "request_seq": index,
                "source": PROVIDER_TOKEN_USAGE_SOURCE,
                "usage_scope": "call",
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cached_input_tokens_source": cached_input_tokens_source,
                "cache_write_input_tokens": _token_or_na(
                    call.get("cache_write_input_tokens")
                ),
                "output_tokens": output_tokens,
                "reasoning_output_tokens": _token_or_na(
                    call.get("reasoning_output_tokens")
                ),
                "total_tokens": input_tokens + output_tokens,
            }
        )

    input_total = sum(int(call["input_tokens"]) for call in normalized_calls)
    output_total = sum(int(call["output_tokens"]) for call in normalized_calls)
    result: dict[str, object] = {
        "source": PROVIDER_TOKEN_USAGE_SOURCE,
        "usage_scope": "run",
        "input_tokens": input_total,
        "cached_input_tokens": _complete_optional_total(
            normalized_calls, "cached_input_tokens"
        ),
        "cache_write_input_tokens": _complete_optional_total(
            normalized_calls, "cache_write_input_tokens"
        ),
        "output_tokens": output_total,
        "reasoning_output_tokens": _complete_optional_total(
            normalized_calls, "reasoning_output_tokens"
        ),
        "total_tokens": input_total + output_total,
        "cache_hit_rate": NOT_AVAILABLE,
        "cache_measurement": {
            "policy": CACHE_MISSING_POLICY,
            "observed_call_count": cache_observed_count,
            "inferred_zero_call_count": cache_inferred_zero_count,
        },
        "call_count": request_count,
        "calls": normalized_calls,
    }
    cached_total = result["cached_input_tokens"]
    if isinstance(cached_total, int) and not isinstance(cached_total, bool):
        if input_total > 0 and cached_total <= input_total:
            result["cache_hit_rate"] = round(cached_total / input_total, 8)
        elif input_total == 0 and cached_total == 0:
            result["cache_hit_rate"] = 0.0
        else:
            result["cached_input_tokens"] = NOT_AVAILABLE

    native_usage = trace_summary.get("token_usage")
    if isinstance(native_usage, Mapping):
        reported_cost = native_usage.get("reported_cost_usd")
        if _nonnegative_number(reported_cost) is not None:
            result["reported_cost_usd"] = copy.deepcopy(reported_cost)
    trace_summary["token_usage"] = result
    return result


def _complete_optional_total(
    calls: list[dict[str, object]], name: str
) -> int | str:
    if name not in _OPTIONAL_TOKEN_FIELDS:
        raise ValueError(f"unsupported optional token field: {name}")
    values = [_nonnegative_integer(call.get(name)) for call in calls]
    if any(value is None for value in values):
        return NOT_AVAILABLE
    return sum(value for value in values if value is not None)


def _token_or_na(value: object) -> int | str:
    token_count = _nonnegative_integer(value)
    return token_count if token_count is not None else NOT_AVAILABLE


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _nonnegative_number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ):
        return float(value)
    return None
