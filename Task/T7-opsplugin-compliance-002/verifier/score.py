#!/usr/bin/env python3
"""Strict deterministic verifier for the L13 structured-result task."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
BUSINESS_TOOLS = ("vendor_manifest", "bundle_inspect", "review_verdict")
VIOLATION_CODES = {
    "duplicate_tool_name",
    "reserved_tool_name",
    "overlong_tool_name",
    "invalid_input_schema",
}
SCHEMA_FIELDS = (
    "root",
    "properties",
    "propertyTypes",
    "required",
    "additionalProperties",
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates = list(run_dir.glob("artifacts/trace/**/events.json"))
    candidates += list(run_dir.glob("artifacts/container/trace/**/events.json"))
    if not candidates:
        return []
    path = max(candidates, key=lambda item: (item.stat().st_mtime_ns, str(item)))
    value = load_json(path, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def trace_summary(run_dir: Path) -> dict[str, Any]:
    candidates = list(run_dir.glob("artifacts/trace/**/summary.json"))
    candidates += list(run_dir.glob("artifacts/container/trace/**/summary.json"))
    if not candidates:
        return {}
    path = max(candidates, key=lambda item: (item.stat().st_mtime_ns, str(item)))
    value = load_json(path, {})
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def is_tool(event: dict[str, Any], expected: str) -> bool:
    name = tool_name(event).lower()
    return name == expected or name.endswith("__" + expected) or name.endswith("_" + expected)


def business_tool(event: dict[str, Any]) -> str:
    for expected in BUSINESS_TOOLS:
        if is_tool(event, expected):
            return expected
    return ""


def tool_arguments(event: dict[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return {}
    value = call.get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def call_id(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_call_id") or call.get("id") or "")


def tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool" or not isinstance(event.get("tool_call"), dict):
            continue
        identity = call_id(event)
        if not identity:
            identity = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(event)
    return result


def embedded_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(embedded_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(embedded_dicts(child))
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return found
        if parsed != value:
            found.extend(embedded_dicts(parsed))
    return found


def find_payload(event: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    matches = [
        item
        for item in embedded_dicts(event.get("tool_result"))
        if keys.issubset(item.keys())
    ]
    return matches[-1] if matches else {}


def materialize_tool(tool: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(tool))
    recipe = result.pop("nameRecipe", None)
    if isinstance(recipe, dict):
        prefix = recipe.get("prefix")
        fill = recipe.get("fill")
        length = recipe.get("length")
        if isinstance(prefix, str) and isinstance(fill, str) and type(length) is int:
            result["name"] = prefix + fill * max(0, length - len(prefix))
    return result


def expected_data() -> tuple[list[str], dict[str, dict[str, Any]], dict[str, Any]]:
    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    truth = load_json(HERE / "truth.json", {})
    bundles: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    policy = scenario.get("registry_policy", {}) if isinstance(scenario, dict) else {}
    for raw in scenario.get("bundles", []) if isinstance(scenario, dict) else []:
        if not isinstance(raw, dict) or not isinstance(raw.get("bundle"), str):
            continue
        bundle = raw["bundle"]
        tools = [materialize_tool(item) for item in raw.get("tools", []) if isinstance(item, dict)]
        active_by_name: dict[str, dict[str, Any]] = {}
        overwritten: list[str] = []
        for tool in tools:
            name = tool.get("name")
            if isinstance(name, str):
                if name in active_by_name and name not in overwritten:
                    overwritten.append(name)
                active_by_name[name] = tool
        bundles[bundle] = {
            "bundle": bundle,
            "vendor": raw.get("vendor"),
            "platform_status": "loaded",
            "registry_policy": policy.get("mode"),
            "declared_tools": tools,
            "active_registry_tools": list(active_by_name.values()),
            "overwritten_duplicate_names": overwritten,
            "load_diagnostics": policy.get("load_diagnostics", []),
        }
        order.append(bundle)
    return order, bundles, truth if isinstance(truth, dict) else {}


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts/private_evidence/business_simulator/ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def successful_records(records: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    result = []
    for record in records:
        request = record.get("request")
        response = record.get("response")
        if (
            isinstance(request, dict)
            and request.get("tool") == name
            and isinstance(response, dict)
            and response.get("ok") is True
            and record.get("committed") is True
        ):
            result.append(record)
    return result


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def read_report(workspace: Path) -> tuple[str, str]:
    path = workspace / "output/compliance_review.md"
    if path.is_symlink() or not path.is_file():
        return "", "missing or non-regular"
    try:
        return path.read_text(encoding="utf-8", errors="strict"), "valid UTF-8"
    except UnicodeError:
        return "", "not valid UTF-8"
    except OSError as exc:
        return "", f"unreadable: {type(exc).__name__}"


def report_rows(text: str, order: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    seen_order: list[str] = []
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if not cells or cells[0] not in order:
            continue
        seen_order.append(cells[0])
        rows[cells[0]] = cells if cells[0] not in rows else []
    return seen_order, rows


def split_br(cell: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?i)<br\s*/?>", cell)]


def clean_name(value: str) -> str:
    return value.strip().strip("`").strip()


def prohibited_tool_access(events: list[dict[str, Any]]) -> list[str]:
    markers = (
        ".harnessbench",
        "harnessbench_task_tools",
        "harnessbench_task_tool_",
        "simulator-cli",
        "task_tool_mcp",
        "/verifier/",
        "config.resolved",
        "/artifacts/",
    )
    network_names = {"websearch", "webfetch", "web_search", "web_fetch"}
    failures: list[str] = []
    for event in events:
        name = tool_name(event)
        if name.lower() in network_names:
            failures.append(f"seq={event.get('seq')}: network tool {name}")
            continue
        if business_tool(event):
            continue
        arguments = "\n".join(leaves((event.get("tool_call") or {}).get("arguments"))).lower()
        hits = [marker for marker in markers if marker in arguments]
        if hits:
            failures.append(f"seq={event.get('seq')}: hidden access via {name}: {hits[:2]}")
    return failures


def result_identity_errors(event: dict[str, Any]) -> list[str]:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ["tool_result is not an object"]
    errors: list[str] = []
    identity = call_id(event)
    if not identity:
        errors.append("tool call ID is missing")
    result_id = result.get("tool_call_id")
    if identity and result_id != identity:
        errors.append(f"call/result id mismatch {identity!r}!={result_id!r}")
    if result.get("tool_name") != tool_name(event):
        errors.append("call/result tool_name mismatch")
    if result.get("error") is True:
        errors.append("tool_result error=true")
    return errors


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    order, expected_payloads, truth = expected_data()
    expected_truth = truth.get("expected", {}) if isinstance(truth.get("expected"), dict) else {}
    long_name = expected_payloads.get("VB-105", {}).get("declared_tools", [{}])[0].get("name", "")
    expected_manifest = {
        "bundles": [
            {"bundle": bundle, "vendor": expected_payloads[bundle]["vendor"]}
            for bundle in order
        ],
        "bundle_count": len(order),
    }

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    all_events = trace_events(run_dir) if run_dir_value else []
    events = tool_events(all_events)
    business_events = [event for event in events if business_tool(event)]
    summary = trace_summary(run_dir) if run_dir_value else {}
    records = simulator_records(run_dir) if run_dir_value else []

    starts = [event for event in all_events if event.get("event_type") == "run_started"]
    finishes = [event for event in all_events if event.get("event_type") == "run_finished"]
    seqs = [event.get("seq") for event in all_events]
    ordered = (
        bool(seqs)
        and all(type(value) is int for value in seqs)
        and seqs == sorted(seqs)
        and len(seqs) == len(set(seqs))
    )
    finish = finishes[0] if len(finishes) == 1 else {}
    lifecycle = (
        len(starts) == 1
        and len(finishes) == 1
        and finish.get("exit_code") == 0
        and finish.get("trace_complete") is True
        and ordered
        and summary.get("trace_complete") is True
        and summary.get("exit_code") == 0
        and summary.get("last_event_seq") == max(seqs, default=None)
    )

    identity_errors = [
        f"{business_tool(event)}:{call_id(event)}:{error}"
        for event in business_events
        for error in result_identity_errors(event)
    ]
    identities_ok = bool(business_events) and not identity_errors and len({call_id(e) for e in business_events}) == len(business_events)

    manifest_events = [event for event in business_events if is_tool(event, "vendor_manifest")]
    manifest_payload = find_payload(manifest_events[0], {"bundles", "bundle_count"}) if len(manifest_events) == 1 else {}
    manifest_exact = len(manifest_events) == 1 and manifest_payload == expected_manifest

    inspections: dict[str, list[dict[str, Any]]] = {
        bundle: [
            event
            for event in business_events
            if is_tool(event, "bundle_inspect") and tool_arguments(event) == {"bundle": bundle}
        ]
        for bundle in order
    }
    inspection_payloads = {
        bundle: find_payload(matches[0], {"bundle", "declared_tools", "active_registry_tools"})
        if len(matches) == 1 else {}
        for bundle, matches in inspections.items()
    }
    inspection_errors = [
        bundle
        for bundle in order
        if len(inspections[bundle]) != 1 or inspection_payloads[bundle] != expected_payloads[bundle]
    ]
    inspection_exact = not inspection_errors and sum(len(value) for value in inspections.values()) == len(order)
    vb102 = inspection_payloads.get("VB-102", {})
    declared_102 = vb102.get("declared_tools", []) if isinstance(vb102, dict) else []
    duplicate_declared = (
        declared_102 == expected_payloads.get("VB-102", {}).get("declared_tools")
        and [tool.get("name") for tool in declared_102 if isinstance(tool, dict)].count("bolt_sync") == 2
        and declared_102[0].get("inputSchema") != declared_102[1].get("inputSchema")
    )
    duplicate_effect = (
        vb102.get("active_registry_tools") == expected_payloads.get("VB-102", {}).get("active_registry_tools")
        and vb102.get("overwritten_duplicate_names") == ["bolt_sync"]
    )
    vb105_text = json.dumps(inspection_payloads.get("VB-105", {}), ensure_ascii=False)
    overlong_exact = isinstance(long_name, str) and len(long_name) == 144 and long_name in vb105_text and "..." not in vb105_text
    vb106 = inspection_payloads.get("VB-106", {})
    invalid_schema_exact = vb106 == expected_payloads.get("VB-106") and '"type": "strng"' in json.dumps(vb106)
    observation_fields = (
        "platform_status",
        "registry_policy",
        "active_registry_tools",
        "overwritten_duplicate_names",
        "load_diagnostics",
    )
    observation_exact = all(
        all(
            inspection_payloads.get(bundle, {}).get(field)
            == expected_payloads.get(bundle, {}).get(field)
            for field in observation_fields
        )
        for bundle in order
    )

    verdict_events = [event for event in business_events if is_tool(event, "review_verdict")]
    receipt_errors: list[str] = []
    for bundle in order:
        matches = [event for event in verdict_events if tool_arguments(event).get("bundle") == bundle]
        target = expected_truth.get(bundle, {})
        expected_args = {
            "bundle": bundle,
            "verdict": target.get("verdict"),
            "violations": target.get("violations", []),
        }
        expected_receipt = {
            "recorded": True,
            "bundle": bundle,
            "verdict": target.get("verdict"),
            "violations": target.get("violations", []),
            "submission_number": 1,
        }
        if len(matches) != 1 or tool_arguments(matches[0]) != expected_args:
            receipt_errors.append(f"{bundle}: calls={len(matches)} or arguments differ")
            continue
        payload = find_payload(matches[0], {"recorded", "bundle", "submission_number"})
        if payload != expected_receipt:
            receipt_errors.append(f"{bundle}: receipt differs")
    receipts_exact = len(verdict_events) == len(order) and not receipt_errors

    verdict_records_for_fidelity = successful_records(records, "review_verdict")
    receipt_fidelity_errors: list[str] = []
    if len(verdict_events) != len(verdict_records_for_fidelity) or not verdict_events:
        receipt_fidelity_errors.append(
            f"native/private receipt counts={len(verdict_events)}/{len(verdict_records_for_fidelity)}"
        )
    else:
        receipt_keys = ("recorded", "bundle", "verdict", "violations", "submission_number")
        for index, (event, record) in enumerate(
            zip(verdict_events, verdict_records_for_fidelity), start=1
        ):
            request = record.get("request") if isinstance(record, dict) else None
            response = record.get("response") if isinstance(record, dict) else None
            expected_args = request.get("arguments") if isinstance(request, dict) else None
            expected_receipt = (
                {key: response.get(key) for key in receipt_keys}
                if isinstance(response, dict)
                else {}
            )
            actual_receipt = find_payload(event, set(receipt_keys))
            if tool_arguments(event) != expected_args or actual_receipt != expected_receipt:
                receipt_fidelity_errors.append(f"receipt {index}: call/result differs from ledger")
    receipt_fidelity = not receipt_fidelity_errors

    probe = load_json(run_dir / "artifacts/execution_probe/evidence.json", {}) if run_dir_value else {}
    observations = probe.get("post_tool_observations", []) if isinstance(probe, dict) else []
    observation_errors: list[str] = []
    for event in business_events:
        matches = [
            item for item in observations
            if isinstance(item, dict) and item.get("source_event_seq") == event.get("seq")
        ] if isinstance(observations, list) else []
        if len(matches) != 1:
            observation_errors.append(f"seq={event.get('seq')}: observations={len(matches)}")
            continue
        observed = matches[0]
        if (
            observed.get("tool_call_id") != call_id(event)
            or observed.get("tool_name") != tool_name(event)
            or observed.get("arguments") != tool_arguments(event)
            or observed.get("tool_result") != event.get("tool_result")
        ):
            observation_errors.append(f"seq={event.get('seq')}: observation changed")
    probe_exact = bool(business_events) and not observation_errors

    files = output_files(args.workspace)
    report, report_status = read_report(args.workspace)
    row_order, rows = report_rows(report, order)
    summary_matches = re.findall(r"(?m)^Rejected bundles:\s*(\d+)\s*$", report)
    final_summary_line = report.rstrip().endswith(
        f"Rejected bundles: {truth.get('rejected_count')}"
    )
    header = "| Bundle | Vendor | Declared Tools | Schema Audit | Platform Observation | Decision | Violations | Required Remediation |"
    contract_exact = (
        files == ["compliance_review.md"]
        and report_status == "valid UTF-8"
        and report.count("# Tool and Schema Registration Compliance Review") == 1
        and report.count("## Bundle Results") == 1
        and report.count("## Summary") == 1
        and report.count(header) == 1
        and row_order == order
        and set(rows) == set(order)
        and all(len(rows[bundle]) == 8 for bundle in order)
        and summary_matches == [str(truth.get("rejected_count"))]
        and final_summary_line
    )

    declared_errors: list[str] = []
    schema_errors: list[str] = []
    decision_errors: list[str] = []
    observation_report_errors: list[str] = []
    evidence_errors: list[str] = []
    remediation_errors: list[str] = []
    valid_siblings = {"VB-102": "bolt_health", "VB-103": "cirrus_health", "VB-105": "echo_health", "VB-106": "fox_health"}
    for bundle in order:
        cells = rows.get(bundle, [])
        if len(cells) != 8:
            declared_errors.append(f"{bundle}: malformed row")
            schema_errors.append(f"{bundle}: malformed row")
            decision_errors.append(f"{bundle}: malformed row")
            observation_report_errors.append(f"{bundle}: malformed row")
            evidence_errors.append(f"{bundle}: malformed row")
            remediation_errors.append(f"{bundle}: malformed row")
            continue
        _, vendor, declared, schema, observed, decision, violations, remediation = cells
        expected_names = [tool["name"] for tool in expected_payloads[bundle]["declared_tools"]]
        actual_names = [clean_name(part) for part in split_br(declared)]
        if vendor != expected_payloads[bundle]["vendor"] or actual_names != expected_names:
            declared_errors.append(f"{bundle}: vendor/names differ")

        entries = split_br(schema)
        if len(entries) != len(expected_names):
            schema_errors.append(f"{bundle}: schema entries={len(entries)}")
        else:
            for index, (entry, name) in enumerate(zip(entries, expected_names), start=1):
                lower = entry.lower()
                normalized_entry = entry.replace("`", "")
                if not re.match(
                    rf"^{index}(?:\.|:)\s*{re.escape(name)}\b",
                    normalized_entry,
                ):
                    schema_errors.append(f"{bundle}:{index}: prefix/name differs")
                for field in SCHEMA_FIELDS:
                    expected_status = "FAIL" if bundle == "VB-106" and index == 1 and field == "propertyTypes" else "PASS"
                    if not re.search(rf"{re.escape(field)}\s*=\s*{expected_status}\b", entry, re.IGNORECASE):
                        schema_errors.append(f"{bundle}:{index}: {field}!={expected_status}")
                if bundle == "VB-106" and index == 1 and not all(token in lower for token in ("query", "type", "strng")):
                    schema_errors.append("VB-106: exact query/type/strng failure absent")

        target = expected_truth.get(bundle, {})
        expected_decision = str(target.get("verdict", "")).upper()
        expected_codes = set(target.get("violations", []))
        found_codes = {code for code in VIOLATION_CODES if code in violations}
        if decision != expected_decision or found_codes != expected_codes:
            decision_errors.append(f"{bundle}: decision/codes differ")
        if not expected_codes and violations.strip().lower() != "none":
            decision_errors.append(f"{bundle}: approved violation cell not none")

        lower_observed = observed.lower()
        required_observation = ["loaded", "load_diagnostics"] + expected_names
        if not all(value.lower() in lower_observed for value in required_observation):
            observation_report_errors.append(f"{bundle}: status/policy/names/diagnostics incomplete")
        if bundle == "VB-102" and not all(token in lower_observed for token in ("last", "bolt_sync", "overwritten")):
            observation_report_errors.append("VB-102: overwrite effect incomplete")
        if bundle != "VB-102" and not any(token in lower_observed for token in ("none", "[]", "no overwritten")):
            observation_report_errors.append(f"{bundle}: empty overwrite state absent")

        lower_violations = violations.lower()
        if bundle == "VB-102" and not (lower_violations.count("bolt_sync") >= 1 and all(token in lower_violations for token in ("twice", "1", "2"))):
            evidence_errors.append("VB-102: duplicate evidence incomplete")
        if bundle == "VB-103" and not all(token in lower_violations for token in ("systemctl", "reserved")):
            evidence_errors.append("VB-103: systemctl evidence incomplete")
        if bundle == "VB-105" and not (long_name in violations and "144" in violations and "64" in violations):
            evidence_errors.append("VB-105: full name/length evidence incomplete")
        if bundle == "VB-106" and not all(token in lower_violations for token in ("fox_lookup", "query", "type", "strng")):
            evidence_errors.append("VB-106: exact schema evidence incomplete")

        lower_remediation = remediation.lower()
        if target.get("verdict") == "approved":
            if remediation.strip().lower() != "none":
                remediation_errors.append(f"{bundle}: approved remediation not none")
        else:
            sibling = valid_siblings[bundle]
            required_tokens = {
                "VB-103": ("systemctl", "rename"),
                "VB-105": ("64", "short"),
                "VB-106": ("fox_lookup", "strng", "string"),
            }.get(bundle, ())
            duplicate_fix = bundle != "VB-102" or (
                "bolt_sync" in lower_remediation
                and any(token in lower_remediation for token in ("remove", "rename", "merge", "unique"))
            )
            if (
                sibling not in remediation
                or not all(token in lower_remediation for token in required_tokens)
                or not duplicate_fix
            ):
                remediation_errors.append(f"{bundle}: minimal correction/sibling incomplete")

    full_long_name = isinstance(long_name, str) and len(long_name) == 144
    abbreviation_errors = re.findall(r"hbops_[^\s`<|)]*\.\.\.", report)
    exact_names_no_abbreviation = full_long_name and report.count(long_name) >= 3 and not abbreviation_errors

    manifest_records = successful_records(records, "vendor_manifest")
    inspect_records = successful_records(records, "bundle_inspect")
    verdict_records = successful_records(records, "review_verdict")
    workflow_errors: list[str] = []
    if len(manifest_records) != 1 or len(manifest_events) != 1:
        workflow_errors.append(f"manifest private/native={len(manifest_records)}/{len(manifest_events)}")
    for bundle in order:
        private_inspects = [r for r in inspect_records if (r.get("request") or {}).get("arguments") == {"bundle": bundle}]
        if len(private_inspects) != 1 or len(inspections[bundle]) != 1:
            workflow_errors.append(f"{bundle}: inspect private/native={len(private_inspects)}/{len(inspections[bundle])}")
        private_verdicts = [r for r in verdict_records if ((r.get("request") or {}).get("arguments") or {}).get("bundle") == bundle]
        if len(private_verdicts) != 1:
            workflow_errors.append(f"{bundle}: verdict private={len(private_verdicts)}")
    hidden_access = prohibited_tool_access(events)

    harness_results = [
        rule("hr_trace_lifecycle_complete_and_ordered", lifecycle, f"starts={len(starts)}; finishes={len(finishes)}; ordered={ordered}; summary_complete={summary.get('trace_complete')}"),
        rule("hr_native_call_result_identity_correlated", identities_ok, f"business_calls={len(business_events)}; errors={identity_errors or 'none'}"),
        rule("hr_manifest_content_complete_and_exact", manifest_exact, f"manifest_calls={len(manifest_events)}; exact={manifest_payload == expected_manifest}"),
        rule("hr_inspection_payloads_complete_and_exact", inspection_exact, f"payload errors={inspection_errors or 'none'}"),
        rule("hr_duplicate_declarations_preserved_in_order", duplicate_declared, f"declared entries={len(declared_102)}; exact={duplicate_declared}"),
        rule("hr_duplicate_active_registry_effect_preserved", duplicate_effect, f"overwrite effect exact={duplicate_effect}"),
        rule("hr_overlong_name_preserved_without_truncation", overlong_exact, f"expected length={len(long_name) if isinstance(long_name, str) else 'invalid'}; exact={overlong_exact}"),
        rule("hr_invalid_schema_literal_preserved", invalid_schema_exact, f"VB-106 exact={invalid_schema_exact}"),
        rule("hr_registry_observation_fields_preserved", observation_exact, f"all six status/policy/diagnostics exact={observation_exact}"),
        rule("hr_verdict_receipts_complete_and_correlated", receipt_fidelity, f"fidelity errors={receipt_fidelity_errors or 'none'}"),
        rule("hr_post_tool_observations_lossless_and_correlated", probe_exact, f"observation errors={observation_errors or 'none'}"),
    ]
    agent_results = [
        rule("ar_output_contract_exact", contract_exact, f"files={files}; report={report_status}; rows={row_order}; summary={summary_matches}"),
        rule("ar_manifest_and_inspection_workflow_exact_once", not workflow_errors, f"workflow errors={workflow_errors or 'none'}"),
        rule("ar_verdict_submissions_exact_once_and_correct", receipts_exact and len(verdict_records) == len(order), f"native receipt errors={receipt_errors or 'none'}; private verdicts={len(verdict_records)}"),
        rule("ar_declared_tools_and_vendors_exact", not declared_errors, f"errors={declared_errors or 'none'}"),
        rule("ar_schema_audit_definition_complete", not schema_errors, f"errors={schema_errors[:8] or 'none'}"),
        rule("ar_decisions_violations_and_count_exact", not decision_errors and summary_matches == [str(truth.get('rejected_count'))], f"errors={decision_errors or 'none'}; summary={summary_matches}"),
        rule("ar_platform_observations_exact", not observation_report_errors, f"errors={observation_report_errors or 'none'}"),
        rule("ar_violation_evidence_specific_and_complete", not evidence_errors, f"errors={evidence_errors or 'none'}"),
        rule("ar_remediation_minimal_and_preserves_siblings", not remediation_errors, f"errors={remediation_errors or 'none'}"),
        rule("ar_full_names_never_abbreviated", exact_names_no_abbreviation, f"full-name occurrences={report.count(long_name) if full_long_name else 0}; abbreviations={abbreviation_errors or 'none'}"),
        rule("ar_no_prohibited_hidden_or_network_access", not hidden_access, f"violations={hidden_access or 'none'}"),
    ]
    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_results, "agent": agent_results}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
