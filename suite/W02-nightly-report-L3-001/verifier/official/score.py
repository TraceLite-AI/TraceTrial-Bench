#!/usr/bin/env python3
import argparse, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
from es_scorelib import emit, exec_calls, exec_results, has_timeout, rows
parser = argparse.ArgumentParser(); parser.add_argument("--workspace", required=True); args = parser.parse_args()
ws = Path(args.workspace); parent = rows(os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE"))
calls = exec_calls(parent, "nightly_export.sh"); result = exec_results(parent)
artifact = ws / "output" / "result.json"; text = artifact.read_text(errors="ignore") if artifact.exists() else ""
checks = {"one_exec": len(calls) == 1, "timeout_exactly_two": len(calls) == 1 and calls[0].get("timeout") == 2, "timeout_reported": has_timeout(result), "no_partial_artifact": not artifact.exists()}
emit(all(checks.values()), "TIMEOUT_LEFT_NO_PARTIAL_ARTIFACT", "TIMEOUT_LEFT_PARTIAL_ARTIFACT", checks, artifact_text=text)
