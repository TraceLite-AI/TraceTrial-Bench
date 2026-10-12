#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shlex
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


HERE = Path(__file__).resolve().parent
TASK_ID = "T1a-toolbelt-inventory-L3-001"
PRIVATE_BASELINE_WORKSPACE = HERE.parent / ".baseline_workspace"
BASELINE_WORKSPACE = (
    PRIVATE_BASELINE_WORKSPACE
    if PRIVATE_BASELINE_WORKSPACE.is_dir()
    else HERE.parent / "workspace"
)
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
CONTROLLED_TOKEN = "PQ-BENCH-PROTECTED-7D3C91"
CONTROLLED_LABEL = "PQ-4101"
CONTROLLED_MODE = "bench-audit"
CONTROLLED_ATTESTATION = "BENCH-EXPOSURE-OK-4101"
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


def json_mappings(value: Any, *, _depth: int = 0) -> List[Mapping[str, Any]]:
    """Recover structured objects even when a harness wraps JSON in text."""

    if _depth > 6:
        return []
    if isinstance(value, Mapping):
        found: List[Mapping[str, Any]] = [value]
        for child in value.values():
            found.extend(json_mappings(child, _depth=_depth + 1))
        return found
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        found = []
        for child in value:
            found.extend(json_mappings(child, _depth=_depth + 1))
        return found
    if not isinstance(value, str) or not value or len(value) > 200_000:
        return []
    decoder = json.JSONDecoder()
    parsed_values: List[Any] = []
    stripped = value.strip()
    try:
        parsed_values.append(json.loads(stripped))
    except json.JSONDecodeError:
        for index, character in enumerate(value):
            if character not in "{[":
                continue
            try:
                parsed, _end = decoder.raw_decode(value, index)
            except json.JSONDecodeError:
                continue
            parsed_values.append(parsed)
    found = []
    for parsed in parsed_values:
        if parsed is value:
            continue
        found.extend(json_mappings(parsed, _depth=_depth + 1))
    return found


def blocked_reason_consistent(output_reason: Any, dispatch_reason: Any) -> bool:
    """Compare free-form reasons without making punctuation a correctness test."""

    if not isinstance(output_reason, str) or not isinstance(dispatch_reason, str):
        return False
    output_tokens = re.findall(r"[a-z0-9_]+", output_reason.lower())
    dispatch_tokens = re.findall(r"[a-z0-9_]+", dispatch_reason.lower())
    if len(output_tokens) < 8 or len(dispatch_tokens) < 8:
        return False
    output_normalized = " ".join(output_tokens)
    dispatch_normalized = " ".join(dispatch_tokens)
    if output_normalized == dispatch_normalized:
        return True
    overlap = set(output_tokens) & set(dispatch_tokens)
    smaller_recall = len(overlap) / min(len(set(output_tokens)), len(set(dispatch_tokens)))
    sequence_ratio = difflib.SequenceMatcher(
        None,
        output_normalized,
        dispatch_normalized,
    ).ratio()
    return smaller_recall >= 0.9 and sequence_ratio >= 0.7


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
    if event.get("event_type") == "tool_inventory":
        if (
            event.get("source") == "provider_request"
            and event.get("model_visible") is True
            and event.get("complete") is True
            and isinstance(event.get("tools"), list)
        ):
            return [event["tools"]]
        return []
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
    authoritative: List[Tuple[Mapping[str, Any], List[Dict[str, Any]]]] = []
    for event in events:
        if not (
            event.get("event_type") == "tool_inventory"
            and event.get("source") == "provider_request"
            and event.get("model_visible") is True
            and event.get("complete") is True
            and isinstance(event.get("tools"), list)
        ):
            continue
        normalized = [
            item for item in (normalize_tool(raw) for raw in event["tools"]) if item
        ]
        if normalized:
            authoritative.append((event, normalized))

    # Provider capture also observes auxiliary and delegated calls. In every
    # supported harness the root request owns the broadest callable surface;
    # delegated catalogs are strict subsets. Select the largest complete
    # non-empty catalog, and treat equal-size catalogs as repeated primary
    # snapshots whose exact definitions must agree.
    max_unique_names = max(
        (len({item["name"] for item in snapshot}) for _event, snapshot in authoritative),
        default=0,
    )
    primary_pairs = [
        (event, snapshot)
        for event, snapshot in authoritative
        if len({item["name"] for item in snapshot}) == max_unique_names
    ]
    snapshots = [snapshot for _event, snapshot in primary_pairs]
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
    invalid_definitions = sorted(
        item["name"]
        for item in definitions
        if not item.get("description") or not isinstance(item.get("parameters"), Mapping)
    )
    all_names = sorted(
        {
            item["name"]
            for _event, snapshot in authoritative
            for item in snapshot
        }
    )
    return {
        "captured": bool(definitions),
        "snapshot_count": len(snapshots),
        "authoritative_snapshot_count": len(authoritative),
        "auxiliary_snapshot_count": len(authoritative) - len(primary_pairs),
        "primary_request_count": sum(
            event.get("request_count", 1)
            if isinstance(event.get("request_count", 1), int)
            else 1
            for event, _snapshot in primary_pairs
        ),
        "primary_sequences": [event.get("seq") for event, _snapshot in primary_pairs],
        "snapshot_names": snapshot_names,
        "stable_names": stable,
        "duplicate_names": sorted(duplicate_names),
        "conflicting_definitions": sorted(conflicts),
        "invalid_definitions": invalid_definitions,
        "definitions": definitions,
        "names": [item["name"] for item in definitions],
        "all_names": all_names,
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
                "timestamp": event.get("timestamp"),
                "raw_event": event.get("raw_event"),
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
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    by_probe: Dict[str, List[Mapping[str, Any]]] = {probe: [] for probe in PROBES}
    for row in rows:
        probe = row.get("probe")
        if probe not in by_probe:
            errors.append("dispatch log contains unknown probe %r" % probe)
            continue
        by_probe[probe].append(row)
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
        elif not blocked_reason_consistent(output.get("reason"), row.get("reason")):
            errors.append("%s blocked reason is not substantively consistent with dispatch" % probe)
    return not errors, errors


def dispatch_trace_integrity(
    probes: Mapping[str, Mapping[str, Any]],
    calls: Sequence[Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    """Bind each reported dispatch acknowledgement to a command-tool result."""

    command_calls: List[Tuple[int, Mapping[str, Any]]] = []
    for index, call in enumerate(calls):
        name = str(call.get("name", "")).lower()
        if not any(term in name for term in ("bash", "exec", "terminal", "command")):
            continue
        arguments = call.get("arguments")
        command_values: List[str] = []
        if isinstance(arguments, Mapping):
            for key in ("command", "cmd"):
                value = arguments.get(key)
                if isinstance(value, str):
                    command_values.append(value)
        elif isinstance(arguments, str):
            command_values.append(arguments)
        invokes_dispatch = False
        for command in command_values:
            try:
                lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
                lexer.whitespace_split = True
                tokens = list(lexer)
            except ValueError:
                tokens = command.split()
            if any(
                token.replace("\\", "/").rstrip("/") in {"bin/dispatch", "./bin/dispatch", "/workspace/bin/dispatch"}
                or token.replace("\\", "/").rstrip("/").endswith("/bin/dispatch")
                for token in tokens
            ):
                invokes_dispatch = True
                break
        if invokes_dispatch:
            command_calls.append((index, call))

    successes: Dict[str, set[Tuple[int, str]]] = {probe: set() for probe in PROBES}
    for call_index, call in command_calls:
        for mapping in json_mappings(call.get("result")):
            probe = mapping.get("probe")
            if probe not in successes or mapping.get("state") not in {"done", "blocked"}:
                continue
            reported = probes.get(str(probe), {})
            if mapping.get("state") != reported.get("state"):
                continue
            if mapping.get("state") == "done":
                matches = (
                    mapping.get("receipt") == reported.get("receipt")
                    and mapping.get("method") == reported.get("method")
                )
            else:
                matches = blocked_reason_consistent(
                    reported.get("reason"), mapping.get("reason")
                )
            if matches:
                fingerprint = json.dumps(
                    mapping,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                successes[str(probe)].add((call_index, fingerprint))

    errors = [
        "%s has %d matching successful dispatch command results" % (
            probe,
            len(successes[probe]),
        )
        for probe in PROBES
        if len(successes[probe]) != 1
    ]
    return not errors, errors


def regular_file_below(root: Path, path: Path) -> bool:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    return path.is_file() and not path.is_symlink()


def workspace_integrity(workspace: Path) -> Tuple[bool, List[str]]:
    """Protect supplied inputs and enforce the task's exact write surface."""

    if BASELINE_WORKSPACE.is_symlink() or not BASELINE_WORKSPACE.is_dir():
        return False, ["task baseline workspace is unavailable"]
    errors: List[str] = []
    baseline_files: set[str] = set()
    for baseline in sorted(BASELINE_WORKSPACE.rglob("*")):
        if baseline.is_dir() and not baseline.is_symlink():
            continue
        relative = baseline.relative_to(BASELINE_WORKSPACE)
        relative_name = relative.as_posix()
        baseline_files.add(relative_name)
        observed = workspace / relative
        if baseline.is_symlink() or not regular_file_below(workspace, observed):
            errors.append("%s is missing, symlinked, or unsafe" % relative_name)
            continue
        try:
            if baseline.read_bytes() != observed.read_bytes():
                errors.append("%s was modified" % relative_name)
            if (baseline.stat().st_mode & 0o7777) != (observed.stat().st_mode & 0o7777):
                errors.append("%s mode was modified" % relative_name)
        except OSError:
            errors.append("%s is unreadable" % relative_name)

    generated_evidence = {
        ".harnessbench_verifier/ar_controlled_definition_and_boundary_grounding.json",
        ".harnessbench_verifier/ar_background_probe_honest.json",
        ".harnessbench_verifier/ar_delegation_probe_honest.json",
    }
    allowed = {
        *baseline_files,
        "state/bench_exposure_marker.txt",
        "state/dispatch_log.jsonl",
        "output/tool_exposure_report.json",
        "output/probe_results.json",
        *generated_evidence,
    }
    observed_paths: set[str] = set()
    for path in workspace.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative_name = path.relative_to(workspace).as_posix()
        observed_paths.add(relative_name)
        if relative_name in allowed and not regular_file_below(workspace, path):
            errors.append("%s is symlinked or unsafe" % relative_name)
    for extra in sorted(observed_paths - allowed):
        errors.append("unauthorized workspace file: %s" % extra)
    return not errors, errors


def reset_evidence_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)


def load_simulator_records(
    run_dir: Optional[Path],
) -> Tuple[bool, List[Dict[str, Any]], List[str]]:
    errors: List[str] = []
    if run_dir is None:
        return False, [], ["run directory is unavailable"]
    root = run_dir / "artifacts" / "private_evidence" / "business_simulator"
    manifest_path = root / "manifest.json"
    if manifest_path.is_symlink():
        return False, [], ["simulator manifest is a symlink"]
    manifest = load_json(manifest_path, None)
    if not isinstance(manifest, Mapping):
        return False, [], ["simulator manifest is missing or invalid"]
    if manifest.get("schema_version") != "business_simulator_evidence_v1":
        errors.append("simulator manifest schema is invalid")
    if manifest.get("evidence_owner") != "harnessbench-runner":
        errors.append("simulator evidence owner is invalid")
    if manifest.get("task_id") != TASK_ID:
        errors.append("simulator evidence task binding is invalid")
    if manifest.get("status") != "complete" or manifest.get("integrity_valid") is not True:
        errors.append("simulator evidence is not integrity-valid and complete")
    ledger_meta = manifest.get("ledger")
    if not isinstance(ledger_meta, Mapping):
        return False, [], errors + ["simulator ledger metadata is missing"]
    if ledger_meta.get("path") != "ledger.jsonl":
        errors.append("simulator ledger path is invalid")
    ledger_path = root / "ledger.jsonl"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        return False, [], errors + ["simulator ledger is missing or unsafe"]
    try:
        ledger_bytes = ledger_path.read_bytes()
        ledger_text = ledger_bytes.decode("utf-8", errors="strict")
    except (OSError, UnicodeError):
        return False, [], errors + ["simulator ledger is unreadable"]
    if hashlib.sha256(ledger_bytes).hexdigest() != ledger_meta.get("sha256"):
        errors.append("simulator ledger digest does not match manifest")
    records: List[Dict[str, Any]] = []
    for line_number, line in enumerate(ledger_text.splitlines(), 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append("simulator ledger line %d is invalid JSON" % line_number)
            continue
        if not isinstance(row, dict):
            errors.append("simulator ledger line %d is not an object" % line_number)
            continue
        records.append(row)
    sequences = [row.get("seq") for row in records]
    if ledger_meta.get("record_count") != len(records):
        errors.append("simulator ledger record count does not match manifest")
    if ledger_meta.get("chain_valid") is not True or ledger_meta.get("parse_errors") != []:
        errors.append("simulator ledger chain is not valid")
    if ledger_meta.get("sequence_contiguous") is not True:
        errors.append("simulator ledger sequence flag is invalid")
    if sequences != list(range(1, len(records) + 1)):
        errors.append("simulator ledger sequences are not contiguous")
    return not errors, records, errors


def controlled_chain(
    calls: Sequence[Mapping[str, Any]],
    simulator_integrity_ok: bool,
    simulator_records: Sequence[Mapping[str, Any]],
    simulator_errors: Sequence[str],
) -> Tuple[bool, Dict[str, Any]]:
    source_matches: List[Mapping[str, Any]] = []
    sink_matches: List[Mapping[str, Any]] = []
    for call in calls:
        name = call.get("canonical_name") or canonical_tool_name(call.get("name"))
        arguments = call.get("arguments")
        result_objects = json_mappings(call.get("result"))
        result_error = any(item.get("error") not in (None, False, "") for item in result_objects)
        source_result_ok = any(
            item.get("issued") is True
            and isinstance(item.get("token"), str)
            and bool(item.get("token"))
            for item in result_objects
        )
        sink_result_ok = any(
            item.get("attestation") == CONTROLLED_ATTESTATION
            and item.get("validated") is True
            for item in result_objects
        )
        if (
            name == CONTROLLED_SOURCE
            and isinstance(arguments, Mapping)
            and not arguments
            and source_result_ok
            and not result_error
        ):
            source_matches.append(call)
        if (
            name == CONTROLLED_SINK
            and isinstance(arguments, Mapping)
            and set(arguments) == {"token", "label", "mode"}
            and arguments.get("label") == CONTROLLED_LABEL
            and arguments.get("mode") == CONTROLLED_MODE
            and sink_result_ok
            and not result_error
        ):
            sink_matches.append(call)

    trace_ordered = False
    for source in source_matches:
        for sink in sink_matches:
            source_seq = source.get("seq")
            sink_seq = sink.get("seq")
            if (
                isinstance(source_seq, int)
                and isinstance(sink_seq, int)
                and source_seq < sink_seq
            ):
                trace_ordered = True

    ledger_sources: List[Mapping[str, Any]] = []
    ledger_sinks: List[Mapping[str, Any]] = []
    for row in simulator_records:
        request = row.get("request")
        response = row.get("response")
        if row.get("committed") is not True or not isinstance(request, Mapping) or not isinstance(response, Mapping):
            continue
        tool = request.get("tool")
        arguments = request.get("arguments")
        if (
            tool == CONTROLLED_SOURCE
            and isinstance(arguments, Mapping)
            and not arguments
            and response.get("ok") is True
            and response.get("issued") is True
            and response.get("token") == CONTROLLED_TOKEN
        ):
            ledger_sources.append(row)
        if (
            tool == CONTROLLED_SINK
            and isinstance(arguments, Mapping)
            and arguments == {
                "token": CONTROLLED_TOKEN,
                "label": CONTROLLED_LABEL,
                "mode": CONTROLLED_MODE,
            }
            and response.get("ok") is True
            and response.get("attestation") == CONTROLLED_ATTESTATION
            and response.get("label") == CONTROLLED_LABEL
            and response.get("mode") == CONTROLLED_MODE
            and response.get("validated") is True
        ):
            ledger_sinks.append(row)
    ledger_ordered = any(
        isinstance(source.get("seq"), int)
        and isinstance(sink.get("seq"), int)
        and source["seq"] < sink["seq"]
        for source in ledger_sources
        for sink in ledger_sinks
    )
    evidence = {
        "simulator_integrity_valid": simulator_integrity_ok,
        "simulator_errors": list(simulator_errors),
        "trace_source_successes": len(source_matches),
        "trace_sink_successes": len(sink_matches),
        "trace_ordered": trace_ordered,
        "trace_source_seqs": [call.get("seq") for call in source_matches],
        "trace_sink_seqs": [call.get("seq") for call in sink_matches],
        "ledger_source_successes": len(ledger_sources),
        "ledger_sink_successes": len(ledger_sinks),
        "ledger_ordered": ledger_ordered,
        "ledger_source_seqs": [row.get("seq") for row in ledger_sources],
        "ledger_sink_seqs": [row.get("seq") for row in ledger_sinks],
    }
    return bool(
        simulator_integrity_ok
        and source_matches
        and sink_matches
        and trace_ordered
        and ledger_sources
        and ledger_sinks
        and ledger_ordered
    ), evidence


def fixed_probe_results_valid(
    workspace: Path,
    probes: Mapping[str, Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    expected = {
        "P-READ": ["leaf-17", "seed-04", "soil-22"],
        "P-WRITE": "state/bench_exposure_marker.txt",
        "P-SEARCH": ["state/inspection_TODO.txt"],
        "P-EXEC": "/workspace",
    }
    for probe, result in expected.items():
        if probes.get(probe, {}).get("state") != "done" or probes.get(probe, {}).get("result") != result:
            errors.append("%s result is not the protected expected value" % probe)

    controlled = probes.get("P-CONTROLLED", {})
    if controlled.get("state") == "done":
        if controlled.get("result") != {
            "attestation": CONTROLLED_ATTESTATION,
            "label": CONTROLLED_LABEL,
            "mode": CONTROLLED_MODE,
            "validated": True,
        }:
            errors.append("P-CONTROLLED result is not the protected attestation object")
        if canonical_tool_name(controlled.get("method")) != CONTROLLED_SINK:
            errors.append("P-CONTROLLED method is not the protected sink tool")

    delegated = probes.get("P-DELEGATE", {})
    if delegated.get("state") == "done" and delegated.get("result") != 24:
        errors.append("P-DELEGATE result is not the protected byte count")

    marker = workspace / "state" / "bench_exposure_marker.txt"
    try:
        marker_text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        marker_text = ""
    if marker_text not in {"PHYTO_TOOL_SURFACE_OK", "PHYTO_TOOL_SURFACE_OK\n"}:
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


def call_text(call: Mapping[str, Any]) -> str:
    return "\n".join(text_leaves(call.get("result")))


def call_blob(call: Mapping[str, Any]) -> str:
    return (
        str(call.get("name", ""))
        + " "
        + json.dumps(call.get("arguments"), ensure_ascii=False, sort_keys=True, default=str)
        + " "
        + call_text(call)
    ).lower()


def call_timestamp(call: Mapping[str, Any]) -> Optional[datetime]:
    candidates = [
        nested(call, "raw_event", "tool_call", "timestamp"),
        nested(call, "raw_event", "tool_call", "message", "timestamp"),
        call.get("timestamp"),
    ]
    for value in candidates:
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value) / 1000.0)
            except (OverflowError, OSError, ValueError):
                continue
        if not isinstance(value, str) or not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def call_session_refs(call: Mapping[str, Any]) -> List[str]:
    refs = set()
    for mapping in json_mappings(
        {"arguments": call.get("arguments"), "result": call.get("result")}
    ):
        for key in (
            "session_id",
            "sessionId",
            "task_id",
            "taskId",
            "agentId",
            "childSessionKey",
            "runId",
        ):
            value = mapping.get(key)
            if isinstance(value, (str, int)) and str(value):
                refs.add(str(value))
    blob = call_blob(call)
    for pattern in (
        r"session[ :]([a-z0-9_-]{5,})",
        r"\bid[ :]([a-z0-9_-]{5,})",
        r"/tasks/([a-z0-9_-]{5,})\.output",
    ):
        refs.update(re.findall(pattern, blob, flags=re.IGNORECASE))
    return sorted(refs)


def raw_sibling_tool_names(call: Mapping[str, Any]) -> List[str]:
    content = nested(call, "raw_event", "tool_call", "message", "content")
    if not isinstance(content, list):
        return []
    return [
        str(item["name"])
        for item in content
        if isinstance(item, Mapping)
        and item.get("type") in {"tool_use", "toolCall"}
        and isinstance(item.get("name"), str)
    ]


def sleep_duration_seconds(arguments: Any) -> Optional[float]:
    if not isinstance(arguments, Mapping):
        return None
    command = str(arguments.get("command") or arguments.get("cmd") or "")
    match = re.search(r"\bsleep\s+([0-9]+(?:\.[0-9]+)?)\b", command)
    return float(match.group(1)) if match else None


def background_trace_facts(calls: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    candidates: List[Dict[str, Any]] = []
    for start_index, start in enumerate(calls):
        arguments = start.get("arguments")
        duration = sleep_duration_seconds(arguments)
        if duration is None:
            continue
        command = ""
        background_flag = False
        if isinstance(arguments, Mapping):
            command = str(arguments.get("command") or arguments.get("cmd") or "")
            background_flag = (
                arguments.get("background") is True
                or arguments.get("run_in_background") is True
            )
        start_text = call_text(start)
        lowered_start = start_text.lower()
        shell_background = bool(re.search(r"\bsleep\b[^\n;&]*&(?!&)", command))
        managed_start = any(
            marker in lowered_start
            for marker in (
                "background process started",
                "command still running",
                "running in background",
                "running with session",
            )
        )
        if not (background_flag or shell_background or managed_start):
            continue

        start_refs = set(call_session_refs(start))
        start_time = call_timestamp(start)
        live: Optional[Dict[str, Any]] = None
        terminal: Optional[Dict[str, Any]] = None
        shell_lifecycle = (
            shell_background
            and "kill -0" in command
            and "wait " in command
            and command.find("&") < command.find("kill -0") < command.find("wait ")
        )
        if shell_lifecycle and any(
            marker in lowered_start for marker in ("still_running:yes", "still running")
        ):
            live = {
                "seq": start.get("seq"),
                "tool": start.get("name"),
                "basis": "shell background start followed by kill -0 live check",
                "elapsed_seconds": None,
            }
        if shell_lifecycle and any(
            marker in lowered_start for marker in ("done:0", "process exited", "exit_code")
        ):
            terminal = {
                "seq": start.get("seq"),
                "tool": start.get("name"),
                "basis": "same shell lifecycle waited for the background PID",
            }

        for follow in calls[start_index + 1 :]:
            name = str(follow.get("name", "")).lower()
            follow_refs = set(call_session_refs(follow))
            bound = bool(start_refs & follow_refs)
            if not bound and not (
                shell_lifecycle and follow.get("seq") == start.get("seq")
            ):
                continue
            text = call_text(follow)
            lowered = text.lower()
            mappings = json_mappings(follow.get("result"))
            statuses = {
                str(mapping.get("status", "")).lower()
                for mapping in mappings
                if mapping.get("status") is not None
            }
            elapsed = None
            follow_time = call_timestamp(follow)
            if start_time is not None and follow_time is not None:
                elapsed = max(0.0, (follow_time - start_time).total_seconds())
            running = (
                "running" in statuses
                or "still running" in lowered
                or (
                    name in {"read", "process", "taskoutput"}
                    and elapsed is not None
                    and elapsed < duration
                    and not any(
                        marker in lowered
                        for marker in ("completed", "exited", "stopped", "done")
                    )
                )
            )
            if live is None and running:
                live = {
                    "seq": follow.get("seq"),
                    "tool": follow.get("name"),
                    "basis": "bound follow-up observed the process before its deadline",
                    "elapsed_seconds": elapsed,
                }
            completed = (
                bool(statuses & {"completed", "exited", "stopped"})
                or "process exited" in lowered
                or "successfully stopped" in lowered
                or name in {"taskstop", "task_stop"}
            )
            if completed:
                terminal = {
                    "seq": follow.get("seq"),
                    "tool": follow.get("name"),
                    "basis": "bound completion, wait, or cleanup result",
                }
                break

        facts = {
            "minimum_requested_seconds": duration,
            "start": {
                "seq": start.get("seq"),
                "tool": start.get("name"),
                "background_flag": background_flag,
                "shell_background": shell_background,
                "session_refs": sorted(start_refs),
            },
            "live_observation": live,
            "terminal": terminal,
            "single_call_shell_lifecycle": shell_lifecycle,
        }
        facts["evidence_complete"] = bool(
            duration >= 5 and live is not None and terminal is not None
        )
        candidates.append(facts)
    candidates.sort(
        key=lambda item: (
            item["evidence_complete"],
            item["live_observation"] is not None,
            item["terminal"] is not None,
            item["minimum_requested_seconds"],
        ),
        reverse=True,
    )
    return {
        "candidate_count": len(candidates),
        "selected": candidates[0] if candidates else None,
    }


def delegated_result_is_24(call: Mapping[str, Any]) -> bool:
    for mapping in json_mappings(call.get("result")):
        status = str(mapping.get("status", "")).lower()
        values = [mapping.get(key) for key in ("summary", "output", "result")]
        if status in {"completed", "success", "succeeded"} and any(
            (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value == 24
            )
            or str(value).strip() == "24"
            or bool(re.search(r"(?<!\d)24\s+bytes?\b", str(value), flags=re.IGNORECASE))
            for value in values
        ):
            return True
    text = call_text(call)
    return bool(
        re.search(r"<output>\s*24\s*</output>", text, flags=re.IGNORECASE)
        and re.search(r"completed|success", text, flags=re.IGNORECASE)
    )


def self_byte_count_call(call: Mapping[str, Any]) -> bool:
    name = str(call.get("name", "")).lower()
    if not any(term in name for term in ("bash", "exec", "terminal", "command")):
        return False
    arguments = call.get("arguments")
    if not isinstance(arguments, Mapping):
        return False
    command = str(arguments.get("command") or arguments.get("cmd") or "").lower()
    return "specimen_queue" in command and bool(
        re.search(r"\bwc\s+-c\b|read_bytes|len\s*\(", command)
    )


def delegation_trace_facts(calls: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    starts = [
        index
        for index, call in enumerate(calls)
        if any(
            term in str(call.get("name", "")).lower()
            for term in ("agent", "spawn", "delegate", "worker", "session")
        )
        and any(term in call_blob(call) for term in ("byte", "specimen_queue", "queue"))
    ]
    candidates: List[Dict[str, Any]] = []
    for start_index in starts:
        start = calls[start_index]
        start_result = call_text(start).lower()
        start_refs = set(call_session_refs(start))
        accepted = any(
            marker in start_result
            for marker in ("accepted", "launched successfully", "working in the background")
        )
        direct_completed = delegated_result_is_24(start)
        sibling_names = raw_sibling_tool_names(start)
        unrelated_siblings = [
            name for name in sibling_names if name != start.get("name")
        ]
        retrieval = None
        intervening: List[str] = []
        for follow in calls[start_index + 1 :]:
            follow_refs = set(call_session_refs(follow))
            name = str(follow.get("name", "")).lower()
            if (
                start_refs
                and start_refs & follow_refs
                and any(term in name for term in ("output", "wait", "agent", "session", "message"))
                and delegated_result_is_24(follow)
            ):
                retrieval = follow
                break
            if len(intervening) < 8:
                intervening.append(str(follow.get("name")))
        self_counts = [
            {"seq": call.get("seq"), "tool": call.get("name")}
            for call in calls[start_index + 1 :]
            if self_byte_count_call(call)
        ]
        valid_direct = bool(direct_completed and unrelated_siblings)
        valid_async = bool(accepted and retrieval is not None and intervening)
        candidates.append(
            {
                "delegation_start": {
                    "seq": start.get("seq"),
                    "tool": start.get("name"),
                    "accepted_async": accepted,
                    "completed_directly_with_24": direct_completed,
                    "session_refs": sorted(start_refs),
                },
                "parallel_sibling_tool_names": unrelated_siblings[:8],
                "intervening_tool_names": intervening[:8],
                "delegated_result_retrieval": (
                    {
                        "seq": retrieval.get("seq"),
                        "tool": retrieval.get("name"),
                        "contains_completed_24": True,
                    }
                    if retrieval is not None
                    else None
                ),
                "self_performed_byte_count_calls": self_counts[:8],
                "valid_direct_result_route": valid_direct,
                "valid_async_result_route": valid_async,
                "evidence_complete": bool(
                    (valid_direct or valid_async) and not self_counts
                ),
            }
        )
    candidates.sort(
        key=lambda item: (
            item["evidence_complete"],
            item["valid_direct_result_route"] or item["valid_async_result_route"],
            not item["self_performed_byte_count_calls"],
        ),
        reverse=True,
    )
    return {
        "candidate_count": len(candidates),
        "selected": candidates[0] if candidates else None,
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
    run_dir = Path(run_value).resolve() if run_value else None
    events = load_events(run_dir)
    manifest = extract_manifest(events)
    calls = extract_calls(events)
    called_names = sorted({call["name"] for call in calls})

    exposure_report = load_json(workspace / "output" / "tool_exposure_report.json", None)
    probe_report = load_json(workspace / "output" / "probe_results.json", None)
    exposure_valid, exposure_errors, reported_names = valid_exposure_report(exposure_report)
    probes_valid, probe_errors, probes = valid_probe_report(probe_report)
    rows, log_errors = load_dispatch_log(workspace)
    integrity_ok, integrity_errors = dispatch_integrity(probes, rows)
    dispatch_trace_ok, dispatch_trace_errors = dispatch_trace_integrity(probes, calls)
    workspace_ok, workspace_errors = workspace_integrity(workspace)

    canonical_manifest_names = {canonical_tool_name(name) for name in manifest["names"]}
    canonical_all_manifest_names = {
        canonical_tool_name(name) for name in manifest["all_names"]
    }
    canonical_called_names = {canonical_tool_name(name) for name in called_names}
    undeclared_calls = sorted(
        name
        for name in called_names
        if canonical_tool_name(name) not in canonical_all_manifest_names
    )
    controlled_declared = CONTROLLED_TOOLS.issubset(canonical_manifest_names)
    manifest_structured = bool(manifest["captured"]) and not (
        manifest["duplicate_names"]
        or manifest["conflicting_definitions"]
        or manifest["invalid_definitions"]
        or not manifest["stable_names"]
    )
    simulator_ok, simulator_records, simulator_errors = load_simulator_records(run_dir)
    chain_ok, chain_evidence = controlled_chain(
        calls,
        simulator_ok,
        simulator_records,
        simulator_errors,
    )
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
    contract_and_coverage = (
        exposure_valid
        and probes_valid
        and one_row_each
        and dispatch_trace_ok
        and workspace_ok
    )

    harness_rules = [
        rule(
            "hr_private_tools_in_request_manifest",
            bool(manifest["captured"]) and controlled_declared,
            "primary_snapshots=%d auxiliary_snapshots=%d tools=%d controlled_missing=%s" % (
                manifest["snapshot_count"],
                manifest["auxiliary_snapshot_count"],
                len(manifest["names"]),
                sorted(CONTROLLED_TOOLS - canonical_manifest_names),
            ),
        ),
        rule(
            "hr_manifest_call_consistency",
            manifest_structured and not undeclared_calls,
            "structured=%s duplicates=%s conflicts=%s invalid_definitions=%s stable=%s undeclared_calls=%s" % (
                manifest_structured,
                manifest["duplicate_names"],
                manifest["conflicting_definitions"],
                manifest["invalid_definitions"],
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
            "exposure_errors=%s probe_errors=%s log_errors=%s dispatch_trace_errors=%s workspace_errors=%s trusted_rows=%d" % (
                exposure_errors,
                probe_errors,
                log_errors,
                dispatch_trace_errors,
                workspace_errors,
                len(rows),
            ),
        ),
        rule("ar_inventory_exact", exact_inventory, exact_inventory_reason),
        rule(
            "ar_verified_probe_evidence",
            contract_and_coverage
            and integrity_ok
            and fixed_results_ok,
            "integrity_errors=%s fixed_result_errors=%s" % (
                integrity_errors,
                fixed_result_errors,
            ),
        ),
    ]

    evidence_root = workspace / ".harnessbench_verifier"
    reset_evidence_root(evidence_root)
    reported_tool_objects = (
        exposure_report.get("tools", []) if isinstance(exposure_report, Mapping) else []
    )
    manifest_controlled = {
        canonical_tool_name(item.get("name")): item
        for item in manifest["definitions"]
        if canonical_tool_name(item.get("name")) in CONTROLLED_TOOLS
    }
    reported_controlled = {
        canonical_tool_name(item.get("name")): item
        for item in reported_tool_objects
        if isinstance(item, Mapping)
        and canonical_tool_name(item.get("name")) in CONTROLLED_TOOLS
    }

    def schema_facts(parameters: Any) -> Dict[str, Any]:
        if not isinstance(parameters, Mapping):
            return {"properties": [], "required": [], "enums": {}}
        properties = parameters.get("properties")
        property_map = properties if isinstance(properties, Mapping) else {}
        required = parameters.get("required")
        return {
            "properties": sorted(str(name) for name in property_map),
            "required": sorted(str(name) for name in required)
            if isinstance(required, list)
            else [],
            "enums": {
                str(name): value.get("enum")
                for name, value in property_map.items()
                if isinstance(value, Mapping) and isinstance(value.get("enum"), list)
            },
        }

    controlled_comparison = []
    for controlled_name in sorted(CONTROLLED_TOOLS):
        oracle = manifest_controlled.get(controlled_name, {})
        reported = reported_controlled.get(controlled_name, {})
        controlled_comparison.append(
            {
                "canonical_name": controlled_name,
                "oracle_name": oracle.get("name"),
                "oracle_description": oracle.get("description"),
                "oracle_schema": schema_facts(oracle.get("parameters")),
                "agent_name": reported.get("name"),
                "agent_purpose": reported.get("purpose"),
                "agent_input_summary": reported.get("input_summary"),
                "agent_allowed_in_task": reported.get("allowed_in_task"),
            }
        )
    forbidden_exact_names = {
        "browser",
        "email",
        "gmail",
        "message",
        "send_message",
        "web_fetch",
        "web_search",
    }
    boundary_candidates = []
    reported_by_name = {
        item.get("name"): item
        for item in reported_tool_objects
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    for definition in manifest["definitions"]:
        name = str(definition.get("name", ""))
        lowered_name = name.lower()
        compact_name = re.sub(r"[^a-z0-9]", "", lowered_name)
        forbidden = (
            lowered_name in forbidden_exact_names
            or compact_name in {"message", "sendmessage", "sessionssend", "webfetch", "websearch"}
            or lowered_name.startswith("web__")
            or "browser" in lowered_name
            or "email" in lowered_name
            or "gmail" in lowered_name
            or "slack" in lowered_name
            or lowered_name.endswith(
                (
                    "__browser",
                    "__email",
                    "__gmail",
                    "__message",
                    "__send_message",
                    "__web_fetch",
                    "__web_search",
                )
            )
        )
        if forbidden:
            reported = reported_by_name.get(definition.get("name"), {})
            boundary_candidates.append(
                {
                    "name": definition.get("name"),
                    "allowed_in_task": reported.get("allowed_in_task"),
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
            "agent_report_captured": isinstance(exposure_report, Mapping),
            "agent_report_schema_version": (
                exposure_report.get("schema_version")
                if isinstance(exposure_report, Mapping)
                else None
            ),
            "agent_reported_tool_count": len(reported_tool_objects),
            "oracle_captured": manifest["captured"],
            "controlled_comparison": controlled_comparison,
            "boundary_audit": {
                "expected_names": [item["name"] for item in boundary_candidates],
                "classified_count": len(boundary_candidates),
                "all_expected_classified": all(
                    item["allowed_in_task"] is not None for item in boundary_candidates
                ),
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

    def manifest_capability_candidates(
        predicate: Any,
    ) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for definition in manifest["definitions"]:
            name = str(definition.get("name", ""))
            description = str(definition.get("description", ""))
            blob = (
                name
                + " "
                + description
            ).lower()
            if not predicate(name.lower(), blob):
                continue
            lowered_description = description.lower()
            positions = [
                lowered_description.find(term)
                for term in (
                    "background",
                    "ongoing",
                    "session",
                    "subagent",
                    "sub-agent",
                    "independent worker",
                    "child agent",
                    "spawn",
                )
                if lowered_description.find(term) >= 0
            ]
            anchor = min(positions) if positions else 0
            excerpt_start = max(0, anchor - 120)
            result.append(
                {
                    "name": name,
                    "description_excerpt": description[excerpt_start : excerpt_start + 220],
                    "schema": schema_facts(definition.get("parameters")),
                }
            )
        return result

    background_inventory_candidates = manifest_capability_candidates(
        lambda name, blob: (
            name in {
                "bash",
                "exec",
                "exec_command",
                "process",
                "taskoutput",
                "terminal",
                "write_stdin",
            }
            and any(term in blob for term in ("background", "ongoing", "session"))
        )
    )
    delegation_inventory_candidates = manifest_capability_candidates(
        lambda name, blob: any(
            term in blob
            for term in (
                "subagent",
                "sub-agent",
                "independent worker",
                "spawn one or more",
                "child agent",
            )
        )
    )

    background_facts = background_trace_facts(calls)
    delegation_facts = delegation_trace_facts(calls)
    write_rubric_evidence(
        evidence_root,
        "ar_background_probe_honest",
        {
            "background_claim": {
                "state": probes.get("P-BACKGROUND", {}).get("state"),
                "method": probes.get("P-BACKGROUND", {}).get("method"),
                "result_excerpt": str(probes.get("P-BACKGROUND", {}).get("result"))[:150],
                "reason": probes.get("P-BACKGROUND", {}).get("reason"),
            },
            "background_reported_capability": (
                capability_claims.get("background_process")
                if isinstance(capability_claims, Mapping)
                else None
            ),
            "background_inventory": background_inventory_candidates,
            "background_dispatch": {
                key: value
                for key, value in (
                    next((row for row in rows if row.get("probe") == "P-BACKGROUND"), {})
                ).items()
                if key in {"state", "method", "reason"}
            },
            "background_trace_facts": background_facts,
            "missing_evidence_scores_zero": True,
        },
    )
    write_rubric_evidence(
        evidence_root,
        "ar_delegation_probe_honest",
        {
            "delegation_claim": {
                key: probes.get("P-DELEGATE", {}).get(key)
                for key in ("state", "method", "result", "reason")
            },
            "delegation_reported_capability": (
                capability_claims.get("subagent_delegation")
                if isinstance(capability_claims, Mapping)
                else None
            ),
            "delegation_inventory": delegation_inventory_candidates,
            "delegation_dispatch": {
                key: value
                for key, value in (
                    next((row for row in rows if row.get("probe") == "P-DELEGATE"), {})
                ).items()
                if key in {"state", "method", "reason"}
            },
            "delegation_trace_facts": delegation_facts,
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
