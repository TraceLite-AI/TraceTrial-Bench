from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harness_tracer.provider_requests import (
    ProviderRequestEvidenceError,
    read_provider_request_events,
)


RATE_LIMIT_SKIP_SCHEMA = "harnessbench_rate_limit_skip_v1"

_TPM_PATTERN = re.compile(
    r"(?:\btpm\b|tpm[_ -]?rate[_ -]?limit|tokens?\s+per\s+minute)",
    re.IGNORECASE,
)
_RPM_PATTERN = re.compile(
    r"(?:\brpm\b|rpm[_ -]?rate[_ -]?limit|requests?\s+per\s+minute)",
    re.IGNORECASE,
)
_RATE_LIMIT_PATTERN = re.compile(
    r"(?:\b429\b|too\s+many\s+requests|rate[_ -]?limit)",
    re.IGNORECASE,
)


def detect_provider_rate_limit(
    trace_summary: Mapping[str, object],
) -> dict[str, object] | None:
    """Return sanitized skip evidence for an observed provider rate limit.

    The provider recorder is authoritative because it observes the actual
    upstream HTTP exchange shared by all model-registry Docker harnesses.  Some
    providers disclose whether a 429 is TPM or RPM and others do not, so the
    policy records that distinction without guessing.
    """

    request_seqs = _provider_429_request_seqs(trace_summary)
    error_message = trace_summary.get("error_message")
    error_text = error_message if isinstance(error_message, str) else ""
    classification = _classification(error_text)
    explicit_rate_limit = bool(_RATE_LIMIT_PATTERN.search(error_text))

    if not request_seqs and not explicit_rate_limit:
        return None

    return {
        "schema_version": RATE_LIMIT_SKIP_SCHEMA,
        "status": "skipped",
        "reason": "provider_rate_limit",
        "policy": "skip_on_rate_limit",
        "classification": classification,
        "http_status": 429 if request_seqs or "429" in error_text else None,
        "observed_request_count": len(request_seqs),
        "request_seqs": request_seqs,
        "source": (
            "provider_request_evidence" if request_seqs else "execution_error"
        ),
    }


def _provider_429_request_seqs(
    trace_summary: Mapping[str, object],
) -> list[int]:
    evidence = trace_summary.get("provider_request_evidence")
    if not isinstance(evidence, Mapping):
        return []
    raw_path = evidence.get("events")
    if not isinstance(raw_path, str) or not raw_path:
        return []
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        return []
    try:
        rows = read_provider_request_events(path)
    except ProviderRequestEvidenceError:
        return []
    return [
        int(row["seq"])
        for row in rows
        if row.get("upstream_status") == 429
    ]


def _classification(error_text: str) -> str:
    if _TPM_PATTERN.search(error_text):
        return "tpm"
    if _RPM_PATTERN.search(error_text):
        return "rpm"
    return "unspecified"


def apply_rate_limit_skip(
    trace_summary: dict[str, Any], *, enabled: bool
) -> dict[str, object] | None:
    if not enabled:
        return None
    evidence = detect_provider_rate_limit(trace_summary)
    if evidence is not None:
        trace_summary["skip"] = evidence
    return evidence
