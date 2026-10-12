#!/usr/bin/env python3
"""Deterministically rescore complete L8a runs without replaying the model."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
TASK_ID = "L8a-jobcursor-L3-001"
EXPECTED_COUNTS = {"harness": 7, "agent": 12}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def layer(rows: list[dict], maximum: float = 50.0) -> dict:
    if not rows or any(row.get("score") not in {0, 1} for row in rows):
        raise ValueError("all configured metrics must produce a binary 0/1 score")
    ids = [str(row.get("id", "")) for row in rows]
    if any(not item_id for item_id in ids) or len(ids) != len(set(ids)):
        raise ValueError("metric IDs must be present and unique")
    passed = sum(int(row["score"]) for row in rows)
    return {
        "points": round(maximum * passed / len(rows), 10),
        "max_points": maximum,
        "passed_items": passed,
        "total_items": len(rows),
        "items": rows,
    }


def rule_results(run_dir: Path, workspace: Path) -> dict[str, list[dict]]:
    env = dict(os.environ)
    env["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    completed = subprocess.run(
        [sys.executable, str(HERE / "score.py"), "--workspace", str(workspace)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    payload = json.loads(completed.stdout)
    rules = payload.get("rule_results")
    if not isinstance(rules, dict):
        raise ValueError("score.py did not emit rule_results")
    for name, count in EXPECTED_COUNTS.items():
        rows = rules.get(name)
        if not isinstance(rows, list) or len(rows) != count:
            raise ValueError(f"{name} metric count={len(rows) if isinstance(rows, list) else 'invalid'} expected={count}")
    return rules


def rescore(run_dir: Path, write: bool) -> dict:
    run_dir = run_dir.resolve(strict=True)
    result_path = run_dir / "result.json"
    saved_result = load_json(result_path)
    if saved_result.get("task_id") != TASK_ID:
        raise ValueError(f"run task_id={saved_result.get('task_id')!r} expected={TASK_ID!r}")
    execution = saved_result.get("execution")
    if not isinstance(execution, dict) or execution.get("status") != "finished" or execution.get("trace_complete") is not True:
        raise ValueError(f"run does not have a complete reusable execution: {run_dir}")
    workspace = run_dir / "agent" / "workspace"
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError(f"regular run workspace is missing: {workspace}")

    rules = rule_results(run_dir, workspace)
    harness = layer(rules["harness"])
    agent = layer(rules["agent"])
    total = round(harness["points"] + agent["points"], 10)
    result = {
        "schema_version": "harnessbench_l8a_deterministic_rescore_v2",
        "run_dir": str(run_dir),
        "execution_reused": True,
        "model_reexecuted": False,
        "status": "pass" if total == 100.0 else "fail",
        "score": total,
        "max_score": 100.0,
        "formula": "50 * harness_passed / harness_total + 50 * agent_passed / agent_total",
        "not_applicable": [],
        "harness_score": harness,
        "agent_score": agent,
    }
    if write:
        output = run_dir / "artifacts" / "verifier_rescore_l8a_v2"
        if output.is_symlink():
            raise ValueError(f"refusing to use symlink: {output}")
        output.mkdir(parents=True, exist_ok=True)
        path = output / "combined_score.json"
        if path.is_symlink():
            raise ValueError(f"refusing to replace symlink: {path}")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result["score_path"] = str(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps([rescore(path, args.write) for path in args.run_dir], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
