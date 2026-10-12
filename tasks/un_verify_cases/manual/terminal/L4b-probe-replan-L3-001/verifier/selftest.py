#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable


HERE = Path(__file__).resolve().parent
TASK = HERE.parent
SPEC = importlib.util.spec_from_file_location("l4b_probe_score", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
score = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(score)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: Any) -> None:
    write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def goal_result(objective: str, status: str, *, result_status: str | None = None) -> dict[str, Any]:
    return {
        "status": result_status or status,
        "goal": {"objective": objective, "status": status},
    }


def build_case(root: Path) -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    for relative in ("tests/test_orders.py", "bin/check"):
        source = TASK / "workspace" / relative
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    write(
        workspace / "src/codes.py",
        'ERROR_CODES = {"UNKNOWN": "E-000", "MOUNT_UNAVAILABLE": "E-101", '
        '"SERVICE_UNREACHABLE": "E-102", "ENDPOINT_INVALID": "E-103"}\n',
    )
    write(
        workspace / "src/errors.py",
        "class ProbeError(Exception):\n    pass\n\n"
        "class MountUnavailable(ProbeError):\n    pass\n\n"
        "class ServiceUnreachable(ProbeError):\n    pass\n\n"
        "class EndpointInvalid(ProbeError):\n    pass\n",
    )
    operation_sources = {
        "mountpt.py": (
            "MOUNT_UNAVAILABLE",
            "def verify(device, min_free_kib):\n"
            "    if min_free_kib <= 0:\n"
            "        return {'ok': False, 'data': None, 'error': {'code': ERROR_CODES['MOUNT_UNAVAILABLE'], 'msg': 'min'}}\n"
            "    if device.startswith('BADDEV-'):\n"
            "        return {'ok': False, 'data': None, 'error': {'code': ERROR_CODES['MOUNT_UNAVAILABLE'], 'msg': 'mount'}}\n"
            "    return {'ok': True, 'data': {'device': device}, 'error': None}\n",
        ),
        "service.py": (
            "SERVICE_UNREACHABLE",
            "def poll(probe_id, timeout_ms):\n"
            "    if timeout_ms <= 0:\n"
            "        return {'ok': False, 'data': None, 'error': {'code': ERROR_CODES['SERVICE_UNREACHABLE'], 'msg': 'timeout'}}\n"
            "    if probe_id.startswith('UNREACH-'):\n"
            "        return {'ok': False, 'data': None, 'error': {'code': ERROR_CODES['SERVICE_UNREACHABLE'], 'msg': 'service'}}\n"
            "    return {'ok': True, 'data': {'probe_id': probe_id}, 'error': None}\n",
        ),
        "endpoint.py": (
            "ENDPOINT_INVALID",
            "def resolve(host):\n"
            "    if not host or len(host) < 5:\n"
            "        return {'ok': False, 'data': None, 'error': {'code': ERROR_CODES['ENDPOINT_INVALID'], 'msg': 'host'}}\n"
            "    return {'ok': True, 'data': {'host': host}, 'error': None}\n",
        ),
    }
    for filename, (_code, body) in operation_sources.items():
        write(workspace / "src" / filename, "from .codes import ERROR_CODES\n\n" + body)
    write(
        workspace / "src/orchestrator.py",
        "from .mountpt import verify\nfrom .service import poll\nfrom .endpoint import resolve\n\n"
        "def run_probe(probe_id, device, min_free_kib, timeout_ms, host):\n"
        "    mountpt = verify(device, min_free_kib)\n"
        "    if not mountpt['ok']:\n"
        "        return {'ok': False, 'code': mountpt['error']['code'], 'msg': mountpt['error']['msg']}\n"
        "    service = poll(probe_id, timeout_ms)\n"
        "    if not service['ok']:\n"
        "        return {'ok': False, 'code': service['error']['code'], 'msg': service['error']['msg']}\n"
        "    endpoint = resolve(host)\n"
        "    if not endpoint['ok']:\n"
        "        return {'ok': False, 'code': endpoint['error']['code'], 'msg': endpoint['error']['msg']}\n"
        "    return {'ok': True, 'mountpt': mountpt['data'], 'service': service['data'], 'endpoint': endpoint['data']}\n",
    )
    write_json(workspace / "output/refactor_summary.json", score.EXPECTED_SUMMARY)
    write(
        workspace / "output/plan_log.md",
        "# Replan\nSuperseded tuple plan was invalidated. The dictionary plan reworked mountpt.py.\n"
        "Final dependency order: codes.py ERROR_CODES; mountpt.py, service.py, endpoint.py "
        "(three operation modules); exception compatibility; orchestrator; bin/check validation.\n"
        "Final bin/check: 3 passed.\n",
    )
    write(
        workspace / "state/check_log.jsonl",
        json.dumps({
            "_writer": "check-cli",
            "rc": 0,
            "raises_left": 0,
            "result_shape_hits": 8,
        }) + "\n",
    )

    turns = json.loads((TASK / "turns.json").read_text(encoding="utf-8"))["turns"]
    events: list[dict[str, Any]] = []

    def emit(event_type: str, **fields: Any) -> dict[str, Any]:
        event = {"seq": len(events) + 1, "event_type": event_type, **fields}
        events.append(event)
        return event

    def tool(turn: int, name: str, arguments: Any, result: Any) -> dict[str, Any]:
        call_id = f"call-{len(events) + 1}"
        return emit(
            "tool",
            conversation_turn=turn,
            tool_call={"tool_name": name, "tool_call_id": call_id, "arguments": arguments},
            tool_result={
                "tool_name": name,
                "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False),
            },
        )

    emit("conversation_started")
    for turn, prompt in enumerate(turns, start=1):
        if turn == 3:
            emit("context_handoff", before_turn=3, action="replay")
        emit("conversation_turn_started", conversation_turn=turn, user_input=prompt)
        if turn == 1:
            tool(1, "create_goal", {"objective": score.PRIMARY_OBJECTIVE},
                 goal_result(score.PRIMARY_OBJECTIVE, "active", result_status="created"))
            tool(1, "get_goal", {}, goal_result(score.PRIMARY_OBJECTIVE, "active"))
            tool(1, "create_goal", {"objective": score.COMPETING_OBJECTIVE}, {
                "status": "rejected",
                "error": "unfinished goal already exists",
                "goal": {"objective": score.PRIMARY_OBJECTIVE, "status": "active"},
            })
            tool(1, "get_goal", {}, goal_result(score.PRIMARY_OBJECTIVE, "active"))
            tool(1, "apply_patch", {"patch": "*** Update File: src/mountpt.py"}, {"ok": True})
        elif turn == 2:
            tool(2, "get_goal", {}, goal_result(score.PRIMARY_OBJECTIVE, "active"))
            tool(2, "apply_patch", {"patch": "*** Update File: src/mountpt.py"}, {"ok": True})
            tool(2, "apply_patch", {"patch": "*** Add File: output/refactor_summary.json"}, {"ok": True})
            tool(2, "apply_patch", {"patch": "*** Add File: output/plan_log.md"}, {"ok": True})
            tool(2, "exec_command", {"cmd": "bin/check"}, {
                "content": "TESTS rc=0\nRAISES_LEFT=0\nRESULT_SHAPE_HITS=8\n---\n3 passed",
                "exit_code": 0,
            })
            tool(2, "update_goal", {"status": "complete"},
                 goal_result(score.PRIMARY_OBJECTIVE, "complete", result_status="completed"))
            tool(2, "get_goal", {}, goal_result(score.PRIMARY_OBJECTIVE, "complete"))
        else:
            tool(3, "get_goal", {}, {"status": "none", "goal": None})
            tool(3, "exec_command", {"cmd": "bin/check"}, {
                "content": "TESTS rc=0\nRAISES_LEFT=0\nRESULT_SHAPE_HITS=8\n---\n3 passed",
                "exit_code": 0,
            })
            tool(3, "exec_command", {"cmd": "sed -n '1,80p' output/refactor_summary.json"}, {"content": "{}"})
            tool(3, "exec_command", {"cmd": "sed -n '1,80p' output/plan_log.md"}, {"content": "# Replan"})
        emit(
            "conversation_turn_finished",
            conversation_turn=turn,
            native_session_id="session-A" if turn < 3 else "session-B",
        )
    emit("conversation_finished", status="finished")
    trace = run_dir / "artifacts/trace/run-golden"
    write_json(trace / "events.json", events)
    write_json(trace / "summary.json", {
        "status": "finished",
        "trace_complete": True,
        "structured_trace_status": "available",
    })
    return workspace, run_dir


def load_events(run_dir: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = next(run_dir.glob("artifacts/trace/run-*/events.json"))
    return path, json.loads(path.read_text(encoding="utf-8"))


def save_events(path: Path, events: list[dict[str, Any]]) -> None:
    write_json(path, events)


def observed(workspace: Path, run_dir: Path) -> dict[str, int]:
    events, summary = score.trace_bundle(run_dir)
    payload = score.assess(workspace, events, summary)
    return {
        item["id"]: item["score"]
        for layer in ("harness", "agent")
        for item in payload[layer]
    }


def check_failure(
    metric: str,
    mutate: Callable[[Path, Path, list[dict[str, Any]]], None],
) -> None:
    with tempfile.TemporaryDirectory(prefix="l4b-probe-fail-") as temporary:
        workspace, run_dir = build_case(Path(temporary))
        path, events = load_events(run_dir)
        mutate(workspace, run_dir, events)
        save_events(path, events)
        scores = observed(workspace, run_dir)
        assert scores[metric] == 0, (metric, scores)


def main() -> int:
    expected_ids = {
        "hr_turn_delivery_and_trace_complete",
        "hr_same_thread_then_fresh_audit_topology",
        "hr_goal_results_are_correlated_and_typed",
        "hr_same_thread_singleton_is_enforced",
        "hr_active_goal_persists_across_turns",
        "hr_goal_completion_is_atomic_and_visible",
        "hr_completed_goal_isolated_from_fresh_audit",
        "ar_turn1_goal_probe_precedes_workspace_work",
        "ar_turn2_persisted_goal_completion_order",
        "ar_turn3_read_only_audit_protocol",
        "ar_latest_host_probe_contract_is_exact",
        "ar_deliverables_and_final_validation_are_exact",
    }
    with tempfile.TemporaryDirectory(prefix="l4b-probe-golden-") as temporary:
        workspace, run_dir = build_case(Path(temporary))
        scores = observed(workspace, run_dir)
        assert set(scores) == expected_ids, scores
        assert all(type(value) is int and value == 1 for value in scores.values()), scores

    check_failure(
        "hr_same_thread_then_fresh_audit_topology",
        lambda _w, _r, events: [
            event.update(native_session_id="session-X")
            for event in events
            if event.get("event_type") == "conversation_turn_finished"
            and event.get("conversation_turn") == 2
        ],
    )
    check_failure(
        "hr_goal_results_are_correlated_and_typed",
        lambda _w, _r, events: next(
            event["tool_result"].pop("tool_call_id")
            for event in events if event.get("event_type") == "tool"
            and score.tool_name(event) == "get_goal"
        ),
    )
    check_failure(
        "hr_active_goal_persists_across_turns",
        lambda _w, _r, events: next(
            event["tool_result"].update(content=json.dumps({"status": "none", "goal": None}))
            for event in events if event.get("event_type") == "tool"
            and event.get("conversation_turn") == 2 and score.tool_name(event) == "get_goal"
        ),
    )
    check_failure(
        "hr_goal_completion_is_atomic_and_visible",
        lambda _w, _r, events: next(
            event["tool_result"].update(content=json.dumps(goal_result(score.PRIMARY_OBJECTIVE, "active")))
            for event in events if event.get("event_type") == "tool"
            and event.get("conversation_turn") == 2 and score.tool_name(event) == "get_goal"
            and event["seq"] > 10
        ),
    )
    check_failure(
        "hr_completed_goal_isolated_from_fresh_audit",
        lambda _w, _r, events: next(
            event["tool_result"].update(content=json.dumps(goal_result(score.PRIMARY_OBJECTIVE, "complete")))
            for event in events if event.get("event_type") == "tool"
            and event.get("conversation_turn") == 3 and score.tool_name(event) == "get_goal"
        ),
    )
    check_failure(
        "ar_latest_host_probe_contract_is_exact",
        lambda workspace, _r, _e: write(
            workspace / "src/errors.py",
            (workspace / "src/errors.py").read_text(encoding="utf-8").replace("ProbeError", "OrderError"),
        ),
    )
    check_failure(
        "ar_deliverables_and_final_validation_are_exact",
        lambda workspace, _r, _e: write(
            workspace / "output/plan_log.md",
            "Superseded tuple plan; dictionary plan; stock rework; 3 passed.\n",
        ),
    )
    print("L4b probe verifier self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
