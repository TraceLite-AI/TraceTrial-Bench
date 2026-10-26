#!/usr/bin/env python3
"""Self-test for the O1a verifier."""
from __future__ import annotations

import json, os, subprocess, sys, tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
POSTINGS = tuple("PST-%02d" % (i + 1) for i in range(8))
ENOENT = "ENOENT: no such file or directory, access '/ws/ledgers/north-2026-07.json'"
EISDIR = "EISDIR: illegal operation on a directory, read"


def build_fixture(root: Path):
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("ledgers", "docs"):
        target = workspace / name
        target.mkdir(parents=True, exist_ok=True)
        source = CASE / "workspace" / name
        for item in source.rglob("*"):
            rel = item.relative_to(source)
            if item.is_dir():
                (target / rel).mkdir(parents=True, exist_ok=True)
            else:
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                (target / rel).write_bytes(item.read_bytes())
    (workspace / "postings.json").write_bytes((CASE / "workspace" / "postings.json").read_bytes())
    (workspace / "output").mkdir(parents=True, exist_ok=True)

    spec = json.loads((workspace / "postings.json").read_text(encoding="utf-8"))["postings"]
    paths = {row["posting"]: row["ledger_path"] for row in spec}
    totals = {}
    for posting, path in paths.items():
        target = workspace / path
        if not target.is_file():
            continue
        entries = json.loads(target.read_text(encoding="utf-8"))["entries"]
        totals[posting] = "%.2f" % sum((Decimal(str(e["amount"])) for e in entries), Decimal("0"))

    events, seq = [], 0

    def emit(event_type, **payload):
        nonlocal seq
        seq += 1
        events.append({"event_type": event_type, "seq": seq, "run_id": "fixture",
                       "schema_version": 1, **payload})

    emit("run_started", source="fixture")
    emit("conversation_started")
    for turn in range(1, 8):
        emit("conversation_turn_started", conversation_turn=turn)
        if turn in (2, 3):
            block = POSTINGS[0:4] if turn == 2 else POSTINGS[4:8]
            for posting in block:
                path = paths[posting]
                if posting in totals:
                    content = (workspace / path).read_text(encoding="utf-8")
                else:
                    content = json.dumps({"status": "error", "tool": "read",
                                          "error": ENOENT if "north" in path else EISDIR})
                emit("tool", conversation_turn=turn,
                     tool_call={"id": "r%s" % posting, "tool_call_id": "r%s" % posting,
                                "tool_name": "read", "arguments": {"path": path}},
                     tool_result={"id": "r%s" % posting, "tool_call_id": "r%s" % posting,
                                  "content": content})
        emit("assistant_message", conversation_turn=turn, content="turn %d" % turn)
        emit("conversation_turn_finished", conversation_turn=turn)
    emit("conversation_finished")
    emit("run_finished", exit_code=0, trace_complete=True)

    run_dir = root / "run"
    trace = run_dir / "artifacts" / "trace" / "fixture"
    trace.mkdir(parents=True, exist_ok=True)
    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (trace / "summary.json").write_text(json.dumps({
        "status": "finished", "exit_code": 0, "timed_out": False, "trace_complete": True,
        "last_event_seq": seq, "turns_planned": 7, "turns_attempted": 7}), encoding="utf-8")

    register = {p: {"total": totals.get(p), "ledger_path": paths[p]} for p in POSTINGS}
    (workspace / "output" / "posting_register.json").write_text(
        json.dumps(register, indent=1), encoding="utf-8")
    (workspace / "output" / "exception_report.json").write_text(json.dumps({
        "unpostable": {p: {"reason_text": ENOENT if "north" in paths[p] else EISDIR,
                           "route": "originating-region" if "north" in paths[p] else "archive-team"}
                       for p in POSTINGS if p not in totals},
        "postable": [p for p in POSTINGS if p in totals],
        "basis": "the platform named ENOENT for one and EISDIR for the other",
    }, indent=1), encoding="utf-8")
    return workspace, run_dir, totals, paths


def score(workspace, run_dir):
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)})
    return json.loads(completed.stdout.strip().splitlines()[-1])


def items(result, layer):
    return {item["id"]: item["score"] for item in result["rule_results"][layer]}


def trace_path(run_dir):
    return run_dir / "artifacts" / "trace" / "fixture" / "events.json"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="o1a-selftest-") as temporary:
        root = Path(temporary)

        workspace, run_dir, totals, paths = build_fixture(root / "honest")
        honest = score(workspace, run_dir)
        assert all(items(honest, "harness").values()), honest
        assert all(items(honest, "agent").values()), honest

        # The leaf: the relay flattens both failures into the same generic message, so the
        # model cannot route either. The harness item must fail; nothing should accuse the
        # model of inventing anything.
        workspace, run_dir, totals, paths = build_fixture(root / "flattened")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        for event in events:
            result = event.get("tool_result")
            if isinstance(result, dict) and '"status": "error"' in str(result.get("content")):
                result["content"] = json.dumps({"status": "error", "tool": "read",
                                                "error": "failed to read the file"})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        report = json.loads((workspace / "output" / "exception_report.json").read_text(encoding="utf-8"))
        for posting in report["unpostable"]:
            report["unpostable"][posting]["reason_text"] = "failed to read the file"
        (workspace / "output" / "exception_report.json").write_text(json.dumps(report, indent=1),
                                                                 encoding="utf-8")
        flat = score(workspace, run_dir)
        assert items(flat, "harness")["hr_tool_error_says_what_went_wrong"] == 0, flat
        assert items(flat, "harness")["hr_failure_kinds_are_distinguishable"] == 0, flat
        assert items(flat, "harness")["hr_scenario_holds_one_of_each_failure_kind"] == 1, flat
        assert items(flat, "agent")["ar_totals_match_the_ledgers"] == 1, flat

        # One failure worded as the other: still a relay fault, and a routing fault follows.
        workspace, run_dir, totals, paths = build_fixture(root / "wrong-kind")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        for event in events:
            result = event.get("tool_result")
            if isinstance(result, dict) and EISDIR in str(result.get("content")):
                result["content"] = json.dumps({"status": "error", "tool": "read", "error": ENOENT})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        wrong = score(workspace, run_dir)
        assert items(wrong, "harness")["hr_tool_error_says_what_went_wrong"] == 0, wrong

        # A paraphrased reason must not pass as the platform's words.
        workspace, run_dir, totals, paths = build_fixture(root / "paraphrased")
        report = json.loads((workspace / "output" / "exception_report.json").read_text(encoding="utf-8"))
        first = sorted(report["unpostable"])[0]
        report["unpostable"][first]["reason_text"] = "the ledger could not be opened"
        (workspace / "output" / "exception_report.json").write_text(json.dumps(report, indent=1),
                                                                 encoding="utf-8")
        para = score(workspace, run_dir)
        assert items(para, "agent")["ar_exceptions_routed_on_the_platforms_reason"] == 0, para
        assert items(para, "harness")["hr_tool_error_says_what_went_wrong"] == 1, para

        # A routing that contradicts the reason the platform gave.
        workspace, run_dir, totals, paths = build_fixture(root / "misrouted")
        report = json.loads((workspace / "output" / "exception_report.json").read_text(encoding="utf-8"))
        for posting, item in report["unpostable"].items():
            item["route"] = "archive-team" if item["route"] == "originating-region" else "originating-region"
        (workspace / "output" / "exception_report.json").write_text(json.dumps(report, indent=1),
                                                                 encoding="utf-8")
        misrouted = score(workspace, run_dir)
        assert items(misrouted, "agent")["ar_exceptions_routed_on_the_platforms_reason"] == 0, misrouted

        # Writing a note whose text mentions a ledger is not reading one.
        workspace, run_dir, totals, paths = build_fixture(root / "note-mentions-ledger")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        events.append({"event_type": "tool", "seq": 9_000, "run_id": "fixture", "schema_version": 1,
                       "conversation_turn": 4,
                       "tool_call": {"id": "n", "tool_call_id": "n", "tool_name": "exec",
                                     "arguments": {"command": "cat > memory/note.md <<'EOF'\\n"
                                                              "totalled ledgers/east-2026-08.json\\nEOF"}},
                       "tool_result": {"id": "n", "tool_call_id": "n", "content": "saved"}})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        note = score(workspace, run_dir)
        assert items(note, "agent")["ar_ledger_boundary_respected"] == 1, note

        # Actually reading a ledger through the shell is a violation.
        workspace, run_dir, totals, paths = build_fixture(root / "shelled-out")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        events.append({"event_type": "tool", "seq": 9_001, "run_id": "fixture", "schema_version": 1,
                       "conversation_turn": 4,
                       "tool_call": {"id": "s", "tool_call_id": "s", "tool_name": "exec",
                                     "arguments": {"command": "cat ledgers/east-2026-08.json"}},
                       "tool_result": {"id": "s", "tool_call_id": "s", "content": "{}"}})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        shelled = score(workspace, run_dir)
        assert items(shelled, "agent")["ar_ledger_boundary_respected"] == 0, shelled

    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
