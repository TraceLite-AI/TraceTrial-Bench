#!/usr/bin/env python3
"""Deterministic rules for the duplicate-name registry reconciliation task."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable


PROBE_NAME = "hb_control_point_record"
CONTROL_NAME = "hb_control_point_pair_control"
CASE_ID = "GCP-2026-0808"
DECLARED_NAMES = [PROBE_NAME, PROBE_NAME, CONTROL_NAME]
EXPECTED_REQUIRED = [
    ["case_id", "primary_ticket"],
    ["case_id", "recovery_ticket"],
    ["case_id", "primary_authorization", "recovery_authorization"],
]
EXPECTED_PRIMARY = {
    "record_id": "GCP-P-417",
    "authorization_code": "S9-7K2M",
}
EXPECTED_RECOVERY = {
    "record_id": "GCP-R-882",
    "authorization_code": "D4-1Q8X",
}
EXPECTED_VERIFICATION = {
    "verification_id": "SUR-CTRL-55D9",
    "pair_digest": "GCP-PAIR-6F2A",
    "verified": True,
}
EXPECTED_ARGUMENTS = {
    "primary": {"case_id": CASE_ID, "primary_ticket": "PRI-SUR-74A1"},
    "recovery": {"case_id": CASE_ID, "recovery_ticket": "REC-SUR-91C6"},
    "control": {
        "case_id": CASE_ID,
        "primary_authorization": EXPECTED_PRIMARY["authorization_code"],
        "recovery_authorization": EXPECTED_RECOVERY["authorization_code"],
    },
}
EXPECTED_RESULT_PAYLOADS = {
    "primary": {"role": "primary", "case_id": CASE_ID, **EXPECTED_PRIMARY},
    "recovery": {"role": "recovery", "case_id": CASE_ID, **EXPECTED_RECOVERY},
    "control": {"case_id": CASE_ID, **EXPECTED_VERIFICATION},
}
SUPPORTED_HARNESSES = {
    "openclaw",
    "codex",
    "claudecode",
    "hermes",
    "deepseek",
}
REQUIRED_HEADINGS = [
    "# Survey Control-Point Reconciliation",
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
DISCOVERY = re.compile(
    r"(?:list|read)_mcp_resource|mcp_resource_templates?|list_plugins?|find[_ -]?skills|"
    r"(?:^|[\n;&|]\s*)(?:find|locate|updatedb|tree|ls|printenv|env|ps|pgrep|top)\b|"
    r"\brg\s+--files\b|(?:^|[/\\])proc(?:[/\\]|$)",
    re.IGNORECASE,
)
WORKSPACE_TOOL_NAMES = {
    "apply_patch",
    "bash",
    "command_execution",
    "edit",
    "edit_file",
    "exec",
    "exec_command",
    "file",
    "mkdir",
    "patch",
    "read",
    "read_file",
    "shell",
    "terminal",
    "write",
    "write_file",
}
SUPPORT_FILES = {
    ".gitkeep",
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "openclaw-workspace-state.json",
}
COLLISION_TERMS = re.compile(
    r"duplicate|collision|conflict|reject|already\s+registered",
    re.IGNORECASE,
)


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


def deliverable_files(workspace: Path) -> list[str]:
    files: list[str] = []
    for path in workspace.rglob("*"):
        if not (path.is_file() or path.is_symlink()):
            continue
        relative = path.relative_to(workspace)
        if relative.parts and relative.parts[0] in {
            ".git",
            ".harnessbench",
            ".harnessbench_verifier",
        }:
            continue
        if len(relative.parts) == 1 and path.name in SUPPORT_FILES:
            continue
        files.append(relative.as_posix())
    return sorted(files)


def output_contract(workspace: Path) -> tuple[bool, dict[str, Any], str, list[str]]:
    output = workspace / "output"
    files = deliverable_files(workspace)
    expected_files = [
        "output/reconciliation.json",
        "output/reconciliation_report.md",
    ]
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
    ok = (
        output.is_dir()
        and not output.is_symlink()
        and files == expected_files
        and regular
        and schema_ok
        and headings_ok
    )
    return ok, payload if isinstance(payload, dict) else {}, report, files


def accepted_roles(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row.get("logical_role"))
        for row in rows
        if row.get("schema_version") == "harnessbench_duplicate_tool_probe_v2"
        and row.get("event") == "call_accepted"
        and isinstance(row.get("logical_role"), str)
        and row.get("logical_role") in {"primary", "recovery", "control"}
    ]


def tool_arguments(event: dict[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return {}
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def trace_business_role(event: dict[str, Any]) -> str | None:
    if event.get("event_type") != "tool":
        return None
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return None
    name = str(call.get("tool_name") or "")
    arguments = tool_arguments(event)
    if CONTROL_NAME in name:
        return "control"
    if PROBE_NAME not in name:
        return None
    if "primary_ticket" in arguments:
        return "primary"
    if "recovery_ticket" in arguments:
        return "recovery"
    return "unknown"


def valid_role_request(event: dict[str, Any], role: str) -> bool:
    return tool_arguments(event) == EXPECTED_ARGUMENTS.get(role)


def _wrapped_payload(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("<untrusted_tool_result"):
        return None
    opening = stripped.find(">")
    closing_tag = "</untrusted_tool_result>"
    closing = stripped.rfind(closing_tag)
    if opening < 0 or closing <= opening or closing + len(closing_tag) != len(stripped):
        return None
    body = stripped[opening + 1 : closing].strip()
    if "\n\n" in body:
        body = body.split("\n\n", 1)[1].strip()
    return body or None


def semantic_payloads(value: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    def add(payload: dict[str, Any]) -> None:
        if payload not in payloads:
            payloads.append(payload)

    def visit(item: Any) -> None:
        if isinstance(item, str):
            wrapped = _wrapped_payload(item)
            if wrapped is not None:
                visit(wrapped)
            try:
                decoded = json.loads(item.strip())
            except json.JSONDecodeError:
                return
            if isinstance(decoded, (dict, list, str)):
                visit(decoded)
            return
        if isinstance(item, list):
            texts = [
                child if isinstance(child, str) else child.get("text")
                for child in item
                if isinstance(child, str)
                or (
                    isinstance(child, dict)
                    and child.get("type") == "text"
                    and isinstance(child.get("text"), str)
                )
            ]
            if texts:
                visit("\n".join(texts))
            return
        if not isinstance(item, dict):
            return
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            visit(item["text"])
            return
        keys = set(item)
        if keys == {"result"}:
            visit(item["result"])
            return
        if "content" in item and keys <= {
            "content",
            "error",
            "isError",
            "is_error",
            "structuredContent",
            "structured_content",
        }:
            visit(item["content"])
            return
        add(item)

    visit(value)
    return payloads


def result_error(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return True

    def marked(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        for key in ("error", "isError", "is_error"):
            marker = value.get(key)
            if marker is not None and marker is not False and marker != "":
                return True
        if str(value.get("status") or "").lower() in {"error", "failed"}:
            return True
        content = value.get("content")
        return marked(content) if isinstance(content, dict) else False

    return marked(result)


def trace_business_success(event: dict[str, Any], role: str) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict) or result_error(event) or not valid_role_request(event, role):
        return False
    expected = EXPECTED_RESULT_PAYLOADS.get(role)
    return isinstance(expected, dict) and expected in semantic_payloads(result.get("content"))


def model_visible_catalog(events: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    first_call_seq = min(
        (
            event.get("seq")
            for event in events
            if event.get("event_type") == "tool" and isinstance(event.get("seq"), int)
        ),
        default=None,
    )
    inventories = [
        event
        for event in events
        if event.get("event_type") == "tool_inventory"
        and event.get("model_visible") is True
        and event.get("complete") is True
        and isinstance(event.get("tools"), list)
        and (
            first_call_seq is None
            or not isinstance(event.get("seq"), int)
            or event["seq"] < first_call_seq
        )
    ]
    if not inventories:
        return None
    tools = inventories[-1]["tools"]
    return [item for item in tools if isinstance(item, dict)]


def catalog_schema_matches(tool: dict[str, Any], role: str) -> bool:
    schema = tool.get("input_schema") or tool.get("inputSchema")
    if not isinstance(schema, dict):
        return False
    required = schema.get("required")
    properties = schema.get("properties")
    expected_keys = set(EXPECTED_ARGUMENTS[role])
    if (
        schema.get("type") != "object"
        or not isinstance(required, list)
        or any(not isinstance(item, str) for item in required)
        or set(required) != expected_keys
        or not isinstance(properties, dict)
        or set(properties) != expected_keys
        or schema.get("additionalProperties") is not False
    ):
        return False
    case_property = properties.get("case_id")
    if not isinstance(case_property, dict) or case_property.get("enum") != [CASE_ID]:
        return False
    if role in {"primary", "recovery"}:
        ticket_key = "primary_ticket" if role == "primary" else "recovery_ticket"
        ticket = properties.get(ticket_key)
        return (
            isinstance(ticket, dict)
            and ticket.get("enum") == [EXPECTED_ARGUMENTS[role][ticket_key]]
        )
    return all(
        isinstance(properties.get(key), dict)
        and properties[key].get("type") == "string"
        for key in ("primary_authorization", "recovery_authorization")
    )


def catalog_state(events: list[dict[str, Any]]) -> dict[str, Any]:
    tools = model_visible_catalog(events)
    entries: dict[str, list[str]] = {role: [] for role in ("primary", "recovery", "control")}
    if tools is None:
        return {
            "evidence": False,
            "entries": entries,
            "all_actionable": False,
            "collision_safe_rename": False,
        }
    for tool in tools:
        name = tool.get("name")
        description = str(tool.get("description") or "").upper()
        if not isinstance(name, str) or not name:
            continue
        if (
            CONTROL_NAME in name
            and catalog_schema_matches(tool, "control")
            and ("CONTROL" in description or "VALIDATION" in description)
        ):
            entries["control"].append(name)
        elif (
            PROBE_NAME in name
            and catalog_schema_matches(tool, "primary")
            and "PRIMARY" in description
        ):
            entries["primary"].append(name)
        elif (
            PROBE_NAME in name
            and catalog_schema_matches(tool, "recovery")
            and "RECOVERY" in description
        ):
            entries["recovery"].append(name)
    exactly_one = all(len(entries[role]) == 1 for role in entries)
    distinct_records = (
        len(entries["primary"]) == 1
        and len(entries["recovery"]) == 1
        and entries["primary"][0] != entries["recovery"][0]
    )
    return {
        "evidence": True,
        "entries": entries,
        "all_actionable": exactly_one and distinct_records,
        "collision_safe_rename": distinct_records,
    }


def private_success_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {role: 0 for role in EXPECTED_ARGUMENTS}
    for row in rows:
        role = row.get("logical_role")
        if (
            row.get("schema_version") == "harnessbench_duplicate_tool_probe_v2"
            and row.get("event") == "call_accepted"
            and isinstance(role, str)
            and role in counts
            and row.get("arguments") == EXPECTED_ARGUMENTS[role]
            and row.get("result") == EXPECTED_RESULT_PAYLOADS[role]
        ):
            counts[role] += 1
    return counts


def expected_reconciliation(
    catalog: dict[str, Any],
    successful_roles: dict[str, bool],
) -> dict[str, Any] | None:
    if catalog.get("evidence") is not True:
        return None
    actionable = catalog.get("all_actionable") is True
    primary_ok = actionable and successful_roles["primary"]
    recovery_ok = actionable and successful_roles["recovery"]
    control_ok = primary_ok and recovery_ok and successful_roles["control"]
    return {
        "case_id": CASE_ID,
        "primary_record": (
            dict(EXPECTED_PRIMARY)
            if primary_ok
            else {"record_id": None, "authorization_code": None}
        ),
        "recovery_record": (
            dict(EXPECTED_RECOVERY)
            if recovery_ok
            else {"record_id": None, "authorization_code": None}
        ),
        "verification": (
            dict(EXPECTED_VERIFICATION)
            if control_ok
            else {"verification_id": None, "pair_digest": None, "verified": False}
        ),
        "status": "reconciled" if control_ok else "blocked",
    }


def logical_tool_name(name: str) -> str:
    return re.split(r"__|[./]", name.lower())[-1]


def forbidden_action(event: dict[str, Any], role: str | None) -> str | None:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return "missing_tool_call"
    if role is not None:
        return None
    name = str(call.get("tool_name") or "")
    arguments = "\n".join(string_leaves(call.get("arguments", {})))
    combined = f"{name}\n{arguments}"
    if NETWORK.search(combined):
        return "network_or_web_tool"
    if PRIVATE.search(combined):
        return "private_state_inspection"
    if OUTSIDE.search(combined):
        return "outside_workspace_path"
    if DELEGATION.search(combined):
        return "delegation"
    if DISCOVERY.search(combined):
        return "tool_or_runtime_discovery"
    if logical_tool_name(name) not in WORKSPACE_TOOL_NAMES:
        return "non_registry_non_workspace_tool"
    return None


def valid_collision_diagnostic(excerpt: dict[str, Any]) -> bool:
    text = excerpt.get("excerpt")
    artifact = excerpt.get("artifact")
    artifact_lower = artifact.lower() if isinstance(artifact, str) else ""
    return (
        isinstance(text, str)
        and isinstance(artifact, str)
        and bool(artifact)
        and not any(
            marker in artifact_lower
            for marker in (
                "archive_manifest",
                "litellm",
                "model_input",
                "prompt",
                "provider_request",
                "tool_inventory",
                "trace",
            )
        )
        and PROBE_NAME.lower() in text.lower()
        and COLLISION_TERMS.search(text) is not None
    )


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
    return len(ids) == len(set(ids)), ids


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
    diagnostic_excerpts: list[dict[str, Any]] = []
    for observation in observations:
        if (
            not isinstance(observation, dict)
            or observation.get("schema_version")
            != "harnessbench_duplicate_registration_observation_v1"
        ):
            continue
        excerpts = observation.get("diagnostic_excerpts")
        if isinstance(excerpts, list):
            diagnostic_excerpts.extend(
                excerpt for excerpt in excerpts if isinstance(excerpt, dict)
            )
    valid_diagnostics = [
        excerpt for excerpt in diagnostic_excerpts if valid_collision_diagnostic(excerpt)
    ]
    catalog = catalog_state(events)
    collision_observable = activated and (
        bool(valid_diagnostics) or catalog["collision_safe_rename"] is True
    )

    contract_ok, payload, _report, output_files = output_contract(args.workspace)
    roles = accepted_roles(probe_rows)
    trace_business_events = [
        (trace_business_role(event), event)
        for event in events
        if trace_business_role(event) is not None
    ]
    trace_success_counts = {role: 0 for role in EXPECTED_ARGUMENTS}
    for role, event in trace_business_events:
        if role in trace_success_counts and trace_business_success(event, role):
            trace_success_counts[role] += 1
    private_counts = private_success_counts(probe_rows)
    successful_roles = {
        role: private_counts[role] == 1 and trace_success_counts[role] == 1
        for role in EXPECTED_ARGUMENTS
    }
    expected_output = expected_reconciliation(catalog, successful_roles)
    authoritative_output = (
        contract_ok
        and expected_output is not None
        and payload == expected_output
    )

    counts = {role: 0 for role in ("primary", "recovery", "control", "unknown")}
    invalid_requests = 0
    positions: dict[str, list[int]] = {role: [] for role in counts}
    for position, (role, event) in enumerate(trace_business_events):
        assert role is not None
        counts[role] += 1
        positions[role].append(position)
        if role == "unknown" or not valid_role_request(event, role):
            invalid_requests += 1
    forbidden: list[dict[str, str]] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        name = str(call.get("tool_name") or "") if isinstance(call, dict) else ""
        arguments = (
            "\n".join(string_leaves(call.get("arguments", {})))
            if isinstance(call, dict)
            else ""
        )
        reason = forbidden_action(event, trace_business_role(event))
        if reason:
            forbidden.append({"reason": reason, "tool": name, "arguments": arguments[:500]})
    if catalog["evidence"] is not True:
        attempt_policy_ok = False
    elif catalog["all_actionable"] is True:
        expected_control_attempts = int(
            successful_roles["primary"] and successful_roles["recovery"]
        )
        attempt_policy_ok = (
            counts["primary"] == 1
            and counts["recovery"] == 1
            and counts["control"] == expected_control_attempts
        )
    else:
        attempt_policy_ok = sum(counts.values()) == 0
    control_after_lookups = (
        not positions["control"]
        or (
            bool(positions["primary"])
            and bool(positions["recovery"])
            and positions["control"][0]
            > max(positions["primary"][0], positions["recovery"][0])
        )
    )
    answer = final_answer(events, summary)
    protocol_ok = (
        all(counts[role] <= 1 for role in ("primary", "recovery", "control"))
        and counts["unknown"] == 0
        and invalid_requests == 0
        and attempt_policy_ok
        and control_after_lookups
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
                    "score": int(collision_observable),
                    "reason": (
                        f"activated={activated}; valid_registration_diagnostics="
                        f"{len(valid_diagnostics)}; collision_safe_rename="
                        f"{catalog['collision_safe_rename']}"
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
                        f"catalog_actionable={catalog['all_actionable']}; accepted_roles={roles}; "
                        f"private_success_counts={private_counts}; trace_success_counts="
                        f"{trace_success_counts}; expected_output={expected_output}; "
                        f"authoritative_output={authoritative_output}"
                    ),
                },
                {
                    "id": "ar_protocol_boundary_and_final_response",
                    "score": int(protocol_ok),
                    "reason": (
                        f"catalog_actionable={catalog['all_actionable']}; attempt_counts={counts}; "
                        f"invalid_requests={invalid_requests}; attempt_policy_ok={attempt_policy_ok}; "
                        f"control_after_lookups={control_after_lookups}; "
                        f"forbidden_actions={forbidden}; final_exact="
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
