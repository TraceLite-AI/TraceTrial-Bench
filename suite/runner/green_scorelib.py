#!/usr/bin/env python3
import json
import os
from pathlib import Path


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None


def load_jsonl(path):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return out
    for line in lines:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                out.append(value)
        except Exception:
            pass
    return out


def parent_rows():
    path = os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE", "")
    return load_jsonl(path) if path else []


def content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def exec_calls(rows):
    found = []
    for row in rows:
        message = row.get("message") or {}
        for item in message.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "toolCall" and item.get("name") == "exec":
                found.append(item.get("arguments") or {})
    return found


def tool_result_text(rows, tool_name="exec"):
    values = []
    for row in rows:
        message = row.get("message") or {}
        if message.get("role") == "toolResult" and message.get("toolName") == tool_name:
            values.append(content_text(message.get("content")))
    return "\n".join(values)


def session_text(rows):
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def write_session(path, messages):
    Path(path).write_text(
        "\n".join(json.dumps({"type": "message", "message": value}, ensure_ascii=False) for value in messages) + "\n",
        encoding="utf-8",
    )


def emit(checks, good, bad, **metrics):
    failed = [key for key, value in checks.items() if not value]
    print(json.dumps({
        "pass": not failed,
        "score": float(not failed),
        "verdict": good if not failed else bad,
        "metrics": {"checks": checks, **metrics},
        "failure_reason": ", ".join(failed) or None,
    }, ensure_ascii=False, indent=2))
