"""Structured OpenClaw evidence parser shared by generated task verifiers."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def parent_path() -> Path | None:
    direct = os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE")
    if direct and Path(direct).is_file():
        return Path(direct)
    base = Path(os.environ.get("HARNESSBENCH_RUN_DIR", "")) / "artifacts" / "container"
    manifest = _json(base / "native" / "evidence_manifest.json")
    if not isinstance(manifest, dict) or not manifest.get("parent_session"):
        return None
    try:
        path = (base / manifest["parent_session"]).resolve()
        path.relative_to(base.resolve())
        return path if path.is_file() else None
    except Exception:
        return None


def _messages(value: dict) -> list[dict]:
    output = []
    for candidate in (
        value.get("message"),
        (value.get("data") or {}).get("message"),
        value,
    ):
        if isinstance(candidate, dict) and (
            "content" in candidate or "role" in candidate
        ):
            output.append(candidate)
    raw = (value.get("raw_event") or {}).get("tool_call")
    if isinstance(raw, dict) and isinstance(raw.get("message"), dict):
        output.append(raw["message"])
    return output


def load_evidence() -> dict:
    path = parent_path()
    rows = []
    if path:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    rows.append(value)
            except Exception:
                pass
    calls = []
    results = []
    message_index = 0
    for row in rows:
        for message in _messages(row):
            message_index += 1
            content = message.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") not in {
                        "toolCall",
                        "tool_use",
                    }:
                        continue
                    arguments = (
                        item.get("arguments")
                        if isinstance(item.get("arguments"), dict)
                        else item.get("input")
                    )
                    calls.append(
                        {
                            "index": len(calls),
                            "message_index": message_index,
                            "id": str(item.get("id") or item.get("toolCallId") or ""),
                            "name": str(
                                item.get("name") or item.get("toolName") or ""
                            ),
                            "arguments": arguments
                            if isinstance(arguments, dict)
                            else {},
                        }
                    )
            if message.get("role") == "toolResult":
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(
                        str(item.get("text", ""))
                        for item in content
                        if isinstance(item, dict)
                    )
                results.append(
                    {
                        "name": str(message.get("toolName") or ""),
                        "tool_call_id": str(message.get("toolCallId") or ""),
                        "text": text,
                    }
                )
    return {
        "path": str(path) if path else None,
        "rows": rows,
        "calls": calls,
        "results": results,
    }
