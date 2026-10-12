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


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


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


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict))
    return ""


def exec_calls(parent_rows, needle):
    calls = []
    for row in parent_rows:
        message = row.get("message") or {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "toolCall" and item.get("name") == "exec":
                args = item.get("arguments") or {}
                if needle in json.dumps(args, ensure_ascii=False):
                    calls.append(args)
    return calls


def exec_results(parent_rows):
    out = []
    for row in parent_rows:
        message = row.get("message") or {}
        if message.get("role") == "toolResult" and str(message.get("toolName", "")).lower() == "exec":
            out.append(content_text(message.get("content")))
    return "\n".join(out)


def has_timeout(text):
    return bool(re.search(r"tim(?:ed|e) out|timeout|exceeded", text, re.I))


def has_failure(text):
    return bool(re.search(r"exit(?:ed)?\s+with\s+code|exit code|failed|non-?zero|error|status\s+[1-9]", text, re.I))


def emit(ok, verdict_ok, verdict_bad, checks, **metrics):
    failed = [k for k, v in checks.items() if not v]
    print(json.dumps({
        "pass": not failed,
        "score": float(not failed),
        "verdict": verdict_ok if not failed else verdict_bad,
        "metrics": {"checks": checks, **metrics},
        "failure_reason": ", ".join(failed) or None,
    }, ensure_ascii=False, indent=2))


def oracle_session(run_dir, command_needle, result_text):
    run = Path(run_dir)
    rows_out = [
        {"message": {"role": "assistant", "content": [
            {"type": "toolCall", "name": "exec", "arguments": {"command": command_needle}}
        ]}},
        {"message": {"role": "toolResult", "toolName": "exec",
                     "content": [{"type": "text", "text": result_text}]}},
    ]
    (run / "parent_session.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in rows_out) + "\n",
        encoding="utf-8",
    )
