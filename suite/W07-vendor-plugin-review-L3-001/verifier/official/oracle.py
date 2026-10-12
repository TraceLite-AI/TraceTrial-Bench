#!/usr/bin/env python3
import json, sys
from pathlib import Path
run = Path(sys.argv[1])
(run / "plugin_inspect.stdout.json").write_text(json.dumps({"plugin":{"status":"failed","toolNames":[]},"diagnostics":["duplicate tool name"]}), encoding="utf-8")
