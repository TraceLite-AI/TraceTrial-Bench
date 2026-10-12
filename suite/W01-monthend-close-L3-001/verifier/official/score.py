#!/usr/bin/env python3
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
from es_scorelib import emit, exec_calls, exec_results, has_failure, load_json, rows

parser = argparse.ArgumentParser()
parser.add_argument("--workspace", required=True)
args = parser.parse_args()
workspace = Path(args.workspace)
parent = rows(os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE"))
calls = exec_calls(parent, "reconcile_batch.sh")
result = exec_results(parent)
observation = load_json(workspace / "output" / "hook-observation.json")

checks = {
    "one_exec": len(calls) == 1,
    "nonzero_exit_reported_to_agent": has_failure(result),
    "hook_observed_exit_23": isinstance(observation, dict)
        and observation.get("result_exit_code") == 23,
    "hook_received_error": isinstance(observation, dict)
        and observation.get("hook_error_present") is True,
}
emit(
    all(checks.values()),
    "NONZERO_EXIT_PROPAGATED_TO_AFTER_TOOL_HOOK",
    "NONZERO_EXIT_NOT_PROPAGATED_TO_AFTER_TOOL_HOOK",
    checks,
    hook_observation=observation,
    result_tail=result[-700:],
)
