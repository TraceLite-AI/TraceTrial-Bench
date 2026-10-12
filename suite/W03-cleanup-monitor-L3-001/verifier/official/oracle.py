#!/usr/bin/env python3
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
rows = [
    {"message": {"role": "assistant", "content": [{"type": "toolCall", "name": "exec", "arguments": {"command": "bash $WORKSPACE/tools/cleanup_job.sh", "timeout": 2}}]}},
    {"message": {"role": "toolResult", "toolName": "exec", "content": [{"type": "text", "text": "CLEANUP: sweep started\nCommand timed out after 2 seconds"}], "details": {"timedOut": True}, "isError": True}},
]
(run / "parent_session.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
output = run / "workspace-after" / "output"
output.mkdir(parents=True, exist_ok=True)
(output / "hook-observation.json").write_text(json.dumps({
    "hook_error_present": True,
    "hook_error": "Command timed out after 2 seconds",
    "result_status": "failed",
    "result_timed_out": True,
    "result_exit_code": None,
}), encoding="utf-8")
