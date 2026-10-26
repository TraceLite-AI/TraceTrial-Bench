from __future__ import annotations

from typing import Any, Mapping


NOT_AVAILABLE = "N/A"
TIMING_METRICS_SCHEMA = "harnessbench_timing_metrics_v1"


def attach_timing_metrics(trace_summary: dict[str, Any]) -> dict[str, object]:
    metrics = calculate_timing_metrics(trace_summary)
    trace_summary["timing_metrics"] = metrics
    return metrics


def calculate_timing_metrics(
    trace_summary: Mapping[str, object],
) -> dict[str, object]:
    duration = _nonnegative_number(trace_summary.get("duration_ms"))
    evidence = trace_summary.get("provider_request_evidence")
    timing = evidence.get("timing") if isinstance(evidence, Mapping) else None
    step_count = _nonnegative_integer(trace_summary.get("step_count"))

    ttft: float | str = NOT_AVAILABLE
    throughput: float | str = NOT_AVAILABLE
    ttft_observed = 0
    decode_observed = 0
    decode_duration: float | None = None
    decode_basis = "N/A"
    source = "none"

    if isinstance(timing, Mapping):
        source = "provider_request_evidence"
        ttft_value = _nonnegative_number(timing.get("ttft_mean_ms"))
        if ttft_value is not None:
            ttft = round(ttft_value, 3)
        ttft_observed = _nonnegative_integer(timing.get("ttft_observed_count")) or 0
        decode_observed = (
            _nonnegative_integer(timing.get("decode_observed_count")) or 0
        )
        decode_duration = _positive_number(timing.get("decode_duration_ms"))
        decode_basis = str(
            timing.get("decode_basis")
            or "first_semantic_output_to_last_semantic_output"
        )

        usage = trace_summary.get("token_usage")
        output_tokens = (
            _nonnegative_integer(usage.get("output_tokens"))
            if isinstance(usage, Mapping)
            else None
        )
        # The token total covers the whole run, so only combine it with decode
        # timing when every provider request has a measurable decode interval.
        if (
            output_tokens is not None
            and output_tokens > 0
            and decode_duration is not None
            and step_count is not None
            and step_count > 0
            and decode_observed == step_count
        ):
            throughput = round(output_tokens / (decode_duration / 1000), 6)
    else:
        cli_timing = trace_summary.get("cli_stream_timing")
        if isinstance(cli_timing, Mapping):
            source = str(cli_timing.get("source") or "cli_stream_timing")
            ttft_value = _nonnegative_number(cli_timing.get("ttft_mean_ms"))
            if ttft_value is not None:
                ttft = round(ttft_value, 3)
            ttft_observed = (
                _nonnegative_integer(cli_timing.get("ttft_observed_count")) or 0
            )
            decode_observed = (
                _nonnegative_integer(cli_timing.get("decode_observed_count")) or 0
            )
            decode_duration = _positive_number(cli_timing.get("decode_duration_ms"))
            decode_basis = str(
                cli_timing.get("decode_basis")
                or "first_cli_output_to_last_cli_output"
            )

            usage = trace_summary.get("token_usage")
            output_tokens = (
                _nonnegative_integer(usage.get("output_tokens"))
                if isinstance(usage, Mapping)
                else None
            )
            request_count = _nonnegative_integer(cli_timing.get("request_count"))
            if (
                output_tokens is not None
                and output_tokens > 0
                and decode_duration is not None
                and request_count is not None
                and request_count > 0
                and decode_observed == request_count
            ):
                throughput = round(output_tokens / (decode_duration / 1000), 6)

    return {
        "schema_version": TIMING_METRICS_SCHEMA,
        "ttft_ms": ttft,
        "output_throughput_tokens_per_sec": throughput,
        "end_to_end_duration_ms": (
            round(duration, 3) if duration is not None else NOT_AVAILABLE
        ),
        "details": {
            "source": source,
            "end_to_end_scope": "trace_execution",
            "ttft_aggregation": "mean_per_model_call",
            "throughput_aggregation": "total_output_tokens_per_total_decode_time",
            "decode_basis": decode_basis,
            "step_count": step_count if step_count is not None else NOT_AVAILABLE,
            "ttft_observed_count": ttft_observed,
            "decode_observed_count": decode_observed,
        },
    }


def _nonnegative_integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _nonnegative_number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _positive_number(value: object) -> float | None:
    result = _nonnegative_number(value)
    return result if result is not None and result > 0 else None
