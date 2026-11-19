"""Trace adapter for opencode (`opencode run --format json`).

opencode prints one JSON event per line. The events used here:

  step_start / step_finish   one model call; step_finish carries token usage
  tool_use                   one finished tool call: input, output, metadata
  text                       assistant text
  error                      a run-level error

Every event carries the native `sessionID`; it is written as `session_id` so
multi-turn conversations can resume the same session with `--session`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


PROVIDER_ID = "harnessbench"
_INTERNAL_ARGS = {
    "harnessbench-conversation",
    "harnessbench-session-id",
}


class OpenCodeAdapter:
    def __init__(self, name: str = "opencode") -> None:
        self.name = name

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        model_ref = model if "/" in model else f"{PROVIDER_ID}/{model}"
        command = [
            harness_path or "opencode",
            "run",
            "--format",
            "json",
            "-m",
            model_ref,
            "--dir",
            ".",
            # Unattended benchmark runs: approve tool permissions that are
            # not explicitly denied instead of waiting for a person.
            "--auto",
        ]
        session = extra_args.get("harnessbench-session-id")
        if session:
            command.extend(["--session", session])
        for key, value in sorted(extra_args.items()):
            if key.startswith("harnessbench-") or key in _INTERNAL_ARGS:
                continue
            command.extend([f"--{key}", value])
        # The prompt goes last, after "--", so a prompt that starts with "-"
        # is never parsed as an option.
        command.extend(["--", input_query])
        return command

    def consume(
        self,
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        run_dir: Path,
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        session_id: str | None = None
        parsed = 0
        errors: list[str] = []
        prompt = process.args[-1] if process.args and "--" in process.args else None

        for line_no, line in enumerate(_lines(process.stdout), start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                writer.write("raw_stdout", text=line, stream_seq=line_no, parseable=False, parse_error=str(exc))
                continue
            if not isinstance(event, dict):
                continue
            parsed += 1
            event_session = _string(event.get("sessionID"))
            if session_id is None and event_session:
                session_id = event_session
                writer.write("run_started", source=self.name, session_id=session_id, raw_event=event)
                if prompt is not None:
                    writer.write("model_input", source=self.name, session_id=session_id, content=prompt)
            kind = event.get("type")
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            if kind == "tool_use":
                _write_tool(writer, part, event, session_id)
            elif kind == "text":
                text = _string(part.get("text"))
                if text:
                    writer.write(
                        "assistant_message",
                        message_id=part.get("messageID"),
                        content=text,
                        content_kind="text",
                        session_id=session_id,
                        raw_event=event,
                    )
            elif kind == "step_finish":
                _write_usage(writer, part, event, session_id)
            elif kind == "error":
                message = _error_message(event.get("error"))
                errors.append(message)
                writer.write("native_error", source=self.name, session_id=session_id, error_message=message, raw_event=event)

        for index, line in enumerate(_lines(process.stderr), start=1):
            writer.write("raw_stderr", text=line, stream_seq=index, severity="error" if process.returncode else "info")

        if parsed == 0:
            return AdapterResult(status="failed", trace_complete=False, structured_trace_status="unavailable",
                                 error_type="opencode_no_events", error_message="opencode printed no JSON events")
        if errors:
            return AdapterResult(status="failed", structured_trace_status="available",
                                 error_type="opencode_error", error_message=errors[-1][:500])
        return AdapterResult(structured_trace_status="available")


def _write_tool(writer: EventWriter, part: dict[str, Any], event: dict[str, Any], session_id: str | None) -> None:
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    call_id = _string(part.get("callID")) or _string(part.get("id")) or "opencode-tool"
    tool_name = _string(part.get("tool")) or "unknown"
    status = _string(state.get("status"))
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    if status == "error":
        content = _string(state.get("error")) or ""
    else:
        content = state.get("output") if isinstance(state.get("output"), str) else json.dumps(state.get("output"))
    tool_result: dict[str, Any] = {
        "tool_call_id": call_id,
        "tool_name": tool_name,
        # `content` is the output opencode reports for the call. Typed
        # metadata (exit code, truncation) is kept apart from it, without
        # its duplicate copy of the output text.
        "content": content,
        "error": status == "error",
        "status": status,
    }
    if isinstance(metadata.get("exit"), int):
        tool_result["exit_code"] = metadata["exit"]
    typed = {key: value for key, value in metadata.items() if key not in {"output", "preview", "display"}}
    if typed:
        tool_result["metadata"] = typed
    times = state.get("time") if isinstance(state.get("time"), dict) else {}
    writer.write(
        "tool",
        session_id=session_id,
        tool_call={
            "tool_call_id": call_id,
            "tool_name": tool_name,
            "arguments": state.get("input") if isinstance(state.get("input"), dict) else {},
            "title": state.get("title"),
        },
        tool_result=tool_result,
        started_at_ms=times.get("start"),
        finished_at_ms=times.get("end"),
        raw_event=event,
    )


def _write_usage(writer: EventWriter, part: dict[str, Any], event: dict[str, Any], session_id: str | None) -> None:
    tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else None
    if tokens is None:
        return
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    fresh, cached = _int(tokens.get("input")), _int(cache.get("read"))
    output, reasoning = _int(tokens.get("output")), _int(tokens.get("reasoning"))
    writer.write(
        "token_usage",
        source="opencode.step_finish",
        usage_scope="call",
        session_id=session_id,
        finish_reason=part.get("reason"),
        usage={
            # opencode reports cache reads separately from fresh input.
            "input_tokens": fresh + cached if fresh is not None and cached is not None else "unknown",
            "cached_input_tokens": cached if cached is not None else "unknown",
            "cache_write_input_tokens": _int(cache.get("write")) if _int(cache.get("write")) is not None else "unknown",
            "output_tokens": output + (reasoning or 0) if output is not None else "unknown",
            "reasoning_output_tokens": reasoning if reasoning is not None else "unknown",
        },
        raw_event=event,
    )


def _error_message(value: Any) -> str:
    if isinstance(value, dict):
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        return _string(data.get("message")) or _string(value.get("name")) or json.dumps(value)[:500]
    return str(value)


def _lines(value: str) -> list[str]:
    return [line for line in value.splitlines() if line.strip()] if value else []


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
