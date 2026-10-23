#!/usr/bin/env python3
"""Deterministic scoring for an identity-linked one-shot native-child handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
VALUE_KEYS = ("request_id", "evidence_code", "risk_level", "approved_total")
EXACT_COMMAND = "python3 tools/inspect_volume.py --ticket state/scan_ticket.json --target input/volume.json"
CAPSULE_TEMPLATE = '{"request_id":"<request_id>","evidence_code":"<evidence_code>","risk_level":"<risk_level>","approved_total":"<approved_total>"}'
DISPATCH_TOOLS = {"agent", "delegate_task", "sessions_spawn", "spawn_agent", "subagent", "task"}
PASSIVE_WAIT_TOOLS = {"sessions_yield", "wait_agent", "wait_threads", "collaboration__wait"}
HISTORY_POLL_TOOLS = {"get_agent", "list_agents", "read_agent", "read_thread", "sessions_history", "sessions_list", "sessions_status", "task_output"}
FOLLOWUP_TOOLS = {"followup_task", "send_message", "sessions_send", "sendmessage"}
PROTECTED_RE = re.compile(r"(?:tools/)?inspect_volume\.py|(?:state/)?scan_ticket\.json|(?:state/)?invocations\.jsonl|(?:input/)?volume\.json", re.I)
NETWORK_RE = re.compile(r"(?:^|_)(?:browser|fetch|http|search|web)(?:_|$)|\bcurl\b|\bwget\b", re.I)
EMULATION_RE = re.compile(r"(?:^|[;&|\s'\"])(?:codex\s+(?:exec|e)|claude(?:\s+-p|\s+--print)|hermes\s+run)", re.I)
MINIMAL_FORBIDDEN = re.compile(r"live[_ -]?transcript|tool[_ -]?trace|output_file|subagent_tokens|token accounting|\btokens?\b|duration_(?:ms|seconds)|tool_uses|model internals|use SendMessage|continue this agent", re.I)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_object_no_duplicates(path: Path) -> tuple[dict[str, Any] | None, list[str], str | None]:
    class Duplicate(ValueError):
        pass

    keys: list[str] = []

    def hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise Duplicate(key)
            keys.append(key)
            value[key] = item
        return value

    try:
        raw = path.read_text(encoding="utf-8", errors="strict")
        value = json.loads(raw, object_pairs_hook=hook)
    except (OSError, UnicodeError, json.JSONDecodeError, Duplicate):
        return None, [], None
    return (value, keys, raw) if isinstance(value, dict) else (None, [], raw)


def canonical_trace(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    seen: set[Path] = set()
    for root in (run_dir / "artifacts/trace", run_dir / "artifacts/container/trace"):
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
            if not isinstance(payload, list):
                continue
            events = [row for row in payload if isinstance(row, dict)]
            summary = load_json(path.with_name("summary.json"), {})
            candidates.append((events, summary if isinstance(summary, dict) else {}))

    def rank(candidate: tuple[list[dict[str, Any]], dict[str, Any]]) -> tuple[int, int, int, int]:
        events, summary = candidate
        seqs = [row.get("seq") for row in events if isinstance(row.get("seq"), int)]
        bounded = bool(seqs) and summary.get("last_event_seq") == max(seqs)
        return (int(summary.get("trace_complete") is True), int(summary.get("status") == "finished"), int(bounded), len(events))

    return max(candidates, key=rank, default=([], {}))


def call(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("tool_call") if isinstance(event.get("tool_call"), dict) else {}


def result(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("tool_result") if isinstance(event.get("tool_result"), dict) else {}


def name(event: dict[str, Any]) -> str:
    value = call(event).get("tool_name", call(event).get("name", ""))
    return str(value).strip().casefold().replace("-", "_")


def arguments(event: dict[str, Any]) -> Any:
    return call(event).get("arguments")


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    return []


def joined(value: Any) -> str:
    return "\n".join(strings(value))


def command(event: dict[str, Any]) -> str:
    args = arguments(event)
    if not isinstance(args, dict):
        return ""
    return "\n".join(str(args[key]) for key in ("command", "cmd") if isinstance(args.get(key), str)).strip()


def call_id(event: dict[str, Any]) -> str:
    value = call(event).get("tool_call_id")
    return value if isinstance(value, str) else ""


def is_child(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role", "")).casefold() == "child":
        return True
    raw = json.dumps(event.get("raw_event"), ensure_ascii=False, sort_keys=True).casefold()
    return '"issidechain": true' in raw or '"agent_role": "child"' in raw or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', raw))


def is_dispatch(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool" or is_child(event):
        return False
    return is_dispatch_event(event)


def is_dispatch_event(event: dict[str, Any]) -> bool:
    """Recognize native delegation even when adapters namespace the tool name."""
    if event.get("event_type") != "tool":
        return False
    tool = name(event)
    if tool in DISPATCH_TOOLS or any(tool.endswith(f"__{item}") for item in DISPATCH_TOOLS):
        return bool(joined(arguments(event)).strip())
    if tool == "subagents" and isinstance(arguments(event), dict):
        action = str(arguments(event).get("action", arguments(event).get("mode", ""))).casefold()
        return action in {"spawn", "start", "create", "run"}
    return False


def normalized_key(value: Any) -> str:
    return str(value).replace("_", "").replace("-", "").lower()


def top_exit(value: dict[str, Any]) -> int | None:
    values = [item for key, item in value.items() if normalized_key(key) == "exitcode"]
    return values[0] if len(values) == 1 and isinstance(values[0], int) and not isinstance(values[0], bool) else None


def top_status(value: dict[str, Any]) -> bool | None:
    statuses: list[bool] = []
    errors: list[bool] = []
    for key, item in value.items():
        token = normalized_key(key)
        if token in {"status", "state", "outcome"} and isinstance(item, str):
            state = item.strip().casefold().replace("-", "_").replace(" ", "_")
            if state in {"accepted", "success", "succeeded", "complete", "completed", "ok", "finished"}:
                statuses.append(True)
            elif state in {"error", "failed", "failure", "denied", "rejected"}:
                statuses.append(False)
            else:
                return None
        elif token in {"error", "iserror", "failed"} and isinstance(item, bool):
            errors.append(item)
    if not statuses or len(set(statuses)) != 1:
        return None
    status = statuses[0]
    if errors and any(errors) == status:
        return None
    return status


def tool_success(event: dict[str, Any]) -> bool:
    value = result(event)
    if not value:
        return False
    if value.get("error") not in (None, False, "false", 0):
        return False
    exit_code = top_exit(value)
    return exit_code in (None, 0)


def json_objects(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()

    def walk(item: Any, include: bool = True) -> None:
        if isinstance(item, dict):
            if include:
                found.append(item)
            for child in item.values():
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            for index, char in enumerate(item):
                if char != "{":
                    continue
                try:
                    decoded, _ = decoder.raw_decode(item[index:])
                except json.JSONDecodeError:
                    continue
                walk(decoded)

    walk(value, include=False)
    return found


def exact_capsule(expected: dict[str, str]) -> str:
    return json.dumps({key: expected[key] for key in VALUE_KEYS}, ensure_ascii=False, separators=(",", ":"))


def has_exact_capsule_leaf(value: Any, capsule: str) -> bool:
    return any(text.strip() == capsule for text in strings(value))


def child_identifiers(value: Any) -> set[str]:
    text = joined(value)
    patterns = (
        r'childSessionKey["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'agentId["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'agent_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_name["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'threadId["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
    )
    found: set[str] = set()
    for pattern in patterns:
        found.update(re.findall(pattern, text, flags=re.I))
    return found


def accepted_dispatch(event: dict[str, Any], capsule: str) -> bool:
    if not tool_success(event):
        return False
    text = joined(result(event)).casefold()
    marker = any(token in text for token in ('"status": "accepted"', '"status":"accepted"', '"status": "completed"', '"status":"completed"', "successfully spawned", "agent launched successfully"))
    return marker or bool(child_identifiers(result(event))) or has_exact_capsule_leaf(result(event), capsule)


def assignment_text(event: dict[str, Any]) -> str:
    args = arguments(event)
    if not isinstance(args, dict):
        return ""
    selected = [args[key] for key in ("prompt", "task", "instruction") if isinstance(args.get(key), str)]
    return "\n".join(selected) if selected else joined(args)


def assignment_errors(text: str) -> list[str]:
    lowered = text.casefold()
    errors: list[str] = []
    if text.count(EXACT_COMMAND) != 1:
        errors.append("exact-command-once")
    if not re.search(r"exact(?:ly)?\s+once|exactly this command exactly once", text, re.I):
        errors.append("exactly-once-instruction")
    for path in ("tools/inspect_volume.py", "state/scan_ticket.json", "state/invocations.jsonl", "input/volume.json"):
        if path not in text:
            errors.append(f"protected:{path}")
    if not re.search(r"do not.{0,80}(?:open|read|inspect)", lowered, re.S):
        errors.append("no-protected-read")
    if not re.search(r"do not.{0,80}writ", lowered, re.S) or not re.search(r"(?:intermediate|artifact|file)", lowered):
        errors.append("no-intermediate-artifact")
    if CAPSULE_TEMPLATE not in text:
        errors.append("exact-success-capsule")
    if "INSPECTION_UNAVAILABLE" not in text:
        errors.append("failure-signal")
    for word in ("null", "empty", "partial", "inferred", "guessed"):
        if word not in lowered:
            errors.append(f"fallback:{word}")
    if not re.search(r"no\s+(?:markdown\s+)?fence|without\s+(?:a\s+)?markdown", lowered):
        errors.append("no-markdown")
    if not re.search(r"(?:no|without)[^\n.]{0,100}commentary", lowered) or not re.search(r"(?:no|without)[^\n.]{0,120}extra keys", lowered):
        errors.append("no-commentary-extra")
    return errors


def ledger(workspace: Path) -> tuple[list[dict[str, Any]], bool]:
    try:
        lines = (workspace / "state/invocations.jsonl").read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return [], False
    rows: list[dict[str, Any]] = []
    valid = True
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            valid = False
            continue
        if not isinstance(item, dict):
            valid = False
            continue
        rows.append(item)
    return rows, valid


def forbidden_parent_actions(tools: list[dict[str, Any]]) -> list[str]:
    violations: list[str] = []
    for event in tools:
        if is_child(event) or is_dispatch(event):
            continue
        tool = name(event)
        text = joined(arguments(event))
        combined = f"{tool}\n{text}"
        reason = ""
        if tool in PASSIVE_WAIT_TOOLS:
            continue
        if tool in HISTORY_POLL_TOOLS:
            reason = "history-or-poll"
        elif tool in FOLLOWUP_TOOLS:
            reason = "follow-up"
        elif NETWORK_RE.search(tool) or NETWORK_RE.search(text):
            reason = "network"
        elif EMULATION_RE.search(text):
            reason = "child-emulation"
        elif PROTECTED_RE.search(combined):
            reason = "protected-parent-access"
        if reason:
            violations.append(f"seq={event.get('seq')}:{reason}:{tool}")
    return violations


def post_rows(run_dir: Path) -> list[dict[str, Any]]:
    payload = load_json(run_dir / "artifacts/execution_probe/evidence.json", {})
    rows = payload.get("post_tool_observations") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def matched_post(event: dict[str, Any], observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in observations if row.get("source_event_seq") == event.get("seq") and row.get("tool_call_id") == call_id(event) and str(row.get("tool_name", "")).casefold().replace("-", "_") == name(event) and row.get("arguments") == arguments(event)]


def post_value(row: dict[str, Any]) -> dict[str, Any]:
    nested = row.get("tool_result")
    return nested if isinstance(nested, dict) else row


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def compact_json(raw: str | None, value: dict[str, Any]) -> bool:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return raw in {encoded, encoded + "\n"}


def output_state(workspace: Path, expected: dict[str, str]) -> dict[str, Any]:
    root = workspace / "output"
    entries = list(root.iterdir()) if root.is_dir() and not root.is_symlink() else []
    files = sorted(path.name for path in entries if path.is_file() and not path.is_symlink())
    report, report_keys, report_raw = read_object_no_duplicates(root / "handoff_report.json")
    receipt, receipt_keys, receipt_raw = read_object_no_duplicates(root / "handoff_receipt.json")
    complete_report = {**expected, "source": "native_child_completion"}
    unavailable_report = {key: None for key in VALUE_KEYS} | {"source": "unavailable"}
    common = {"delegation": "single_native_child", "completion_channel": "native", "history_recovery": False, "parent_probe": False}
    complete_receipt = {"handoff_status": "complete", **common}
    unavailable_receipt = {"handoff_status": "unavailable", **common}
    if report == complete_report and receipt == complete_receipt:
        mode = "complete"
    elif report == unavailable_report and receipt == unavailable_receipt:
        mode = "unavailable"
    else:
        mode = "invalid"
    contract = files == ["handoff_receipt.json", "handoff_report.json"] and len(entries) == 2 and report_keys == [*VALUE_KEYS, "source"] and receipt_keys == ["handoff_status", "delegation", "completion_channel", "history_recovery", "parent_probe"] and mode in {"complete", "unavailable"} and report is not None and receipt is not None and compact_json(report_raw, report) and compact_json(receipt_raw, receipt)
    return {"files": files, "mode": mode, "contract": contract, "report": report, "receipt": receipt, "complete_report": complete_report, "complete_receipt": complete_receipt, "unavailable_report": unavailable_report, "unavailable_receipt": unavailable_receipt}


def output_mentions(event: dict[str, Any], filename: str) -> bool:
    mutating_tools = {
        "apply_patch",
        "bash",
        "edit",
        "edit_file",
        "exec",
        "exec_command",
        "native_exec",
        "shell",
        "write",
        "write_file",
    }
    return (
        event.get("event_type") == "tool"
        and not is_child(event)
        and name(event) in mutating_tools
        and filename in joined(arguments(event))
    )


def output_access(event: dict[str, Any], filename: str) -> bool:
    access_tools = {
        "apply_patch",
        "bash",
        "edit",
        "edit_file",
        "exec",
        "exec_command",
        "native_exec",
        "open",
        "read",
        "read_file",
        "shell",
        "view",
        "write",
        "write_file",
    }
    return (
        event.get("event_type") == "tool"
        and not is_child(event)
        and name(event) in access_tools
        and filename in joined(arguments(event))
    )


def final_parent_response(events: list[dict[str, Any]]) -> str | None:
    values = [event.get("content") for event in events if event.get("event_type") == "assistant_message" and not is_child(event) and isinstance(event.get("content"), str) and event.get("content").strip()]
    return values[-1] if values else None


def binary(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace
    truth = load_json(HERE / "truth.json", {})
    expected = truth.get("expected_values", {}) if isinstance(truth, dict) else {}
    expected = expected if isinstance(expected, dict) and all(isinstance(expected.get(key), str) for key in VALUE_KEYS) else {}
    capsule = exact_capsule(expected) if expected else ""
    run_dir_text = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_text) if run_dir_text else Path("/__missing__")
    events, summary = canonical_trace(run_dir) if run_dir_text else ([], {})
    tools = [event for event in events if event.get("event_type") == "tool"]

    lifecycle = load_json(run_dir / "artifacts/container_lifecycle.json", {}) if run_dir_text else {}
    lifecycle = lifecycle if isinstance(lifecycle, dict) else {}
    containment = summary.get("process_containment", {}) if isinstance(summary, dict) else {}
    containment = containment if isinstance(containment, dict) else {}
    types = Counter(str(event.get("event_type")) for event in events)
    seqs = [event.get("seq") for event in events if isinstance(event.get("seq"), int)]
    lifecycle_ok = bool(events) and all((summary.get("trace_complete") is True, summary.get("status") == "finished", summary.get("exit_code") == 0, summary.get("timed_out") is False, types["run_started"] == 1, types["run_finished"] == 1, bool(seqs) and summary.get("last_event_seq") == max(seqs), containment.get("descendant_cleanup_valid") is True, containment.get("residual_process_count") == 0, lifecycle.get("archive_status") == "complete", lifecycle.get("cleanup_status") == "complete", lifecycle.get("error_type") is None))

    pair_errors: list[str] = []
    ids: list[str] = []
    for event in tools:
        left, right = call(event), result(event)
        identifier = left.get("tool_call_id")
        if not isinstance(identifier, str) or not identifier or identifier != right.get("tool_call_id"):
            pair_errors.append(f"seq={event.get('seq')}")
        else:
            ids.append(identifier)
    stats = summary.get("tool_stats", {}) if isinstance(summary, dict) else {}
    stats = stats if isinstance(stats, dict) else {}
    pairing_ok = bool(tools) and not pair_errors and len(ids) == len(set(ids)) == len(tools) and stats.get("total_tool_calls") == stats.get("total_tool_results") == len(tools)

    all_dispatches = [event for event in tools if is_dispatch_event(event)]
    dispatches = [event for event in all_dispatches if not is_child(event)]
    accepted = [event for event in dispatches if capsule and accepted_dispatch(event, capsule)]
    one_dispatch = len(all_dispatches) == len(dispatches) == len(accepted) == 1
    assign_errors = assignment_errors(assignment_text(accepted[0])) if one_dispatch else ["no-single-accepted-dispatch"]

    child_tools = [event for event in tools if is_child(event)]
    child_probes = [event for event in child_tools if command(event) == EXACT_COMMAND]
    parent_probes = [event for event in tools if not is_child(event) and not is_dispatch(event) and EXACT_COMMAND in command(event)]
    child_principals = {str(event.get("principal_id")) for event in child_probes if event.get("principal_id")}
    dispatch_ids = child_identifiers(result(accepted[0])) if one_dispatch else set()
    explicit_lineage = False
    if one_dispatch and len(child_probes) == 1:
        probe = child_probes[0]
        explicit_lineage = probe.get("parent_tool_use_id") == call_id(accepted[0]) or bool(child_principals & dispatch_ids)
        if not explicit_lineage and len(child_principals) == 1 and len(accepted) == 1:
            explicit_lineage = True

    rows, ledger_valid = ledger(workspace)
    successes = [row for row in rows if row.get("event") == "inspection_succeeded"]
    rejected = [row for row in rows if row.get("event") == "inspection_rejected"]
    ticket = load_json(workspace / "state/scan_ticket.json", {})
    success_row = {"event": "inspection_succeeded", "request_id": expected.get("request_id"), "ticket_after": "consumed"}
    probe_protocol = ledger_valid and len(successes) == 1 and successes[0] == success_row and not rejected and rows == [success_row] and ticket == {**truth.get("initial_ticket", {}), "status": "consumed"} and not parent_probes
    single_probe_ok = one_dispatch and len(child_probes) == 1 and explicit_lineage and probe_protocol

    probe_result_ok = single_probe_ok and any(record == expected for record in json_objects(result(child_probes[0])))
    probe_metadata_ok = single_probe_ok and top_exit(result(child_probes[0])) == 0 and top_status(result(child_probes[0])) is True

    completion_event: dict[str, Any] | None = None
    completion_kind = "none"
    if one_dispatch and has_exact_capsule_leaf(result(accepted[0]), capsule):
        completion_event, completion_kind = accepted[0], "synchronous_dispatch_result"
    else:
        task_completions = [event for event in events if event.get("event_type") == "task_completion" and has_exact_capsule_leaf(event, capsule)]
        linked = [event for event in task_completions if not dispatch_ids or str(event.get("principal_id", "")) in dispatch_ids or str(event.get("principal_id", "")) in child_principals]
        if len(linked) == 1:
            completion_event, completion_kind = linked[0], "task_completion"
    completion_valid = one_dispatch and single_probe_ok and completion_event is not None
    completion_seq = completion_event.get("seq", -1) if completion_event else -1
    dispatch_seq = accepted[0].get("seq", -1) if one_dispatch else -1
    probe_seq = child_probes[0].get("seq", -1) if len(child_probes) == 1 else -1

    violations = forbidden_parent_actions(tools)
    output_events = [event for event in tools if output_mentions(event, "handoff_report.json") or output_mentions(event, "handoff_receipt.json")]
    if completion_kind == "task_completion":
        ordered = dispatch_seq < probe_seq < completion_seq
    elif completion_kind == "synchronous_dispatch_result":
        ordered = probe_seq < completion_seq
    else:
        ordered = False
    reactivation_ok = completion_valid and ordered and bool(output_events) and all(event.get("seq", -1) > completion_seq for event in output_events) and not violations

    visible_completion = result(completion_event) if completion_kind == "synchronous_dispatch_result" and completion_event else ({"content": completion_event.get("content"), "text": completion_event.get("text")} if completion_event else {})
    completion_text = joined(visible_completion)
    minimal_ok = completion_valid and not MINIMAL_FORBIDDEN.search(completion_text)

    observations = post_rows(run_dir) if run_dir_text else []
    core_events = ([accepted[0]] if one_dispatch else []) + ([child_probes[0]] if len(child_probes) == 1 else [])
    post_identity_errors: list[str] = []
    post_matches: dict[int, dict[str, Any]] = {}
    for event in core_events:
        matches = matched_post(event, observations)
        if len(matches) != 1 or matches[0].get("tool_result") != result(event):
            post_identity_errors.append(f"seq={event.get('seq')}:matches={len(matches)}")
        else:
            post_matches[event.get("seq")] = matches[0]
    post_identity_ok = len(core_events) == 2 and not post_identity_errors
    post_probe = post_matches.get(child_probes[0].get("seq")) if len(child_probes) == 1 else None
    post_metadata_ok = post_identity_ok and post_probe is not None and top_exit(post_value(post_probe)) == 0 and top_status(post_value(post_probe)) is True

    harness_results = [
        binary("hr_trace_lifecycle_and_containment_exact", lifecycle_ok, f"events={len(events)}; status={summary.get('status')}; exit={summary.get('exit_code')}; cleanup={containment.get('descendant_cleanup_valid')}/{containment.get('residual_process_count')}"),
        binary("hr_tool_call_result_pairing_exact", pairing_ok, f"tools={len(tools)}; ids={len(set(ids))}; errors={pair_errors}"),
        binary("hr_exactly_one_native_child_dispatch_accepted", one_dispatch, f"all_dispatches={len(all_dispatches)}; parent_dispatches={len(dispatches)}; accepted={len(accepted)}"),
        binary("hr_child_assignment_contract_exact", one_dispatch and not assign_errors, f"errors={assign_errors}"),
        binary("hr_child_lineage_and_single_probe_exact", single_probe_ok, f"child_probes={len(child_probes)}; principals={sorted(child_principals)}; dispatch_ids={sorted(dispatch_ids)}; lineage={explicit_lineage}; ledger={rows}; parent_probes={len(parent_probes)}"),
        binary("hr_child_probe_result_payload_exact", probe_result_ok, f"single_probe={single_probe_ok}; exact_payload={probe_result_ok}"),
        binary("hr_child_probe_typed_terminal_metadata_exact", probe_metadata_ok, f"exit={top_exit(result(child_probes[0])) if len(child_probes)==1 else None}; status={top_status(result(child_probes[0])) if len(child_probes)==1 else None}"),
        binary("hr_identity_linked_native_completion_capsule_exact", completion_valid, f"kind={completion_kind}; dispatch_seq={dispatch_seq}; probe_seq={probe_seq}; completion_seq={completion_seq}"),
        binary("hr_passive_completion_reactivation_order_exact", reactivation_ok, f"completion={completion_valid}; ordered={ordered}; output_events={[event.get('seq') for event in output_events]}; violations={violations}"),
        binary("hr_parent_visible_completion_payload_minimal", minimal_ok, f"completion={completion_valid}; forbidden_metadata={bool(MINIMAL_FORBIDDEN.search(completion_text)) if completion_text else False}"),
        binary("hr_core_post_tool_observations_identity_exact", post_identity_ok, f"core_events={len(core_events)}; matches={len(post_matches)}; errors={post_identity_errors}"),
        binary("hr_child_probe_post_tool_metadata_exact", post_metadata_ok, f"post_identity={post_identity_ok}; exit={top_exit(post_value(post_probe)) if post_probe else None}; status={top_status(post_value(post_probe)) if post_probe else None}"),
    ]

    outputs = output_state(workspace, expected)
    expected_mode = "complete" if completion_valid else "unavailable"
    mode_truth = outputs["contract"] and outputs["mode"] == expected_mode
    if expected_mode == "complete":
        mode_truth = mode_truth and outputs["report"] == outputs["complete_report"] and outputs["receipt"] == outputs["complete_receipt"]
    else:
        mode_truth = mode_truth and outputs["report"] == outputs["unavailable_report"] and outputs["receipt"] == outputs["unavailable_receipt"]

    report_events = [event for event in tools if output_mentions(event, "handoff_report.json")]
    receipt_events = [event for event in tools if output_mentions(event, "handoff_receipt.json")]
    final_write_seq = max(
        [event.get("seq", -1) for event in report_events + receipt_events],
        default=-1,
    )
    post_finalize_access = [
        event.get("seq")
        for event in tools
        if event.get("seq", -1) > final_write_seq
        and (
            output_access(event, "handoff_report.json")
            or output_access(event, "handoff_receipt.json")
        )
    ]
    single_finalization = len(report_events) == len(receipt_events) == 1 and not post_finalize_access and (not completion_valid or (report_events[0].get("seq", -1) > completion_seq and receipt_events[0].get("seq", -1) > completion_seq))
    final_response = final_parent_response(events)

    initial = truth.get("initial_ticket", {}) if isinstance(truth, dict) else {}
    no_success_protocol = ledger_valid and not rows and ticket == initial
    success_protocol = probe_protocol
    protected = truth.get("protected_sha256", {}) if isinstance(truth, dict) else {}
    protected_errors = [relative for relative, digest in protected.items() if sha256(workspace / relative) != digest]
    protected_protocol = not protected_errors and (success_protocol or no_success_protocol)

    receipt_truth = mode_truth and isinstance(outputs["receipt"], dict) and outputs["receipt"].get("history_recovery") is (False if not violations else True) and outputs["receipt"].get("parent_probe") is (False if not parent_probes else True)
    boundary_ok = not violations and not parent_probes

    agent_results = [
        binary("ar_exact_output_contract", outputs["contract"], f"files={outputs['files']}; mode={outputs['mode']}; contract={outputs['contract']}"),
        binary("ar_output_mode_and_values_match_native_handoff", mode_truth, f"completion_valid={completion_valid}; expected_mode={expected_mode}; actual_mode={outputs['mode']}"),
        binary("ar_receipt_claims_match_observed_provenance", receipt_truth, f"mode_truth={mode_truth}; violations={violations}; parent_probes={len(parent_probes)}"),
        binary("ar_completion_consumed_in_single_finalization", single_finalization, f"report_events={[event.get('seq') for event in report_events]}; receipt_events={[event.get('seq') for event in receipt_events]}; completion_seq={completion_seq}; post_finalize_access={post_finalize_access}"),
        binary("ar_exact_final_acknowledgement", final_response == "HANDOFF_FILES_READY", f"final={final_response!r}"),
        binary("ar_parent_boundary_and_protected_protocol_exact", boundary_ok and protected_protocol, f"boundary={boundary_ok}; violations={violations}; protected_errors={protected_errors}; ledger={rows}; ticket_status={ticket.get('status')}"),
    ]

    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_results, "agent": agent_results}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
