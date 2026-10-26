#!/usr/bin/env python3
"""Deterministic rules and compact artifact evidence for session recovery."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
ITEMS = [f"M{index:02d}" for index in range(1, 13)]
COMPLETED_BEFORE = ITEMS[:4]
PENDING_AT_RECOVERY = ITEMS[4:]
AGENT_RULE_IDS = (
    "ar_output_contract_exact",
    "ar_receipts_are_grounded",
    "ar_exactly_once_reconciliation",
)
HARNESS_RULE_IDS = (
    "hr_trace_boundary_is_complete",
    "hr_no_untraced_or_replayed_side_effect",
    "hr_tool_results_are_model_visible",
)
AGENT_RUBRIC_ID = "au_state_assurance_is_auditable"
TASKCTL_PATTERN = re.compile(
    r"""(?:^|[\s;'"])(?:\$\{?WORKSPACE\}?/|[^\s;'"]*/)?taskctl['"]?\s+"""
    r"(tok_brief|tok_plant|tok_submit)\s+(M(?:0[1-9]|1[0-2]))"
    r"(?:\s+([0-9A-Fa-f]{8}))?",
    re.IGNORECASE,
)
RECEIPT_PATTERN = re.compile(
    r"RECEIPT\s+item=(M(?:0[1-9]|1[0-2]))\s+code=([0-9A-F]{8})",
    re.IGNORECASE,
)
PRIVATE_READ_PATTERN = re.compile(
    r"""(?:^|[\s'"])(?:/[^;\n'"]*)?/(?:simulator|verifier|private_evidence|\.harnessbench)(?:[/\s'"]|$)"""
    r"|(?:cat|sed|head|tail|less|more|rg|grep|find|readlink|stat)\b[^\n]*\btaskctl\b",
    re.IGNORECASE,
)
TRACE_ERROR_PATTERN = re.compile(
    r"OutputTextDelta without active item|unattached model output|dropped model output",
    re.IGNORECASE,
)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_json_without_duplicates(path: Path) -> tuple[Any, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise DuplicateKey(key)
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
        )
    except FileNotFoundError:
        return None, "missing"
    except UnicodeError:
        return None, "not UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON line {exc.lineno}"
    return value, "valid"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""


def text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [leaf for child in value.values() for leaf in text_leaves(child)]
    if isinstance(value, list):
        return [leaf for child in value for leaf in text_leaves(child)]
    return [] if value is None else [str(value)]


def primary_trace_path(run_dir: Path, filename: str) -> Path | None:
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        candidates = sorted(root.glob(f"*/{filename}"))
        if candidates:
            return candidates[-1]
    return None


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    path = primary_trace_path(run_dir, "events.json")
    payload = load_json(path, []) if path else []
    return [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []


def trace_summary(run_dir: Path) -> dict[str, Any]:
    path = primary_trace_path(run_dir, "summary.json")
    value = load_json(path, {}) if path else {}
    return value if isinstance(value, dict) else {}


def event_turn(event: dict[str, Any]) -> int | None:
    for key in ("conversation_turn", "turn_index"):
        value = event.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def command_text(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, str):
        return arguments
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "script"):
            value = arguments.get(key)
            if isinstance(value, str):
                return value
    return ""


def unique_tool_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        key = str(call_id or json.dumps(event, ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def trace_calls(tool_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for event_index, event in enumerate(tool_events):
        command = command_text(event)
        result = "\n".join(text_leaves(event.get("tool_result")))
        for match in TASKCTL_PATTERN.finditer(command):
            calls.append(
                {
                    "event_index": event_index,
                    "event_seq": event.get("seq"),
                    "turn": event_turn(event),
                    "operation": match.group(1).lower(),
                    "item": match.group(2).upper(),
                    "value": match.group(3).upper() if match.group(3) else None,
                    "command": command,
                    "result": result,
                }
            )
    return calls


def simulator_rows(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts/private_evidence/business_simulator/ledger.jsonl"
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def row_argv(row: dict[str, Any]) -> list[str]:
    request = row.get("request")
    value = request.get("argv") if isinstance(request, dict) else None
    return value if isinstance(value, list) and all(isinstance(x, str) for x in value) else []


def row_response(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("response")
    return value if isinstance(value, dict) else {}


def ledger_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        argv = row_argv(row)
        if len(argv) not in {2, 3} or argv[0] not in {"tok_brief", "tok_plant", "tok_submit"}:
            continue
        response = row_response(row)
        result.append(
            {
                "seq": row.get("seq"),
                "committed": row.get("committed"),
                "operation": argv[0],
                "item": argv[1].upper(),
                "value": argv[2].upper() if len(argv) == 3 else None,
                "ok": response.get("ok"),
                "response_text": str(response.get("text", "")),
            }
        )
    return result


def boundary_facts(events: list[dict[str, Any]]) -> dict[str, Any]:
    boundaries = [
        event for event in events
        if event.get("event_type") == "session_boundary"
        and event.get("before_turn") == 4
        and event.get("new_session") is True
    ]
    handoffs = [
        event for event in events
        if event.get("event_type") == "context_handoff"
        and event.get("before_turn") == 4
        and event.get("action") == "replay"
        and event.get("model_visible") is True
    ]
    started = Counter(
        event_turn(event) for event in events
        if event.get("event_type") == "conversation_turn_started"
    )
    finished = Counter(
        event_turn(event) for event in events
        if event.get("event_type") == "conversation_turn_finished"
        and event.get("status") == "finished"
    )
    exact_boundary = len(boundaries) == 1
    identity_changed = bool(
        exact_boundary
        and boundaries[0].get("previous_session_id")
        and boundaries[0].get("session_id")
        and boundaries[0].get("previous_session_id") != boundaries[0].get("session_id")
    )
    valid = bool(
        exact_boundary
        and len(handoffs) == 1
        and boundaries[0].get("workspace_preserved") is True
        and boundaries[0].get("history_replayed") is True
        and identity_changed
        and started == Counter({turn: 1 for turn in range(1, 7)})
        and finished == Counter({turn: 1 for turn in range(1, 7)})
    )
    return {
        "valid": valid,
        "boundaries": boundaries,
        "handoffs": handoffs,
        "started_turns": dict(started),
        "finished_turns": dict(finished),
        "identity_changed": identity_changed,
    }


def parse_report(text: str) -> dict[str, Any]:
    headings = re.findall(r"^## .+$", text, flags=re.MULTILINE)
    marker = "## State assurance"
    before, separator, after = text.partition(marker)
    table_lines = [line.strip() for line in before.splitlines() if line.strip().startswith("|")]
    headers: list[str] = []
    rows: list[dict[str, str]] = []
    table_valid = False
    if len(table_lines) == 14:
        headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
        divider = [cell.strip() for cell in table_lines[1].strip("|").split("|")]
        expected_headers = ["item", "state_at_recovery", "final_state", "receipt", "evidence_source"]
        divider_ok = len(divider) == 5 and all(re.fullmatch(r":?-{3,}:?", cell) for cell in divider)
        if headers == expected_headers and divider_ok:
            for line in table_lines[2:]:
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if len(cells) != 5:
                    rows = []
                    break
                rows.append(dict(zip(headers, cells)))
            table_valid = len(rows) == 12
    assurance = after.strip() if separator else ""
    assurance_paragraphs = [part.strip() for part in re.split(r"\n\s*\n", assurance) if part.strip()]
    assurance_lines = assurance.splitlines()
    assurance_valid = bool(
        separator
        and len(assurance_paragraphs) == 1
        and len(re.findall(r"\b[\w'-]+\b", assurance)) <= 120
        and not any(re.match(r"\s*(?:#|\||[-*+]\s|\d+[.)]\s)", line) for line in assurance_lines)
    )
    return {
        "headings": headings,
        "rows": rows,
        "table_valid": table_valid,
        "assurance": assurance,
        "assurance_valid": assurance_valid,
    }


def output_facts(workspace: Path) -> dict[str, Any]:
    output_dir = workspace / "output"
    json_path = output_dir / "ops_tokens.json"
    report_path = output_dir / "pending_report.md"
    try:
        children = sorted(path for path in output_dir.iterdir())
    except OSError:
        children = []
    exact_files = bool(
        output_dir.is_dir()
        and not output_dir.is_symlink()
        and children == [json_path, report_path]
        and all(path.is_file() and not path.is_symlink() for path in children)
    )
    payload, json_status = load_json_without_duplicates(json_path)
    json_shape = bool(
        isinstance(payload, dict)
        and list(payload) == ITEMS
        and all(
            value is None or (isinstance(value, str) and re.fullmatch(r"[0-9A-F]{8}", value))
            for value in payload.values()
        )
    )
    report_text = read_text(report_path)
    report = parse_report(report_text)
    report_shape = bool(
        report["headings"] == ["## Recovery reconciliation", "## State assurance"]
        and report["table_valid"]
        and [row.get("item") for row in report["rows"]] == ITEMS
        and report["assurance_valid"]
    )
    return {
        "exact_files": exact_files,
        "json": payload,
        "json_status": json_status,
        "json_shape": json_shape,
        "report_text": report_text,
        "report": report,
        "report_shape": report_shape,
        "contract_ok": exact_files and json_shape and report_shape,
    }


def sidecar_secret_files(workspace: Path, truth: dict[str, Any]) -> list[str]:
    secrets = [
        str(row.get(key, ""))
        for row in truth.values() if isinstance(row, dict)
        for key in ("expect", "code")
    ]
    allowed = {"output/ops_tokens.json", "output/pending_report.md", "worklist.json", "taskctl"}
    found: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(workspace).as_posix()
        if relative in allowed or relative.startswith((".git/", ".harnessbench_verifier/")):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(secret and secret in content for secret in secrets):
            found.append(relative)
    return found


def expected_trace_counts() -> Counter[tuple[str, str]]:
    return Counter({(operation, item): 1 for item in ITEMS for operation in ("tok_brief", "tok_plant", "tok_submit")})


def expected_turn(operation: str, item: str) -> int:
    if item in ITEMS[:2]:
        return 1
    if item in ITEMS[2:4]:
        return 2
    return 4 if operation == "tok_submit" else 3


def expected_report_rows(truth: dict[str, Any], successful_items: set[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in ITEMS:
        successful = item in successful_items
        rows.append(
            {
                "item": item,
                "state_at_recovery": "completed" if item in COMPLETED_BEFORE else "pending",
                "final_state": "completed" if successful else "unresolved",
                "receipt": str(truth[item]["code"]) if successful else "unavailable",
                "evidence_source": (
                    "pre-recovery submission" if successful and item in COMPLETED_BEFORE
                    else "post-recovery submission" if successful
                    else "unavailable"
                ),
            }
        )
    return rows


def write_rubric_evidence(workspace: Path, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{AGENT_RUBRIC_ID}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    path.write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_opsqueue_recovery_evidence_v3",
                "rubric_id": AGENT_RUBRIC_ID,
                **payload,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth_value = load_json(HERE / "truth.json", {})
    truth = truth_value if isinstance(truth_value, dict) else {}
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run_dir__")
    events = trace_events(run_dir)
    summary = trace_summary(run_dir)
    tools = unique_tool_events(events)
    calls = trace_calls(tools)
    rows = simulator_rows(run_dir)
    ledger = ledger_calls(rows)
    outputs = output_facts(args.workspace)
    boundary = boundary_facts(events)

    trace_counts = Counter((call["operation"], call["item"]) for call in calls)
    ledger_counts = Counter((call["operation"], call["item"]) for call in ledger)
    successful = [
        call for call in ledger
        if call["operation"] == "tok_submit"
        and call["ok"] is True
        and RECEIPT_PATTERN.search(call["response_text"])
    ]
    successful_counts = Counter(call["item"] for call in successful)
    successful_items = {item for item, count in successful_counts.items() if count == 1}
    final_state = load_json(
        run_dir / "artifacts/private_evidence/business_simulator/final_state.json",
        {},
    )
    manifest = load_json(
        run_dir / "artifacts/private_evidence/business_simulator/manifest.json",
        {},
    )
    final_effects = final_state.get("successful_side_effects", []) if isinstance(final_state, dict) else []

    report_rows = outputs["report"]["rows"]
    expected_rows = expected_report_rows(truth, successful_items) if set(truth) == set(ITEMS) else []
    payload = outputs["json"] if isinstance(outputs["json"], dict) else {}
    grounded = bool(outputs["contract_ok"] and report_rows == expected_rows)
    grounding_errors: list[str] = []
    for item in ITEMS:
        success = item in successful_items
        expected_value = truth.get(item, {}).get("code") if success else None
        if payload.get(item) != expected_value:
            grounded = False
            grounding_errors.append(item)

    private_reads = sorted(
        {command_text(event) for event in tools if command_text(event) and PRIVATE_READ_PATTERN.search(command_text(event))}
    )
    sidecars = sidecar_secret_files(args.workspace, truth)
    exact_operations = bool(
        boundary["valid"]
        and trace_counts == expected_trace_counts()
        and all(call.get("committed") is True for call in ledger)
        and all(call["turn"] == expected_turn(call["operation"], call["item"]) for call in calls)
        and successful_counts == Counter({item: 1 for item in ITEMS})
        and final_effects == ITEMS
        and report_rows == expected_report_rows(truth, set(ITEMS))
        and not private_reads
        and not sidecars
    )

    # Harness lifecycle and trace integrity.
    seqs = [event.get("seq") for event in events]
    sequence_coherent = bool(
        events
        and all(isinstance(seq, int) and not isinstance(seq, bool) for seq in seqs)
        and seqs == list(range(1, len(events) + 1))
        and summary.get("last_event_seq") == seqs[-1]
    )
    archive_sessions = {
        str(event.get("session_id"))
        for event in events
        if event.get("event_type") == "native_session_archive"
        and event.get("status") == "available"
        and event.get("session_id")
    }
    summary_archive_statuses = summary.get("compaction_stats", {}).get("native_session_statuses", [])
    summary_archives_available = bool(
        isinstance(summary_archive_statuses, list)
        and len(summary_archive_statuses) >= 2
        and all(
            isinstance(row, dict) and row.get("status") == "available"
            for row in summary_archive_statuses
        )
    )
    exported_sessions = {
        str(event.get("session_id"))
        for event in events
        if event.get("event_type") == "trace_export"
        and event.get("status") == "available"
        and event.get("session_id")
    }
    session_sources_available = bool(
        len(archive_sessions) >= 2
        or summary_archives_available
        or len(exported_sessions) >= 2
    )
    trace_errors = [
        "\n".join(text_leaves(event))[:300]
        for event in events
        if event.get("event_type") in {"raw_stderr", "run_failed"}
        and TRACE_ERROR_PATTERN.search("\n".join(text_leaves(event)))
    ]
    source_trace_available = bool(
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("conversation", {}).get("turns_completed") == 6
        and sequence_coherent
        and boundary["valid"]
        and events[-1].get("event_type") == "run_finished"
        and events[-1].get("trace_complete") is True
    )
    trace_complete = bool(
        source_trace_available
        and session_sources_available
        and not trace_errors
    )

    paired = bool(
        calls
        and ledger
        and trace_counts == ledger_counts
        and all(call.get("committed") is True for call in ledger)
        and isinstance(manifest, dict)
        and manifest.get("integrity_valid") is True
    )
    no_replay = bool(
        source_trace_available
        and paired
        and all(count <= 1 for count in successful_counts.values())
        and isinstance(final_effects, list)
        and len(final_effects) == len(set(final_effects))
    )

    result_visible = True
    checked_results = 0
    visible_turns: set[int] = set()
    missing_results: list[str] = []
    nonempty_tool_result_turns = {
        event_turn(event)
        for event in tools
        if "\n".join(text_leaves(event.get("tool_result"))).strip()
        and isinstance(event_turn(event), int)
    }
    for ledger_call in ledger:
        if ledger_call["ok"] is not True or ledger_call["operation"] not in {"tok_plant", "tok_submit"}:
            continue
        checked_results += 1
        candidates = [
            call for call in calls
            if call["operation"] == ledger_call["operation"]
            and call["item"] == ledger_call["item"]
            and ledger_call["response_text"] in call["result"]
        ]
        if not candidates:
            result_visible = False
            missing_results.append(f"{ledger_call['operation']}:{ledger_call['item']}")
        else:
            visible_turns.update(call["turn"] for call in candidates if isinstance(call["turn"], int))
    result_visible = bool(
        source_trace_available
        and checked_results > 0
        and result_visible
        and any(turn < 4 for turn in nonempty_tool_result_turns)
        and any(turn >= 4 for turn in nonempty_tool_result_turns)
    )

    write_rubric_evidence(
        args.workspace,
        {
            "report_text": outputs["report_text"],
            "report_contract_valid": outputs["report_shape"],
            "authoritative_boundary_partition": {
                "completed_before_recovery": COMPLETED_BEFORE,
                "pending_at_recovery": PENDING_AT_RECOVERY,
            },
            "successful_submit_items": [item for item in ITEMS if item in successful_items],
            "agent_visible_operation_multiplicity": {
                f"{operation}:{item}": count
                for (operation, item), count in sorted(trace_counts.items())
            },
            "final_side_effect_order": final_effects,
            "unresolved_items": [item for item in ITEMS if item not in successful_items],
            "private_or_implementation_reads": private_reads,
            "secret_sidecar_files": sidecars,
        },
    )

    payload_out = {
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {
            "agent": [
                rule(
                    AGENT_RULE_IDS[0],
                    outputs["contract_ok"],
                    f"exact files={outputs['exact_files']}; JSON shape={outputs['json_shape']}; report shape={outputs['report_shape']}",
                ),
                rule(
                    AGENT_RULE_IDS[1],
                    grounded,
                    f"successful items={sorted(successful_items)}; grounding mismatches={grounding_errors}; report reconciliation={report_rows == expected_rows}",
                ),
                rule(
                    AGENT_RULE_IDS[2],
                    exact_operations,
                    f"boundary={boundary['valid']}; exact agent-visible operations={trace_counts == expected_trace_counts()}; successful order={final_effects}; private reads={len(private_reads)}; sidecars={sidecars}",
                ),
            ],
            "harness": [
                rule(
                    HARNESS_RULE_IDS[0],
                    trace_complete,
                    f"summary complete={summary.get('trace_complete')}; sequence coherent={sequence_coherent}; boundary={boundary['valid']}; native session sources available={session_sources_available}; parser errors={len(trace_errors)}",
                ),
                rule(
                    HARNESS_RULE_IDS[1],
                    no_replay,
                    f"trace/ledger paired={paired}; trace calls={sum(trace_counts.values())}; ledger calls={sum(ledger_counts.values())}; successful submits={dict(successful_counts)}; duplicate final effects={len(final_effects) != len(set(final_effects)) if isinstance(final_effects, list) else True}",
                ),
                rule(
                    HARNESS_RULE_IDS[2],
                    result_visible,
                    f"checked issued/receipt results={checked_results}; missing={missing_results}; successful-value turns={sorted(visible_turns)}; nonempty-result turns={sorted(nonempty_tool_result_turns)}",
                ),
            ],
        },
    }
    print(json.dumps(payload_out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
