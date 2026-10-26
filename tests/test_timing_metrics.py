from __future__ import annotations

from tracetrial_bench.timing_metrics import calculate_timing_metrics


def test_calculates_task_metrics_from_complete_model_call_timing() -> None:
    metrics = calculate_timing_metrics(
        {
            "duration_ms": 12_000,
            "step_count": 2,
            "token_usage": {"output_tokens": 240},
            "provider_request_evidence": {
                "timing": {
                    "ttft_mean_ms": 450.0,
                    "ttft_observed_count": 2,
                    "decode_duration_ms": 4_000.0,
                    "decode_observed_count": 2,
                    "decode_basis": "first_semantic_output_to_response_eof",
                }
            },
        }
    )

    assert metrics["ttft_ms"] == 450.0
    assert metrics["output_throughput_tokens_per_sec"] == 60.0
    assert metrics["end_to_end_duration_ms"] == 12_000.0
    assert metrics["details"]["ttft_aggregation"] == "mean_per_model_call"
    assert (
        metrics["details"]["decode_basis"]
        == "first_semantic_output_to_response_eof"
    )


def test_subscription_mode_degrades_model_call_metrics_to_na() -> None:
    metrics = calculate_timing_metrics(
        {
            "duration_ms": 8_500,
            "step_count": "N/A",
            "token_usage": {"output_tokens": 100},
        }
    )

    assert metrics["ttft_ms"] == "N/A"
    assert metrics["output_throughput_tokens_per_sec"] == "N/A"
    assert metrics["end_to_end_duration_ms"] == 8_500.0


def test_subscription_cli_stream_timing_supplies_ttft_and_throughput() -> None:
    metrics = calculate_timing_metrics(
        {
            "duration_ms": 8_500,
            "step_count": "N/A",
            "token_usage": {"output_tokens": 100},
            "cli_stream_timing": {
                "source": "cli_json_stdout_line_observation",
                "request_count": 1,
                "ttft_mean_ms": 500.0,
                "ttft_observed_count": 1,
                "decode_duration_ms": 2_000.0,
                "decode_observed_count": 1,
            },
        }
    )

    assert metrics["ttft_ms"] == 500.0
    assert metrics["output_throughput_tokens_per_sec"] == 50.0
    assert metrics["details"]["source"] == "cli_json_stdout_line_observation"


def test_throughput_is_na_when_decode_timing_does_not_cover_every_call() -> None:
    metrics = calculate_timing_metrics(
        {
            "duration_ms": 1_000,
            "step_count": 2,
            "token_usage": {"output_tokens": 20},
            "provider_request_evidence": {
                "timing": {
                    "ttft_mean_ms": 100.0,
                    "ttft_observed_count": 2,
                    "decode_duration_ms": 500.0,
                    "decode_observed_count": 1,
                }
            },
        }
    )

    assert metrics["ttft_ms"] == 100.0
    assert metrics["output_throughput_tokens_per_sec"] == "N/A"


def test_throughput_is_na_when_complete_decode_duration_rounds_to_zero() -> None:
    metrics = calculate_timing_metrics(
        {
            "duration_ms": 1_000,
            "step_count": 1,
            "token_usage": {"output_tokens": 20},
            "provider_request_evidence": {
                "timing": {
                    "ttft_mean_ms": 100.0,
                    "ttft_observed_count": 1,
                    "decode_duration_ms": 0.0,
                    "decode_observed_count": 1,
                    "decode_basis": "first_semantic_output_to_response_eof",
                }
            },
        }
    )

    assert metrics["ttft_ms"] == 100.0
    assert metrics["output_throughput_tokens_per_sec"] == "N/A"
