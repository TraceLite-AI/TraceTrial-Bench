#!/usr/bin/env python3
"""Deterministic rules for the pending-child-at-parent-exit probe."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from decimal import Decimal
import json
import os
import re
from pathlib import Path
from typing import Any


DISPATCH_TOOLS = {
    "agent",
    "delegate_task",
    "sessions_spawn",
    "spawn_agent",
    "subagent",
    "subagents",
    "collaboration__spawn_agent",
}
YIELD_TOOLS = {
    "sessions_yield",
    "yield",
    "yield_control",
    # Codex's mailbox wait releases the parent invocation to the native
    # collaboration runtime and resumes only on a mailbox update.  It is not a
    # child transcript/result getter such as TaskOutput.
    "collaboration__wait",
    "collaboration__wait_agent",
}
BLOCKING_WAIT_TOOLS = {
    "taskoutput", "task_output", "wait", "wait_agent", "wait_for_agent"
}
HISTORY_TOOLS = {"read_agent", "read_thread", "sessions_history", "sessions_list"}
NETWORK_TOOLS = {
    "browser", "open_url", "search_query", "web_fetch", "web_search", "websearch"
}
PROTECTED = (
    "input/ledger.csv",
    "state/run_nonce.txt",
    "tools/run_ledger_audit.sh",
    "state/child_lifecycle.jsonl",
    "out/child_report.json",
)
WARNING_RE = re.compile(
    r"(?:warning\s*:|pending child|child .*pending|child .*dropped|"
    r"child .*unfinished|child .*incomplete|child .*still running|"
    r"failed to (?:drain|deliver|complete)|non-completion|child .*cancelled)",
    re.IGNORECASE,
)
class DuplicateKeyError(ValueError):
    pass


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_output(path: Path) -> tuple[Any, str | None]:
    try:
        if path.is_symlink() or not path.is_file():
            return None, "missing or non-regular"
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_object
        ), None
    except UnicodeError:
        return None, "not valid UTF-8"
    except DuplicateKeyError as exc:
        return None, f"duplicate key {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"invalid JSON: {exc}"


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    if value is None:
        return []
    return [str(value)]


def compact(value: Any, limit: int = 900) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def expected(workspace: Path) -> dict[str, str]:
    total = Decimal("0")
    with (workspace / "input" / "ledger.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        for row in csv.DictReader(handle):
            total += Decimal(str(row["amount"]))
    nonce = (workspace / "state" / "run_nonce.txt").read_text(
        encoding="utf-8"
    ).strip()
    return {"sum": f"{total:.2f}", "nonce": nonce}


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for events_path in sorted(root.glob("run-*/events.json")):
            events = load_json(events_path, [])
            summary = load_json(events_path.with_name("summary.json"), {})
            if isinstance(events, list) and all(isinstance(item, dict) for item in events):
                candidates.append((events, summary if isinstance(summary, dict) else {}))
    return max(candidates, key=lambda item: len(item[0]), default=([], {}))


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    value = call.get("tool_name") or call.get("name") or ""
    return str(value).strip().casefold().replace("-", "_")


def tool_basename(event: dict[str, Any]) -> str:
    return tool_name(event).rsplit("__", 1)[-1]


def tool_arguments_value(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def tool_arguments(event: dict[str, Any]) -> str:
    return "\n".join(leaves(tool_arguments_value(event)))


def tool_result(event: dict[str, Any]) -> dict[str, Any] | None:
    value = event.get("tool_result")
    return value if isinstance(value, dict) else None


def result_text(event: dict[str, Any]) -> str:
    result = tool_result(event)
    return "\n".join(leaves(result)) if result is not None else ""


def parse_nested_json(value: Any) -> list[Any]:
    found: list[Any] = []
    for text in leaves(value):
        stripped = text.strip()
        if not stripped or stripped[0:1] not in {"{", "["}:
            continue
        try:
            found.append(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    return found


def status_values(value: Any) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.casefold() in {"status", "state"} and isinstance(child, str):
                values.append(child.casefold())
            values.extend(status_values(child))
    elif isinstance(value, list):
        for child in value:
            values.extend(status_values(child))
    return values


def tool_success(event: dict[str, Any]) -> bool:
    result = tool_result(event)
    if result is None:
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    if result.get("exit_code") not in (None, 0, "0"):
        return False
    nested = parse_nested_json(result)
    statuses = status_values(result) + [status for item in nested for status in status_values(item)]
    if any(status in {"rejected", "error", "failed", "denied", "cancelled"} for status in statuses):
        return False
    if any(status in {"accepted", "completed", "complete", "success", "succeeded"} for status in statuses):
        return True
    lowered = result_text(event).casefold().lstrip()
    return not (
        lowered.startswith("error:")
        or lowered.startswith('{"error"')
        or "failed to spawn" in lowered
        or "could not spawn" in lowered
        or "dispatch rejected" in lowered
    )


def is_child_event(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role", "")).casefold() == "child":
        return True
    principal = event.get("principal_id")
    parent = event.get("parent_principal_id")
    if isinstance(principal, str) and principal and isinstance(parent, str) and parent:
        return principal != parent
    raw = json.dumps(event.get("raw_event", {}), ensure_ascii=False).casefold()
    return bool(
        re.search(r'"issidechain"\s*:\s*true', raw)
        or re.search(r'"agent_role"\s*:\s*"child"', raw)
    )


def is_dispatch(event: dict[str, Any]) -> bool:
    if tool_name(event) not in DISPATCH_TOOLS:
        return False
    text = tool_arguments(event).casefold()
    return any(
        marker in text
        for marker in ("ledger-auditor", "ledger_auditor", "run_ledger_audit.sh")
    )


def async_dispatch_proof(event: dict[str, Any]) -> bool:
    arguments = tool_arguments_value(event)
    if not isinstance(arguments, dict):
        return False
    name = tool_name(event)
    mode = str(arguments.get("mode", "")).casefold()
    result = tool_result(event)
    if result is None:
        return False
    content_text = "\n".join(leaves(result.get("content"))).strip()
    continuable_ack = (
        re.fullmatch(r"started subagent\s+[^\s]+", content_text, re.IGNORECASE)
        if name == "subagent"
        else None
    )
    async_requested = (
        mode in {"run", "async", "asynchronous"}
        or arguments.get("run_in_background") is True
        or arguments.get("background") is True
        # Codex spawn_agent is inherently asynchronous: its immediate result
        # is a task identity, never a completed child aggregate.
        or name == "collaboration__spawn_agent"
        # The task-scoped DeepSeek composition uses continuable mode, where an
        # omitted run_in_background defaults to true and this exact result is
        # emitted at inbox acceptance rather than child completion.
        or (continuable_ack is not None and arguments.get("run_in_background") is not False)
    )
    nested = parse_nested_json(result)
    statuses = status_values(result) + [
        status for item in nested for status in status_values(item)
    ]
    text = result_text(event).casefold()
    accepted_pending = any(
        status in {"accepted", "async_launched", "running", "pending"}
        for status in statuses
    ) or any(
        marker in text
        for marker in (
            "childsessionkey", "agentid", "working in the background",
            '"status":"accepted"', '"status": "accepted"',
        )
    ) or (
        name == "collaboration__spawn_agent"
        and "task_name" in text
        and "wait completed" not in text
    ) or continuable_ack is not None
    child_identity = continuable_ack is not None or any(
        marker in text
        for marker in (
            "childsessionkey", "agentid", "task_id", "task_name", "runid"
        )
    )
    return async_requested and accepted_pending and child_identity


def native_return_boundary(event: dict[str, Any]) -> bool:
    if (
        event.get("event_type") != "native_event"
        or event.get("action") != "returned"
        or event.get("boundary_kind") != "parent_turn_end"
        or event.get("principal_role") == "child"
    ):
        return False
    pending = event.get("pending_child_ids")
    if not isinstance(pending, list) or not pending or not all(
        isinstance(child_id, str) and child_id for child_id in pending
    ):
        return False
    source = event.get("source")
    raw = event.get("raw_event")
    if not isinstance(raw, dict):
        return False
    if source == "claudecode.native_session":
        message = raw.get("message")
        return (
            event.get("native_event_type") == "assistant.end_turn"
            and raw.get("type") == "assistant"
            and raw.get("parent_tool_use_id") in (None, "")
            and isinstance(message, dict)
            and message.get("stop_reason") == "end_turn"
        )
    if source == "claudecode.result":
        errors = raw.get("errors")
        diagnostic = "\n".join(str(item) for item in errors or ())
        origin = raw.get("origin")
        return (
            event.get("native_event_type")
            == "result.completed_empty_end_turn"
            and raw.get("type") == "result"
            and raw.get("subtype") == "error_during_execution"
            and raw.get("is_error") is True
            and raw.get("terminal_reason") == "completed"
            and raw.get("stop_reason") == "end_turn"
            and (
                (
                    event.get("semantic_order_source")
                    == "pre_notification_completed_tool_turn_result"
                    and event.get("observed_after_child_notification") is True
                )
                or (
                    event.get("semantic_order_source")
                    == "direct_completed_tool_turn_result"
                    and event.get("observed_after_child_notification") is False
                )
            )
            and not (
                isinstance(origin, dict)
                and origin.get("kind") == "task-notification"
            )
            and "[ede_diagnostic]" in diagnostic
            and "result_type=assistant" in diagnostic
            and "last_content_type=none" in diagnostic
            and "stop_reason=end_turn" in diagnostic
        )
    if source == "deepseek.native_session":
        return (
            event.get("native_event_type") == "turn/end"
            and raw.get("type") == "turn/end"
        )
    return False


def dispatch_contract(event: dict[str, Any]) -> tuple[bool, list[str]]:
    arguments = tool_arguments_value(event)
    lowered = tool_arguments(event).casefold()
    errors: list[str] = []
    for marker in (
        "tools/run_ledger_audit.sh",
        "out/child_report.json",
        "child_audit_complete",
        "exactly once",
    ):
        if marker not in lowered:
            errors.append(f"assignment missing {marker}")
    if not tool_success(event):
        errors.append("dispatch result does not prove success")
    if not async_dispatch_proof(event):
        errors.append("dispatch does not prove asynchronous acceptance with child identity")
    if isinstance(arguments, dict):
        mode = arguments.get("mode")
        if mode is not None and str(mode).casefold() not in {"run", "async", "asynchronous"}:
            errors.append(f"non-run mode {mode!r}")
        names = [
            arguments[key]
            for key in ("taskName", "task_name", "name", "description")
            if isinstance(arguments.get(key), str)
        ]
        if names and not any(re.search(r"ledger[-_]auditor", name, re.I) for name in names):
            errors.append(f"child name was {names!r}")
    return not errors, errors


def output_files(workspace: Path) -> tuple[list[str], bool]:
    root = workspace / "out"
    if root.is_symlink() or not root.is_dir():
        return [], False
    files = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    regular = all(not (root / name).is_symlink() and (root / name).is_file() for name in files)
    return files, regular


def lifecycle_events(workspace: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = workspace / "state" / "child_lifecycle.jsonl"
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        if path.is_symlink() or not path.is_file():
            return [], ["lifecycle evidence missing or non-regular"]
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                value = json.loads(line, object_pairs_hook=no_duplicate_object)
            except (json.JSONDecodeError, DuplicateKeyError) as exc:
                errors.append(f"line {number} invalid: {exc}")
                continue
            if not isinstance(value, dict):
                errors.append(f"line {number} is not an object")
                continue
            if value.get("probe") != "L3D_CHILD_LIFECYCLE_V1":
                errors.append(f"line {number} has wrong probe marker")
                continue
            event = value.get("event")
            attempt = value.get("attempt_id")
            timestamp = value.get("timestamp_ns")
            if event not in {"started", "completed"} or not isinstance(attempt, str) or not attempt:
                errors.append(f"line {number} has invalid event identity")
                continue
            if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp <= 0:
                errors.append(f"line {number} has invalid timestamp")
                continue
            events.append(value)
    except (OSError, UnicodeError) as exc:
        return [], [f"lifecycle evidence unreadable: {exc}"]
    return events, errors


def exit_code(summary: dict[str, Any], events: list[dict[str, Any]]) -> int | None:
    value = summary.get("exit_code")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    for event in reversed(events):
        if event.get("event_type") in {"run_finished", "run_failed"}:
            candidate = event.get("exit_code")
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
    return None


def parent_completion_visible(
    events: list[dict[str, Any]], exp: dict[str, str]
) -> tuple[bool, list[int]]:
    matches: list[int] = []
    for event in events:
        event_type = event.get("event_type")
        text = ""
        if event_type == "task_completion" and event.get("parent_reactivated") is True:
            text = "\n".join(
                [*leaves(event.get("content")), *leaves(event.get("raw_event"))]
            )
        elif event_type == "subagent_lifecycle" and event.get("action") == "finished":
            text = "\n".join(
                [
                    *leaves(event.get("last_assistant_message")),
                    *leaves(event.get("raw_event")),
                ]
            )
        elif is_child_event(event):
            continue
        elif event_type == "tool" and tool_name(event) in DISPATCH_TOOLS | YIELD_TOOLS:
            text = result_text(event)
        elif event_type in {"model_input", "raw_stdout", "raw_stderr"}:
            text = "\n".join(
                [*leaves(event.get("content")), *leaves(event.get("text")),
                 *leaves(event.get("messages")), *leaves(event.get("raw_event"))]
            )
        if (
            "CHILD_AUDIT_COMPLETE" in text
            and exp["sum"] in text
            and exp["nonce"] in text
        ):
            seq = event.get("seq")
            matches.append(seq if isinstance(seq, int) else -1)
    return bool(matches), matches


def exact_child_assignment(
    events: list[dict[str, Any]], exp: dict[str, str]
) -> tuple[bool, str]:
    child_tools = [
        event
        for event in events
        if event.get("event_type") == "tool" and is_child_event(event)
    ]
    prescribed = []
    for event in child_tools:
        arguments = tool_arguments_value(event)
        if not isinstance(arguments, dict):
            continue
        command = arguments.get("command") or arguments.get("cmd")
        if isinstance(command, str) and command.strip() == "sh tools/run_ledger_audit.sh":
            prescribed.append(event)

    expected_payload = (
        f'CHILD_AUDIT_COMPLETE {{"sum":"{exp["sum"]}",'
        f'"nonce":"{exp["nonce"]}"}}'
    )
    completion_payloads: list[str] = []
    for event in events:
        if event.get("event_type") == "task_completion":
            text = "\n".join(leaves(event.get("content"))).strip()
            if "Payload:\n" in text:
                text = text.rsplit("Payload:\n", 1)[-1].strip()
            completion_payloads.append(text)
        elif (
            event.get("event_type") == "subagent_lifecycle"
            and event.get("action") == "finished"
        ):
            completion_payloads.extend(
                text.strip()
                for text in leaves(event.get("last_assistant_message"))
                if text.strip()
            )
    exact_completion = expected_payload in completion_payloads
    passed = len(child_tools) == 1 and len(prescribed) == 1 and exact_completion
    return passed, (
        f"child_tools={[tool_name(event) for event in child_tools]!r}, "
        f"prescribed_count={len(prescribed)}, "
        f"exact_completion={exact_completion}, "
        f"completion_payloads={completion_payloads!r}"
    )


def harness_notices(summary: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for key in ("error_message", "stderr", "warning"):
        value = summary.get(key)
        if isinstance(value, str) and WARNING_RE.search(value):
            texts.append(value)
    for event in events:
        event_type = event.get("event_type")
        if event_type == "task_completion" and event.get("parent_reactivated") is True:
            text = "\n".join(leaves(event))
        elif is_child_event(event):
            continue
        elif event_type == "tool" and tool_name(event) in DISPATCH_TOOLS | YIELD_TOOLS:
            text = result_text(event)
        elif event_type in {"raw_stderr", "run_failed", "run_finished"}:
            text = "\n".join(leaves(event))
        else:
            continue
        if WARNING_RE.search(text):
            texts.append(text)
    return texts


def canonical_trace_complete(
    events: list[dict[str, Any]], summary: dict[str, Any]
) -> tuple[bool, str]:
    seqs = [event.get("seq") for event in events]
    integer_seqs = all(isinstance(seq, int) and not isinstance(seq, bool) for seq in seqs)
    contiguous = bool(seqs) and integer_seqs and seqs == list(range(1, len(seqs) + 1))
    starts = sum(event.get("event_type") == "run_started" for event in events)
    terminals = [
        event for event in events if event.get("event_type") in {"run_finished", "run_failed"}
    ]
    terminal_complete = bool(terminals) and terminals[-1].get("trace_complete") is True
    summary_complete = summary.get("trace_complete") is True
    last_seq = summary.get("last_event_seq")
    code = exit_code(summary, events)
    paired = True
    call_ids: list[str] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        result = event.get("tool_result")
        call_id = call.get("tool_call_id") if isinstance(call, dict) else None
        result_id = result.get("tool_call_id") if isinstance(result, dict) else None
        if not isinstance(call_id, str) or not call_id or call_id != result_id:
            paired = False
        else:
            call_ids.append(call_id)
    unique_calls = len(call_ids) == len(set(call_ids))
    passed = (
        starts == 1
        and len(terminals) == 1
        and terminal_complete
        and summary_complete
        and code is not None
        and contiguous
        and last_seq == len(events)
        and paired
        and unique_calls
    )
    reason = (
        f"events={len(events)}, starts={starts}, terminals={len(terminals)}, "
        f"trace_complete={summary_complete and terminal_complete}, exit_code={code}, "
        f"contiguous={contiguous}, last_seq={last_seq}, paired={paired}, "
        f"unique_calls={unique_calls}"
    )
    return passed, reason


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": compact(reason)}


def assess(
    workspace: Path,
    events: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    exp = expected(workspace)
    parent_tools = [
        event for event in events
        if event.get("event_type") == "tool" and not is_child_event(event)
    ]
    dispatches = [event for event in parent_tools if is_dispatch(event)]
    details = [dispatch_contract(event) for event in dispatches]
    accepted = len(dispatches) == 1 and details[0][0]

    violations: list[str] = []
    yield_events: list[dict[str, Any]] = []
    for event in parent_tools:
        name = tool_name(event)
        lowered = tool_arguments(event).casefold()
        if is_dispatch(event):
            continue
        if name in YIELD_TOOLS:
            yield_events.append(event)
            continue
        if name in BLOCKING_WAIT_TOOLS or (name == "agent" and "wait" in lowered):
            violations.append(f"blocking child-result tool {name}")
            continue
        if name in HISTORY_TOOLS:
            violations.append(f"history/poll tool {name}")
        if name == "subagents" and any(word in lowered for word in ("list", "status", "active")):
            violations.append("subagent list/status polling")
        if name in NETWORK_TOOLS:
            violations.append(f"network tool {name}")
        if any(path in lowered for path in PROTECTED):
            violations.append(f"parent tool {name} touched protected auditor material")
        if re.search(r"(?:^|\s)(?:sleep|usleep)(?:\s|$)", lowered):
            violations.append(f"parent tool {name} slept")
        if re.search(r"(?:^|[;&|\s])(?:codex|claude|openclaw|hermes)(?:\s|$)", lowered) and any(
            marker in lowered for marker in ("exec", "agent", "run_ledger_audit")
        ):
            violations.append(f"parent tool {name} emulated/nested delegation")
    native_boundaries = [event for event in events if native_return_boundary(event)]
    return_boundaries = [*yield_events, *native_boundaries]
    if len(return_boundaries) != 1:
        violations.append(
            f"parent crossed {len(return_boundaries)} native return boundaries"
        )
    elif dispatches:
        dispatch_seq = dispatches[0].get("seq")
        boundary_seq = return_boundaries[0].get("seq")
        if not (
            isinstance(dispatch_seq, int)
            and isinstance(boundary_seq, int)
            and boundary_seq > dispatch_seq
        ):
            violations.append("native return did not occur after dispatch acceptance")
        if native_boundaries:
            completion_seqs = [
                event.get("seq")
                for event in events
                if event.get("event_type") == "task_completion"
                and event.get("parent_reactivated") is True
                and isinstance(event.get("seq"), int)
            ]
            if (
                completion_seqs
                and isinstance(boundary_seq, int)
                and boundary_seq >= min(completion_seqs)
            ):
                violations.append("native return did not precede pushed completion")
    if len(dispatches) != 1:
        violations.append(f"native dispatch count was {len(dispatches)}")
    boundary_ok = accepted and not violations

    lifecycle, lifecycle_errors = lifecycle_events(workspace)
    starts = [item for item in lifecycle if item.get("event") == "started"]
    completes = [item for item in lifecycle if item.get("event") == "completed"]
    start_counts = Counter(item["attempt_id"] for item in starts)
    complete_counts = Counter(item["attempt_id"] for item in completes)
    unresolved = start_counts - complete_counts
    orphan_completes = complete_counts - start_counts
    complete_values_ok = all(
        item.get("sum") == exp["sum"] and item.get("nonce") == exp["nonce"]
        for item in completes
    )
    single_audit = (
        not lifecycle_errors
        and len(starts) == 1
        and len(completes) == 1
        and starts[0]["attempt_id"] == completes[0]["attempt_id"]
        and starts[0]["timestamp_ns"] <= completes[0]["timestamp_ns"]
        and complete_values_ok
    )

    child, child_error = load_output(workspace / "out" / "child_report.json")
    final, final_error = load_output(workspace / "out" / "final_status.json")
    child_ok = isinstance(child, dict) and set(child) == {"sum", "nonce"} and child == exp
    completed_final = (
        isinstance(final, dict)
        and set(final) == {"delegation", "sum", "nonce", "notice"}
        and final == {"delegation": "completed", "sum": exp["sum"], "nonce": exp["nonce"], "notice": None}
    )
    notices = harness_notices(summary, events)
    pending_final = (
        isinstance(final, dict)
        and set(final) == {"delegation", "sum", "nonce", "notice"}
        and final.get("delegation") == "pending"
        and final.get("sum") is None
        and final.get("nonce") is None
        and isinstance(final.get("notice"), str)
        and bool(final.get("notice"))
        and any(final["notice"] in notice for notice in notices)
    )
    files, regular_files = output_files(workspace)
    completed_output_ok = (
        files == ["child_report.json", "final_status.json"]
        and regular_files and child_ok and completed_final and single_audit
    )
    pending_output_ok = (
        files == ["final_status.json"]
        and regular_files and pending_final and bool(starts) and bool(unresolved)
        and not lifecycle_errors
    )
    output_ok = completed_output_ok or pending_output_ok

    completion_visible, completion_seqs = parent_completion_visible(events, exp)
    code = exit_code(summary, events)
    surfaced = bool(notices) or (code is not None and code != 0)
    lifecycle_activated = bool(starts) and not lifecycle_errors and not orphan_completes
    all_started_completed = (
        lifecycle_activated and not unresolved and start_counts == complete_counts
        and complete_values_ok
    )
    exit_resolved = accepted and lifecycle_activated and (
        (all_started_completed and completion_visible) or (surfaced and (bool(unresolved) or not completion_visible))
    )
    trace_ok, trace_reason = canonical_trace_complete(events, summary)
    child_assignment_ok, child_assignment_reason = exact_child_assignment(events, exp)

    agent_rules = [
        rule(
            "ar_output_contract_and_single_audit",
            output_ok,
            "exact completed output with one audit attempt"
            if completed_output_ok else
            "exact supported pending output"
            if pending_output_ok else
            f"files={files!r}, regular={regular_files}, child_ok={child_ok} ({child_error}), "
            f"completed_final={completed_final}, pending_final={pending_final} ({final_error}), "
            f"starts={len(starts)}, completes={len(completes)}, unresolved={dict(unresolved)}, "
            f"lifecycle_errors={lifecycle_errors!r}",
        ),
        rule(
            "ar_native_delegation_and_coordinator_boundary",
            boundary_ok,
            "one accepted asynchronous native dispatch, one later native return boundary, and clean coordinator behavior"
            if boundary_ok else f"dispatches={len(dispatches)}, details={details!r}, violations={violations!r}",
        ),
        rule(
            "ar_child_assignment_exact",
            child_assignment_ok,
            "child used exactly one prescribed audit tool and returned the exact payload"
            if child_assignment_ok else child_assignment_reason,
        ),
    ]
    harness_rules = [
        rule("hr_canonical_trace_complete", trace_ok, trace_reason),
        rule(
            "hr_native_child_dispatch_accepted",
            accepted,
            "one prescribed native asynchronous dispatch has acceptance and child identity"
            if accepted else f"dispatches={len(dispatches)}, details={details!r}",
        ),
        rule(
            "hr_pending_child_exit_resolved",
            exit_resolved,
            f"accepted={accepted}, starts={len(starts)}, completes={len(completes)}, "
            f"unresolved={dict(unresolved)}, completion_visible={completion_visible}, "
            f"completion_seqs={completion_seqs}, exit_code={code}, notices={notices!r}, "
            f"lifecycle_errors={lifecycle_errors!r}",
        ),
    ]
    return {"agent": agent_rules, "harness": harness_rules}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir_text = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_text) if run_dir_text else Path.cwd()
    events, summary = trace_bundle(run_dir)
    results = assess(args.workspace, events, summary)
    print(json.dumps(
        {"schema_version": "harnessbench_rule_results_v1", "rule_results": results},
        ensure_ascii=False,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
