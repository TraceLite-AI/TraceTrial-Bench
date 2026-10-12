#!/usr/bin/env python3
import json
import os
import re
from pathlib import Path


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def rows(path):
    out = []
    if not path or not Path(path).exists():
        return out
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except Exception:
            pass
    return out


def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict))
    return ""


def tool_calls(parent_rows, name):
    out = []
    for row in parent_rows:
        msg = row.get("message") or {}
        for item in msg.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "toolCall" and item.get("name") == name:
                out.append(item.get("arguments") or {})
    return out


def tool_results(parent_rows, name):
    out = []
    for row in parent_rows:
        msg = row.get("message") or {}
        if msg.get("role") == "toolResult" and msg.get("toolName") == name:
            out.append(text_of(msg.get("content")))
    return out


def plugin_inspect():
    run = Path(os.environ.get("HARNESSBENCH_RUN_DIR", ""))
    data = load_json(run / "plugin_inspect.stdout.json")
    plugin = (data or {}).get("plugin") or {}
    diagnostics = (data or {}).get("diagnostics") or plugin.get("diagnostics") or []
    return data, plugin, diagnostics


def rejected_or_diagnosed(plugin, diagnostics, pattern):
    status = plugin.get("status")
    diag = json.dumps(diagnostics, ensure_ascii=False)
    return status not in {"loaded", None} or bool(re.search(pattern, diag, re.I))


def emit(ok, verdict_ok, verdict_bad, checks, **metrics):
    failed = [k for k, v in checks.items() if not v]
    print(json.dumps({
        "pass": not failed,
        "score": float(not failed),
        "verdict": verdict_ok if not failed else verdict_bad,
        "metrics": {"checks": checks, **metrics},
        "failure_reason": ", ".join(failed) or None,
    }, ensure_ascii=False, indent=2))


def write_oracle_session(run_dir, tool_name, args=None, result_text="OK"):
    run = Path(run_dir)
    rows_out = [
        {"message": {"role": "assistant", "content": [
            {"type": "toolCall", "name": tool_name, "arguments": args or {}}
        ]}},
        {"message": {"role": "toolResult", "toolName": tool_name,
                     "content": [{"type": "text", "text": result_text}]}},
    ]
    (run / "parent_session.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows_out) + "\n",
        encoding="utf-8",
    )
