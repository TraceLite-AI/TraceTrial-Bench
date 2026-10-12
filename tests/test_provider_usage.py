from __future__ import annotations

from harnessbench.provider_request_recorder import PROVIDER_USAGE_SCHEMA
from harnessbench.provider_usage import (
    CACHE_MISSING_POLICY,
    PROVIDER_TOKEN_USAGE_SOURCE,
    attach_provider_token_usage,
)


def _provider_usage(*calls: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": PROVIDER_USAGE_SCHEMA,
        "status": "available",
        "request_count": len(calls),
        "observed_count": len(calls),
        "complete_count": len(calls),
        "calls": list(calls),
    }


def _call(
    request_seq: int,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int | str = "N/A",
    cache_write_input_tokens: int | str = "N/A",
    reasoning_output_tokens: int | str = "N/A",
) -> dict[str, object]:
    return {
        "request_seq": request_seq,
        "status": "available",
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def test_complete_provider_usage_replaces_native_aggregate() -> None:
    summary = {
        "token_usage": {
            "input_tokens": 999,
            "output_tokens": 999,
            "reported_cost_usd": 0.25,
            "calls": [{"usage_scope": "run"}],
        },
        "provider_request_evidence": {
            "usage": _provider_usage(
                _call(
                    1,
                    input_tokens=100,
                    output_tokens=10,
                    cached_input_tokens=40,
                    cache_write_input_tokens=3,
                    reasoning_output_tokens=4,
                ),
                _call(
                    2,
                    input_tokens=120,
                    output_tokens=12,
                    cached_input_tokens=60,
                    cache_write_input_tokens=2,
                    reasoning_output_tokens=5,
                ),
            )
        },
    }

    usage = attach_provider_token_usage(summary)

    assert usage is not None
    assert summary["token_usage"] == usage
    assert usage["source"] == PROVIDER_TOKEN_USAGE_SOURCE
    assert usage["input_tokens"] == 220
    assert usage["cached_input_tokens"] == 100
    assert usage["cache_write_input_tokens"] == 5
    assert usage["output_tokens"] == 22
    assert usage["reasoning_output_tokens"] == 9
    assert usage["total_tokens"] == 242
    assert usage["cache_hit_rate"] == 0.45454545
    assert usage["call_count"] == 2
    assert usage["reported_cost_usd"] == 0.25
    assert usage["cache_measurement"] == {
        "policy": CACHE_MISSING_POLICY,
        "observed_call_count": 2,
        "inferred_zero_call_count": 0,
    }
    assert [call["usage_scope"] for call in usage["calls"]] == ["call", "call"]
    assert [call["total_tokens"] for call in usage["calls"]] == [110, 132]
    assert [call["cached_input_tokens_source"] for call in usage["calls"]] == [
        "provider_response",
        "provider_response",
    ]


def test_missing_cache_is_zero_when_same_run_proves_support() -> None:
    summary = {
        "provider_request_evidence": {
            "usage": _provider_usage(
                _call(1, input_tokens=4_796, output_tokens=266),
                _call(2, input_tokens=5_093, output_tokens=814),
                _call(
                    3,
                    input_tokens=5_945,
                    output_tokens=19,
                    cached_input_tokens=4_096,
                    reasoning_output_tokens=13,
                ),
            )
        }
    }

    usage = attach_provider_token_usage(summary)

    assert usage is not None
    assert usage["input_tokens"] == 15_834
    assert usage["output_tokens"] == 1_099
    assert usage["cached_input_tokens"] == 4_096
    assert usage["cache_write_input_tokens"] == "N/A"
    assert usage["reasoning_output_tokens"] == "N/A"
    assert usage["cache_hit_rate"] == 0.25868384
    assert [call["cached_input_tokens"] for call in usage["calls"]] == [
        0,
        0,
        4_096,
    ]
    assert [call["cached_input_tokens_source"] for call in usage["calls"]] == [
        "inferred_zero",
        "inferred_zero",
        "provider_response",
    ]
    assert usage["cache_measurement"] == {
        "policy": CACHE_MISSING_POLICY,
        "observed_call_count": 1,
        "inferred_zero_call_count": 2,
    }
    assert summary["provider_request_evidence"]["usage"]["calls"][0][
        "cached_input_tokens"
    ] == "N/A"


def test_all_missing_cache_stays_na() -> None:
    summary = {
        "provider_request_evidence": {
            "usage": _provider_usage(
                _call(1, input_tokens=10, output_tokens=2),
                _call(2, input_tokens=20, output_tokens=3),
            )
        }
    }

    usage = attach_provider_token_usage(summary)

    assert usage is not None
    assert usage["cached_input_tokens"] == "N/A"
    assert usage["cache_hit_rate"] == "N/A"
    assert [call["cached_input_tokens"] for call in usage["calls"]] == [
        "N/A",
        "N/A",
    ]
    assert [call["cached_input_tokens_source"] for call in usage["calls"]] == [
        "unavailable",
        "unavailable",
    ]
    assert usage["cache_measurement"] == {
        "policy": CACHE_MISSING_POLICY,
        "observed_call_count": 0,
        "inferred_zero_call_count": 0,
    }


def test_partial_provider_usage_does_not_replace_native_totals() -> None:
    native = {
        "input_tokens": 30,
        "output_tokens": 5,
        "total_tokens": 35,
    }
    summary = {
        "token_usage": native,
        "provider_request_evidence": {
            "usage": {
                "schema_version": PROVIDER_USAGE_SCHEMA,
                "status": "partial",
                "request_count": 2,
                "observed_count": 1,
                "complete_count": 1,
                "calls": [
                    _call(1, input_tokens=10, output_tokens=2),
                    {
                        "request_seq": 2,
                        "status": "unavailable",
                        "input_tokens": "N/A",
                        "cached_input_tokens": "N/A",
                        "cache_write_input_tokens": "N/A",
                        "output_tokens": "N/A",
                        "reasoning_output_tokens": "N/A",
                        "total_tokens": "N/A",
                    },
                ],
            }
        },
    }

    assert attach_provider_token_usage(summary) is None
    assert summary["token_usage"] is native


def test_zero_token_provider_call_is_exact() -> None:
    summary = {
        "provider_request_evidence": {
            "usage": _provider_usage(
                _call(
                    1,
                    input_tokens=0,
                    output_tokens=0,
                    cached_input_tokens=0,
                    cache_write_input_tokens=0,
                    reasoning_output_tokens=0,
                )
            )
        }
    }

    usage = attach_provider_token_usage(summary)

    assert usage is not None
    assert usage["total_tokens"] == 0
    assert usage["cache_hit_rate"] == 0.0
