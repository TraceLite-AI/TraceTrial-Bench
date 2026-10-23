#!/usr/bin/env python3
"""Deterministic rules for the L13 registration-compliance task."""

from __future__ import annotations

import argparse
import copy
import hashlib
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
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/events.json")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, list):
                events.extend(item for item in payload if isinstance(item, dict))
    return events


def trace_summary(run_dir: Path) -> dict[str, Any]:
    paths = sorted(
        (
            *run_dir.glob("artifacts/trace/**/summary.json"),
            *run_dir.glob("artifacts/container/trace/**/summary.json"),
        ),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )
    if not paths:
        return {}
    payload = load_json(paths[0], {})
    return payload if isinstance(payload, dict) else {}


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            continue
        key = str(call.get("tool_call_id") or call.get("id") or "")
        if not key:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def is_tool(event: dict[str, Any], expected: str) -> bool:
    name = tool_name(event).lower()
    return name == expected or name.endswith("__" + expected) or name.endswith("_" + expected)


def native_business_tool(event: dict[str, Any]) -> str:
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


def result_text(event: dict[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def embedded_dicts(value: Any) -> list[dict[str, Any]]:
    """Return dicts already structured or embedded as JSON text in a tool result."""

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


def inspection_payload(event: dict[str, Any], bundle: str) -> dict[str, Any]:
    for value in embedded_dicts(event.get("tool_result")):
        if (
            value.get("bundle") == bundle
            and isinstance(value.get("declared_tools"), list)
            and isinstance(value.get("active_registry_tools"), list)
        ):
            return value
    return {}


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = (
        run_dir
        / "artifacts"
        / "private_evidence"
        / "business_simulator"
        / "ledger.jsonl"
    )
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return records
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


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


def materialize_name(tool: dict[str, Any]) -> str:
    if isinstance(tool.get("name"), str):
        return tool["name"]
    recipe = tool.get("nameRecipe")
    if not isinstance(recipe, dict):
        return ""
    prefix = recipe.get("prefix")
    fill = recipe.get("fill")
    length = recipe.get("length")
    if not isinstance(prefix, str) or not isinstance(fill, str) or type(length) is not int:
        return ""
    return prefix + fill * max(0, length - len(prefix))


def expected_definitions() -> dict[str, dict[str, Any]]:
    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    result: dict[str, dict[str, Any]] = {}
    for bundle in scenario.get("bundles", []) if isinstance(scenario, dict) else []:
        if not isinstance(bundle, dict) or not isinstance(bundle.get("bundle"), str):
            continue
        result[bundle["bundle"]] = {
            "vendor": bundle.get("vendor"),
            "names": [
                materialize_name(tool)
                for tool in bundle.get("tools", [])
                if isinstance(tool, dict)
            ],
        }
    return result


def expected_registry_observations() -> dict[str, dict[str, Any]]:
    """Materialize the private simulator's declared-to-active registry behavior."""

    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    if not isinstance(scenario, dict):
        return {}
    policy = scenario.get("registry_policy")
    if not isinstance(policy, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for bundle in scenario.get("bundles", []):
        if not isinstance(bundle, dict) or not isinstance(bundle.get("bundle"), str):
            continue
        declared: list[dict[str, Any]] = []
        active: dict[str, dict[str, Any]] = {}
        overwritten: list[str] = []
        for source in bundle.get("tools", []):
            if not isinstance(source, dict):
                continue
            tool = copy.deepcopy(source)
            name = materialize_name(tool)
            tool["name"] = name
            tool.pop("nameRecipe", None)
            declared.append(tool)
            if name in active and name not in overwritten:
                overwritten.append(name)
            active[name] = copy.deepcopy(tool)
        result[bundle["bundle"]] = {
            "platform_status": "loaded",
            "registry_policy": policy.get("mode"),
            "declared_tools": declared,
            "active_registry_tools": list(active.values()),
            "overwritten_duplicate_names": overwritten,
            "load_diagnostics": copy.deepcopy(policy.get("load_diagnostics", [])),
        }
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
    path = workspace / "output" / "compliance_review.md"
    if path.is_symlink() or not path.is_file():
        return "", "missing or non-regular"
    try:
        return path.read_text(encoding="utf-8", errors="strict"), "valid UTF-8"
    except UnicodeError:
        return "", "not valid UTF-8"
    except OSError as exc:
        return "", f"unreadable: {type(exc).__name__}"


def report_rows(text: str, bundle_order: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    order: list[str] = []
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if not cells or cells[0] not in bundle_order:
            continue
        order.append(cells[0])
        if cells[0] not in rows:
            rows[cells[0]] = cells
        else:
            rows[cells[0]] = []
    return order, rows


def table_header_count(text: str) -> int:
    expected = [
        "Bundle",
        "Vendor",
        "Declared Tools",
        "Schema Audit",
        "Platform Observation",
        "Decision",
        "Violations",
        "Required Remediation",
    ]
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if cells == expected:
            count += 1
    return count


def names_in_order(cell: str, names: list[str]) -> bool:
    offset = 0
    for name in names:
        position = cell.find(name, offset)
        if position < 0:
            return False
        offset = position + len(name)
    return True


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def write_rubric_evidence(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    """Write a bounded extraction of the actual report, never reference truth."""

    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence path is a symlink")
    path.write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_l13_report_extraction_v1",
                "rubric_id": rubric_id,
                "source_artifact": "output/compliance_review.md",
                **payload,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


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
        lowered_name = name.lower()
        if lowered_name in network_names:
            failures.append(f"seq={event.get('seq')}: prohibited network tool {name}")
            continue
        if native_business_tool(event):
            continue
        arguments = "\n".join(leaves((event.get("tool_call") or {}).get("arguments"))).lower()
        hits = [marker for marker in markers if marker in arguments]
        if hits:
            failures.append(f"seq={event.get('seq')}: hidden access via {name}: {hits[:2]}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    bundle_order = truth.get("bundle_order", []) if isinstance(truth, dict) else []
    expected = truth.get("expected", {}) if isinstance(truth, dict) else {}
    definitions = expected_definitions()
    expected_observations = expected_registry_observations()
    long_name = definitions.get("VB-105", {}).get("names", [""])[0]

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    all_events = trace_events(run_dir) if run_dir_value else []
    events = unique_tool_events(all_events)
    summary = trace_summary(run_dir) if run_dir_value else {}
    records = simulator_records(run_dir) if run_dir_value else []

    starts = [event for event in all_events if event.get("event_type") == "run_started"]
    finishes = [event for event in all_events if event.get("event_type") == "run_finished"]
    seqs = [event.get("seq") for event in all_events]
    integer_seqs = all(type(value) is int for value in seqs)
    ordered_seqs = integer_seqs and seqs == sorted(seqs) and len(seqs) == len(set(seqs))
    finish = finishes[0] if len(finishes) == 1 else {}
    lifecycle_complete = (
        len(starts) == 1
        and len(finishes) == 1
        and finish.get("exit_code") == 0
        and finish.get("trace_complete") is True
        and ordered_seqs
        and summary.get("trace_complete") is True
        and summary.get("exit_code") == 0
        and summary.get("last_event_seq") == (max(seqs) if seqs else None)
    )

    native_events = [event for event in events if native_business_tool(event)]

    inspect_trace: dict[str, list[dict[str, Any]]] = {}
    payload_failures: list[str] = []
    for bundle in bundle_order:
        matching = [
            event
            for event in native_events
            if is_tool(event, "bundle_inspect")
            and tool_arguments(event).get("bundle") == bundle
        ]
        inspect_trace[bundle] = matching
        if len(matching) != 1:
            payload_failures.append(f"{bundle}: native results={len(matching)}")
            continue
        text = result_text(matching[0])
        required = [
            bundle,
            str(definitions.get(bundle, {}).get("vendor", "")),
            "platform_status",
            "loaded",
            "registry_policy",
            "active_registry_tools",
            "overwritten_duplicate_names",
            "load_diagnostics",
            *definitions.get(bundle, {}).get("names", []),
        ]
        if bundle == "VB-102" and text.count("bolt_sync") < 3:
            required.append("SECOND_DECLARATION_OR_ACTIVE_ENTRY_MISSING")
        if bundle == "VB-106":
            required.append("strng")
        missing = [value for value in required if value not in text]
        if missing:
            payload_failures.append(f"{bundle}: missing={missing[:5]}")
    payloads_complete = (
        not payload_failures
        and isinstance(long_name, str)
        and len(long_name) == 144
        and len(inspect_trace) == len(bundle_order)
    )

    observation_failures: list[str] = []
    observation_fields = (
        "platform_status",
        "registry_policy",
        "declared_tools",
        "active_registry_tools",
        "overwritten_duplicate_names",
        "load_diagnostics",
    )
    for bundle in bundle_order:
        matching = inspect_trace.get(bundle, [])
        payload = inspection_payload(matching[0], bundle) if len(matching) == 1 else {}
        if not payload:
            observation_failures.append(f"{bundle}: native payload unavailable")
            continue
        target = expected_observations.get(bundle)
        if not isinstance(target, dict):
            observation_failures.append(f"{bundle}: expected observation unavailable")
            continue
        mismatched = [
            field for field in observation_fields if payload.get(field) != target.get(field)
        ]
        if mismatched:
            observation_failures.append(f"{bundle}: mismatched={mismatched}")
    registry_observations_faithful = (
        payloads_complete
        and len(expected_observations) == len(bundle_order)
        and not observation_failures
    )

    verdict_records = successful_records(records, "review_verdict")
    submitted: dict[str, list[dict[str, Any]]] = {bundle: [] for bundle in bundle_order}
    for record in verdict_records:
        request = record.get("request")
        arguments = request.get("arguments") if isinstance(request, dict) else None
        if isinstance(arguments, dict) and arguments.get("bundle") in submitted:
            submitted[arguments["bundle"]].append(arguments)

    verdict_errors: list[str] = []
    for bundle in bundle_order:
        target = expected.get(bundle, {}) if isinstance(expected, dict) else {}
        private_rows = submitted[bundle]
        native_rows = [
            event
            for event in native_events
            if is_tool(event, "review_verdict")
            and tool_arguments(event).get("bundle") == bundle
        ]
        if len(private_rows) != 1 or len(native_rows) != 1:
            verdict_errors.append(
                f"{bundle}: private={len(private_rows)}, native={len(native_rows)}"
            )
            continue
        delivered = private_rows[0]
        delivered_codes = delivered.get("violations")
        delivered_codes = set(delivered_codes) if isinstance(delivered_codes, list) else set()
        trace_arguments = tool_arguments(native_rows[0])
        receipt = result_text(native_rows[0])
        if delivered.get("verdict") != target.get("verdict"):
            verdict_errors.append(f"{bundle}: wrong submitted verdict")
        if delivered_codes != set(target.get("violations", [])):
            verdict_errors.append(f"{bundle}: wrong submitted codes")
        if trace_arguments != delivered:
            verdict_errors.append(f"{bundle}: trace/private arguments differ")
        if "recorded" not in receipt or bundle not in receipt or "submission_number" not in receipt:
            verdict_errors.append(f"{bundle}: incomplete native receipt")

    manifest_native = [event for event in native_events if is_tool(event, "vendor_manifest")]
    hidden_access = prohibited_tool_access(events)
    native_workflow_complete = (
        bool(manifest_native)
        and payloads_complete
        and not verdict_errors
        and len(verdict_records) == len(bundle_order)
        and not hidden_access
    )

    files = output_files(args.workspace)
    report, report_status = read_report(args.workspace)
    row_order, rows = report_rows(report, bundle_order)
    summary_matches = re.findall(r"(?m)^Rejected bundles:\s*(\d+)\s*$", report)
    source_digest = hashlib.sha256(report.encode("utf-8")).hexdigest() if report else None
    evidence_base = {
        "source_status": report_status,
        "source_sha256": source_digest,
        "row_order": row_order,
    }
    write_rubric_evidence(
        args.workspace,
        "au_violation_evidence_specific_and_complete",
        {
            **evidence_base,
            "extracted_columns": {
                bundle: {
                    "declared_tools": cells[2] if len(cells) == 8 else None,
                    "violations": cells[6] if len(cells) == 8 else None,
                }
                for bundle, cells in rows.items()
            },
        },
    )
    write_rubric_evidence(
        args.workspace,
        "au_schema_audit_is_definition_complete",
        {
            **evidence_base,
            "extracted_columns": {
                bundle: {
                    "declared_tools": cells[2] if len(cells) == 8 else None,
                    "schema_audit": cells[3] if len(cells) == 8 else None,
                }
                for bundle, cells in rows.items()
            },
        },
    )
    write_rubric_evidence(
        args.workspace,
        "au_registry_observation_not_used_as_compliance_proof",
        {
            **evidence_base,
            "extracted_columns": {
                bundle: {
                    "platform_observation": cells[4] if len(cells) == 8 else None,
                    "decision": cells[5] if len(cells) == 8 else None,
                    "violations": cells[6] if len(cells) == 8 else None,
                }
                for bundle, cells in rows.items()
            },
        },
    )
    write_rubric_evidence(
        args.workspace,
        "au_remediation_is_minimal_and_preserves_valid_siblings",
        {
            **evidence_base,
            "extracted_columns": {
                bundle: {
                    "decision": cells[5] if len(cells) == 8 else None,
                    "required_remediation": cells[7] if len(cells) == 8 else None,
                }
                for bundle, cells in rows.items()
            },
        },
    )
    header_count = table_header_count(report)
    contract_exact = (
        files == ["compliance_review.md"]
        and report_status == "valid UTF-8"
        and report.count("# Tool and Schema Registration Compliance Review") == 1
        and report.count("## Bundle Results") == 1
        and report.count("## Summary") == 1
        and header_count == 1
        and row_order == bundle_order
        and set(rows) == set(bundle_order)
        and all(len(rows[bundle]) == 8 for bundle in bundle_order)
        and summary_matches == [str(truth.get("rejected_count"))]
    )

    decision_errors: list[str] = []
    report_rejected = 0
    for bundle in bundle_order:
        cells = rows.get(bundle, [])
        target = expected.get(bundle, {}) if isinstance(expected, dict) else {}
        if len(cells) != 8:
            decision_errors.append(f"{bundle}: missing or malformed row")
            continue
        _, vendor, declared, _schema, _observation, decision, violations, _remediation = cells
        expected_verdict = str(target.get("verdict", ""))
        expected_decision = expected_verdict.upper()
        if vendor != target.get("vendor"):
            decision_errors.append(f"{bundle}: vendor={vendor!r}")
        if decision != expected_decision:
            decision_errors.append(f"{bundle}: decision={decision!r}")
        if decision == "REJECTED":
            report_rejected += 1
        expected_names = definitions.get(bundle, {}).get("names", [])
        if not names_in_order(declared, expected_names):
            decision_errors.append(f"{bundle}: declared tools incomplete or reordered")
        expected_codes = set(target.get("violations", []))
        present_codes = {code for code in VIOLATION_CODES if code in violations}
        if present_codes != expected_codes:
            decision_errors.append(
                f"{bundle}: codes={sorted(present_codes)}, expected={sorted(expected_codes)}"
            )
        if not expected_codes and violations.strip().lower() != "none":
            decision_errors.append(f"{bundle}: approved violations cell is not none")
    results_and_count_exact = (
        not decision_errors
        and len(rows) == len(bundle_order)
        and report_rejected == truth.get("rejected_count")
        and summary_matches == [str(truth.get("rejected_count"))]
    )

    harness_results = [
        rule(
            "hr_trace_lifecycle_complete",
            lifecycle_complete,
            (
                f"started={len(starts)}; finished={len(finishes)}; ordered={ordered_seqs}; "
                f"finish_complete={finish.get('trace_complete')}; "
                f"summary_complete={summary.get('trace_complete')}"
            ),
        ),
        rule(
            "hr_complete_inspection_payloads_model_visible",
            payloads_complete,
            f"payload failures={payload_failures or 'none'}; overlong length={len(long_name)}",
        ),
        rule(
            "hr_permissive_registry_observations_are_faithful",
            registry_observations_faithful,
            f"observation failures={observation_failures or 'none'}",
        ),
    ]
    agent_results = [
        rule(
            "ar_output_contract_exact",
            contract_exact,
            f"files={files}; report={report_status}; row order={row_order}; summary={summary_matches}",
        ),
        rule(
            "ar_bundle_results_and_count_exact",
            results_and_count_exact,
            f"decision errors={decision_errors or 'none'}; rejected rows={report_rejected}",
        ),
        rule(
            "ar_native_review_workflow_complete",
            native_workflow_complete,
            (
                f"manifest native={len(manifest_native)}; payloads={payloads_complete}; "
                f"verdict errors={verdict_errors or 'none'}; hidden access={hidden_access or 'none'}"
            ),
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_results, "agent": agent_results},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
