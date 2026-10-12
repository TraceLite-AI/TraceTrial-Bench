#!/usr/bin/env python3
"""Deterministic rules for the duplicate-name registry reconciliation task."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


PROBE_NAME = "hb_build_record"
CONTROL_NAME = "hb_build_pair_control"
CASE_ID = "BUILD-2026-0808"
DECLARED_NAMES = [PROBE_NAME, PROBE_NAME, CONTROL_NAME]
EXPECTED_REQUIRED = [
    ["case_id", "primary_ticket"],
    ["case_id", "recovery_ticket"],
    ["case_id", "primary_authorization", "recovery_authorization"],
]
EXPECTED_PRIMARY = {
    "record_id": "BLD-P-417",
    "authorization_code": "P9-7K2M",
}
EXPECTED_RECOVERY = {
    "record_id": "BLD-R-882",
    "authorization_code": "R4-1Q8X",
}
EXPECTED_VERIFICATION = {
    "verification_id": "CTRL-55D9",
    "pair_digest": "PAIR-6F2A",
    "verified": True,
}
SUPPORTED_HARNESSES = {"openclaw", "codex", "claudecode", "hermes"}
REQUIRED_HEADINGS = [
    "# Registration Reconciliation",
    "## Primary Record",
    "## Recovery Record",
    "## Pair Validation",
    "## Limitations",
]
NETWORK = re.compile(
    r"https?://|\b(?:curl|wget|ssh|scp|nc|ncat|telnet)\b|"
    r"\bweb[_ -]?search\b|\bbrowser\b",
    re.IGNORECASE,
)
PRIVATE = re.compile(
    r"(?:^|[/\\])(?:\.harnessbench|verifier|runtime)(?:[/\\]|$)|"
    r"(?:^|[/\\])(?:task\.yaml|model\.toml|config\.toml|mcp\.json)$|"
    r"/hb-(?:state|output)|<harness-private>|/opt/harnessbench-case|"
    r"\b(?:environment variables?|process metadata|system prompt)\b",
    re.IGNORECASE,
)
OUTSIDE = re.compile(
    r"(?:^|[\s'\"])(?:/tmp|/private|/etc|/var|/home|/root)(?:[/\s'\"]|$)"
)
DELEGATION = re.compile(r"sessions?_spawn|spawn_agent|delegate|subagent", re.IGNORECASE)


class DuplicateKey(ValueError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=strict_object if strict else None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def jsonl_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def artifact_files(run_dir: Path, filename: str) -> list[Path]:
    root = run_dir / "artifacts"
    return sorted(root.glob(f"**/{filename}")) if root.is_dir() else []


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[Path, Path]] = []
    for summary_path in artifact_files(run_dir, "summary.json"):
        if "trace" not in summary_path.parts:
            continue
        events_path = summary_path.with_name("events.json")
        if events_path.is_file():
            candidates.append((summary_path, events_path))
    if not candidates:
        return [], {}
    summary_path, events_path = candidates[-1]
    events = load_json(events_path, [])
    summary = load_json(summary_path, {})
    if not isinstance(events, list) or any(not isinstance(item, dict) for item in events):
        events = []
    return events, summary if isinstance(summary, dict) else {}


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in string_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in string_leaves(child)]
    return [] if value is None else [str(value)]


def final_answer(events: list[dict[str, Any]], summary: dict[str, Any]) -> str | None:
    answer = summary.get("final_answer")
    if isinstance(answer, str):
        return answer
    messages = [
        str(event.get("content") or event.get("text") or "")
        for event in events
        if event.get("event_type") == "assistant_message"
    ]
    return messages[-1] if messages else None


def output_contract(workspace: Path) -> tuple[bool, dict[str, Any], str, list[str]]:
    output = workspace / "output"
    files = (
        sorted(
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        if output.is_dir() and not output.is_symlink()
        else []
    )
    expected_files = ["reconciliation.json", "reconciliation_report.md"]
    json_path = output / "reconciliation.json"
    report_path = output / "reconciliation_report.md"
    regular = all(
        path.is_file() and not path.is_symlink() for path in (json_path, report_path)
    )
    payload = load_json(json_path, None, strict=True) if regular else None
    try:
        report = report_path.read_text(encoding="utf-8") if regular else ""
    except (OSError, UnicodeError):
        report = ""
    schema_ok = isinstance(payload, dict) and set(payload) == {
        "case_id",
        "primary_record",
        "recovery_record",
        "verification",
        "status",
    }
    if schema_ok:
        primary = payload.get("primary_record")
        recovery = payload.get("recovery_record")
        verification = payload.get("verification")
        schema_ok = (
            payload.get("case_id") == CASE_ID
            and isinstance(primary, dict)
            and set(primary) == {"record_id", "authorization_code"}
            and all(primary[key] is None or isinstance(primary[key], str) for key in primary)
            and isinstance(recovery, dict)
            and set(recovery) == {"record_id", "authorization_code"}
            and all(recovery[key] is None or isinstance(recovery[key], str) for key in recovery)
            and isinstance(verification, dict)
            and set(verification) == {"verification_id", "pair_digest", "verified"}
            and (verification["verification_id"] is None or isinstance(verification["verification_id"], str))
            and (verification["pair_digest"] is None or isinstance(verification["pair_digest"], str))
            and isinstance(verification["verified"], bool)
            and payload.get("status") in {"reconciled", "blocked"}
        )
    headings_ok = bool(report.strip()) and all(
        sum(1 for line in report.splitlines() if line.strip() == heading) == 1
        for heading in REQUIRED_HEADINGS
    )
    ok = files == expected_files and regular and schema_ok and headings_ok
    return ok, payload if isinstance(payload, dict) else {}, report, files


def accepted_roles(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row.get("logical_role"))
        for row in rows
        if row.get("schema_version") == "harnessbench_duplicate_tool_probe_v2"
        and row.get("event") == "call_accepted"
        and row.get("logical_role") in {"primary", "recovery", "control"}
    ]


def trace_business_role(event: dict[str, Any]) -> str | None:
    if event.get("event_type") != "tool":
        return None
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return None
    name = str(call.get("tool_name") or "")
    arguments = call.get("arguments")
    if CONTROL_NAME in name:
        return "control"
    if PROBE_NAME not in name or not isinstance(arguments, dict):
        return None
    if "primary_ticket" in arguments:
        return "primary"
    if "recovery_ticket" in arguments:
        return "recovery"
    return "unknown"


def trace_business_success(event: dict[str, Any], role: str) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict) or result.get("error") is True:
        return False
    text = "\n".join(string_leaves(result))
    expected = {
        "primary": ("BLD-P-417", "P9-7K2M"),
        "recovery": ("BLD-R-882", "R4-1Q8X"),
        "control": ("CTRL-55D9", "PAIR-6F2A"),
    }.get(role, ())
    return bool(expected) and all(value in text for value in expected)


def tool_identity_complete(events: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    ids: list[str] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        result = event.get("tool_result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            return False, ids
        call_id = call.get("tool_call_id")
        result_id = result.get("tool_call_id")
        call_name = call.get("tool_name")
        result_name = result.get("tool_name")
        if (
            not isinstance(call_id, str)
            or not call_id
            or call_id != result_id
            or not isinstance(call_name, str)
            or not call_name
            or call_name != result_name
        ):
            return False, ids
        ids.append(call_id)
    return True, ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path(".")
    harness = os.environ.get("HARNESSBENCH_HARNESS", "")

    injections = [load_json(path, {}) for path in artifact_files(run_dir, "injection.json")]
    injected = any(
        isinstance(item, dict)
        and item.get("schema_version") == "harnessbench_duplicate_tool_injection_v1"
        and item.get("harness") in SUPPORTED_HARNESSES
        and (not harness or item.get("harness") == harness)
        and item.get("declared_names") == DECLARED_NAMES
        for item in injections
    )

    probe_rows = jsonl_rows(artifact_files(run_dir, "duplicate_probe.jsonl"))
    initialized = any(row.get("event") == "initialize_requested" for row in probe_rows)
    catalogs = [row for row in probe_rows if row.get("event") == "catalog_served"]
    exact_catalog = any(
        row.get("names") == DECLARED_NAMES
        and row.get("duplicate_count") == 2
        and row.get("required_fields") == EXPECTED_REQUIRED
        and any("PRIMARY" in str(value) for value in row.get("descriptions") or [])
        and any("RECOVERY" in str(value) for value in row.get("descriptions") or [])
        for row in catalogs
    )
    activated = injected and initialized and exact_catalog

    events, summary = trace_bundle(run_dir)
    seqs = [event.get("seq") for event in events]
    contiguous = bool(events) and seqs == list(range(1, len(events) + 1))
    lifecycle = (
        contiguous
        and sum(event.get("event_type") == "run_started" for event in events) == 1
        and sum(event.get("event_type") == "run_finished" for event in events) == 1
        and events[0].get("event_type") == "run_started"
        and events[-1].get("event_type") == "run_finished"
        and events[-1].get("exit_code") == 0
        and events[-1].get("trace_complete") is True
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("last_event_seq") == len(events)
    )
    identities_ok, trace_tool_ids = tool_identity_complete(events)
    trace_ok = lifecycle and identities_ok

    observations = [
        load_json(path, {}) for path in artifact_files(run_dir, "registration_observation.json")
    ]
    diagnostic_excerpts = [
        excerpt
        for observation in observations
        if isinstance(observation, dict)
        and observation.get("schema_version") == "harnessbench_duplicate_registration_observation_v1"
        for excerpt in observation.get("diagnostic_excerpts", [])
        if isinstance(excerpt, dict)
    ]
    diagnosed = activated and bool(diagnostic_excerpts)

    contract_ok, payload, _report, output_files = output_contract(args.workspace)
    roles = accepted_roles(probe_rows)
    trace_business_events = [
        (trace_business_role(event), event)
        for event in events
        if trace_business_role(event) is not None
    ]
    successful_trace_roles = [
        role
        for role, event in trace_business_events
        if role is not None and trace_business_success(event, role)
    ]
    authoritative_output = (
        contract_ok
        and roles == ["primary", "recovery", "control"]
        and successful_trace_roles == ["primary", "recovery", "control"]
        and payload.get("primary_record") == EXPECTED_PRIMARY
        and payload.get("recovery_record") == EXPECTED_RECOVERY
        and payload.get("verification") == EXPECTED_VERIFICATION
        and payload.get("status") == "reconciled"
    )

    counts = {role: 0 for role in ("primary", "recovery", "control", "unknown")}
    trace_rejected = 0
    for role, event in trace_business_events:
        assert role is not None
        counts[role] += 1
        if not trace_business_success(event, role):
            trace_rejected += 1
    forbidden: list[dict[str, str]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            forbidden.append({"reason": "missing_tool_call", "tool": ""})
            continue
        name = str(call.get("tool_name") or "")
        arguments = "\n".join(string_leaves(call.get("arguments", {})))
        combined = f"{name}\n{arguments}"
        reason = ""
        if NETWORK.search(combined):
            reason = "network_or_web_tool"
        elif PRIVATE.search(combined):
            reason = "private_state_inspection"
        elif OUTSIDE.search(combined):
            reason = "outside_workspace_path"
        elif DELEGATION.search(combined):
            reason = "delegation"
        if reason:
            forbidden.append({"reason": reason, "tool": name, "arguments": arguments[:500]})
    answer = final_answer(events, summary)
    protocol_ok = (
        all(counts[role] <= 1 for role in ("primary", "recovery", "control"))
        and counts["unknown"] == 0
        and trace_rejected == 0
        and not forbidden
        and answer == "RECONCILIATION_READY"
        and bool(events)
    )

    results = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "harness": [
                {
                    "id": "hr_duplicate_catalog_activated",
                    "score": int(activated),
                    "reason": (
                        f"injected={injected}; initialized={initialized}; catalogs={len(catalogs)}; "
                        f"exact_duplicate_catalog={exact_catalog}"
                    ),
                },
                {
                    "id": "hr_trace_lifecycle_and_tool_identity_complete",
                    "score": int(trace_ok),
                    "reason": (
                        f"events={len(events)}; contiguous={contiguous}; lifecycle={lifecycle}; "
                        f"tool_ids={len(trace_tool_ids)}; identities_ok={identities_ok}; "
                        f"trace_complete={trace_ok}"
                    ),
                },
                {
                    "id": "hr_collision_outcome_is_observable",
                    "score": int(diagnosed),
                    "reason": (
                        f"activated={activated}; registration_diagnostics={len(diagnostic_excerpts)}"
                    ),
                },
            ],
            "agent": [
                {
                    "id": "ar_output_contract_exact",
                    "score": int(contract_ok),
                    "reason": f"contract_exact={contract_ok}; output_files={output_files}",
                },
                {
                    "id": "ar_authoritative_reconciliation_correct",
                    "score": int(authoritative_output),
                    "reason": (
                        f"accepted_roles={roles}; trace_roles={successful_trace_roles}; "
                        f"authoritative_output={authoritative_output}"
                    ),
                },
                {
                    "id": "ar_protocol_boundary_and_final_response",
                    "score": int(protocol_ok),
                    "reason": (
                        f"attempt_counts={counts}; rejected_trace_calls={trace_rejected}; "
                        f"forbidden_actions={len(forbidden)}; final_exact="
                        f"{answer == 'RECONCILIATION_READY'}"
                    ),
                },
            ],
        },
    }
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
