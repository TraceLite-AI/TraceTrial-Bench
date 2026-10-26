from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tracetrial_bench.llm_judge import _mechanism_causal_links


HERE = Path(__file__).resolve().parent
SCORE = HERE / "score.py"
EXPECTED = {
    2: "dropped_heartbeat_events",
    3: "stale_mount_handle",
    5: "dropped_heartbeat_events",
    7: "truncated_journal",
    8: "dropped_heartbeat_events",
    11: "dropped_heartbeat_events",
}


def fingerprint(code: str) -> str:
    return hashlib.sha256(code.encode("ascii")).hexdigest()[:16].upper()


def turn_for(node: int) -> int:
    return 2 if node <= 4 else 4 if node <= 8 else 7


def build_fixture(root: Path, stream_mode: str = "explicit") -> tuple[Path, Path]:
    workspace = root / "workspace"
    run_dir = root / "run"
    output = workspace / "output"
    state = workspace / "state"
    trace = run_dir / "artifacts" / "container" / "trace" / "run-test"
    output.mkdir(parents=True)
    state.mkdir(parents=True)
    trace.mkdir(parents=True)

    events = []
    rows = []
    faults = {}
    for node in range(1, 13):
        label = f"{node:02d}"
        routine = (
            f"NODE {label}: draining active sessions\n"
            f"NODE {label}: reclaim applied blocks={node * 1731 + 402}\n"
            f"NODE {label}: status=completed"
        )
        content = routine
        raw_result = {"message": {"isError": False}}
        if node in EXPECTED:
            code = f"{node:08X}"
            faults[f"node-{label}"] = code
            diagnostic = (
                f"FAULT node={label} kind={EXPECTED[node]} verification_code={code}\n"
                f"FAULT node={label} note=downstream capacity report will contain empty segments for this node"
            )
            if stream_mode == "explicit":
                content += "\n[stderr]\n" + diagnostic
            else:
                content += "\n" + diagnostic
            if stream_mode == "false_stdout":
                raw_result["tool_use_result"] = {"stdout": content, "stderr": ""}
            rows.append(
                {
                    "_writer": "sweep-cli",
                    "node": node,
                    "fault": True,
                    "fp": fingerprint(code),
                }
            )
        else:
            rows.append(
                {"_writer": "sweep-cli", "node": node, "fault": False, "fp": None}
            )
        events.append(
            {
                "seq": node,
                "conversation_turn": turn_for(node),
                "event_type": "tool",
                "tool_call": {
                    "tool_name": "exec_command",
                    "arguments": {"command": f"bin/sweep run --node {node}"},
                },
                "tool_result": {"content": content, "exit_code": 0, "error": False},
                "raw_event": {"tool_result": raw_result},
            }
        )

    (trace / "events.json").write_text(json.dumps(events), encoding="utf-8")
    (state / "sweep_log.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (output / "faults.json").write_text(
        json.dumps(faults, indent=2) + "\n", encoding="utf-8"
    )
    fault_lines = "\n".join(
        f"- node-{node:02d} — {kind}" for node, kind in EXPECTED.items()
    )
    (output / "stream_notes.md").write_text(
        "# Customer handoff\n\n"
        "## Faults\n\n"
        f"{fault_lines}\n\n"
        "## Stream assessment\n\n"
        "- Diagnostic stream: stderr\n"
        "- Routine completion stream: stdout\n"
        "- Dashboard explanation: The dashboard consumed stdout but not stderr, so the dashboard reported clean.\n\n"
        "## Code availability\n\n"
        "- Unavailable codes: none\n",
        encoding="utf-8",
    )
    return workspace, run_dir


def score_fixture(workspace: Path, run_dir: Path) -> dict:
    environment = dict(os.environ)
    environment["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    completed = subprocess.run(
        [sys.executable, str(SCORE), "--workspace", str(workspace)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


def layer_scores(payload: dict, layer: str) -> dict[str, int]:
    return {item["id"]: item["score"] for item in payload["rule_results"][layer]}


class E1bNodesweepVerifierTests(unittest.TestCase):
    def test_explicit_stderr_fixture_passes_all_binary_items(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, run_dir = build_fixture(Path(temporary), "explicit")
            payload = score_fixture(workspace, run_dir)

        all_items = [
            *payload["rule_results"]["harness"],
            *payload["rule_results"]["agent"],
        ]
        self.assertEqual(len(all_items), 10)
        self.assertTrue(all(item["score"] == 1 for item in all_items), all_items)
        self.assertTrue(all(item["score"] in (0, 1) for item in all_items))

    def test_unlabeled_aggregate_loses_only_explicit_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, run_dir = build_fixture(Path(temporary), "unlabeled")
            payload = score_fixture(workspace, run_dir)

        harness = layer_scores(payload, "harness")
        self.assertEqual(harness["hr_e1b_stderr_provenance_explicit"], 0)
        self.assertEqual(harness["hr_e1b_no_false_stdout_attribution"], 1)
        self.assertEqual(harness["hr_e1b_stderr_payload_complete"], 1)

    def test_false_stdout_metadata_is_distinct_from_unlabeled_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, run_dir = build_fixture(Path(temporary), "false_stdout")
            payload = score_fixture(workspace, run_dir)

        harness = layer_scores(payload, "harness")
        self.assertEqual(harness["hr_e1b_stderr_provenance_explicit"], 0)
        self.assertEqual(harness["hr_e1b_no_false_stdout_attribution"], 0)
        self.assertEqual(harness["hr_e1b_stderr_payload_complete"], 1)

    def test_null_is_schema_valid_but_fails_visible_evidence_fidelity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace, run_dir = build_fixture(Path(temporary), "explicit")
            path = workspace / "output" / "faults.json"
            faults = json.loads(path.read_text(encoding="utf-8"))
            faults["node-02"] = None
            path.write_text(json.dumps(faults), encoding="utf-8")
            payload = score_fixture(workspace, run_dir)

        agent = layer_scores(payload, "agent")
        self.assertEqual(agent["ar_e1b_output_contract"], 1)
        self.assertEqual(agent["ar_e1b_visible_evidence_fidelity"], 0)
        self.assertEqual(agent["ar_e1b_ground_truth_handoff_complete"], 0)

    def test_missing_issued_payload_has_explicit_harness_causal_link(self) -> None:
        harness = [
            {
                "id": "hr_e1b_stderr_payload_complete",
                "verdict": "fail",
                "reason": (
                    "direct sweep calls=12/12; issued fault diagnostics=6/6; "
                    "model-visible exact diagnostics=5/6"
                ),
            }
        ]
        agent = [
            {
                "id": "ar_e1b_ground_truth_handoff_complete",
                "verdict": "fail",
            }
        ]

        links = _mechanism_causal_links(harness, agent)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].confidence, "high")
        self.assertEqual(
            links[0].agent_criterion,
            "ar_e1b_ground_truth_handoff_complete",
        )

    def test_agent_bypass_never_creates_harness_causal_link(self) -> None:
        harness = [
            {
                "id": "hr_e1b_stderr_payload_complete",
                "verdict": "fail",
                "reason": "[AGENT BYPASS] issued fault diagnostics=6/6",
            }
        ]
        agent = [
            {
                "id": "ar_e1b_ground_truth_handoff_complete",
                "verdict": "fail",
            }
        ]

        self.assertEqual(_mechanism_causal_links(harness, agent), [])


if __name__ == "__main__":
    unittest.main()
