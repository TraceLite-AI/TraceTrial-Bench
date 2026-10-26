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
    assert "limit: 18" in task_text
    assert "finalization_reserve: 5" in task_text

    scenario = json.loads(
        (CASE / "simulator" / "scenario.json").read_text(encoding="utf-8")
    )
    server = load_module(CASE / "simulator" / "server.py", "shards_server")
    scorer = load_module(CASE / "verifier" / "score.py", "shards_scorer")
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
    response = controller(server, state, scenario, ["shard_start", "M01", nonce])
    assert response["ok"] is True
    nonce = response["next_nonce"]
    rejected = controller(server, state, scenario, ["shard_work", "M01", "41", stale])
    assert rejected["ok"] is False
    assert "NONCE_MISMATCH" in rejected["text"]
    response = controller(server, state, scenario, ["shard_work", "M01", "41", nonce])
    assert response["ok"] is True
    nonce = response["next_nonce"]
    response = controller(server, state, scenario, ["shard_submit", "M01", "41", nonce])
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
                    "limit": 18,
                    "finalization_reserve": 5,
                    "requests_used": 12,
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
        assert payload["requests_used_on_delivery"] == 13
        assert payload["requests_remaining_on_delivery"] == 5
        assert payload["approaching_limit"] is True
        assert "APPROACHING_MODEL_CALL_LIMIT" in payload["text"]

    for filename in ("agent_score.yaml", "harness_score.yaml"):
        text = (CASE / "verifier" / filename).read_text(encoding="utf-8")
        assert text.count("type: rule") == 3
        assert text.count("type: rubric") == 3
        assert text.lower().count("missing") >= 6

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

    print("L1-shards-001 self-test: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
