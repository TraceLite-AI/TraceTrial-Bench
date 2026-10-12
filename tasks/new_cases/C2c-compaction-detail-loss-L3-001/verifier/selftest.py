#!/usr/bin/env python3
"""Behavioral self-tests for the academic C2c task and verifier."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any


CASE = Path(__file__).resolve().parent.parent


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCORE = load_module("c2c_academic_score", CASE / "verifier" / "score.py")
SERVER = load_module("c2c_academic_server", CASE / "simulator" / "server.py")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tool_event(turn: int, name: str, arguments: dict[str, Any], content: str) -> dict[str, Any]:
    return {
        "event_type": "tool",
        "conversation_turn": turn,
        "tool_call": {
            "tool_call_id": "placeholder",
            "tool_name": name,
            "arguments": arguments,
            "provider_executed": True,
        },
        "tool_result": {
            "tool_call_id": "placeholder",
            "tool_name": name,
            "content": content,
            "error": False,
            "exit_code": 0,
        },
    }


def resequence(events: list[dict[str, Any]]) -> None:
    for seq, event in enumerate(events, start=1):
        event["seq"] = seq
        if event.get("event_type") == "tool":
            call_id = f"call-{seq:04d}"
            event["tool_call"]["tool_call_id"] = call_id
            event["tool_result"]["tool_call_id"] = call_id


def fixture(root: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    baseline = root / "baseline"
    workspace = root / "workspace"
    shutil.copytree(CASE / "workspace", baseline)
    shutil.copytree(CASE / "workspace", workspace)
    rows = SCORE.observation_rows(baseline)
    anomalies = SCORE.expected_anomalies(rows)
    details = SCORE.expected_details(rows, anomalies)
    literature = SCORE.expected_literature()

    for batch in SCORE.BATCHES:
        path = workspace / "observations" / f"{batch}.md"
        text = path.read_text(encoding="utf-8")
        for row in rows.values():
            if row["batch"] == batch:
                text = text.replace(row["serial"], "MASKED")
                text = text.replace(row["captured_at"], "REDACTED")
        path.write_text(text, encoding="utf-8")
    write_json(workspace / "output" / "observation_anomalies.json", {"anomalies": anomalies})
    write_json(workspace / "output" / "literature_review.json", {"queries": literature})
    write_json(workspace / "output" / "observation_details.json", {"details": details})

    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    state = SERVER.initial_state(scenario)
    events: list[dict[str, Any]] = [
        {"event_type": "conversation_started", "mode": "fixed_turns", "planned_turns": 3},
        {"event_type": "native_event", "raw_event": {"data": {"contextWindow": 200000}}},
        {"event_type": "conversation_turn_started", "turn_index": 1},
        {"event_type": "run_started", "conversation_turn": 1},
    ]
    for batch in SCORE.BATCHES:
        content = (baseline / "observations" / f"{batch}.md").read_text(encoding="utf-8")
        events.append(tool_event(1, "read_file", {"path": f"observations/{batch}.md"}, content))
    events.extend(
        [
            {"event_type": "assistant_message", "conversation_turn": 1, "content": "Audit complete."},
            {"event_type": "run_finished", "conversation_turn": 1, "trace_complete": True, "exit_code": 0},
            {"event_type": "conversation_turn_finished", "turn_index": 1, "status": "finished"},
            {"event_type": "conversation_turn_started", "turn_index": 2},
            {"event_type": "run_started", "conversation_turn": 2},
        ]
    )
    ledger_rows: list[dict[str, Any]] = []
    page_count = 0
    summary = "Academic web_search literature review progress: Q01 Q02 Q03 Q04 Q05 Q06 Q07 Q08 Q09 Q10 Q11 Q12.\n" + "\n".join(
        f"{row['observation_id']}: instrument_last4={row['instrument_last4']}; captured_at={row['captured_at']}"
        for row in details
    )
    for query_id in SCORE.QUERY_IDS:
        query = scenario["queries"][query_id]["query"]
        cursor = None
        for _page in range(1, 4):
            arguments = {"query_id": query_id, "query": query}
            if cursor is not None:
                arguments["cursor"] = cursor
            request = {"tool": "web_search", "arguments": arguments}
            response = SERVER.handle(request, state, scenario)
            assert response["ok"] is True
            ledger_rows.append({"request": request, "response": response, "committed": True})
            events.append(
                tool_event(
                    2,
                    "mcp__harnessbench__web_search",
                    arguments,
                    response["_task_tool_content"],
                )
            )
            cursor = response["next_cursor"]
            page_count += 1
            if page_count == 30:
                events.append(
                    {
                        "event_type": "context_compaction",
                        "conversation_turn": 2,
                        "source": "codex.native_session",
                        "native_event_type": "compacted",
                        "native_event_key": "selftest:compact-1",
                        "summary_present": True,
                        "summary": summary,
                    }
                )
    assert page_count == 36
    events.extend(
        [
            tool_event(2, "write_file", {"path": "output/literature_review.json"}, "written"),
            {"event_type": "assistant_message", "conversation_turn": 2, "content": "Review complete."},
            {"event_type": "run_finished", "conversation_turn": 2, "trace_complete": True, "exit_code": 0},
            {"event_type": "conversation_turn_finished", "turn_index": 2, "status": "finished"},
            {"event_type": "conversation_turn_started", "turn_index": 3},
            {"event_type": "run_started", "conversation_turn": 3},
            tool_event(3, "write_file", {"path": "output/observation_details.json"}, "written"),
            {"event_type": "assistant_message", "conversation_turn": 3, "content": "Details delivered."},
            {"event_type": "run_finished", "conversation_turn": 3, "trace_complete": True, "exit_code": 0},
            {"event_type": "conversation_turn_finished", "turn_index": 3, "status": "finished"},
            {"event_type": "conversation_finished", "status": "finished", "turns_completed": 3},
            {"event_type": "run_finished", "trace_complete": True, "exit_code": 0},
        ]
    )
    resequence(events)
    run_dir = root / "run"
    trace = run_dir / "artifacts" / "container" / "trace" / "run-selftest"
    write_json(trace / "events.json", events)
    write_json(
        trace / "summary.json",
        {
            "status": "finished",
            "trace_complete": True,
            "conversation": {"native_session_ids": ["session-selftest"]},
            "token_usage": {
                "calls": [
                    {
                        "usage_scope": "call",
                        "conversation_turn": 2,
                        "input_tokens": 200001,
                    }
                ]
            },
        },
    )
    ledger = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in ledger_rows), encoding="utf-8")
    return workspace, baseline, run_dir, {"events_path": trace / "events.json", "details": details, "scenario": scenario, "state": state}


def score_map(payload: dict[str, Any], layer: str) -> dict[str, int]:
    return {row["id"]: row["score"] for row in payload["rule_results"][layer]}


def main() -> int:
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))["turns"]
    assert len(turns) == 3
    assert (CASE / "prompt.md").read_text(encoding="utf-8") == turns[0]
    assert "web_search" in turns[1]
    assert "observation_details.json" in turns[2]

    scenario = json.loads((CASE / "simulator" / "scenario.json").read_text(encoding="utf-8"))
    state = SERVER.initial_state(scenario)
    first_query = scenario["queries"]["Q01"]["query"]
    first = SERVER.handle(
        {"tool": "web_search", "arguments": {"query_id": "Q01", "query": first_query}},
        state,
        scenario,
    )
    assert first["ok"] is True and first["payload_octets"] >= 40_000
    repeated = SERVER.handle(
        {"tool": "web_search", "arguments": {"query_id": "Q01", "query": first_query}},
        state,
        scenario,
    )
    assert repeated["ok"] is False and repeated["error_type"] == "CursorMismatch"

    with tempfile.TemporaryDirectory() as temporary:
        workspace, baseline, run_dir, meta = fixture(Path(temporary))
        perfect = SCORE.evaluate(workspace, baseline, run_dir)
        assert set(score_map(perfect, "agent").values()) == {1}
        assert set(score_map(perfect, "harness").values()) == {1}

        summary_path = meta["events_path"].parent / "summary.json"
        summary_value = json.loads(summary_path.read_text(encoding="utf-8"))
        events = json.loads(meta["events_path"].read_text(encoding="utf-8"))
        without_compaction = [
            row for row in events if row.get("event_type") != "context_compaction"
        ]
        resequence(without_compaction)
        write_json(meta["events_path"], without_compaction)
        crossed_without_compaction = SCORE.evaluate(workspace, baseline, run_dir)
        crossed_rule = next(
            row
            for row in crossed_without_compaction["rule_results"]["harness"]
            if row["id"] == "hr_context_window_compaction_contract"
        )
        assert crossed_rule["score"] == 0
        assert "threshold_crossed_without_compaction" in crossed_rule["reason"]

        summary_value["token_usage"]["calls"][0]["input_tokens"] = 199999
        write_json(summary_path, summary_value)
        below_threshold = SCORE.evaluate(workspace, baseline, run_dir)
        below_rule = next(
            row
            for row in below_threshold["rule_results"]["harness"]
            if row["id"] == "hr_context_window_compaction_contract"
        )
        assert below_rule["score"] == 1
        assert "below_threshold_no_violation" in below_rule["reason"]

        write_json(meta["events_path"], events)
        summary_value["token_usage"]["calls"][0]["input_tokens"] = 200001
        write_json(summary_path, summary_value)

        events = json.loads(meta["events_path"].read_text(encoding="utf-8"))
        web_event = next(
            row for row in events if SCORE.canonical_tool_name(row) == "web_search"
        )
        web_event["tool_result"]["content"] = web_event["tool_result"]["content"].replace(
            "SEARCH_PAGE_END", "SEARCH_PAGE_CUT", 1
        )
        write_json(meta["events_path"], events)
        missing_page = SCORE.evaluate(workspace, baseline, run_dir)
        assert score_map(missing_page, "harness")["hr_academic_search_pressure_model_visible"] == 0

        events = json.loads(meta["events_path"].read_text(encoding="utf-8"))
        web_event = next(
            row for row in events if SCORE.canonical_tool_name(row) == "web_search"
        )
        web_event["tool_result"]["content"] = web_event["tool_result"]["content"].replace(
            "SEARCH_PAGE_CUT", "SEARCH_PAGE_END", 1
        )
        compact = next(row for row in events if row.get("event_type") == "context_compaction")
        compact["summary"] = "Anomaly IDs retained, experimental details omitted."
        write_json(meta["events_path"], events)
        lost_summary = SCORE.evaluate(workspace, baseline, run_dir)
        assert score_map(lost_summary, "harness")["hr_active_summary_preserved_all_details"] == 0

        details_path = workspace / "output" / "observation_details.json"
        wrong = json.loads(details_path.read_text(encoding="utf-8"))
        wrong["details"][0]["instrument_last4"] = "9999"
        write_json(details_path, wrong)
        wrong_detail = SCORE.evaluate(workspace, baseline, run_dir)
        assert score_map(wrong_detail, "agent")["ar_post_compaction_instrument_bindings_exact"] == 0
        assert score_map(wrong_detail, "agent")["ar_post_compaction_timestamp_bindings_exact"] == 1

    print("C2c academic web-search selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
