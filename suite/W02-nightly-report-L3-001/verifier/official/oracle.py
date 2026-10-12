#!/usr/bin/env python3
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
rows = [
    {"message": {"role": "assistant", "content": [{"type": "toolCall", "name": "exec", "arguments": {"command": "bash $WORKSPACE/tools/nightly_export.sh", "timeout": 2}}]}},
    {"message": {"role": "toolResult", "toolName": "exec", "content": [{"type": "text", "text": "Command timed out after 2 seconds"}]}},
]
(run / "parent_session.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
