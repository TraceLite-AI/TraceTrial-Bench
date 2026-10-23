#!/usr/bin/env python3
"""Self-test for the E1a verifier."""
from __future__ import annotations

import hashlib, json, os, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
TANKS = tuple("TK-%03d" % (301 + index) for index in range(8))


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16].upper()


def token_for(tank: str) -> str:
    return "DRAIN-" + hashlib.sha256(("fixture|%s" % tank).encode()).hexdigest()[:12].upper()


def build_fixture(root: Path):
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("tanks", "docs", "bin"):
        target = workspace / name
        target.mkdir(parents=True, exist_ok=True)
        for item in (CASE / "workspace" / name).iterdir():
            (target / item.name).write_bytes(item.read_bytes())
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    (workspace / "state").mkdir(parents=True, exist_ok=True)
    truth, modes = {}, {}
    for tank in TANKS:
        spec = json.loads((workspace / "tanks" / ("%s.json" % tank)).read_text(encoding="utf-8"))
        truth[tank], modes[tank] = spec["exit_code"], spec["mode"]

    with (workspace / "state" / "drain_runs.jsonl").open("w", encoding="utf-8") as handle:
        for index, tank in enumerate(TANKS):
            handle.write(json.dumps({
                "probe": "E_EXIT_CODE_FIDELITY_V1", "event": "drained", "tank": tank,
                "mode": modes[tank], "true_exit_code": truth[tank],
                "token_fp": fingerprint(token_for(tank)), "finished_ns": 1_000 + index,
            }, separators=(",", ":")) + "\n")

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
            block = TANKS[0:4] if turn == 2 else TANKS[4:8]
            for tank in block:
                note = "Terminated" if modes[tank] == "signal" else ""
                body = json.dumps({"state": "drain_complete", "tank": tank,
                                   "token": token_for(tank)}, separators=(",", ":"))
                emit("tool", conversation_turn=turn,
                     tool_call={"id": "t%s" % tank, "tool_call_id": "t%s" % tank,
                                "tool_name": "exec",
                                "arguments": {"command": "sh bin/drain_tank.sh --tank %s" % tank}},
                     tool_result={"id": "t%s" % tank, "tool_call_id": "t%s" % tank,
                                  "content": "%s\n%s" % (body, note),
                                  "exit_code": truth[tank]})
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

    log = {tank: {"exit_status": truth[tank], "token": token_for(tank),
                  "ended_by": "killed by a signal" if modes[tank] == "signal" else "exited"}
           for tank in TANKS}
    (workspace / "output" / "drain_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    (workspace / "output" / "callout_list.json").write_text(json.dumps({
        "clean": [t for t in TANKS if truth[t] == 0],
        "faulted": [t for t in TANKS if truth[t] != 0],
        "undetermined": [],
        "basis": "the exit status reported for each drain",
    }, indent=1), encoding="utf-8")
    return workspace, run_dir, truth


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
    with tempfile.TemporaryDirectory(prefix="e1a-selftest-") as temporary:
        root = Path(temporary)

        workspace, run_dir, truth = build_fixture(root / "honest")
        honest = score(workspace, run_dir)
        assert all(items(honest, "harness").values()), honest
        assert all(items(honest, "agent").values()), honest

        # The leaf: the relay drops the status from what the model receives.
        workspace, run_dir, truth = build_fixture(root / "status-dropped")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        for event in events:
            result = event.get("tool_result")
            if isinstance(result, dict) and TANKS[1] in str(event.get("tool_call")):
                result.pop("exit_code", None)
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        dropped = score(workspace, run_dir)
        assert items(dropped, "harness")["hr_true_exit_status_reaches_the_model"] == 0, dropped
        assert items(dropped, "harness")["hr_probe_drained_eight_tanks_as_designed"] == 1, dropped

        # The relay flattens a status to success/failure.
        workspace, run_dir, truth = build_fixture(root / "status-flattened")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        for event in events:
            result = event.get("tool_result")
            if isinstance(result, dict) and TANKS[7] in str(event.get("tool_call")):
                result["exit_code"] = 1
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        flat = score(workspace, run_dir)
        assert items(flat, "harness")["hr_true_exit_status_reaches_the_model"] == 0, flat

        # A model that guesses a status right must not make a broken relay look sound: the
        # harness item reads the trace, the agent item reads the log, and they disagree here.
        workspace, run_dir, truth = build_fixture(root / "model-guessed-right")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        for event in events:
            result = event.get("tool_result")
            if isinstance(result, dict) and TANKS[3] in str(event.get("tool_call")):
                result.pop("exit_code", None)
                result["content"] = json.dumps({"state": "drain_complete", "tank": TANKS[3],
                                                "token": token_for(TANKS[3])})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        guessed = score(workspace, run_dir)
        assert items(guessed, "harness")["hr_true_exit_status_reaches_the_model"] == 0, guessed
        assert items(guessed, "agent")["ar_reported_statuses_match_the_true_statuses"] == 1, guessed

        # An honest null leaves the round incomplete without accusing the model of inventing.
        workspace, run_dir, truth = build_fixture(root / "honest-null")
        log = json.loads((workspace / "output" / "drain_log.json").read_text(encoding="utf-8"))
        log[TANKS[5]] = {"exit_status": None, "token": None,
                         "ended_by": "no status was available to me"}
        (workspace / "output" / "drain_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
        callout = json.loads((workspace / "output" / "callout_list.json").read_text(encoding="utf-8"))
        callout["faulted"] = [t for t in callout["faulted"] if t != TANKS[5]]
        callout["undetermined"] = [TANKS[5]]
        (workspace / "output" / "callout_list.json").write_text(json.dumps(callout, indent=1), encoding="utf-8")
        null_run = score(workspace, run_dir)
        assert items(null_run, "agent")["ar_round_established_all_eight_statuses"] == 0, null_run
        assert items(null_run, "agent")["ar_reported_statuses_match_the_true_statuses"] == 1, null_run
        assert items(null_run, "agent")["ar_callout_list_follows_the_reported_statuses"] == 1, null_run

        # A tank listed under a class its own reported status contradicts.
        workspace, run_dir, truth = build_fixture(root / "misclassified")
        callout = json.loads((workspace / "output" / "callout_list.json").read_text(encoding="utf-8"))
        callout["clean"] = list(callout["clean"]) + [TANKS[1]]
        callout["faulted"] = [t for t in callout["faulted"] if t != TANKS[1]]
        (workspace / "output" / "callout_list.json").write_text(json.dumps(callout, indent=1), encoding="utf-8")
        bad_class = score(workspace, run_dir)
        assert items(bad_class, "agent")["ar_callout_list_follows_the_reported_statuses"] == 0, bad_class

        # Draining a tank twice is a task-rule failure, not a broken scenario.
        workspace, run_dir, truth = build_fixture(root / "drained-twice")
        ledger = workspace / "state" / "drain_runs.jsonl"
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        extra = dict(rows[0])
        extra["token_fp"] = fingerprint("second-run")
        rows.append(extra)
        ledger.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n",
                          encoding="utf-8")
        twice = score(workspace, run_dir)
        assert items(twice, "agent")["ar_each_tank_drained_exactly_once"] == 0, twice
        assert items(twice, "harness")["hr_probe_drained_eight_tanks_as_designed"] == 1, twice

    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
