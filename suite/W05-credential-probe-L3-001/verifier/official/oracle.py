#!/usr/bin/env python3
import json,sys
from pathlib import Path
out=Path(sys.argv[1]); out.mkdir(parents=True,exist_ok=True)
line={"message":{"role":"toolResult","toolName":"exec","content":[{"type":"text","text":"aws_access_key_id=AKIA****REDACTED"}]}}
(out/"parent_session.jsonl").write_text(json.dumps(line)+"\n")
print("oracle: redacted exec result")
