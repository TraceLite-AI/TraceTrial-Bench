from __future__ import annotations

from typing import Any, Mapping


NOT_AVAILABLE = "N/A"
STEP_COUNT_SCHEMA = "harnessbench_step_count_v1"
STEP_DEFINITION = "model_invocation"


def attach_step_count(trace_summary: dict[str, Any]) -> int | str:
    step_count, details = calculate_step_count(trace_summary)
    trace_summary["step_count"] = step_count
    trace_summary["step_count_details"] = details
    return step_count


def calculate_step_count(
    trace_summary: Mapping[str, object],
) -> tuple[int | str, dict[str, object]]:
    provider_evidence = trace_summary.get("provider_request_evidence")
    if isinstance(provider_evidence, Mapping):
        request_count = _nonnegative_integer(provider_evidence.get("request_count"))
        if request_count is not None:
            return request_count, _details(
                status="exact",
                source="provider_request_evidence",
                observed_call_events=request_count,
                excluded_internal_events=0,
            )

    token_usage = trace_summary.get("token_usage")
    calls = token_usage.get("calls") if isinstance(token_usage, Mapping) else None
    if not isinstance(calls, list) or not calls:
        return NOT_AVAILABLE, _details(
            status="unavailable",
            source="none",
            reason="no_model_call_evidence",
        )

    scopes = [
        call.get("usage_scope", "unknown")
        for call in calls
        if isinstance(call, Mapping)
    ]
    if len(scopes) != len(calls) or any(
        scope not in {"call", "turn", "internal"} for scope in scopes
    ):
        return NOT_AVAILABLE, _details(
            status="unavailable",
            source="token_usage",
            reason="aggregate_or_unknown_usage_scope",
        )

    call_count = sum(scope in {"call", "turn"} for scope in scopes)
    internal_count = sum(scope == "internal" for scope in scopes)
    return call_count, _details(
        status="derived",
        source="per_call_token_usage",
        observed_call_events=call_count,
        excluded_internal_events=internal_count,
    )


def _details(
    *,
    status: str,
    source: str,
    reason: str | None = None,
    observed_call_events: int | None = None,
    excluded_internal_events: int | None = None,
) -> dict[str, object]:
    return {
        "schema_version": STEP_COUNT_SCHEMA,
        "status": status,
        "source": source,
        "definition": STEP_DEFINITION,
        "reason": reason,
        "observed_call_events": observed_call_events,
        "excluded_internal_events": excluded_internal_events,
    }


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None
