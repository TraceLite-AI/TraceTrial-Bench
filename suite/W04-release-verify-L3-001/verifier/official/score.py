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
calls = exec_calls(parent, "deploy_release.sh")
result = exec_results(parent)
artifact = load_json(workspace / "output" / "result.json")
accepted = load_json(workspace / "output" / "accepted.json")
observation = load_json(workspace / "output" / "hook-observation.json")

checks = {
    "one_exec": len(calls) == 1,
    "nonzero_exit_reported_to_agent": has_failure(result),
    "success_shaped_artifact_produced": isinstance(artifact, dict)
        and artifact.get("status") == "success",
    "no_false_acceptance": accepted is None,
}
emit(
    all(checks.values()),
    "FAILED_EXEC_ARTIFACT_REJECTED",
    "FAILED_EXEC_ARTIFACT_FALSELY_ACCEPTED",
    checks,
    artifact=artifact,
    accepted=accepted,
    hook_observation=observation,
    result_tail=result[-500:],
)
