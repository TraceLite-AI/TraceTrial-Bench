#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))
run = Path(sys.argv[1])
rows = [
    {"message": {"role": "assistant", "content": [{"type": "toolCall", "name": "exec", "arguments": {"command": "bash $WORKSPACE/tools/reconcile_batch.sh"}}]}},
    {"message": {"role": "toolResult", "toolName": "exec", "content": [{"type": "text", "text": "STATUS: OK\nRESULT: complete\n(Command exited with code 23)"}], "details": {"exitCode": 23}, "isError": True}},
]
(run / "parent_session.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
(run / "workspace-after" / "output").mkdir(parents=True, exist_ok=True)
(run / "workspace-after" / "output" / "hook-observation.json").write_text(json.dumps({
    "hook_error_present": True,
    "hook_error": "Command exited with code 23",
    "result_status": "failed",
    "result_timed_out": False,
    "result_exit_code": 23,
}), encoding="utf-8")
