from __future__ import annotations

import json
from pathlib import Path

from harness_tracer.provider_requests import (
    PROVIDER_REQUEST_EVIDENCE_SCHEMA,
    PROVIDER_REQUEST_EVIDENCE_VERSION,
    extract_structured_tool_inventory,
)
from harnessbench.rate_limit_policy import (
    apply_rate_limit_skip,
    detect_provider_rate_limit,
)


def _write_provider_events(path: Path, *statuses: int) -> None:
    inventory = extract_structured_tool_inventory(b'{"tools":[]}')
    rows = [
        {
            "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
            "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
            "seq": sequence,
            "timestamp": f"2026-08-16T00:00:0{sequence}Z",
            "method": "POST",
            "path": "/v1/chat/completions",
            "upstream_status": status,
            "model_visible": 200 <= status < 300,
            "error_type": None,
            "tool_inventory": inventory,
        }
        for sequence, status in enumerate(statuses, start=1)
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_detects_tpm_from_429_provider_evidence_and_error_text(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    _write_provider_events(events, 200, 429, 429)
    summary = {
        "error_message": "Rate limit reached for TPM",
        "provider_request_evidence": {"events": str(events)},
    }

    evidence = detect_provider_rate_limit(summary)

    assert evidence == {
        "schema_version": "harnessbench_rate_limit_skip_v1",
        "status": "skipped",
        "reason": "provider_rate_limit",
        "policy": "skip_on_rate_limit",
        "classification": "tpm",
        "http_status": 429,
        "observed_request_count": 2,
        "request_seqs": [2, 3],
        "source": "provider_request_evidence",
    }


def test_429_without_disclosed_limit_dimension_is_not_guessed_as_tpm(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    _write_provider_events(events, 429)

    evidence = detect_provider_rate_limit(
        {"provider_request_evidence": {"events": str(events)}}
    )

    assert evidence is not None
    assert evidence["classification"] == "unspecified"
    assert evidence["request_seqs"] == [1]


def test_policy_is_opt_in_and_does_not_mutate_disabled_summary(
    tmp_path: Path,
) -> None:
    events = tmp_path / "events.jsonl"
    _write_provider_events(events, 429)
    summary = {"provider_request_evidence": {"events": str(events)}}

    assert apply_rate_limit_skip(summary, enabled=False) is None
    assert "skip" not in summary

    evidence = apply_rate_limit_skip(summary, enabled=True)

    assert evidence is not None
    assert summary["skip"] == evidence


def test_non_rate_limit_error_is_not_skipped() -> None:
    assert detect_provider_rate_limit({"error_message": "model failed"}) is None
