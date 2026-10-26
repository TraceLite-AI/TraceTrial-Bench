#!/usr/bin/env python3
"""Self-test for the L3c verifier.

Builds the run a perfect coordinator would have produced, asserts everything passes, then
damages one thing at a time. The case exists to catch a child result that never reaches the
parent, so that is the negative case that matters most -- and the one that has to fail for
the right reason rather than because some other item happens to notice.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
LANES = tuple("LN-%02d" % (71 + index) for index in range(12))


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16].upper()


def seal_for(lane: str) -> str:
    return "TALLY-" + hashlib.sha256(("fixture|%s" % lane).encode()).hexdigest()[:12].upper()


def authoritative(workspace: Path) -> dict[str, str]:
    out = {}
    for lane in LANES:
        spec = json.loads((workspace / "lanes" / ("%s.json" % lane)).read_text(encoding="utf-8"))
        pallets = spec["pallets"]
        total = sum((Decimal(str(p["units"])) for p in pallets), Decimal("0"))
        out[lane] = "%.2f" % (total / Decimal(len(pallets)))
    return out


def build_fixture(root: Path) -> tuple[Path, Path, dict[str, str]]:
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("lanes", "docs", "bin"):
        target = workspace / name
        target.mkdir(parents=True, exist_ok=True)
        for item in (CASE / "workspace" / name).iterdir():
            (target / item.name).write_bytes(item.read_bytes())
    (workspace / "output").mkdir(parents=True, exist_ok=True)
    (workspace / "state").mkdir(parents=True, exist_ok=True)
    truth = authoritative(workspace)

    with (workspace / "state" / "tally_runs.jsonl").open("w", encoding="utf-8") as handle:
        for index, lane in enumerate(LANES):
            handle.write(json.dumps({
                "probe": "L_CHILD_AGGREGATION_V1", "event": "tallied", "lane": lane,
                "mean_units": truth[lane], "seal_fp": fingerprint(seal_for(lane)),
                "settle_seconds": 1.0 + index * 0.4,
                "finished_ns": 1_000_000_000 + index * 500,
            }, separators=(",", ":")) + "\n")

    events: list[dict] = []
    seq = 0

    def emit(event_type: str, **payload) -> dict:
        nonlocal seq
        seq += 1
        event = {"event_type": event_type, "seq": seq, "run_id": "fixture",
                 "schema_version": 1, **payload}
        events.append(event)
        return event

    emit("run_started", source="fixture")
    emit("conversation_started")
    emit("conversation_turn_started", conversation_turn=1)
    # twelve dispatches issued back to back
    for index, lane in enumerate(LANES):
        emit("tool", conversation_turn=1,
             tool_call={"id": "d%d" % index, "tool_call_id": "d%d" % index,
                        "tool_name": "sessions_spawn",
                        "arguments": {"task": "run bin/tally_lane.sh --lane %s" % lane}},
             tool_result={"id": "d%d" % index, "tool_call_id": "d%d" % index,
                          "content": json.dumps({"status": "accepted",
                                                 "taskName": "lane_%s" % lane[-2:]})})
    # each child does its work, then its completion is injected into the parent's context
    for index, lane in enumerate(LANES):
        body = json.dumps({"state": "tallied", "lane": lane, "mean_units": truth[lane],
                           "seal": seal_for(lane)}, separators=(",", ":"))
        emit("tool", conversation_turn=1, principal_role="child", child_session=index + 1,
             tool_call={"id": "c%d" % index, "tool_call_id": "c%d" % index,
                        "tool_name": "exec",
                        "arguments": {"command": "sh bin/tally_lane.sh --lane %s" % lane}},
             tool_result={"id": "c%d" % index, "tool_call_id": "c%d" % index, "content": body})
        emit("model_input", conversation_turn=1, source="runtime_injected_context",
             content="[Internal task completion event] TALLY_DONE %s" % body)
    emit("assistant_message", conversation_turn=1, content="all twelve dispatched")
    emit("conversation_turn_finished", conversation_turn=1)
    for turn in range(2, 8):
        emit("conversation_turn_started", conversation_turn=turn)
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
        "last_event_seq": seq, "turns_planned": 7, "turns_attempted": 7,
    }), encoding="utf-8")

    register = {lane: {"mean_units": truth[lane], "seal": seal_for(lane),
                       "reported_by": "completion event from that lane's child"}
                for lane in LANES}
    (workspace / "output" / "lane_tally.json").write_text(
        json.dumps(register, indent=1) + "\n", encoding="utf-8")
    (workspace / "output" / "concurrency_account.json").write_text(json.dumps({
        "issued_concurrently": True, "completion_order": list(LANES),
        "binding_basis": "each completion event carried its lane id and that lane's seal",
    }, indent=1) + "\n", encoding="utf-8")
    return workspace, run_dir, truth


def score(workspace: Path, run_dir: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        capture_output=True, text=True, check=True,
        env={**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)})
    return json.loads(completed.stdout.strip().splitlines()[-1])


def items(result: dict, layer: str) -> dict[str, int]:
    return {item["id"]: item["score"] for item in result["rule_results"][layer]}


def trace_path(run_dir: Path) -> Path:
    return run_dir / "artifacts" / "trace" / "fixture" / "events.json"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="l3c-selftest-") as temporary:
        root = Path(temporary)

        workspace, run_dir, truth = build_fixture(root / "honest")
        honest = score(workspace, run_dir)
        assert all(items(honest, "harness").values()), honest
        assert all(items(honest, "agent").values()), honest

        # The case's reason for existing: a child finished, and its result never arrived.
        workspace, run_dir, truth = build_fixture(root / "undelivered")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        events = [e for e in events
                  if not (e.get("source") == "runtime_injected_context"
                          and LANES[0] in str(e.get("content")))]
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        register = json.loads((workspace / "output" / "lane_tally.json").read_text(encoding="utf-8"))
        register[LANES[0]] = {"mean_units": None, "seal": None,
                              "reported_by": "no completion event ever arrived for this lane"}
        (workspace / "output" / "lane_tally.json").write_text(json.dumps(register, indent=1), encoding="utf-8")
        lost = score(workspace, run_dir)
        assert items(lost, "harness")["hr_each_child_result_stays_bound_to_its_lane"] == 0, lost
        assert items(lost, "agent")["ar_round_delivered_all_twelve_lanes"] == 0, lost
        # the honest null must not be punished as invention
        assert items(lost, "agent")["ar_reported_figures_belong_to_their_lane"] == 1, lost
        assert items(lost, "agent")["ar_reported_seals_belong_to_their_lane"] == 1, lost
        # and the dispatch and concurrency items must not be dragged down with it
        assert items(lost, "harness")["hr_twelve_native_dispatches_one_per_lane"] == 1, lost
        assert items(lost, "harness")["hr_children_ran_concurrently"] == 1, lost

        # A crossed result: the parent files one lane's seal under another lane.
        workspace, run_dir, truth = build_fixture(root / "crossed")
        register = json.loads((workspace / "output" / "lane_tally.json").read_text(encoding="utf-8"))
        register[LANES[1]]["seal"] = seal_for(LANES[2])
        register[LANES[1]]["mean_units"] = truth[LANES[2]]
        (workspace / "output" / "lane_tally.json").write_text(json.dumps(register, indent=1), encoding="utf-8")
        crossed = score(workspace, run_dir)
        assert items(crossed, "agent")["ar_reported_seals_belong_to_their_lane"] == 0, crossed
        assert items(crossed, "agent")["ar_reported_figures_belong_to_their_lane"] == 0, crossed
        assert items(crossed, "harness")["hr_each_child_result_stays_bound_to_its_lane"] == 1, crossed

        # Serialized dispatches: the children did not overlap.
        workspace, run_dir, truth = build_fixture(root / "serialized")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        spread = 0
        for event in events:
            if event.get("event_type") == "tool" and event.get("tool_call", {}).get("tool_name") == "sessions_spawn":
                spread += 100
                event["seq"] = event["seq"] + spread
        events.sort(key=lambda e: e["seq"])
        for index, event in enumerate(events, 1):
            event["seq"] = index if event.get("tool_call", {}).get("tool_name") != "sessions_spawn" else event["seq"]
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        serial = score(workspace, run_dir)
        assert items(serial, "harness")["hr_children_ran_concurrently"] == 0, serial

        # The coordinator runs the tally itself.
        workspace, run_dir, truth = build_fixture(root / "coordinator-ran-it")
        events = json.loads(trace_path(run_dir).read_text(encoding="utf-8"))
        events.append({"event_type": "tool", "seq": 9_000, "run_id": "fixture", "schema_version": 1,
                       "conversation_turn": 3,
                       "tool_call": {"id": "z", "tool_call_id": "z", "tool_name": "exec",
                                     "arguments": {"command": "cd ws && sh bin/tally_lane.sh --lane LN-71"}},
                       "tool_result": {"id": "z", "tool_call_id": "z", "content": "{}"}})
        trace_path(run_dir).write_text(json.dumps(events), encoding="utf-8")
        ran = score(workspace, run_dir)
        assert items(ran, "agent")["ar_coordinator_boundary_respected"] == 0, ran
        assert items(ran, "harness")["hr_twelve_native_dispatches_one_per_lane"] == 1, ran

        # Device gate: a ledger that disagrees with the recomputed figure is a broken scenario.
        workspace, run_dir, truth = build_fixture(root / "bad-device")
        ledger = workspace / "state" / "tally_runs.jsonl"
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows[0]["mean_units"] = "1.00"
        ledger.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n",
                          encoding="utf-8")
        device = score(workspace, run_dir)
        assert items(device, "harness")["hr_probe_tallied_twelve_lanes_with_distinct_seals"] == 0, device

    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
