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
    "Refactor the touring-stage equipment dispatch service incrementally while preserving compatibility and validation."
)
COMPETING_OBJECTIVE = "Prepare an unrelated playbill documentation cleanup."
HANDOFF_OBJECTIVE = (
    "Finish the touring-equipment dispatch replan, validate it, and deliver the required artifacts."
)
TURN_HASHES = [
    "f436622cd44d478fca9b6786a8543aa010b80a2aacc81ce7134146f998844070",
    "ea4b0c14d0e1bde0f1f1351dea30af93ce7fea0700cd931ee04d662564e920b4",
    "9b8f51e465e09626d26a37fd36ac8a16d2f92540aac47dd4e25d9f2639978c85",
]
PROTECTED_HASHES = {
    "tests/test_orders.py": "f3b3463f1548f5c3793d4f238c914fbe6b4bfe468d497c611c56239f446ea429",
    "bin/check": "67b3b9273209ff6e2335709bb2d8ab6d8808cf47dec767a94804004de33f85c2",
}
EXPECTED_CODES = {
    "UNKNOWN": "E-000",
    "OUT_OF_STOCK": "E-101",
    "PAYMENT_DECLINED": "E-102",
    "ADDRESS_INVALID": "E-103",
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
    "singleton",
)
NO_GOAL_MARKERS = (
    "no active goal",
    "no current goal",
    "goal not found",
    "no goal for this thread",
)
FORBIDDEN_TOOL_MARKERS = (
    "update_plan",
    "create_plan",
    "taskcreate",
    "task_create",
    "memory_",
    "spawn_agent",
    "delegate_task",
    "sessions_spawn",
    "subagents",
    "web__run",
    "web_search",
    "search_query",
    "webfetch",
    "web_fetch",
    "browser",
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
    return result_id in (None, "", call.get("tool_call_id"))


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
    return semantic and (error_flag or failure_state or "reject" in text)


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


def trace_and_boundaries(
    events: list[dict[str, Any]], summary: dict[str, Any]
) -> tuple[bool, str]:
    exact, exact_reason = turns_exact(events)
    finished = [event for event in events if event.get("event_type") == "conversation_turn_finished"]
    failures = [event for event in events if event.get("event_type") == "run_failed"]
    terminals = [event for event in events if event.get("event_type") == "conversation_finished"]
    identities = thread_identities(events)
    per_turn_single = all(bool(identities.get(turn)) for turn in (1, 2, 3))
    representatives = [identities[turn] for turn in (1, 2, 3)] if per_turn_single else []
    distinct = len(set(representatives)) == 3 if representatives else False
    handoffs = [event for event in events if event.get("event_type") == "context_handoff"]
    handoff_turns = sorted(
        int(event.get("before_turn")) for event in handoffs
        if isinstance(event.get("before_turn"), int)
        and str(event.get("action", "")).casefold() == "replay"
    )
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
    passed = exact and lifecycle and distinct and handoff_turns == [2, 3]
    return passed, (
        f"exact={exact} ({exact_reason}), lifecycle={lifecycle}, failures={len(failures)}, "
        f"identities={identities}, "
        f"representatives_distinct={distinct}, replay_boundaries={handoff_turns}, "
        f"summary_status={summary.get('status')}, trace_complete={summary.get('trace_complete')}"
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
        if create_success(event, PRIMARY_OBJECTIVE) or create_success(event, HANDOFF_OBJECTIVE)
    ]
    successful_updates = [
        event_seq(event) for event in goals if update_success(event, HANDOFF_OBJECTIVE)
    ]
    unreadable_gets = [
        event_seq(event) for event in goals
        if tool_name(event) == "get_goal"
        and not (result_visible(event) and not missing_tool(event) and (goal_records(event) or no_goal(event)))
    ]
    passed = all((
        counts["create_goal"] >= 2,
        counts["get_goal"] >= 1,
        counts["update_goal"] >= 1,
        not invisible,
        not unavailable,
        not unreadable_gets,
        len(successful_creates) >= 2,
        bool(successful_updates),
    ))
    return passed, (
        f"counts={counts}, invisible={invisible}, unavailable={unavailable}, "
        f"unreadable_gets={unreadable_gets}, successful_creates={successful_creates}, "
        f"successful_updates={successful_updates}"
    )


def singleton_and_scope(goals: list[dict[str, Any]]) -> tuple[bool, str]:
    by_turn = {turn: [event for event in goals if event_turn(event) == turn] for turn in (1, 2, 3)}
    names = {turn: [tool_name(event) for event in by_turn[turn]] for turn in (1, 2, 3)}
    t1_creates = [event for event in by_turn[1] if tool_name(event) == "create_goal"]
    t1_gets = [event for event in by_turn[1] if tool_name(event) == "get_goal"]
    t2_creates = [event for event in by_turn[2] if tool_name(event) == "create_goal"]
    t2_gets = [event for event in by_turn[2] if tool_name(event) == "get_goal"]
    t2_updates = [event for event in by_turn[2] if tool_name(event) == "update_goal"]
    t3_gets = [event for event in by_turn[3] if tool_name(event) == "get_goal"]
    checks = {
        "t1_sequence": names[1][:4] == ["create_goal", "get_goal", "create_goal", "get_goal"],
        "primary_created": len(t1_creates) >= 1 and create_success(t1_creates[0], PRIMARY_OBJECTIVE),
        "primary_read_before": len(t1_gets) >= 1 and goal_state(t1_gets[0], PRIMARY_OBJECTIVE, "active"),
        "singleton_rejected": len(t1_creates) >= 2 and rejected_singleton(t1_creates[1]),
        "primary_read_after": len(t1_gets) >= 2 and goal_state(t1_gets[1], PRIMARY_OBJECTIVE, "active"),
        "t2_empty": len(t2_gets) >= 1 and no_goal(t2_gets[0]),
        "handoff_created": len(t2_creates) >= 1 and create_success(t2_creates[0], HANDOFF_OBJECTIVE),
        "handoff_active": len(t2_gets) >= 2 and goal_state(t2_gets[1], HANDOFF_OBJECTIVE, "active"),
        "handoff_completed": len(t2_updates) == 1 and update_success(t2_updates[0], HANDOFF_OBJECTIVE),
        "handoff_read_complete": len(t2_gets) >= 3 and goal_state(t2_gets[-1], HANDOFF_OBJECTIVE, "complete"),
        "t3_empty": len(t3_gets) >= 1 and no_goal(t3_gets[0]),
    }
    return all(checks.values()), f"checks={checks}, goal_names={names}"


def contains_forbidden_tool(events: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        name = tool_name(event)
        if any(marker in name for marker in FORBIDDEN_TOOL_MARKERS):
            found.append(name)
    return found


def goal_protocol(events: list[dict[str, Any]], goals: list[dict[str, Any]]) -> tuple[bool, str]:
    by_turn = {turn: [event for event in goals if event_turn(event) == turn] for turn in (1, 2, 3)}
    names = {turn: [tool_name(event) for event in by_turn[turn]] for turn in (1, 2, 3)}
    all_tools = {turn: tools_in_turn(events, turn) for turn in (1, 2, 3)}
    creates = [event for event in goals if tool_name(event) == "create_goal"]
    gets = [event for event in goals if tool_name(event) == "get_goal"]
    updates = [event for event in goals if tool_name(event) == "update_goal"]
    t2_checks = [event for event in all_tools[2] if "bin/check" in arguments_text(event)]
    summary_writes = [event for event in all_tools[2] if "refactor_summary.json" in arguments_text(event)]
    plan_writes = [event for event in all_tools[2] if "plan_log.md" in arguments_text(event)]
    update = updates[0] if len(updates) == 1 else None
    order_ok = (
        update is not None and bool(t2_checks) and bool(summary_writes) and bool(plan_writes)
        and max(event_seq(summary_writes[-1]), event_seq(plan_writes[-1])) < event_seq(t2_checks[-1])
        < event_seq(update)
    )
    first_tools = {
        turn: [tool_name(event) for event in all_tools[turn][:4]] for turn in (1, 2, 3)
    }
    objective_ok = (
        len(creates) == 3
        and [event_turn(event) for event in creates] == [1, 1, 2]
        and PRIMARY_OBJECTIVE in arguments_text(creates[0])
        and COMPETING_OBJECTIVE in arguments_text(creates[1])
        and HANDOFF_OBJECTIVE in arguments_text(creates[2])
    )
    update_ok = (
        update is not None and event_turn(update) == 2
        and "complete" in arguments_text(update).casefold()
        and "blocked" not in arguments_text(update).casefold()
        and update_success(update, HANDOFF_OBJECTIVE)
    )
    no_false_claim = not any(
        "goal" in str(event.get("content") or "").casefold()
        and any(marker in str(event.get("content") or "").casefold() for marker in ("created", "completed", "preserved"))
        for event in events
        if event.get("event_type") == "assistant_message"
        and any(missing_tool(goal) for goal in by_turn.get(event_turn(event) or 0, []))
    )
    passed = all((
        objective_ok,
        len(gets) == 6,
        len(updates) == 1,
        first_tools[1] == ["create_goal", "get_goal", "create_goal", "get_goal"],
        first_tools[2][:3] == ["get_goal", "create_goal", "get_goal"],
        first_tools[3][:1] == ["get_goal"],
        names[2][-2:] == ["update_goal", "get_goal"],
        names[3] == ["get_goal"],
        order_ok,
        update_ok,
        not contains_forbidden_tool(events),
        no_false_claim,
    ))
    return passed, (
        f"names={names}, create_turns={[event_turn(e) for e in creates]}, gets={len(gets)}, "
        f"updates={len(updates)}, first_tools={first_tools}, output_check_update_order={order_ok}, "
        f"update_ok={update_ok}, forbidden={contains_forbidden_tool(events)}, "
        f"no_false_claim={no_false_claim}"
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
    codes_ok = codes_error is None and codes_assignments == 1 and codes_value == EXPECTED_CODES
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
        and classes == ["OrderError", "OutOfStock", "PaymentDeclined", "AddressInvalid"]
        and code_attrs == 0
    )
    if not errors_ok:
        errors.append(f"legacy exceptions invalid: parse={errors_error}, classes={classes}, code_attrs={code_attrs}")

    operations_ok = True
    for filename, code_name in {
        "stock.py": "OUT_OF_STOCK",
        "payment.py": "PAYMENT_DECLINED",
        "shipping.py": "ADDRESS_INVALID",
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
        operation_ok = all((
            imports_mapping,
            not imports_legacy,
            not any(isinstance(node, (ast.Raise, ast.Try, ast.Tuple)) for node in ast.walk(tree)),
            bool(shapes) and all(shape == {"ok", "data", "error"} for shape in shapes),
            code_name in source,
        ))
        operations_ok = operations_ok and operation_ok
        if not operation_ok:
            errors.append(
                f"{filename}: mapping={imports_mapping}, legacy={imports_legacy}, shapes={shapes}, "
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
            {"reserve", "charge", "quote"}.issubset(calls),
            {"ok", "code", "msg"} in shapes,
            {"ok", "reserve", "charge", "quote"} in shapes,
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
        ("tuple",), ("dictionary", "dict"), ("stock",), ("rework", "reworked"),
        ("codes.py", "error_codes"), ("payment",), ("shipping",), ("exception", "compatibility"),
        ("orchestrator",), ("bin/check",), ("3 passed", "tests_passed", "three passing"),
    )
    plan_ok = all(any(marker in plan for marker in group) for group in groups)
    log_ok, log_reason = last_check_log(workspace)
    t2_tools = tools_in_turn(events, 2)
    checks = [event for event in t2_tools if "bin/check" in arguments_text(event)]
    real_check = any(
        result_visible(event)
        and re.search(r"TESTS\s+rc=0\b", result_text(event), re.IGNORECASE)
        and "3 passed" in result_text(event).casefold()
        for event in checks
    )
    updates = [event for event in goals if tool_name(event) == "update_goal" and event_turn(event) == 2]
    writes = [
        event for event in t2_tools
        if "refactor_summary.json" in arguments_text(event) or "plan_log.md" in arguments_text(event)
    ]
    order_ok = (
        bool(writes) and bool(checks) and len(updates) == 1
        and max(event_seq(event) for event in writes) < event_seq(checks[-1]) < event_seq(updates[0])
        and update_success(updates[0], HANDOFF_OBJECTIVE)
    )
    passed = all((
        names == ["plan_log.md", "refactor_summary.json"], regular, summary_ok,
        plan_ok, log_ok, real_check, order_ok,
    ))
    return passed, (
        f"files={names}, regular={regular}, summary_ok={summary_ok} ({summary_error}), "
        f"plan_ok={plan_ok}, log_ok={log_ok} ({log_reason}), checks={len(checks)}, "
        f"real_check={real_check}, output_check_completion_order={order_ok}"
    )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": compact(reason)}


def assess(
    workspace: Path, events: list[dict[str, Any]], summary: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    goals = goal_events(events)
    trace_ok, trace_reason = trace_and_boundaries(events, summary)
    visible_ok, visible_reason = real_goal_results(goals)
    scope_ok, scope_reason = singleton_and_scope(goals)
    protocol_ok, protocol_reason = goal_protocol(events, goals)
    refactor_ok, refactor_reason = final_refactor(workspace)
    outputs_ok, outputs_reason = outputs_and_validation(workspace, events, goals)
    return {
        "harness": [
            rule("hr_trace_and_thread_boundaries_complete", trace_ok, trace_reason),
            rule("hr_native_goal_results_are_real_and_visible", visible_ok, visible_reason),
            rule("hr_singleton_and_thread_scope_semantics_hold", scope_ok, scope_reason),
        ],
        "agent": [
            rule("ar_native_goal_protocol_is_followed", protocol_ok, protocol_reason),
            rule("ar_final_refactor_matches_latest_contract", refactor_ok, refactor_reason),
            rule("ar_outputs_and_validation_are_exact", outputs_ok, outputs_reason),
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
