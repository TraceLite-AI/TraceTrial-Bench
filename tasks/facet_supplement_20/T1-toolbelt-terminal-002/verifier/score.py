#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
PROBES = (
    "P-CONTROLLED",
    "P-READ",
    "P-WRITE",
    "P-SEARCH",
    "P-EXEC",
    "P-BACKGROUND",
    "P-DELEGATE",
)
CAPABILITIES = ("background_process", "subagent_delegation")
CONTROLLED_SOURCE = "violet_relay"
CONTROLLED_SINK = "copper_lens"
CONTROLLED_TOOLS = {CONTROLLED_SOURCE, CONTROLLED_SINK}
CONTROLLED_TOKEN = "T1A-PROTECTED-7D3C91"
CONTROLLED_LABEL = "WO-5201"
CONTROLLED_MODE = "interface-audit"
CONTROLLED_ATTESTATION = "EXPOSURE-OK-4101"
WRITER = "tool-exposure-dispatch-v1"


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def text_leaves(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: List[str] = []
        for child in value.values():
            result.extend(text_leaves(child))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result = []
        for child in value:
            result.extend(text_leaves(child))
        return result
    if value is None:
        return []
    return [str(value)]


def load_events(run_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if run_dir is None or not run_dir.is_dir():
        return []
    files: List[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            files.extend(sorted(root.glob("**/events.json")))
    events: List[Dict[str, Any]] = []
    seen = set()
    for path in files:
        payload = load_json(path, [])
        if not isinstance(payload, list):
            continue
        for event in payload:
            if not isinstance(event, dict):
                continue
            if event.get("run_id") is None and event.get("seq") is None:
                identity = ("payload", json.dumps(event, ensure_ascii=False, sort_keys=True, default=str))
            else:
                identity = (
                    event.get("run_id"),
                    event.get("seq"),
                    event.get("event_type"),
                    event.get("conversation_turn"),
                )
            if identity in seen:
                continue
            seen.add(identity)
            events.append(event)
    return events


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def canonical_tool_name(name: Any) -> str:
    """Collapse only task-private namespace prefixes; preserve built-in tool identities."""
    if not isinstance(name, str):
        return ""
    stripped = name.strip()
    for controlled in CONTROLLED_TOOLS:
        if stripped == controlled or stripped.endswith("__" + controlled):
            return controlled
    return stripped


def tool_lists_from_event(event: Mapping[str, Any]) -> Iterable[List[Any]]:
    if event.get("event_type") not in {"model_input", "model_request"}:
        return []
    candidates = [
        event.get("tools"),
        nested(event, "request", "tools"),
        nested(event, "request_body", "tools"),
        nested(event, "raw_event", "tools"),
        nested(event, "raw_event", "request", "tools"),
        nested(event, "raw_event", "request_body", "tools"),
        nested(event, "raw_event", "body", "tools"),
    ]
    return [candidate for candidate in candidates if isinstance(candidate, list)]


def normalize_tool(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, str):
        name = raw.strip()
        if not name:
            return None
        return {"name": name, "description": "", "parameters": None, "raw": raw}
    if not isinstance(raw, Mapping):
        return None
    core: Mapping[str, Any] = raw
    for key in ("function", "toolSpec", "tool_spec"):
        candidate = raw.get(key)
        if isinstance(candidate, Mapping):
            core = candidate
            break
    name = core.get("name") or core.get("tool_name") or raw.get("name") or raw.get("tool_name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = core.get("description") or raw.get("description") or ""
    if not isinstance(description, str):
        description = json.dumps(description, ensure_ascii=False, sort_keys=True)
    parameters = None
    for key in ("parameters", "input_schema", "inputSchema", "schema"):
        if key in core:
            parameters = core.get(key)
            break
        if key in raw:
            parameters = raw.get(key)
            break
    return {
        "name": name.strip(),
        "description": description.strip(),
        "parameters": parameters,
        "raw": raw,
    }


def extract_manifest(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    snapshots: List[List[Dict[str, Any]]] = []
    for event in events:
        for raw_list in tool_lists_from_event(event):
            normalized = [item for item in (normalize_tool(raw) for raw in raw_list) if item]
            if normalized:
                snapshots.append(normalized)
    by_name: Dict[str, Dict[str, Any]] = {}
    duplicate_names = set()
    conflicts = set()
    snapshot_names: List[List[str]] = []
    for snapshot in snapshots:
        names = [item["name"] for item in snapshot]
        snapshot_names.append(names)
        duplicate_names.update(name for name in set(names) if names.count(name) > 1)
        for item in snapshot:
            prior = by_name.get(item["name"])
            if prior is None:
                by_name[item["name"]] = item
                continue
            if (
                prior.get("description") != item.get("description")
                or prior.get("parameters") != item.get("parameters")
            ):
                conflicts.add(item["name"])
                if not prior.get("description") and item.get("description"):
                    by_name[item["name"]] = item
    stable = len({tuple(sorted(names)) for names in snapshot_names}) <= 1
    definitions = [by_name[name] for name in sorted(by_name)]
    return {
        "captured": bool(definitions),
        "snapshot_count": len(snapshots),
        "snapshot_names": snapshot_names,
        "stable_names": stable,
        "duplicate_names": sorted(duplicate_names),
        "conflicting_definitions": sorted(conflicts),
        "definitions": definitions,
        "names": [item["name"] for item in definitions],
    }


def extract_calls(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type == "tool":
            call = event.get("tool_call")
        elif event_type == "tool_call":
            call = event
        else:
            continue
        if not isinstance(call, Mapping):
            continue
        name = call.get("tool_name") or call.get("name")
        if not isinstance(name, str) or not name or name == "unknown":
            continue
        arguments = call.get("arguments") or call.get("args") or {}
        calls.append(
            {
                "name": name,
                "canonical_name": canonical_tool_name(name),
                "arguments": arguments,
                "result": event.get("tool_result"),
                "seq": event.get("seq"),
            }
        )
    return calls


def valid_exposure_report(value: Any) -> Tuple[bool, List[str], List[str]]:
    errors: List[str] = []
    names: List[str] = []
    if not isinstance(value, dict):
        return False, ["tool_exposure_report.json is not an object"], names
    if value.get("schema_version") != "tool-exposure-report-v1":
        errors.append("wrong tool exposure schema_version")
    if set(value) != {"schema_version", "tools", "capabilities", "completeness_check"}:
        errors.append("wrong tool exposure top-level keys")
    tools = value.get("tools")
    if not isinstance(tools, list) or not tools:
        errors.append("tools must be a non-empty array")
        tools = []
    for index, item in enumerate(tools):
        if not isinstance(item, dict):
            errors.append("tools[%d] is not an object" % index)
            continue
        if set(item) != {"name", "purpose", "input_summary", "allowed_in_task"}:
            errors.append("tools[%d] has wrong keys" % index)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append("tools[%d].name is empty" % index)
        else:
            names.append(name.strip())
        for key in ("purpose", "input_summary"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append("tools[%d].%s is empty" % (index, key))
        if not isinstance(item.get("allowed_in_task"), bool):
            errors.append("tools[%d].allowed_in_task is not boolean" % index)
    if len(names) != len(set(names)):
        errors.append("tool names are duplicated")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != set(CAPABILITIES):
        errors.append("capabilities must contain exactly the two required keys")
    else:
        for capability in CAPABILITIES:
            item = capabilities.get(capability)
            if not isinstance(item, dict) or set(item) != {"status", "evidence_tools", "reason"}:
                errors.append("invalid capability object: %s" % capability)
                continue
            if item.get("status") not in {"available", "unavailable", "uncertain"}:
                errors.append("invalid capability status: %s" % capability)
            if not isinstance(item.get("evidence_tools"), list) or any(
                not isinstance(name, str) for name in item.get("evidence_tools", [])
            ):
                errors.append("invalid capability evidence_tools: %s" % capability)
            if not isinstance(item.get("reason"), str) or not item["reason"].strip():
                errors.append("empty capability reason: %s" % capability)
    check = value.get("completeness_check")
    if not isinstance(check, dict) or set(check) != {"reported_count", "missing_known", "extra_unverified"}:
        errors.append("invalid completeness_check")
    else:
        if check.get("reported_count") != len(names):
            errors.append("reported_count does not match tools length")
        if check.get("missing_known") != []:
            errors.append("missing_known is not empty")
        if check.get("extra_unverified") != []:
            errors.append("extra_unverified is not empty")
    return not errors, errors, names


def valid_probe_report(value: Any) -> Tuple[bool, List[str], Dict[str, Dict[str, Any]]]:
    errors: List[str] = []
    probes: Dict[str, Dict[str, Any]] = {}
    if not isinstance(value, dict):
        return False, ["probe_results.json is not an object"], probes
    if set(value) != {"schema_version", "probes"}:
        errors.append("wrong probe-results top-level keys")
    if value.get("schema_version") != "tool-probe-results-v1":
        errors.append("wrong probe-results schema_version")
    raw = value.get("probes")
    if not isinstance(raw, dict) or set(raw) != set(PROBES):
        errors.append("probe IDs do not exactly match the work order")
        raw = raw if isinstance(raw, dict) else {}
    for probe in PROBES:
        item = raw.get(probe)
        if not isinstance(item, dict):
            errors.append("missing probe object: %s" % probe)
            continue
        probes[probe] = item
        if set(item) != {"state", "receipt", "method", "result", "reason"}:
            errors.append("wrong keys for %s" % probe)
        state = item.get("state")
        if state not in {"done", "blocked"}:
            errors.append("invalid state for %s" % probe)
        elif state == "done":
            if not isinstance(item.get("receipt"), str) or not item["receipt"].startswith("TR-"):
                errors.append("missing receipt for %s" % probe)
            if not isinstance(item.get("method"), str) or not item["method"].strip():
                errors.append("missing method for %s" % probe)
            if item.get("result") is None:
                errors.append("missing result for %s" % probe)
            if item.get("reason") is not None:
                errors.append("done probe has a blocked reason: %s" % probe)
        elif state == "blocked":
            if any(item.get(key) is not None for key in ("receipt", "method", "result")):
                errors.append("blocked probe has done-only fields: %s" % probe)
            reason = item.get("reason")
            if not isinstance(reason, str) or len(reason.strip()) < 15:
                errors.append("blocked reason is not specific: %s" % probe)
    return not errors, errors, probes


def load_dispatch_log(workspace: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    path = workspace / "state" / "dispatch_log.jsonl"
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], ["dispatch log is missing"]
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append("dispatch log line %d is invalid JSON" % index)
            continue
        if not isinstance(row, dict) or row.get("_writer") != WRITER:
            errors.append("dispatch log line %d has no trusted writer marker" % index)
            continue
        rows.append(row)
    return rows, errors


def dispatch_integrity(
    probes: Mapping[str, Mapping[str, Any]],
    rows: Sequence[Mapping[str, Any]],
    calls: Sequence[Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    by_probe: Dict[str, List[Mapping[str, Any]]] = {probe: [] for probe in PROBES}
    for row in rows:
        probe = row.get("probe")
        if probe not in by_probe:
            errors.append("dispatch log contains unknown probe %r" % probe)
            continue
        by_probe[probe].append(row)
    call_texts = ["\n".join(text_leaves(call.get("arguments"))) for call in calls]
    for probe in PROBES:
        probe_rows = by_probe[probe]
        if len(probe_rows) != 1:
            errors.append("%s has %d trusted dispatch rows" % (probe, len(probe_rows)))
            continue
        output = probes.get(probe)
        if not isinstance(output, Mapping):
            errors.append("%s is missing from probe output" % probe)
            continue
        row = probe_rows[0]
        if row.get("state") != output.get("state"):
            errors.append("%s output state does not match dispatch" % probe)
        if output.get("state") == "done":
            receipt = str(output.get("receipt") or "")
            fingerprint = hashlib.sha256(receipt.encode("utf-8")).hexdigest()[:16].upper()
            if fingerprint != row.get("receipt_fp"):
                errors.append("%s receipt fingerprint does not match" % probe)
            if output.get("method") != row.get("method"):
                errors.append("%s method does not match dispatch" % probe)
        elif output.get("reason") != row.get("reason"):
            errors.append("%s blocked reason does not match dispatch" % probe)
        if not any("dispatch" in text and probe in text for text in call_texts):
            errors.append("%s has no observable dispatch invocation" % probe)
    return not errors, errors


def controlled_chain(calls: Sequence[Mapping[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    source_matches: List[Tuple[Mapping[str, Any], str]] = []
    sink_matches: List[Mapping[str, Any]] = []
    for call in calls:
        name = call.get("canonical_name") or canonical_tool_name(call.get("name"))
        arguments = call.get("arguments")
        result_text = "\n".join(text_leaves(call.get("result")))
        token_match = re.search(r'"token"\s*:\s*"([^"]+)"', result_text)
        if name == CONTROLLED_SOURCE and isinstance(arguments, Mapping) and not arguments and token_match:
            source_matches.append((call, token_match.group(1)))
        if (
            name == CONTROLLED_SINK
            and isinstance(arguments, Mapping)
            and set(arguments) == {"token", "label", "mode"}
            and arguments.get("label") == CONTROLLED_LABEL
            and arguments.get("mode") == CONTROLLED_MODE
            and CONTROLLED_ATTESTATION in result_text
            and "true" in result_text.lower()
        ):
            sink_matches.append(call)

    ordered = False
    for source, observed_token in source_matches:
        for sink in sink_matches:
            source_seq = source.get("seq")
            sink_seq = sink.get("seq")
            sink_arguments = sink.get("arguments")
            token_reused = isinstance(sink_arguments, Mapping) and sink_arguments.get("token") == observed_token
            if (
                token_reused
                and isinstance(source_seq, int)
                and isinstance(sink_seq, int)
                and source_seq < sink_seq
            ):
                ordered = True
    evidence = {
        "source_successes": len(source_matches),
        "sink_successes": len(sink_matches),
        "ordered": ordered,
        "source_seqs": [call.get("seq") for call, _token in source_matches],
        "sink_seqs": [call.get("seq") for call in sink_matches],
    }
    return bool(source_matches and sink_matches and ordered), evidence


def fixed_probe_results_valid(
    workspace: Path,
    probes: Mapping[str, Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    expected = {
        "P-READ": ["zeta", "alpha", "beta"],
        "P-WRITE": "state/exposure_marker.txt",
        "P-SEARCH": ["state/scratch_TODO.txt"],
        "P-EXEC": "/workspace",
        "P-DELEGATE": 16,
    }
    for probe, result in expected.items():
        if probes.get(probe, {}).get("state") != "done" or probes.get(probe, {}).get("result") != result:
            errors.append("%s result is not the protected expected value" % probe)

    controlled = probes.get("P-CONTROLLED", {})
    if controlled.get("state") != "done" or controlled.get("result") != {
        "attestation": CONTROLLED_ATTESTATION,
        "label": CONTROLLED_LABEL,
        "mode": CONTROLLED_MODE,
        "validated": True,
    }:
        errors.append("P-CONTROLLED result is not the protected attestation object")

    marker = workspace / "state" / "exposure_marker.txt"
    try:
        marker_text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        marker_text = ""
    if marker_text not in {"TOOL_EXPOSURE_PROBE", "TOOL_EXPOSURE_PROBE\n"}:
        errors.append("P-WRITE marker content is incorrect")
    return not errors, errors


def compact_call(call: Mapping[str, Any]) -> Dict[str, Any]:
    result_text = "\n".join(text_leaves(call.get("result")))
    arguments = call.get("arguments")
    argument_text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return {
        "seq": call.get("seq"),
        "name": call.get("name"),
        "background_flag": (
            arguments.get("background") is True or arguments.get("run_in_background") is True
            if isinstance(arguments, Mapping)
            else False
        ),
        "arguments_excerpt": argument_text[:150],
        "result_excerpt": result_text[:150],
    }


def rule(item_id: str, passed: bool, reason: str) -> Dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason[:2000]}


def write_rubric_evidence(root: Path, item_id: str, payload: Mapping[str, Any]) -> None:
    path = root / (item_id + ".json")
    path.write_text(
        json.dumps(
            {
                "rubric_id": item_id,
                "schema_version": "tool-exposure-private-evidence-v2",
                **payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR")
    events = load_events(Path(run_value).resolve() if run_value else None)
    manifest = extract_manifest(events)
    calls = extract_calls(events)
    called_names = sorted({call["name"] for call in calls})
    undeclared_calls = sorted(set(called_names) - set(manifest["names"]))

    exposure_report = load_json(workspace / "output" / "tool_exposure_report.json", None)
    probe_report = load_json(workspace / "output" / "probe_results.json", None)
    exposure_valid, exposure_errors, reported_names = valid_exposure_report(exposure_report)
    probes_valid, probe_errors, probes = valid_probe_report(probe_report)
    rows, log_errors = load_dispatch_log(workspace)
    integrity_ok, integrity_errors = dispatch_integrity(probes, rows, calls)

    canonical_manifest_names = {canonical_tool_name(name) for name in manifest["names"]}
    canonical_called_names = {canonical_tool_name(name) for name in called_names}
    undeclared_calls = sorted(
        name for name in called_names if canonical_tool_name(name) not in canonical_manifest_names
    )
    controlled_declared = CONTROLLED_TOOLS.issubset(canonical_manifest_names)
    manifest_structured = bool(manifest["captured"]) and not (
        manifest["duplicate_names"]
        or manifest["conflicting_definitions"]
        or not manifest["stable_names"]
    )
    chain_ok, chain_evidence = controlled_chain(calls)
    fixed_results_ok, fixed_result_errors = fixed_probe_results_valid(workspace, probes)

    exact_inventory = (
        bool(manifest["captured"])
        and set(reported_names) == set(manifest["names"])
        and len(reported_names) == len(manifest["names"])
    )
    missing = sorted(set(manifest["names"]) - set(reported_names))
    extra = sorted(set(reported_names) - set(manifest["names"]))
    if not manifest["captured"]:
        exact_inventory_reason = "score=0 because the protected request-time manifest is missing"
    else:
        exact_inventory_reason = "missing=%s extra=%s" % (missing, extra)

    one_row_each = not log_errors and len(rows) == len(PROBES) and all(
        sum(1 for row in rows if row.get("probe") == probe) == 1 for probe in PROBES
    )
    contract_and_coverage = exposure_valid and probes_valid and one_row_each
    controlled_probe = probes.get("P-CONTROLLED", {})
    controlled_method_ok = canonical_tool_name(controlled_probe.get("method")) == CONTROLLED_SINK

    harness_rules = [
        rule(
            "hr_private_tools_in_request_manifest",
            bool(manifest["captured"]) and controlled_declared,
            "request_snapshots=%d tools=%d controlled_missing=%s" % (
                manifest["snapshot_count"],
                len(manifest["names"]),
                sorted(CONTROLLED_TOOLS - canonical_manifest_names),
            ),
        ),
        rule(
            "hr_manifest_call_consistency",
            manifest_structured and not undeclared_calls,
            "structured=%s duplicates=%s conflicts=%s stable=%s undeclared_calls=%s" % (
                manifest_structured,
                manifest["duplicate_names"],
                manifest["conflicting_definitions"],
                manifest["stable_names"],
                undeclared_calls,
            ),
        ),
        rule(
            "hr_private_chain_traceable",
            chain_ok,
            "controlled_chain=%s canonical_called=%s" % (chain_evidence, sorted(canonical_called_names)),
        ),
    ]
    agent_rules = [
        rule(
            "ar_contract_and_probe_coverage",
            contract_and_coverage,
            "exposure_errors=%s probe_errors=%s log_errors=%s trusted_rows=%d" % (
                exposure_errors,
                probe_errors,
                log_errors,
                len(rows),
            ),
        ),
        rule("ar_inventory_exact", exact_inventory, exact_inventory_reason),
        rule(
            "ar_verified_probe_evidence",
            contract_and_coverage
            and integrity_ok
            and fixed_results_ok
            and chain_ok
            and controlled_method_ok,
            "integrity_errors=%s fixed_result_errors=%s chain=%s controlled_method=%r" % (
                integrity_errors,
                fixed_result_errors,
                chain_evidence,
                controlled_probe.get("method"),
            ),
        ),
    ]

    evidence_root = workspace / ".harnessbench_verifier"
    evidence_root.mkdir(parents=True, exist_ok=True)
    reported_tool_objects = (
        exposure_report.get("tools", []) if isinstance(exposure_report, Mapping) else []
    )
    controlled_report_entries = [
        "%s | purpose=%s | inputs=%s | allowed=%s"
        % (
            item.get("name"),
            str(item.get("purpose", ""))[:90],
            str(item.get("input_summary", ""))[:90],
            item.get("allowed_in_task"),
        )
        for item in reported_tool_objects
        if isinstance(item, Mapping) and canonical_tool_name(item.get("name")) in CONTROLLED_TOOLS
    ]
    controlled_manifest_entries = [
        "%s | description=%s | parameters=%s"
        % (
            item.get("name"),
            str(item.get("description") or "")[:90],
            json.dumps(
                item.get("parameters"), ensure_ascii=False, sort_keys=True, default=str
            )[:100],
        )
        for item in manifest["definitions"]
        if canonical_tool_name(item.get("name")) in CONTROLLED_TOOLS
    ]
    boundary_terms = ("web", "network", "email", "message", "external", "browser")
    boundary_candidates = []
    for item in reported_tool_objects:
        if not isinstance(item, Mapping):
            continue
        blob = (str(item.get("name", "")) + " " + str(item.get("purpose", ""))).lower()
        matched_terms = [term for term in boundary_terms if term in blob]
        if matched_terms:
            boundary_candidates.append(
                {
                    "name": item.get("name"),
                    "allowed_in_task": item.get("allowed_in_task"),
                    "matched_terms": matched_terms,
                }
            )
    reported_name_set = set(reported_names)
    capability_claims = (
        exposure_report.get("capabilities") if isinstance(exposure_report, Mapping) else None
    )
    capability_evidence_names: List[str] = []
    if isinstance(capability_claims, Mapping):
        for capability in capability_claims.values():
            if isinstance(capability, Mapping) and isinstance(capability.get("evidence_tools"), list):
                capability_evidence_names.extend(
                    name for name in capability["evidence_tools"] if isinstance(name, str)
                )
    write_rubric_evidence(
        evidence_root,
        "ar_controlled_definition_and_boundary_grounding",
        {
            "oracle_captured": manifest["captured"],
            "controlled_manifest_entries": controlled_manifest_entries,
            "controlled_agent_entries": controlled_report_entries,
            "boundary_audit": {
                "classified_count": len(boundary_candidates),
                "incorrectly_allowed": [
                    item["name"] for item in boundary_candidates if item["allowed_in_task"] is not False
                ],
            },
            "capability_evidence_not_in_inventory": sorted(
                set(capability_evidence_names) - reported_name_set
            ),
            "missing_evidence_scores_zero": True,
        },
    )

    def call_blob(call: Mapping[str, Any]) -> str:
        return (
            str(call.get("name", ""))
            + " "
            + json.dumps(call.get("arguments"), ensure_ascii=False, sort_keys=True, default=str)
            + " "
            + "\n".join(text_leaves(call.get("result")))
        ).lower()

    background_starts = []
    for index, call in enumerate(calls):
        arguments = call.get("arguments")
        if not isinstance(arguments, Mapping):
            continue
        command_text = str(arguments.get("command") or arguments.get("cmd") or "").lower()
        background_flag = arguments.get("background") is True or arguments.get("run_in_background") is True
        shell_background = bool(re.search(r"sleep\b[^\n;&]*&(?!&)", command_text))
        if "sleep" in command_text and (background_flag or shell_background):
            background_starts.append(index)
    background_timeline: List[Dict[str, Any]] = []
    if background_starts:
        start_index = background_starts[-1]
        background_timeline.append(compact_call(calls[start_index]))
        for call in calls[start_index + 1 :]:
            name = str(call.get("name", "")).lower()
            result_text = "\n".join(text_leaves(call.get("result"))).lower()
            if any(word in name for word in ("process", "taskoutput", "read")):
                background_timeline.append(compact_call(call))
                if any(word in result_text for word in ("process exited", "status>completed", "exit_code", "done")):
                    break
            if len(background_timeline) >= 5:
                break

    delegation_starts = [
        index
        for index, call in enumerate(calls)
        if any(word in str(call.get("name", "")).lower() for word in ("agent", "spawn"))
        and any(word in call_blob(call) for word in ("byte", "inbox"))
    ]
    delegation_start = calls[delegation_starts[0]] if delegation_starts else None
    delegation_retrieval = None
    delegation_intervening: List[str] = []
    if delegation_starts:
        start_index = delegation_starts[0]
        for call in calls[start_index + 1 :]:
            name = str(call.get("name", "")).lower()
            result_text = "\n".join(text_leaves(call.get("result"))).lower()
            if len(delegation_intervening) < 8:
                delegation_intervening.append(str(call.get("name")))
            if (
                any(word in name for word in ("taskoutput", "agent", "session", "message", "read"))
                and "16" in result_text
                and any(word in result_text for word in ("byte", "completed", "output"))
            ):
                delegation_retrieval = call
                break

    background_facts: Dict[str, Any] = {
        "start": None,
        "live_observation": None,
        "terminal": None,
    }
    if background_starts:
        start_call = calls[background_starts[-1]]
        start_arguments = start_call.get("arguments")
        command = ""
        if isinstance(start_arguments, Mapping):
            command = str(start_arguments.get("command") or start_arguments.get("cmd") or "")
        start_result = "\n".join(text_leaves(start_call.get("result")))
        background_facts["start"] = {
            "seq": start_call.get("seq"),
            "tool": start_call.get("name"),
            "command": command[:200],
        }
        if any(word in start_result.lower() for word in ("still running", "alive")):
            background_facts["live_observation"] = {
                "seq": start_call.get("seq"),
                "text": start_result[:100],
            }
        if any(word in start_result.lower() for word in ("process exited", "done", "completed")):
            background_facts["terminal"] = {
                "seq": start_call.get("seq"),
                "text": start_result[-100:],
            }
        following_background_calls = (
            calls[background_starts[-1] + 1 :]
            if background_facts["terminal"] is None
            else []
        )
        for call in following_background_calls:
            name = str(call.get("name", "")).lower()
            if not any(word in name for word in ("process", "taskoutput", "read")):
                continue
            call_arguments = call.get("arguments")
            argument_values = [
                value
                for value in text_leaves(call_arguments)
                if len(value) >= 6 and value in start_result
            ]
            if name.find("process") < 0 and not argument_values:
                continue
            result_text = "\n".join(text_leaves(call.get("result")))
            lowered = result_text.lower()
            if background_facts["live_observation"] is None and any(
                word in lowered for word in ("still running", "alive", "contents are empty")
            ):
                background_facts["live_observation"] = {
                    "seq": call.get("seq"),
                    "tool": call.get("name"),
                    "text": result_text[:100],
                }
            if any(word in lowered for word in ("process exited", "status>completed", "exit_code", "done")):
                background_facts["terminal"] = {
                    "seq": call.get("seq"),
                    "tool": call.get("name"),
                    "text": result_text[-100:],
                }
                break
    write_rubric_evidence(
        evidence_root,
        "ar_background_probe_honest",
        {
            "claim": {
                "state": probes.get("P-BACKGROUND", {}).get("state"),
                "method": probes.get("P-BACKGROUND", {}).get("method"),
                "result_excerpt": str(probes.get("P-BACKGROUND", {}).get("result"))[:150],
            },
            "dispatch": {
                key: value
                for key, value in (
                    next((row for row in rows if row.get("probe") == "P-BACKGROUND"), {})
                ).items()
                if key in {"state", "method"}
            },
            "trace_facts": background_facts,
            "missing_evidence_scores_zero": True,
        },
    )
    write_rubric_evidence(
        evidence_root,
        "ar_delegation_probe_honest",
        {
            "claim": {
                key: probes.get("P-DELEGATE", {}).get(key)
                for key in ("state", "method", "result")
            },
            "dispatch": {
                key: value
                for key, value in (
                    next((row for row in rows if row.get("probe") == "P-DELEGATE"), {})
                ).items()
                if key in {"state", "method"}
            },
            "delegation_start": (
                {
                    "seq": delegation_start.get("seq"),
                    "tool": delegation_start.get("name"),
                    "background": compact_call(delegation_start).get("background_flag"),
                    "accepted": "accepted" in "\n".join(text_leaves(delegation_start.get("result"))).lower()
                    or "launched" in "\n".join(text_leaves(delegation_start.get("result"))).lower(),
                }
                if delegation_start
                else None
            ),
            "intervening_tool_names": delegation_intervening[:5],
            "delegated_result_retrieval": (
                {
                    "seq": delegation_retrieval.get("seq"),
                    "tool": delegation_retrieval.get("name"),
                    "result_tail": "\n".join(text_leaves(delegation_retrieval.get("result")))[-180:],
                }
                if delegation_retrieval
                else None
            ),
            "missing_evidence_scores_zero": True,
        },
    )

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_rules, "agent": agent_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
