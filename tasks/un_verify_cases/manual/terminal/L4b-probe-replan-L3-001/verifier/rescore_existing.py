#!/usr/bin/env python3
"""Deterministically rescore complete L4b trajectories without model replay."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[5]))

from harnessbench.attribution import DeviceHealth, LayerVerdicts, classify
from harnessbench.llm_judge import (
    _attribution_rules,
    _finalize_layer,
    _mechanism_causal_links,
)
from harnessbench.score_config import load_score_sheet


SPEC = importlib.util.spec_from_file_location("l4b_probe_rescore", HERE / "score.py")
assert SPEC is not None and SPEC.loader is not None
SCORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCORE)


def rescore(run_dir: Path, *, write: bool) -> dict[str, Any]:
    run_dir = run_dir.resolve(strict=True)
    workspace = run_dir / "agent" / "workspace"
    if not workspace.is_dir():
        raise ValueError(f"workspace is missing: {workspace}")
    events, summary = SCORE.trace_bundle(run_dir)
    if not events:
        raise ValueError(f"structured trace evidence is required: {run_dir}")

    rules = SCORE.assess(workspace, events, summary)
    harness_sheet = load_score_sheet(HERE / "harness_score.yaml", expected_layer="harness")
    agent_sheet = load_score_sheet(HERE / "agent_score.yaml", expected_layer="agent")
    harness = _finalize_layer(harness_sheet, rules["harness"])
    agent = _finalize_layer(agent_sheet, rules["agent"])
    harness_rules = _attribution_rules(harness)
    agent_rules = _attribution_rules(agent)
    links = _mechanism_causal_links(harness_rules, agent_rules)
    attribution = classify(
        harness=LayerVerdicts.from_rules(harness_rules),
        agent=LayerVerdicts.from_rules(agent_rules),
        device=DeviceHealth(ok=True),
        causal_links=links,
    )
    total = round(float(harness["points"]) + float(agent["points"]), 10)
    result = {
        "schema_version": "harnessbench_l4b_deterministic_rescore_v1",
        "run_dir": str(run_dir),
        "execution_reused": True,
        "execution_complete": summary.get("trace_complete") is True,
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
        "attribution": attribution.to_dict(),
        "not_applicable": [],
    }
    if write:
        output = run_dir / "artifacts" / "verifier_rescore_v1"
        if output.is_symlink():
            raise ValueError(f"refusing symlinked output: {output}")
        output.mkdir(parents=True, exist_ok=True)
        path = output / "combined_score.json"
        if path.is_symlink():
            raise ValueError(f"refusing symlinked score: {path}")
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
    results = [rescore(run_dir, write=args.write) for run_dir in args.run_dir]
    print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
