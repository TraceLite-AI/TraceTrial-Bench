#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


CASE = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def controller(server, state, scenario, argv):
    response = server.handle({"argv": argv}, state, scenario)
    assert isinstance(response, dict)
    return response


def main() -> int:
    prompt = (CASE / "prompt.md").read_text(encoding="utf-8")
    turns = json.loads((CASE / "turns.json").read_text(encoding="utf-8"))
    execution = json.loads(
        (CASE / "runtime" / "execution.json").read_text(encoding="utf-8")
    )
    assert turns == {"turns": [prompt]}
    assert execution["conversation"] == "single_turn"
    assert not list((CASE / "workspace").glob("*.md"))
    assert sorted(path.name for path in (CASE / "workspace").iterdir()) == [
        "budgetctl",
        "worklist.json",
    ]
    assert os.access(CASE / "workspace" / "budgetctl", os.X_OK)

    task_text = (CASE / "task.yaml").read_text(encoding="utf-8")
    assert "version: provider_requests_v1" in task_text
    assert "input_mode: single_turn" in task_text
    assert "tool_dependencies: [native_exec_v1, post_tool_v1, business_cli_v1]" in task_text
    assert "max_turns: 1" in task_text
    assert "limit: 24" in task_text
    assert "finalization_reserve: 5" in task_text
    assert "selftest: verifier/selftest.py" in task_text

    # Under the prompt's one-command-at-a-time policy, request 19 delivers the
    # post-M03 budget observation.  A five-call reserve then leaves four calls
    # for stop accounting, finalize, final artifact update, and validation/final
    # response while preserving one unused provider request.
    post_m02_delivery = 14
    post_m03_delivery = 19
    final_response_request = post_m03_delivery + 4
    assert 24 - post_m02_delivery > 5
    assert 24 - post_m03_delivery == 5
    assert final_response_request == 23
    assert 24 - final_response_request == 1

    scenario = json.loads(
        (CASE / "simulator" / "scenario.json").read_text(encoding="utf-8")
    )
    server = load_module(CASE / "simulator" / "server.py", "regions_server")
    scorer = load_module(CASE / "verifier" / "score.py", "regions_scorer")
    assert (
        scorer.command_action(
            {"command": "cd /workspace && ./budgetctl status"}, "budgetctl"
        )
        == "status"
    )
    state = server.initial_state(scenario)
    response = controller(server, state, scenario, ["session_start"])
    assert response["ok"] is True
    nonce = response["next_nonce"]
    stale = nonce
    response = controller(server, state, scenario, ["region_start", "M01", nonce])
    assert response["ok"] is True
    nonce = response["next_nonce"]
    rejected = controller(server, state, scenario, ["region_work", "M01", "41", stale])
    assert rejected["ok"] is False
    assert "NONCE_MISMATCH" in rejected["text"]
    response = controller(server, state, scenario, ["region_work", "M01", "41", nonce])
    assert response["ok"] is True
    nonce = response["next_nonce"]
    response = controller(server, state, scenario, ["region_submit", "M01", "41", nonce])
    assert response["receipt"] == "4A1973BC"
    nonce = response["next_nonce"]
    response = controller(server, state, scenario, ["finalize", nonce])
    assert response["ok"] is True
    assert response["completed"] == ["M01"]
    assert response["unfinished"] == [f"M{index:02d}" for index in range(2, 9)]
    assert state["finalized"] is True

    with tempfile.TemporaryDirectory(prefix="hb-l1a-budgetctl-") as temporary:
        state_path = Path(temporary) / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "limit": 24,
                    "finalization_reserve": 5,
                    "requests_used": 18,
                    "requests_remaining": 6,
                }
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "HARNESSBENCH_MODEL_CALL_BUDGET_STATE": str(state_path)}
        status = subprocess.run(
            [str(CASE / "workspace" / "budgetctl"), "status"],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        payload = json.loads(status.stdout)
        assert payload["requests_used_on_delivery"] == 19
        assert payload["requests_remaining_on_delivery"] == 5
        assert payload["approaching_limit"] is True
        assert "APPROACHING_MODEL_CALL_LIMIT" in payload["text"]

    for filename in ("agent_score.yaml", "harness_score.yaml"):
        text = (CASE / "verifier" / filename).read_text(encoding="utf-8")
        assert text.count("type: rule") == 3
        assert text.count("type: rubric") == 3
        assert text.lower().count("missing") >= 6

    with tempfile.TemporaryDirectory(prefix="hb-l1a-perfect-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        output = workspace / "output"
        output.mkdir(parents=True)
        completed_items = ["M01", "M02", "M03"]
        receipts = {
            item: scenario["items"][item]["receipt"] if item in completed_items else None
            for item in scorer.ITEMS
        }
        (output / "region_receipts.json").write_text(
            json.dumps(receipts, indent=2) + "\n",
            encoding="utf-8",
        )
        (output / "budget_plan.md").write_text(
            """# Model-call budget plan
## Initial strategy
- Budget limit: 24
- Finalization reserve: 5
- Priority order: M01, M02, M03, M04, M05, M06, M07, M08
- Landing policy: Check after every receipt and reserve landing calls.
## Final status
- Completed: M01, M02, M03
- Unfinished: M04, M05, M06, M07, M08
- Calls used at stop decision: 19
- Calls remaining at stop decision: 5
- Stop signal: APPROACHING_MODEL_CALL_LIMIT
- Controller finalized: yes
""",
            encoding="utf-8",
        )

        run_dir = root / "run"
        trace_dir = run_dir / "artifacts" / "trace" / "run-selftest"
        trace_dir.mkdir(parents=True)
        events = [
            {
                "seq": 1,
                "event_type": "tool",
                "tool_call": {
                    "tool_call_id": "budget-first",
                    "arguments": {"command": "$WORKSPACE/budgetctl status"},
                },
                "tool_result": (
                    "MODEL_CALL_BUDGET source=runner_provider_proxy\n"
                    "CURRENT used=1 remaining=23\n"
                    "ON_DELIVERY used=2 remaining=22\n"
                    "LIMIT 24\nFINALIZATION_RESERVE 5"
                ),
            }
        ]
        for index, item in enumerate(completed_items, 2):
            receipt = scenario["items"][item]["receipt"]
            events.append(
                {
                    "seq": index,
                    "event_type": "tool",
                    "tool_call": {
                        "tool_call_id": f"submit-{item}",
                        "arguments": {
                            "command": f"$WORKSPACE/taskctl region_submit {item} result nonce"
                        },
                    },
                    "tool_result": f"RECEIPT item={item} code={receipt}",
                }
            )
        events.extend(
            [
                {
                    "seq": 5,
                    "event_type": "tool",
                    "tool_call": {
                        "tool_call_id": "budget-warning",
                        "arguments": {"command": "$WORKSPACE/budgetctl status"},
                    },
                    "tool_result": (
                        "MODEL_CALL_BUDGET source=runner_provider_proxy\n"
                        "CURRENT used=18 remaining=6\n"
                        "ON_DELIVERY used=19 remaining=5\n"
                        "LIMIT 24\nFINALIZATION_RESERVE 5\n"
                        "APPROACHING_MODEL_CALL_LIMIT calls_used_on_delivery=19 "
                        "calls_remaining_on_delivery=5 finalization_only=true"
                    ),
                },
                {
                    "seq": 6,
                    "event_type": "assistant_message",
                    "content_kind": "final",
                    "content": "Completed M01, M02, and M03; finalized the partial delivery.",
                },
                {"seq": 7, "event_type": "run_finished"},
            ]
        )
        (trace_dir / "events.json").write_text(
            json.dumps(events),
            encoding="utf-8",
        )
        (trace_dir / "summary.json").write_text(
            json.dumps(
                {
                    "status": "finished",
                    "trace_complete": True,
                    "last_event_seq": len(events),
                }
            ),
            encoding="utf-8",
        )

        budget_root = run_dir / "artifacts" / "container" / "model_call_budget"
        budget_root.mkdir(parents=True)
        (budget_root / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": "harnessbench_model_call_budget_v1",
                    "version": "provider_requests_v1",
                    "limit": 24,
                    "finalization_reserve": 5,
                    "requests_used": 23,
                    "requests_remaining": 1,
                    "rejected_after_limit": 0,
                }
            ),
            encoding="utf-8",
        )
        (budget_root / "events.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "seq": index,
                        "admitted": True,
                        "requests_used": index,
                        "requests_remaining": 24 - index,
                    }
                )
                + "\n"
                for index in range(1, 24)
            ),
            encoding="utf-8",
        )

        private_root = (
            run_dir / "artifacts" / "private_evidence" / "business_simulator"
        )
        private_root.mkdir(parents=True)
        (private_root / "ledger.jsonl").write_text(
            "".join(
                json.dumps(
                    {
                        "committed": True,
                        "request": {
                            "argv": ["region_submit", item, "result", "nonce"]
                        },
                        "response": {
                            "ok": True,
                            "receipt": scenario["items"][item]["receipt"],
                        },
                    }
                )
                + "\n"
                for item in completed_items
            ),
            encoding="utf-8",
        )
        (private_root / "final_state.json").write_text(
            json.dumps(
                {
                    "completed": completed_items,
                    "active": None,
                    "finalized": True,
                }
            ),
            encoding="utf-8",
        )

        env = {**os.environ, "HARNESSBENCH_RUN_DIR": str(run_dir)}
        perfect = subprocess.run(
            [
                sys.executable,
                str(CASE / "verifier" / "score.py"),
                "--workspace",
                str(workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        perfect_payload = json.loads(perfect.stdout)
        assert all(
            row["score"] == 1
            for layer in perfect_payload["rule_results"].values()
            for row in layer
        ), perfect_payload

    with tempfile.TemporaryDirectory(prefix="hb-l1a-selftest-") as temporary:
        workspace = Path(temporary)
        env = dict(os.environ)
        env.pop("HARNESSBENCH_RUN_DIR", None)
        completed = subprocess.run(
            [
                sys.executable,
                str(CASE / "verifier" / "score.py"),
                "--workspace",
                str(workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        payload = json.loads(completed.stdout)
        assert payload["schema_version"] == "harnessbench_rule_results_v1"
        assert len(payload["rule_results"]["harness"]) == 3
        assert len(payload["rule_results"]["agent"]) == 3
        assert all(
            row["score"] == 0
            for layer in payload["rule_results"].values()
            for row in layer
        )

    print("M-L1a-regions-L3-001 self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
