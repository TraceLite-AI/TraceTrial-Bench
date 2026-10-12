#!/usr/bin/env python3
"""Directly rescore complete G2b trajectories without replaying the model."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("g2b_coverage_score_for_rescore", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
SCORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCORE
SPEC.loader.exec_module(SCORE)


def layer(rows: list[dict], maximum: float = 50.0) -> dict:
    if not rows or any(row.get("score") not in {0, 1} for row in rows):
        raise ValueError("all configured metrics must produce a binary 0/1 score")
    passed = sum(row["score"] for row in rows)
    return {
        "points": round(maximum * passed / len(rows), 10),
        "max_points": maximum,
        "passed_items": passed,
        "total_items": len(rows),
        "items": rows,
    }


def rescore(run_dir: Path, write: bool) -> dict:
    run_dir = run_dir.resolve(strict=True)
    workspace = run_dir / "agent" / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"workspace is missing: {workspace}")
    rules = SCORE.evaluate(workspace, run_dir)["rule_results"]
    agent = layer(rules["agent"])
    harness = layer(rules["harness"])
    total = round(agent["points"] + harness["points"], 10)
    result = {
        "schema_version": "harnessbench_g2b_deterministic_rescore_v1",
        "run_dir": str(run_dir),
        "execution_reused": True,
        "model_reexecuted": False,
        "status": "pass" if total == 100.0 else "fail",
        "score": total,
        "max_score": 100.0,
        "formula": (
            "50 * harness_passed_items / harness_total_items + "
            "50 * agent_passed_items / agent_total_items"
        ),
        "harness_score": harness,
        "agent_score": agent,
        "not_applicable": [],
    }
    if write:
        output = run_dir / "artifacts" / "verifier_rescore_v1"
        if output.is_symlink():
            raise ValueError(f"refusing to use symlink: {output}")
        output.mkdir(parents=True, exist_ok=True)
        path = output / "combined_score.json"
        if path.is_symlink():
            raise ValueError(f"refusing to replace symlink: {path}")
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result["score_path"] = str(path)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", action="append", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    print(json.dumps(
        [rescore(run_dir, args.write) for run_dir in args.run_dir],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
