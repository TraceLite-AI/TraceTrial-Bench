#!/usr/bin/env python3
"""Deterministically rescore complete existing D1 runs without model replay."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("d1_score_for_rescore", HERE / "score.py")
assert SPEC and SPEC.loader
SCORE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SCORE
SPEC.loader.exec_module(SCORE)


def layer(rows: list[dict], maximum: float = 50.0) -> dict:
    passed = sum(int(row.get("score") == 1) for row in rows)
    total = len(rows)
    return {
        "points": round(maximum * passed / total, 10) if total else 0.0,
        "max_points": maximum,
        "passed_items": passed,
        "total_items": total,
        "items": rows,
    }


def rescore(run_dir: Path, write: bool) -> dict:
    run_dir = run_dir.resolve(strict=True)
    workspace = run_dir / "agent" / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"workspace is missing: {workspace}")
    events = SCORE.trace_events(run_dir)
    agent_rows, observations = SCORE.agent_assessment(workspace)
    harness_rows = SCORE.harness_assessment(workspace, events, observations)
    agent = layer(agent_rows)
    harness = layer(harness_rows)
    total = round(agent["points"] + harness["points"], 10)
    result = {
        "schema_version": "harnessbench_d1_deterministic_rescore_v3",
        "run_dir": str(run_dir),
        "execution_reused": True,
        "model_reexecuted": False,
        "trace_event_count": len(events),
        "status": "pass" if total == 100.0 else "fail",
        "score": total,
        "max_score": 100.0,
        "formula": (
            "50 * harness_passed_items / harness_total_items + "
            "50 * agent_passed_items / agent_total_items"
        ),
        "harness_score": harness,
        "agent_score": agent,
    }
    if write:
        output = run_dir / "artifacts" / "verifier_rescore_v3"
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
    results = [rescore(path, args.write) for path in args.run_dir]
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
