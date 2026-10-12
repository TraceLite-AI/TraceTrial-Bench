#!/usr/bin/env python3
"""Deterministic rules for singleton, task-thread-scoped native goal tracking."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any


PRIMARY_OBJECTIVE = (
    "Refactor the host probe service incrementally while preserving compatibility and validation."
)
COMPETING_OBJECTIVE = "Prepare an unrelated documentation cleanup."
TURN_HASHES = [
    "bc56f17507ae636af7a0550316469b15f3a0b47d36df7ff5af52d9fbe8071fac",
    "6c29cd5a5be3d49204572470d6992c996659f59d28c1f935fcd2eeab44eb7bd9",
    "a6e7684c7713c4071c082126578db6dd3751b0a9afe01187a479b593183e9c4e",
]
PROTECTED_HASHES = {
    "tests/test_orders.py": "32c5958d97481b9cb741c1cdfbd4c8c12625936ac3338a23717e231029812866",
    "bin/check": "89c1ca4e88c6070b7ffbb261c924a9fdb8d786f7ba20774ae157f3ee2b9c1e69",
}
EXPECTED_CODES = {
    "UNKNOWN": "E-000",
    "MOUNT_UNAVAILABLE": "E-101",
    "SERVICE_UNREACHABLE": "E-102",
    "ENDPOINT_INVALID": "E-103",
}
EXPECTED_SUMMARY = {
    "contract": "dict_result_v1",
    "codes_source": "src/codes.py",
    "legacy_exceptions_preserved": True,
    "tests_passed": 3,
}
MISSING_TOOL_MARKERS = (
    "tool 'create_goal' does not exist",
    'tool "create_goal" does not exist',
    "tool 'get_goal' does not exist",
    'tool "get_goal" does not exist',
    "tool 'update_goal' does not exist",
    'tool "update_goal" does not exist',
    "unknown tool",
    "goal tool is not available",
    "goal tools are not available",
    "native goal tool is not available",
)
REJECTION_MARKERS = (
    "unfinished goal",
    "active goal already",
    "goal already exists",
    "already has a goal",
    "cannot create",
    "singleton",
)
NO_GOAL_MARKERS = (
    "no active goal",
    "no current goal",
    "goal not found",
    "no goal for this thread",
)
EXPECTED_EXCEPTION_CLASSES = [
    "ProbeError",
    "MountUnavailable",
    "ServiceUnreachable",
    "EndpointInvalid",
]
COMMAND_FIELDS = {"command", "cmd", "script"}
WORKSPACE_HINTS = (
    "/workspace", "src/", "tests/", "bin/check", "output/", "state/",
    "mountpt.py", "service.py", "endpoint.py", "errors.py", "orchestrator.py",
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


def compact(value: Any, limit: int = 1300) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_strict_json(path: Path) -> tuple[Any, str | None]:
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


def sha256(path: Path) -> str:
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


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


def event_turn(event: dict[str, Any]) -> int | None:
    for key in ("conversation_turn", "turn_index"):
        value = event.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def event_seq(event: dict[str, Any]) -> int:
    value = event.get("seq")
    return value if isinstance(value, int) else 10**12


def json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    raw = str(call.get("tool_name") or call.get("name") or "").strip().casefold()
    normalized = raw.replace("-", "_").replace(".", "__")
    for goal_name in ("create_goal", "get_goal", "update_goal"):
        if normalized == goal_name or normalized.endswith("__" + goal_name):
            return goal_name
    return normalized


def arguments_value(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def arguments_text(event: dict[str, Any]) -> str:
    return json_text(arguments_value(event))


def command_text(event: dict[str, Any]) -> str:
    """Return executable command fields without mistaking file content for a command."""
    value = arguments_value(event)
    if not isinstance(value, dict):
        return ""
    return "\n".join(
        str(field_value)
        for field, field_value in value.items()
        if str(field).casefold() in COMMAND_FIELDS and isinstance(field_value, str)
    )


def workspace_access(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool" or tool_name(event) in {
        "create_goal", "get_goal", "update_goal",
    }:
        return False
    text = arguments_text(event).casefold()
    return any(hint in text for hint in WORKSPACE_HINTS)


def successful_check(event: dict[str, Any]) -> bool:
    command = command_text(event).casefold()
    result = result_text(event)
    return all((
        "bin/check" in command,
        result_visible(event),
        bool(re.search(r"TESTS\s+rc=0\b", result, re.IGNORECASE)),
        "3 passed" in result.casefold(),
    ))


def artifact_write(event: dict[str, Any]) -> bool:
    text = arguments_text(event).casefold()
    artifact_names = ("refactor_summary.json", "plan_log.md")
    if not any(name in text for name in artifact_names):
        return False
    name = tool_name(event)
    if any(marker in name for marker in ("write", "edit", "patch")):
        return True
    command = command_text(event).casefold()
    if not command:
        return False
    if "apply_patch" in command:
        return True
    return any(
        re.search(
            rf"(?:cat\s*>|tee|touch|cp|mv)\s*[^\n;]*{re.escape(artifact_name)}",
            command,
        )
        is not None
        for artifact_name in artifact_names
    )


def mutation_target(event: dict[str, Any]) -> str:
    """Best-effort target classification for the read-only audit."""
    name = tool_name(event)
    text = arguments_text(event).casefold()
    command = command_text(event).casefold()
    mutating_name = any(marker in name for marker in ("write", "edit", "patch", "delete"))
    mutating_command = bool(command) and any(
        re.search(pattern, command) is not None
        for pattern in (
            r"(?:cat\s*>|tee|touch|mkdir|rm|mv|cp)\s+[^\n;]*(?:src/|output/)",
            r"(?:sed\s+-i|perl\s+-pi|apply_patch)[^\n]*(?:src/|output/)",
            r"git\s+(?:add|commit)\b",
        )
    )
    if not (mutating_name or mutating_command):
        return ""
    if "src/" in text:
        return "source"
    if "output/" in text or "refactor_summary.json" in text or "plan_log.md" in text:
        return "output"
    return "other"


def result_value(event: dict[str, Any]) -> dict[str, Any] | None:
    result = event.get("tool_result")
    return result if isinstance(result, dict) else None


def result_text(event: dict[str, Any]) -> str:
    return json_text(result_value(event))


def result_visible(event: dict[str, Any]) -> bool:
    result = result_value(event)
    call = event.get("tool_call")
    if not result or not isinstance(call, dict) or not call.get("tool_call_id"):
        return False
    result_id = result.get("tool_call_id")
    return isinstance(result_id, str) and bool(result_id) and result_id == call.get("tool_call_id")


def nested_json_values(value: Any) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        values.append(value)
        for child in value.values():
            values.extend(nested_json_values(child))
    elif isinstance(value, list):
        values.append(value)
        for child in value:
            values.extend(nested_json_values(child))
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in {"{", "["}:
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None
            if parsed is not None:
                values.extend(nested_json_values(parsed))
    return values


def result_objects(event: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in nested_json_values(result_value(event)) if isinstance(item, dict)]


def status_values(event: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for obj in result_objects(event):
        for key, value in obj.items():
            if key.casefold() in {"status", "state"} and isinstance(value, str):
                values.add(value.casefold())
    return values


def goal_records(event: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for obj in result_objects(event):
        goal = obj.get("goal")
        if isinstance(goal, dict):
            records.append(goal)
        if isinstance(obj.get("objective"), str) and isinstance(obj.get("status"), str):
            records.append(obj)
    unique: list[dict[str, Any]] = []
    for record in records:
        if record not in unique:
            unique.append(record)
    return unique


def missing_tool(event: dict[str, Any]) -> bool:
    text = result_text(event).casefold()
    return any(marker in text for marker in MISSING_TOOL_MARKERS)


def goal_state(event: dict[str, Any], objective: str, state: str) -> bool:
    if missing_tool(event):
        return False
    target_state = state.casefold()
    return any(
        record.get("objective") == objective
        and str(record.get("status") or record.get("state") or "").casefold()
        in ({"complete", "completed"} if target_state == "complete" else {target_state, "in_progress", "unfinished"})
        for record in goal_records(event)
    )


def no_goal(event: dict[str, Any]) -> bool:
    if missing_tool(event):
        return False
    for obj in result_objects(event):
        if "goal" in obj and obj.get("goal") is None:
            return True
        if str(obj.get("status", "")).casefold() in {"none", "empty", "not_found", "missing"}:
            return True
    text = result_text(event).casefold()
    return any(marker in text for marker in NO_GOAL_MARKERS)


def rejected_singleton(event: dict[str, Any]) -> bool:
    if missing_tool(event):
        return False
    text = result_text(event).casefold()
    semantic = any(marker in text for marker in REJECTION_MARKERS)
    result = result_value(event) or {}
    error_flag = result.get("error") not in (None, False, "false", 0, "")
    failure_state = bool(status_values(event) & {"error", "rejected", "failed", "denied"})
    return semantic and (
        error_flag or failure_state or "reject" in text or "cannot create" in text
    )


def create_success(event: dict[str, Any], objective: str) -> bool:
    return (
        tool_name(event) == "create_goal"
        and result_visible(event)
        and not missing_tool(event)
        and objective in arguments_text(event)
        and goal_state(event, objective, "active")
        and bool(status_values(event) & {"created", "success", "succeeded", "active"})
    )


def update_success(event: dict[str, Any], objective: str) -> bool:
    return (
        tool_name(event) == "update_goal"
        and result_visible(event)
        and not missing_tool(event)
        and "complete" in arguments_text(event).casefold()
        and goal_state(event, objective, "complete")
    )


def goal_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            event for event in events
            if event.get("event_type") == "tool"
            and tool_name(event) in {"create_goal", "get_goal", "update_goal"}
        ],
        key=event_seq,
    )


def events_in_turn(events: list[dict[str, Any]], turn: int) -> list[dict[str, Any]]:
    return sorted([event for event in events if event_turn(event) == turn], key=event_seq)


def tools_in_turn(events: list[dict[str, Any]], turn: int) -> list[dict[str, Any]]:
    return [event for event in events_in_turn(events, turn) if event.get("event_type") == "tool"]


def turns_exact(events: list[dict[str, Any]]) -> tuple[bool, str]:
    starts = sorted(
        [event for event in events if event.get("event_type") == "conversation_turn_started"],
        key=event_seq,
    )
    indices = [event_turn(event) for event in starts]
    delivered: list[str] = []
    for event in starts:
        value = event.get("user_input")
        if not isinstance(value, str):
            value = event.get("input")
        delivered.append(value if isinstance(value, str) else "")
    hashes = [hashlib.sha256(value.encode("utf-8")).hexdigest() for value in delivered]
    passed = len(starts) == 3 and indices == [1, 2, 3] and hashes == TURN_HASHES
    return passed, f"count={len(starts)}, indices={indices}, hashes={hashes}"


def thread_identities(events: list[dict[str, Any]]) -> dict[int, str]:
    """Use the adapter's canonical per-turn identity, not mixed identity namespaces."""
    identities: dict[int, str] = {}
    finished = sorted(
        [event for event in events if event.get("event_type") == "conversation_turn_finished"],
        key=event_seq,
    )
    for event in finished:
        turn = event_turn(event)
        value = event.get("native_session_id")
        if turn is not None and isinstance(value, str) and value:
            identities[turn] = value
    return identities


def turn_delivery_and_trace(
    events: list[dict[str, Any]], summary: dict[str, Any]
) -> tuple[bool, str]:
    exact, exact_reason = turns_exact(events)
    finished = [event for event in events if event.get("event_type") == "conversation_turn_finished"]
    failures = [event for event in events if event.get("event_type") == "run_failed"]
    terminals = [event for event in events if event.get("event_type") == "conversation_finished"]
    lifecycle = (
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("structured_trace_status") == "available"
        and len(finished) == 3
        and [event_turn(event) for event in sorted(finished, key=event_seq)] == [1, 2, 3]
        and not failures
        and len(terminals) == 1
        and terminals[0].get("status") == "finished"
    )
    passed = exact and lifecycle
    return passed, (
        f"exact={exact} ({exact_reason}), lifecycle={lifecycle}, failures={len(failures)}, "
        f"summary_status={summary.get('status')}, trace_complete={summary.get('trace_complete')}"
    )


def thread_topology(events: list[dict[str, Any]]) -> tuple[bool, str]:
    identities = thread_identities(events)
    complete = set(identities) == {1, 2, 3} and all(identities.values())
    topology = complete and identities[1] == identities[2] and identities[3] != identities[2]
    handoffs = [event for event in events if event.get("event_type") == "context_handoff"]
    handoff_turns = sorted(
        int(event.get("before_turn")) for event in handoffs
        if isinstance(event.get("before_turn"), int)
        and str(event.get("action", "")).casefold() == "replay"
    )
    passed = bool(complete and topology and handoff_turns == [3])
    return passed, (
        f"identities={identities}, complete={complete}, same_then_fresh={topology}, "
        f"replay_boundaries={handoff_turns}"
    )


def real_goal_results(goals: list[dict[str, Any]]) -> tuple[bool, str]:
    counts = {
        name: sum(tool_name(event) == name for event in goals)
        for name in ("create_goal", "get_goal", "update_goal")
    }
    invisible = [event_seq(event) for event in goals if not result_visible(event)]
    unavailable = [event_seq(event) for event in goals if missing_tool(event)]
    successful_creates = [
        event_seq(event) for event in goals
        if create_success(event, PRIMARY_OBJECTIVE)
    ]
    successful_updates = [
        event_seq(event) for event in goals if update_success(event, PRIMARY_OBJECTIVE)
    ]
    unreadable_gets = [
        event_seq(event) for event in goals
        if tool_name(event) == "get_goal"
        and not (result_visible(event) and not missing_tool(event) and (goal_records(event) or no_goal(event)))
    ]
    passed = all((
        counts["create_goal"] >= 2,
        counts["get_goal"] >= 5,
        counts["update_goal"] >= 1,
        not invisible,
        not unavailable,
        not unreadable_gets,
        len(successful_creates) >= 1,
        len(successful_updates) >= 1,
    ))
    return passed, (
        f"counts={counts}, invisible={invisible}, unavailable={unavailable}, "
        f"unreadable_gets={unreadable_gets}, successful_creates={successful_creates}, "
        f"successful_updates={successful_updates}"
    )


def same_thread_singleton(goals: list[dict[str, Any]]) -> tuple[bool, str]:
    by_turn = {turn: [event for event in goals if event_turn(event) == turn] for turn in (1, 2, 3)}
    names = {turn: [tool_name(event) for event in by_turn[turn]] for turn in (1, 2, 3)}
    t1_creates = [event for event in by_turn[1] if tool_name(event) == "create_goal"]
    t1_gets = [event for event in by_turn[1] if tool_name(event) == "get_goal"]
    primary_create = next(
        (event for event in t1_creates if create_success(event, PRIMARY_OBJECTIVE)), None
    )
    read_before = next(
        (
            event for event in t1_gets
            if primary_create is not None
            and event_seq(event) > event_seq(primary_create)
            and goal_state(event, PRIMARY_OBJECTIVE, "active")
        ),
        None,
    )
    competing_create = next(
        (
            event for event in t1_creates
            if read_before is not None
            and event_seq(event) > event_seq(read_before)
            and COMPETING_OBJECTIVE in arguments_text(event)
        ),
        None,
    )
    read_after = next(
        (
            event for event in t1_gets
            if competing_create is not None
            and event_seq(event) > event_seq(competing_create)
            and goal_state(event, PRIMARY_OBJECTIVE, "active")
        ),
        None,
    )
    checks = {
        "primary_created": primary_create is not None,
        "primary_read_before": read_before is not None,
        "competing_objective_exact": competing_create is not None,
        "singleton_rejected": competing_create is not None and rejected_singleton(competing_create),
        "primary_read_after": read_after is not None,
    }
    return all(checks.values()), f"checks={checks}, goal_names={names}"


def active_goal_persists(
    events: list[dict[str, Any]], goals: list[dict[str, Any]]
) -> tuple[bool, str]:
    by_turn = {turn: [event for event in goals if event_turn(event) == turn] for turn in (1, 2, 3)}
    names = {turn: [tool_name(event) for event in by_turn[turn]] for turn in (1, 2, 3)}
    t2_gets = [event for event in by_turn[2] if tool_name(event) == "get_goal"]
    identities = thread_identities(events)
    t1_gets = [event for event in by_turn[1] if tool_name(event) == "get_goal"]
    final_t1_active = bool(t1_gets) and goal_state(t1_gets[-1], PRIMARY_OBJECTIVE, "active")
    first_t2_active = bool(t2_gets) and goal_state(t2_gets[0], PRIMARY_OBJECTIVE, "active")
    checks = {
        "turn1_final_active": final_t1_active,
        "turn2_initial_active": first_t2_active,
        "same_native_thread": identities.get(1) == identities.get(2) and bool(identities.get(1)),
    }
    return all(checks.values()), f"checks={checks}, goal_names={names}"


def goal_completion_atomic(goals: list[dict[str, Any]]) -> tuple[bool, str]:
    turn_goals = [event for event in goals if event_turn(event) == 2]
    names = [tool_name(event) for event in turn_goals]
    updates = [event for event in turn_goals if tool_name(event) == "update_goal"]
    update = updates[0] if len(updates) == 1 else None
    later = [] if update is None else [
        event for event in turn_goals if event_seq(event) > event_seq(update)
    ]
    completed = later[0] if later else None
    checks = {
        "active_observed": any(
            goal_state(event, PRIMARY_OBJECTIVE, "active")
            for event in turn_goals if tool_name(event) == "get_goal"
            and (update is None or event_seq(event) < event_seq(update))
        ),
        "one_update": len(updates) == 1,
        "update_complete": update is not None and update_success(update, PRIMARY_OBJECTIVE),
        "next_goal_op_reads_complete": completed is not None
        and tool_name(completed) == "get_goal" and goal_state(
            completed, PRIMARY_OBJECTIVE, "complete"
        ),
    }
    return all(checks.values()), f"checks={checks}, goal_names={names}"


def fresh_audit_isolated(
    events: list[dict[str, Any]], goals: list[dict[str, Any]]
) -> tuple[bool, str]:
    turn_goals = [event for event in goals if event_turn(event) == 3]
    identities = thread_identities(events)
    checks = {
        "fresh_native_thread": bool(identities.get(3))
        and identities.get(3) != identities.get(2),
        "first_goal_is_get": bool(turn_goals) and tool_name(turn_goals[0]) == "get_goal",
        "no_goal": bool(turn_goals) and result_visible(turn_goals[0])
        and no_goal(turn_goals[0]),
    }
    return all(checks.values()), f"checks={checks}, identities={identities}"


def first_workspace_seq(events: list[dict[str, Any]], turn: int) -> int:
    accesses = [event_seq(event) for event in events_in_turn(events, turn) if workspace_access(event)]
    return min(accesses, default=10**12)


def turn1_goal_protocol(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> tuple[bool, str]:
    turn_goals = [event for event in goals if event_turn(event) == 1]
    names = [tool_name(event) for event in turn_goals]
    workspace_seq = first_workspace_seq(events, 1)
    before_workspace = bool(turn_goals) and max(event_seq(event) for event in turn_goals) < workspace_seq
    semantic_ok, semantic_reason = same_thread_singleton(goals)
    passed = names == ["create_goal", "get_goal", "create_goal", "get_goal"] and before_workspace and semantic_ok
    return passed, (
        f"goal_names={names}, goal_seqs={[event_seq(e) for e in turn_goals]}, "
        f"first_workspace_seq={workspace_seq}, before_workspace={before_workspace}, "
        f"singleton=({semantic_reason})"
    )


def turn2_persisted_protocol(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> tuple[bool, str]:
    turn_goals = [event for event in goals if event_turn(event) == 2]
    names = [tool_name(event) for event in turn_goals]
    initial_get_before_workspace = (
        len(turn_goals) == 3
        and event_seq(turn_goals[0]) < first_workspace_seq(events, 2)
    )
    writes = [event for event in tools_in_turn(events, 2) if artifact_write(event)]
    summary_writes = [
        event for event in writes if "refactor_summary.json" in arguments_text(event).casefold()
    ]
    plan_writes = [event for event in writes if "plan_log.md" in arguments_text(event).casefold()]
    checks = [event for event in tools_in_turn(events, 2) if successful_check(event)]
    update = next((event for event in turn_goals if tool_name(event) == "update_goal"), None)
    output_check_update_order = bool(
        summary_writes and plan_writes and checks and update is not None
        and max(event_seq(event) for event in writes) < event_seq(checks[-1]) < event_seq(update)
    )
    persisted_ok, persisted_reason = active_goal_persists(events, goals)
    completion_ok, completion_reason = goal_completion_atomic(goals)
    passed = all((
        names == ["get_goal", "update_goal", "get_goal"],
        initial_get_before_workspace,
        output_check_update_order,
        persisted_ok,
        completion_ok,
    ))
    return passed, (
        f"goal_names={names}, first_workspace_seq={first_workspace_seq(events, 2)}, "
        f"initial_get_before_workspace={initial_get_before_workspace}, "
        f"summary_write_seqs={[event_seq(e) for e in summary_writes]}, "
        f"plan_write_seqs={[event_seq(e) for e in plan_writes]}, "
        f"successful_check_seqs={[event_seq(e) for e in checks]}, "
        f"update_seq={event_seq(update) if update else None}, order={output_check_update_order}, "
        f"persistence=({persisted_reason}), completion=({completion_reason})"
    )


def turn3_audit_protocol(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> tuple[bool, str]:
    tools = tools_in_turn(events, 3)
    turn_goals = [event for event in goals if event_turn(event) == 3]
    get_first = bool(tools) and tool_name(tools[0]) == "get_goal"
    only_empty_get = (
        len(turn_goals) == 1 and tool_name(turn_goals[0]) == "get_goal" and no_goal(turn_goals[0])
    )
    checks = [event for event in tools if successful_check(event)]
    read_summary = any(
        "refactor_summary.json" in arguments_text(event).casefold()
        and not artifact_write(event) for event in tools
    )
    read_plan = any(
        "plan_log.md" in arguments_text(event).casefold()
        and not artifact_write(event) for event in tools
    )
    forbidden_mutations = [
        (event_seq(event), mutation_target(event)) for event in tools
        if mutation_target(event) in {"source", "output"}
    ]
    passed = all((get_first, only_empty_get, bool(checks), read_summary, read_plan, not forbidden_mutations))
    return passed, (
        f"tool_names={[tool_name(e) for e in tools]}, get_first={get_first}, "
        f"only_empty_get={only_empty_get}, successful_checks={[event_seq(e) for e in checks]}, "
        f"read_summary={read_summary}, read_plan={read_plan}, mutations={forbidden_mutations}"
    )


def parse_python(path: Path) -> tuple[ast.Module | None, str, str | None]:
    try:
        if path.is_symlink() or not path.is_file():
            return None, "", "missing or non-regular"
        source = path.read_text(encoding="utf-8")
        return ast.parse(source), source, None
    except (OSError, UnicodeError, SyntaxError) as exc:
        return None, "", str(exc)


def dict_return_shapes(tree: ast.AST) -> list[set[str] | None]:
    shapes: list[set[str] | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        if not isinstance(node.value, ast.Dict):
            shapes.append(None)
            continue
        keys: set[str] = set()
        valid = True
        for key in node.value.keys:
            if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                valid = False
                break
            keys.add(key.value)
        shapes.append(keys if valid else None)
    return shapes


def dict_fields(node: ast.AST | None) -> dict[str, ast.AST] | None:
    if not isinstance(node, ast.Dict):
        return None
    fields: dict[str, ast.AST] = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or key.value in fields:
            return None
        fields[key.value] = value
    return fields


def operation_returns_exact(tree: ast.AST, code_name: str, expected_count: int) -> tuple[bool, str]:
    returns = [node for node in ast.walk(tree) if isinstance(node, ast.Return)]
    details: list[str] = []
    valid = len(returns) == expected_count
    success_count = 0
    failure_count = 0
    for index, node in enumerate(returns):
        outer = dict_fields(node.value)
        if outer is None or set(outer) != {"ok", "data", "error"}:
            valid = False
            details.append(f"return[{index}]=bad outer shape")
            continue
        ok_value = outer["ok"]
        is_true = isinstance(ok_value, ast.Constant) and ok_value.value is True
        is_false = isinstance(ok_value, ast.Constant) and ok_value.value is False
        if is_true:
            success_count += 1
            if not (isinstance(outer["error"], ast.Constant) and outer["error"].value is None):
                valid = False
                details.append(f"return[{index}]=success error not None")
            if isinstance(outer["data"], ast.Constant) and outer["data"].value is None:
                valid = False
                details.append(f"return[{index}]=success data is None")
        elif is_false:
            failure_count += 1
            error = dict_fields(outer["error"])
            data_is_none = isinstance(outer["data"], ast.Constant) and outer["data"].value is None
            code = error.get("code") if error else None
            exact_code = (
                isinstance(code, ast.Subscript)
                and isinstance(code.value, ast.Name) and code.value.id == "ERROR_CODES"
                and isinstance(code.slice, ast.Constant) and code.slice.value == code_name
            )
            if not data_is_none or error is None or set(error) != {"code", "msg"} or not exact_code:
                valid = False
                details.append(f"return[{index}]=bad failure payload")
        else:
            valid = False
            details.append(f"return[{index}]=ok not literal bool")
    valid = valid and success_count == 1 and failure_count == expected_count - 1
    return valid, (
        f"returns={len(returns)}/{expected_count}, success={success_count}, "
        f"failure={failure_count}, details={details}"
    )


def final_refactor(workspace: Path) -> tuple[bool, str]:
    protected = {
        relative: sha256(workspace / relative) == expected
        for relative, expected in PROTECTED_HASHES.items()
    }
    errors: list[str] = []
    codes_tree, _, codes_error = parse_python(workspace / "src" / "codes.py")
    codes_assignments = 0
    codes_value: Any = None
    if codes_tree is not None:
        for node in codes_tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ERROR_CODES" for target in node.targets
            ):
                codes_assignments += 1
                try:
                    codes_value = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    pass
    codes_runtime_nodes = [] if codes_tree is None else [
        node for node in codes_tree.body
        if not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        )
    ]
    codes_ok = all((
        codes_error is None,
        codes_assignments == 1,
        codes_value == EXPECTED_CODES,
        len(codes_runtime_nodes) == 1,
    ))
    if not codes_ok:
        errors.append(f"codes invalid: parse={codes_error}, count={codes_assignments}, value={codes_value!r}")

    errors_tree, _, errors_error = parse_python(workspace / "src" / "errors.py")
    classes: list[str] = []
    code_attrs = 0
    if errors_tree is not None:
        classes = [node.name for node in errors_tree.body if isinstance(node, ast.ClassDef)]
        for node in ast.walk(errors_tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                code_attrs += sum(isinstance(target, ast.Name) and target.id == "code" for target in targets)
    errors_ok = (
        errors_error is None
        and classes == EXPECTED_EXCEPTION_CLASSES
        and code_attrs == 0
    )
    if not errors_ok:
        errors.append(f"legacy exceptions invalid: parse={errors_error}, classes={classes}, code_attrs={code_attrs}")

    operations_ok = True
    for filename, (code_name, return_count) in {
        "mountpt.py": ("MOUNT_UNAVAILABLE", 3),
        "service.py": ("SERVICE_UNREACHABLE", 3),
        "endpoint.py": ("ENDPOINT_INVALID", 2),
    }.items():
        tree, source, parse_error = parse_python(workspace / "src" / filename)
        if tree is None:
            operations_ok = False
            errors.append(f"{filename} parse={parse_error}")
            continue
        imports_mapping = any(
            isinstance(node, ast.ImportFrom) and node.module == "codes"
            and any(alias.name == "ERROR_CODES" for alias in node.names)
            for node in ast.walk(tree)
        )
        imports_legacy = any(
            isinstance(node, ast.ImportFrom) and node.module == "errors" for node in ast.walk(tree)
        )
        shapes = dict_return_shapes(tree)
        returns_ok, returns_reason = operation_returns_exact(tree, code_name, return_count)
        operation_ok = all((
            imports_mapping,
            not imports_legacy,
            not any(isinstance(node, (ast.Raise, ast.Try, ast.Tuple)) for node in ast.walk(tree)),
            returns_ok,
            code_name in source,
        ))
        operations_ok = operations_ok and operation_ok
        if not operation_ok:
            errors.append(
                f"{filename}: mapping={imports_mapping}, legacy={imports_legacy}, shapes={shapes}, "
                f"returns=({returns_reason}), "
                f"forbidden_ast={any(isinstance(node, (ast.Raise, ast.Try, ast.Tuple)) for node in ast.walk(tree))}"
            )

    orch_tree, _, orch_error = parse_python(workspace / "src" / "orchestrator.py")
    orch_ok = False
    if orch_tree is not None:
        calls = {
            node.func.id for node in ast.walk(orch_tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        shapes = dict_return_shapes(orch_tree)
        tuple_targets = any(
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(target, (ast.Tuple, ast.List))
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
            for node in ast.walk(orch_tree)
        )
        orch_ok = all((
            orch_error is None,
            not any(isinstance(node, (ast.Try, ast.Raise)) for node in ast.walk(orch_tree)),
            not tuple_targets,
            calls == {"verify", "poll", "resolve"},
            {"ok", "code", "msg"} in shapes,
            {"ok", "mountpt", "service", "endpoint"} in shapes,
            shapes.count({"ok", "code", "msg"}) == 3,
            shapes.count({"ok", "mountpt", "service", "endpoint"}) == 1,
        ))
        if not orch_ok:
            errors.append(f"orchestrator: parse={orch_error}, calls={calls}, shapes={shapes}, tuple_targets={tuple_targets}")
    else:
        errors.append(f"orchestrator parse={orch_error}")
    passed = all(protected.values()) and codes_ok and errors_ok and operations_ok and orch_ok
    return passed, f"protected={protected}, errors={errors}"


def output_names(workspace: Path) -> tuple[list[str], bool]:
    root = workspace / "output"
    if root.is_symlink() or not root.is_dir():
        return [], False
    names = sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )
    regular = all(not (root / name).is_symlink() and (root / name).is_file() for name in names)
    return names, regular


def last_check_log(workspace: Path) -> tuple[bool, str]:
    path = workspace / "state" / "check_log.jsonl"
    try:
        if path.is_symlink() or not path.is_file():
            return False, "missing or non-regular"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, str(exc)
    if not records or not isinstance(records[-1], dict):
        return False, "no valid records"
    last = records[-1]
    passed = (
        last.get("_writer") == "check-cli" and last.get("rc") == 0
        and last.get("raises_left") == 0
        and isinstance(last.get("result_shape_hits"), int)
        and last["result_shape_hits"] >= 3
    )
    return passed, f"count={len(records)}, last={last!r}"


def outputs_and_validation(
    workspace: Path, events: list[dict[str, Any]], goals: list[dict[str, Any]]
) -> tuple[bool, str]:
    names, regular = output_names(workspace)
    summary, summary_error = load_strict_json(workspace / "output" / "refactor_summary.json")
    summary_ok = summary_error is None and summary == EXPECTED_SUMMARY
    try:
        plan_path = workspace / "output" / "plan_log.md"
        plan = (
            plan_path.read_text(encoding="utf-8").casefold()
            if plan_path.is_file() and not plan_path.is_symlink() else ""
        )
    except (OSError, UnicodeError):
        plan = ""
    groups = (
        ("superseded", "rejected", "invalidated"),
        ("tuple",), ("dictionary", "dict"), ("mountpt",), ("rework", "reworked"),
        ("codes.py", "error_codes"), ("service",), ("endpoint",), ("exception", "compatibility"),
        ("orchestrator",), ("bin/check",), ("3 passed", "tests_passed", "three passing"),
    )
    group_positions = [
        min((plan.find(marker) for marker in group if marker in plan), default=-1)
        for group in groups
    ]
    dependency_groups = (
        ("codes.py", "error_codes"),
        ("mountpt.py", "three operation modules"),
        ("exception", "compatibility"),
        ("orchestrator",),
        ("bin/check", "validation"),
    )
    dependency_positions = [
        max(plan.find("final dependency order"), plan.find("dependency order"))
    ]
    cursor = dependency_positions[0]
    ordered_dependencies = cursor >= 0
    for group in dependency_groups:
        position = min(
            (plan.find(marker, cursor + 1) for marker in group if plan.find(marker, cursor + 1) >= 0),
            default=-1,
        )
        ordered_dependencies = ordered_dependencies and position > cursor
        cursor = position
    plan_ok = all(position >= 0 for position in group_positions) and ordered_dependencies
    log_ok, log_reason = last_check_log(workspace)
    t2_tools = tools_in_turn(events, 2)
    checks = [event for event in t2_tools if successful_check(event)]
    real_check = bool(checks)
    updates = [event for event in goals if tool_name(event) == "update_goal" and event_turn(event) == 2]
    writes = [event for event in t2_tools if artifact_write(event)]
    summary_writes = [event for event in writes if "refactor_summary.json" in arguments_text(event).casefold()]
    plan_writes = [event for event in writes if "plan_log.md" in arguments_text(event).casefold()]
    order_ok = (
        bool(summary_writes) and bool(plan_writes) and bool(checks)
        and max(event_seq(event) for event in writes) < event_seq(checks[-1])
    )
    passed = all((
        names == ["plan_log.md", "refactor_summary.json"], regular, summary_ok,
        plan_ok, log_ok, real_check, order_ok,
    ))
    return passed, (
        f"files={names}, regular={regular}, summary_ok={summary_ok} ({summary_error}), "
        f"plan_ok={plan_ok}, dependency_order={ordered_dependencies}, log_ok={log_ok} ({log_reason}), checks={len(checks)}, "
        f"real_check={real_check}, output_check_completion_order={order_ok}"
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": compact(reason)}


def assess(
    workspace: Path, events: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    goals = goal_events(events)
    trace_ok, trace_reason = turn_delivery_and_trace(events, summary)
    boundaries_ok, boundaries_reason = thread_topology(events)
    visible_ok, visible_reason = real_goal_results(goals)
    singleton_ok, singleton_reason = same_thread_singleton(goals)
    persistence_ok, persistence_reason = active_goal_persists(events, goals)
    completion_ok, completion_reason = goal_completion_atomic(goals)
    isolation_ok, isolation_reason = fresh_audit_isolated(events, goals)
    turn1_ok, turn1_reason = turn1_goal_protocol(events, goals)
    turn2_ok, turn2_reason = turn2_persisted_protocol(events, goals)
    turn3_ok, turn3_reason = turn3_audit_protocol(events, goals)
    refactor_ok, refactor_reason = final_refactor(workspace)
    outputs_ok, outputs_reason = outputs_and_validation(workspace, events, goals)
    return {
        "harness": [
            rule("hr_turn_delivery_and_trace_complete", trace_ok, trace_reason),
            rule("hr_same_thread_then_fresh_audit_topology", boundaries_ok, boundaries_reason),
            rule("hr_goal_results_are_correlated_and_typed", visible_ok, visible_reason),
            rule("hr_same_thread_singleton_is_enforced", singleton_ok, singleton_reason),
            rule("hr_active_goal_persists_across_turns", persistence_ok, persistence_reason),
            rule("hr_goal_completion_is_atomic_and_visible", completion_ok, completion_reason),
            rule("hr_completed_goal_isolated_from_fresh_audit", isolation_ok, isolation_reason),
        ],
        "agent": [
            rule("ar_turn1_goal_probe_precedes_workspace_work", turn1_ok, turn1_reason),
            rule("ar_turn2_persisted_goal_completion_order", turn2_ok, turn2_reason),
            rule("ar_turn3_read_only_audit_protocol", turn3_ok, turn3_reason),
            rule("ar_latest_host_probe_contract_is_exact", refactor_ok, refactor_reason),
            rule("ar_deliverables_and_final_validation_are_exact", outputs_ok, outputs_reason),
        ],
    }


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
