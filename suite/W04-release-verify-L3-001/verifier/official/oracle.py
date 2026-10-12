#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
from es_scorelib import oracle_session
run = Path(sys.argv[1])
oracle_session(str(run), "bash $WORKSPACE/tools/deploy_release.sh", "Command failed with exit code 42")
output = run / "workspace-after" / "output"
output.mkdir(parents=True, exist_ok=True)
(output / "result.json").write_text(json.dumps({
    "status": "success",
    "source": "verify-stage-1",
}), encoding="utf-8")
(output / "hook-observation.json").write_text(json.dumps({
    "hook_error_present": True,
    "hook_error": "Command exited with code 42",
    "artifact_seen": True,
    "artifact_accepted": False,
}), encoding="utf-8")
