from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import (
    archive_records,
    emit_context_compaction,
    extract_summary,
    write_archive_event,
)
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


_GATEWAY_ARG = "harnessbench-gateway"
_DEFAULT_COMPLETION_TIMEOUT_SECONDS = 300.0
_TRUNCATED_CHARACTERS = re.compile(
    r"\[\.\.\.\s*(?P<count>[0-9][0-9_,]*)\s+more\s+characters?\s+truncated\b",
    re.IGNORECASE,
)


class OpenClawAdapter:
    name = "openclaw"

    def __init__(
        self,
        *,
        completion_poll_interval: float = 0.1,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if completion_poll_interval < 0:
            raise ValueError("OpenClaw completion poll interval must not be negative")
        self.completion_poll_interval = completion_poll_interval
        self.monotonic = monotonic
        self.sleep = sleep
        self.native_child_delegation = False

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        executable = harness_path or "openclaw"
        self.native_child_delegation = (
            extra_args.get("harnessbench-native-child-delegation") == "true"
        )
        command = [executable, "agent"]
        if extra_args.get(_GATEWAY_ARG) != "true":
            command.append("--local")
        command.extend(
            [
                "--json",
                "--message",
                input_query,
                "--model",
                model,
            ]
        )
        session_key = extra_args.get("session-key") or f"agent:main:{run_dir.name}"
        command.extend(["--session-key", session_key])
        agent = extra_args.get("agent")
        if agent:
            command.extend(["--agent", agent])
        thinking = extra_args.get("thinking")
        if thinking:
            command.extend(["--thinking", thinking])
        for key, value in sorted(extra_args.items()):
            if key.startswith("harnessbench-approval-") or key in {
                _GATEWAY_ARG,
                "agent",
                "harnessbench-conversation",
                "harnessbench-session-id",
                "harnessbench-task-tools",
                "harnessbench-task-tool-bridge",
                "harnessbench-task-tool-config",
                "harnessbench-native-child-delegation",
                "session-key",
                "thinking",
            }:
                continue
            command.extend([f"--{key}", value])
        return command

    def consume(
        self,
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        run_dir: Path,
        env: dict[str, str] | None = None,
        allowed_state_dir: str | None = None,
        completion_timeout: float | None = None,
        managed_gateway: bool = False,
    ) -> AdapterResult:
        result = AdapterResult(structured_trace_status="available")
        try:
            payload = _normalize_cli_payload(_extract_json_payload(process.stdout))
        except json.JSONDecodeError as exc:
            writer.write(
                "raw_stdout",
                text=process.stdout,
                stream_seq=1,
                parseable=False,
                parse_error=f"JSON parse error: {exc.msg}",
            )
            result.structured_trace_status = "unavailable"
            result.trace_complete = False
            return result

        writer.write(
            "run_started",
            source="openclaw",
            session_key=payload.get("sessionKey"),
            external_run_id=payload.get("runId"),
            raw_event=_run_started_raw_event(payload),
        )

        trajectory = payload.get("trajectory") or []
        if not payload.get("sessionKey") and not payload.get("runId"):
            result.structured_trace_status = "unavailable"
        elif not trajectory:
            result.structured_trace_status = "unavailable"

        pending_tools: dict[str, dict[str, Any]] = {}
        for step in trajectory:
            self._map_step(step, writer, payload, pending_tools)

        for pending in pending_tools.values():
            writer.write(
                "tool",
                tool_call=pending["tool_call"],
                tool_result=None,
                raw_event={"tool_call": pending["raw_event"], "tool_result": None},
            )

        state_dir = Path(allowed_state_dir) if allowed_state_dir else None
        completion_timed_out = False
        initial_session_messages = _current_session_messages(
            payload,
            allowed_state_dir=state_dir,
        )
        session_yielded = _session_called_tool(
            initial_session_messages or [],
            "sessions_yield",
        )
        if managed_gateway and (_is_yielded_payload(payload) or session_yielded):
            wait_timeout = (
                _DEFAULT_COMPLETION_TIMEOUT_SECONDS
                if completion_timeout is None
                else max(0.0, completion_timeout)
            )
            current_session_messages, completion_timed_out = (
                self._wait_for_parent_continuation(
                    payload,
                    allowed_state_dir=state_dir,
                    timeout=wait_timeout,
                    initial_assistant_ids=_assistant_record_ids(
                        initial_session_messages or []
                    ),
                )
            )
        else:
            current_session_messages = initial_session_messages
        if state_dir is not None:
            native_records = _all_session_records(
                payload,
                allowed_state_dir=state_dir,
            )
            agent_meta = ((payload.get("meta") or {}).get("agentMeta") or {})
            native_session_id = (
                agent_meta.get("sessionId")
                if isinstance(agent_meta.get("sessionId"), str)
                else payload.get("sessionKey")
            )
            archive = archive_records(
                harness=self.name,
                session_id=native_session_id,
                records=native_records,
                run_dir=run_dir,
                destination_name="openclaw-session.trace.jsonl",
            )
            write_archive_event(
                writer,
                harness=self.name,
                session_id=native_session_id,
                archive=archive,
            )
            summary_artifact = (
                archive.path.relative_to(run_dir).as_posix()
                if archive.path is not None
                else None
            )
            seen_compactions: set[str] = set()
            for record in native_records:
                if record.get("type") != "compaction":
                    continue
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="openclaw.native_session",
                    native_event_type="compaction",
                    record=record,
                    session_id=native_session_id,
                    summary=extract_summary(record),
                    summary_artifact=summary_artifact,
                    seen=seen_compactions,
                    notice_visible_to_model=True,
                )
        if current_session_messages is not None:
            writer.write(
                "provider_request_count",
                source="openclaw.current_session",
                count=sum(
                    (record.get("message") or {}).get("role") == "assistant"
                    for record in current_session_messages
                ),
                binding="current_prompt_turn",
            )

        deferred_parent_messages: list[dict[str, Any]] = []
        main_principal_id = str(payload.get("sessionKey") or "main")
        if not trajectory and current_session_messages:
            if self.native_child_delegation:
                before_completion, deferred_parent_messages = (
                    _split_session_at_yield(current_session_messages)
                )
                self._map_session_tools(
                    before_completion,
                    writer,
                    principal_id=main_principal_id,
                    principal_role="main",
                )
            else:
                self._map_session_tools(
                    current_session_messages,
                    writer,
                    principal_id=main_principal_id,
                    principal_role="main",
                )
        descendant_expected = 0
        descendant_found = 0
        if state_dir is not None and current_session_messages:
            descendant_expected, descendant_found = (
                self._map_descendant_session_tools(
                    payload,
                    current_session_messages,
                    writer,
                    allowed_state_dir=state_dir,
                )
            )
            if self.native_child_delegation:
                self._map_descendant_completions(
                    payload,
                    current_session_messages,
                    writer,
                    allowed_state_dir=state_dir,
                )
        if deferred_parent_messages:
            self._map_session_tools(
                deferred_parent_messages,
                writer,
                principal_id=main_principal_id,
                principal_role="main",
            )

        if payload.get("response") and not any(
            step.get("type") == "assistant_message" for step in trajectory
        ):
            writer.write(
                "assistant_message",
                content=payload.get("response"),
                message_id=payload.get("runId"),
                raw_event={
                    "response": payload.get("response"),
                    "runId": payload.get("runId"),
                    "sessionKey": payload.get("sessionKey"),
                },
            )
        if (
            current_session_messages
            and not any(
                step.get("type") == "assistant_message" for step in trajectory
            )
            and not _payload_assistant_text(payload)
        ):
            session_text, session_record = _latest_visible_assistant(
                current_session_messages
            )
            if session_text:
                writer.write(
                    "assistant_message",
                    content=session_text,
                    message_id=session_record.get("id") if session_record else None,
                    raw_event=session_record,
                )
        self._map_current_agent_payload(payload, writer, trajectory)
        if (
            current_session_messages is not None
            and descendant_found == descendant_expected
        ):
            result.structured_trace_status = "available"
        elif descendant_expected:
            result.structured_trace_status = "unavailable"
        if completion_timed_out:
            result.status = "failed"
            result.error_type = "openclaw_subagent_completion_timeout"
            result.error_message = (
                "OpenClaw parent did not resume after sessions_yield before timeout"
            )
        result.trace_complete = (
            result.status == "finished"
            and result.structured_trace_status == "available"
        )
        return result

    def _wait_for_parent_continuation(
        self,
        payload: dict[str, Any],
        *,
        allowed_state_dir: Path | None,
        timeout: float,
        initial_assistant_ids: set[str],
    ) -> tuple[list[dict[str, Any]] | None, bool]:
        deadline = self.monotonic() + timeout
        latest: list[dict[str, Any]] | None = None
        while True:
            latest = _current_session_messages(
                payload,
                allowed_state_dir=allowed_state_dir,
            )
            if (
                latest
                and _assistant_after_sessions_yield(
                    latest,
                    excluded_record_ids=initial_assistant_ids,
                )[0]
                and not _session_has_pending_tool_calls(latest)
            ):
                return latest, False
            if self.monotonic() >= deadline:
                return latest, True
            self.sleep(self.completion_poll_interval)

    def _map_current_agent_payload(
        self,
        payload: dict[str, Any],
        writer: EventWriter,
        trajectory: list[dict[str, Any]],
    ) -> None:
        meta = payload.get("meta") or {}
        agent_meta = meta.get("agentMeta") or {}
        has_assistant_message = any(
            step.get("type") == "assistant_message" for step in trajectory
        )
        content = (
            meta.get("finalAssistantVisibleText")
            or meta.get("finalAssistantRawText")
            or _payload_text(payload)
        )
        if content and not has_assistant_message and not payload.get("response"):
            writer.write(
                "assistant_message",
                content=content,
                message_id=agent_meta.get("sessionId"),
                finish_reason=(meta.get("completion") or {}).get("finishReason"),
                raw_event=_assistant_raw_event(payload),
            )

        has_trajectory_usage = any(
            step.get("type") == "token_usage" for step in trajectory
        )
        aggregate_usage = agent_meta.get("usage")
        last_call_usage = agent_meta.get("lastCallUsage")
        usage = None if has_trajectory_usage else aggregate_usage or last_call_usage
        if usage:
            input_tokens = _complete_input_tokens(
                usage.get("input"),
                usage.get("cacheRead"),
                usage.get("cacheWrite"),
            )
            writer.write(
                "token_usage",
                source="openclaw.agent_meta",
                usage_scope="run" if aggregate_usage else "call",
                usage={
                    "input_tokens": input_tokens,
                    "cached_input_tokens": usage.get("cacheRead", "unknown"),
                    "cache_write_input_tokens": usage.get(
                        "cacheWrite", "unknown"
                    ),
                    "output_tokens": usage.get("output", "unknown"),
                    "reasoning_output_tokens": "unknown",
                },
                raw_event=_usage_raw_event(agent_meta),
            )

    def _map_step(
        self,
        step: dict[str, Any],
        writer: EventWriter,
        root: dict[str, Any],
        pending_tools: dict[str, dict[str, Any]],
    ) -> None:
        step_type = step.get("type")
        raw_event = {"raw_event": step}
        if step_type == "model_input":
            writer.write(
                "model_input",
                turn_id=step.get("turn_id"),
                model=step.get("model"),
                provider=step.get("provider", "openclaw"),
                messages=step.get("messages"),
                tools=step.get("tools", []),
                request_params=step.get("request_params", {}),
                **raw_event,
            )
            return
        if step_type == "assistant_message":
            writer.write(
                "assistant_message",
                turn_id=step.get("turn_id"),
                message_id=step.get("message_id"),
                content=step.get("content"),
                reasoning=step.get("reasoning"),
                finish_reason=step.get("finish_reason"),
                **raw_event,
            )
            return
        if step_type == "tool_call":
            tool_call_id = step.get("tool_call_id")
            if tool_call_id is not None:
                pending_tools[tool_call_id] = {
                    "tool_call": _openclaw_tool_call(step),
                    "raw_event": step,
                }
            return
        if step_type == "tool_result":
            tool_call_id = step.get("tool_call_id")
            pending = (
                pending_tools.pop(tool_call_id, None)
                if tool_call_id is not None
                else None
            )
            writer.write(
                "tool",
                tool_call=pending["tool_call"]
                if pending
                else _openclaw_tool_call(step),
                tool_result=_openclaw_tool_result(step),
                raw_event={
                    "tool_call": pending["raw_event"] if pending else None,
                    "tool_result": step,
                },
            )
            return
        if step_type == "token_usage":
            usage = step.get("usage", {})
            cache_write = usage.get(
                "cache_write_input_tokens",
                usage.get(
                    "cache_write_tokens",
                    usage.get("cache_creation_input_tokens", "unknown"),
                ),
            )
            writer.write(
                "token_usage",
                turn_id=step.get("turn_id"),
                source="openclaw.trajectory",
                usage_scope="call",
                usage={
                    "input_tokens": _complete_input_tokens(
                        usage.get("input_tokens"),
                        usage.get("cached_input_tokens"),
                        cache_write,
                    ),
                    "cached_input_tokens": usage.get("cached_input_tokens", "unknown"),
                    "cache_write_input_tokens": cache_write,
                    "output_tokens": usage.get("output_tokens", "unknown"),
                    "reasoning_output_tokens": usage.get(
                        "reasoning_output_tokens", "unknown"
                    ),
                },
                **raw_event,
            )
            return
        writer.write(
            "raw_stdout",
            text=json.dumps(step, ensure_ascii=False),
            parseable=True,
            raw_event=step,
        )

    def _map_session_tools(
        self,
        current_messages: list[dict[str, Any]],
        writer: EventWriter,
        *,
        principal_id: str | None = None,
        parent_principal_id: str | None = None,
        principal_role: str | None = None,
    ) -> bool:
        principal = _without_none(
            {
                "principal_id": principal_id,
                "parent_principal_id": parent_principal_id,
                "principal_role": principal_role,
            }
        )
        pending_tools: dict[str, dict[str, Any]] = {}
        wrote_tool = False
        for record in current_messages:
            message = record.get("message") or {}
            role = message.get("role")
            if role == "assistant":
                for item in message.get("content") or []:
                    if not isinstance(item, dict) or item.get("type") != "toolCall":
                        continue
                    tool_call_id = item.get("id")
                    if not tool_call_id:
                        continue
                    pending_tools[tool_call_id] = {
                        "tool_call": _session_tool_call(record, item),
                        "raw_event": record,
                    }
                continue
            if role == "toolResult":
                tool_call_id = message.get("toolCallId")
                pending = (
                    pending_tools.pop(tool_call_id, None)
                    if tool_call_id is not None
                    else None
                )
                writer.write(
                    "tool",
                    tool_call=pending["tool_call"]
                    if pending
                    else _session_tool_call_from_result(record),
                    tool_result=_session_tool_result(record),
                    raw_event={
                        "tool_call": pending["raw_event"] if pending else None,
                        "tool_result": record,
                    },
                    **principal,
                )
                wrote_tool = True

        for pending in pending_tools.values():
            writer.write(
                "tool",
                tool_call=pending["tool_call"],
                tool_result=None,
                raw_event={"tool_call": pending["raw_event"], "tool_result": None},
                **principal,
            )
            wrote_tool = True

        return wrote_tool

    def _map_descendant_session_tools(
        self,
        payload: dict[str, Any],
        current_messages: list[dict[str, Any]],
        writer: EventWriter,
        *,
        allowed_state_dir: Path,
    ) -> tuple[int, int]:
        child_keys = _spawned_child_session_keys(current_messages)
        if not child_keys:
            return 0, 0
        session_files = _descendant_session_files(
            payload,
            allowed_state_dir=allowed_state_dir,
        )
        parent_key = str(payload.get("sessionKey") or "main")
        found = 0
        for child_key in sorted(child_keys):
            session_file = session_files.get(child_key)
            if session_file is None:
                continue
            messages = _load_session_messages(
                session_file,
                allowed_state_dir=allowed_state_dir,
            )
            if not messages:
                continue
            found += 1
            self._map_session_tools(
                messages,
                writer,
                principal_id=child_key,
                parent_principal_id=parent_key,
                principal_role="child",
            )
        return len(child_keys), found

    def _map_descendant_completions(
        self,
        payload: dict[str, Any],
        current_messages: list[dict[str, Any]],
        writer: EventWriter,
        *,
        allowed_state_dir: Path,
    ) -> None:
        """Join native child completion with the resumed parent turn.

        OpenClaw persists the child transcript and resumed parent response but
        not the transient push notification as a parent transcript message.
        This event contains only native persisted content and records whether
        a continuation after ``sessions_yield`` proves parent reactivation.
        """

        child_keys = _spawned_child_session_keys(current_messages)
        if not child_keys:
            return
        session_files = _descendant_session_files(
            payload,
            allowed_state_dir=allowed_state_dir,
        )
        parent_key = str(payload.get("sessionKey") or "main")
        parent_text, parent_record = _assistant_after_sessions_yield(current_messages)
        for child_key in sorted(child_keys):
            session_file = session_files.get(child_key)
            if session_file is None:
                continue
            records = _load_session_messages(
                session_file,
                allowed_state_dir=allowed_state_dir,
            )
            content, child_record = _latest_visible_assistant(records)
            if not content:
                continue
            writer.write(
                "task_completion",
                status="completed",
                content=content,
                principal_id=child_key,
                parent_principal_id=parent_key,
                principal_role="child",
                parent_reactivated=bool(parent_text),
                parent_message_id=(
                    parent_record.get("id") if parent_record is not None else None
                ),
                source="openclaw.native_session_join",
                raw_event={
                    "child_completion": child_record,
                    "parent_continuation": parent_record,
                },
            )


def _openclaw_tool_call(step: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "tool_call_id": step.get("tool_call_id"),
            "tool_name": step.get("tool_name", "unknown"),
            "arguments": step.get("arguments", {}),
            "provider_executed": step.get("provider_executed", True),
            "source_message_id": step.get("source_message_id"),
        }
    )


def _openclaw_tool_result(step: dict[str, Any]) -> dict[str, Any]:
    content = step.get("content")
    return _without_none(
        {
            "tool_call_id": step.get("tool_call_id"),
            "tool_name": step.get("tool_name", "unknown"),
            "content": content,
            "structured_output": _first_result_value(
                step,
                "structured_output",
                "structuredOutput",
            ),
            "error": _first_result_value(step, "error", "is_error", "isError"),
            "output_files": _first_result_value(
                step,
                "output_files",
                "outputFiles",
            )
            or [],
            **_openclaw_result_metadata(step, content),
        }
    )


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _first_result_value(payload: dict[str, Any], *keys: str) -> Any:
    details = payload.get("details")
    sources = (payload, details if isinstance(details, dict) else {})
    for source in sources:
        for key in keys:
            if key in source and source[key] is not None:
                return source[key]
    return None


def _integer_result_value(payload: dict[str, Any], *keys: str) -> int | None:
    value = _first_result_value(payload, *keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value.strip()):
        return int(value.strip())
    return None


def _nonnegative_integer_result_value(
    payload: dict[str, Any], *keys: str
) -> int | None:
    value = _integer_result_value(payload, *keys)
    return value if value is not None and value >= 0 else None


def _string_result_value(payload: dict[str, Any], *keys: str) -> str | None:
    value = _first_result_value(payload, *keys)
    return value if isinstance(value, str) and value else None


def _boolean_result_value(payload: dict[str, Any], *keys: str) -> bool | None:
    value = _first_result_value(payload, *keys)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _openclaw_result_metadata(
    payload: dict[str, Any],
    content: Any,
) -> dict[str, Any]:
    """Preserve bounded native result metadata in the normalized trace.

    OpenClaw session records keep command provenance in ``message.details``.
    Copy only the stable scalar fields needed for diagnosis; never copy the
    potentially large native ``aggregated`` payload into normalized events.
    """

    marker = (
        _TRUNCATED_CHARACTERS.search(content)
        if isinstance(content, str)
        else None
    )
    omitted_characters = _nonnegative_integer_result_value(
        payload,
        "omitted_characters",
        "omittedCharacters",
        "omitted_chars",
        "omittedChars",
    )
    if marker is not None:
        omitted_characters = int(
            marker.group("count").replace(",", "").replace("_", "")
        )

    truncated = _boolean_result_value(
        payload,
        "truncated",
        "is_truncated",
        "isTruncated",
    )
    if marker is not None or omitted_characters is not None:
        truncated = True

    return _without_none(
        {
            "status": _string_result_value(payload, "status"),
            "exit_code": _integer_result_value(payload, "exit_code", "exitCode"),
            "duration_ms": _nonnegative_integer_result_value(
                payload,
                "duration_ms",
                "durationMs",
            ),
            "truncated": truncated,
            "omitted_characters": omitted_characters,
            "native_details_truncated": _boolean_result_value(
                payload,
                "persisted_details_truncated",
                "persistedDetailsTruncated",
            ),
            "native_details_bytes_at_least": _nonnegative_integer_result_value(
                payload,
                "original_details_bytes_at_least",
                "originalDetailsBytesAtLeast",
            ),
        }
    )


def _payload_text(payload: dict[str, Any]) -> str | None:
    texts = [
        item.get("text")
        for item in payload.get("payloads", [])
        if isinstance(item, dict) and item.get("text")
    ]
    return "\n".join(texts) if texts else None


def _normalize_cli_payload(payload: dict[str, Any]) -> dict[str, Any]:
    gateway_result = payload.get("result")
    if not isinstance(gateway_result, dict):
        return payload
    normalized = dict(gateway_result)
    normalized.setdefault("runId", payload.get("runId"))
    meta = normalized.get("meta") or {}
    system_prompt_report = meta.get("systemPromptReport") or {}
    normalized.setdefault("sessionKey", system_prompt_report.get("sessionKey"))
    return normalized


def _is_yielded_payload(payload: dict[str, Any]) -> bool:
    meta = payload.get("meta") or {}
    return meta.get("yielded") is True or meta.get("livenessState") == "paused"


def _payload_assistant_text(payload: dict[str, Any]) -> str | None:
    meta = payload.get("meta") or {}
    return (
        payload.get("response")
        or meta.get("finalAssistantVisibleText")
        or meta.get("finalAssistantRawText")
        or _payload_text(payload)
    )


def _assistant_after_sessions_yield(
    records: list[dict[str, Any]],
    *,
    excluded_record_ids: set[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    yield_index: int | None = None
    for index, record in enumerate(records):
        message = record.get("message") or {}
        if _message_calls_tool(message, "sessions_yield") or (
            message.get("role") == "toolResult"
            and message.get("toolName") == "sessions_yield"
        ):
            yield_index = index
    if yield_index is None:
        return None, None
    return _latest_visible_assistant(
        records[yield_index + 1 :],
        excluded_record_ids=excluded_record_ids,
    )


def _latest_visible_assistant(
    records: list[dict[str, Any]],
    *,
    excluded_record_ids: set[str] | None = None,
) -> tuple[str | None, dict[str, Any] | None]:
    excluded = excluded_record_ids or set()
    for record in reversed(records):
        if str(record.get("id") or "") in excluded:
            continue
        message = record.get("message") or {}
        if message.get("role") != "assistant":
            continue
        text = _message_text(message).strip()
        if text:
            return text, record
    return None, None


def _assistant_record_ids(records: list[dict[str, Any]]) -> set[str]:
    return {
        str(record.get("id"))
        for record in records
        if (record.get("message") or {}).get("role") == "assistant"
        and record.get("id")
    }


def _session_called_tool(records: list[dict[str, Any]], tool_name: str) -> bool:
    return any(
        _message_calls_tool(record.get("message") or {}, tool_name)
        for record in records
    )


def _session_has_pending_tool_calls(records: list[dict[str, Any]]) -> bool:
    """Return true while a persisted assistant tool call lacks its result."""

    pending: set[str] = set()
    for record in records:
        message = record.get("message") or {}
        if message.get("role") == "assistant":
            for item in message.get("content") or []:
                if not isinstance(item, dict) or item.get("type") not in {
                    "toolCall",
                    "tool_use",
                    "toolUse",
                }:
                    continue
                tool_call_id = item.get("id")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    return True
                pending.add(tool_call_id)
        elif message.get("role") == "toolResult":
            tool_call_id = message.get("toolCallId")
            if isinstance(tool_call_id, str):
                pending.discard(tool_call_id)
    return bool(pending)


def _split_session_at_yield(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep parent work after a native yield behind child completion events."""

    boundary: int | None = None
    for index, record in enumerate(records):
        message = record.get("message") or {}
        if _message_calls_tool(message, "sessions_yield") or (
            message.get("role") == "toolResult"
            and message.get("toolName") == "sessions_yield"
        ):
            boundary = index
    if boundary is None:
        return records, []
    return records[: boundary + 1], records[boundary + 1 :]


def _message_calls_tool(message: dict[str, Any], tool_name: str) -> bool:
    if message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(item, dict)
        and item.get("type") in {"toolCall", "tool_use", "toolUse"}
        and (item.get("name") or item.get("toolName")) == tool_name
        for item in content
    )


def _current_session_messages(
    payload: dict[str, Any],
    *,
    allowed_state_dir: Path | None,
) -> list[dict[str, Any]] | None:
    meta = payload.get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    session_file = agent_meta.get("sessionFile")
    if not isinstance(session_file, str) or not session_file:
        return None
    messages = _load_session_messages(
        session_file,
        allowed_state_dir=allowed_state_dir,
        expected_session_id=(
            agent_meta.get("sessionId")
            if isinstance(agent_meta.get("sessionId"), str)
            else None
        ),
    )
    if not messages:
        return None
    current = _messages_for_current_turn(messages, meta.get("finalPromptText"))
    return current or None


def _load_session_messages(
    session_file: str | Path,
    *,
    allowed_state_dir: Path | None = None,
    expected_session_id: str | None = None,
) -> list[dict[str, Any]]:
    marker = _parse_sqlite_session_marker(str(session_file))
    if marker is not None:
        session_id, store_path = marker
        if expected_session_id is None or session_id != expected_session_id:
            return []
        return _load_sqlite_session_messages(
            store_path,
            session_id=session_id,
            allowed_state_dir=allowed_state_dir,
        )
    session_path = Path(session_file)
    if allowed_state_dir is not None:
        try:
            state_root = allowed_state_dir.resolve(strict=True)
            resolved_session = session_path.resolve(strict=True)
            resolved_session.relative_to(state_root)
        except (OSError, ValueError):
            return []
        if not resolved_session.is_file():
            return []
        session_path = resolved_session
    try:
        lines = session_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("type") == "message" and isinstance(record.get("message"), dict):
            records.append(record)
    return records


def _all_session_records(
    payload: dict[str, Any],
    *,
    allowed_state_dir: Path,
) -> list[dict[str, Any]]:
    meta = payload.get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    session_file = agent_meta.get("sessionFile")
    expected_session_id = agent_meta.get("sessionId")
    if not isinstance(session_file, str) or not session_file:
        return []
    marker = _parse_sqlite_session_marker(session_file)
    if marker is not None:
        session_id, store_path = marker
        if isinstance(expected_session_id, str) and session_id != expected_session_id:
            return []
        return _load_all_sqlite_session_records(
            store_path,
            session_id=session_id,
            allowed_state_dir=allowed_state_dir,
        )
    try:
        state_root = allowed_state_dir.resolve(strict=True)
        session_path = Path(session_file)
        if session_path.is_symlink():
            return []
        resolved_session = session_path.resolve(strict=True)
        resolved_session.relative_to(state_root)
        if not resolved_session.is_file():
            return []
        lines = resolved_session.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError, ValueError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(record, dict):
            return []
        records.append(record)
    return records


def _spawned_child_session_keys(records: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        message = record.get("message") or {}
        if message.get("role") != "toolResult" or message.get("toolName") != "sessions_spawn":
            continue
        for key, value in _walk_values(message):
            normalized = key.replace("_", "").replace("-", "").lower()
            if normalized == "childsessionkey" and isinstance(value, str) and value:
                keys.add(value)
        for text in _content_strings(message.get("content")):
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                continue
            for key, value in _walk_values(payload):
                normalized = key.replace("_", "").replace("-", "").lower()
                if normalized == "childsessionkey" and isinstance(value, str) and value:
                    keys.add(value)
    return keys


def _descendant_session_files(
    payload: dict[str, Any],
    *,
    allowed_state_dir: Path,
) -> dict[str, Path]:
    meta = payload.get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    raw_parent = agent_meta.get("sessionFile")
    if not isinstance(raw_parent, str) or raw_parent.startswith("sqlite:"):
        return {}
    try:
        state_root = allowed_state_dir.resolve(strict=True)
        parent = Path(raw_parent).resolve(strict=True)
        parent.relative_to(state_root)
    except (OSError, ValueError):
        return {}
    result: dict[str, Path] = {}
    for trajectory in sorted(parent.parent.glob("*.trajectory.jsonl")):
        try:
            if trajectory.is_symlink():
                continue
            resolved_trajectory = trajectory.resolve(strict=True)
            resolved_trajectory.relative_to(state_root)
            lines = resolved_trajectory.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError, ValueError):
            continue
        session_key: str | None = None
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict) or record.get("type") != "session.started":
                continue
            value = record.get("sessionKey")
            if isinstance(value, str) and value:
                session_key = value
                break
        if session_key is None:
            continue
        stem = trajectory.name[: -len(".trajectory.jsonl")]
        transcript = trajectory.with_name(f"{stem}.jsonl")
        try:
            resolved_transcript = transcript.resolve(strict=True)
            resolved_transcript.relative_to(state_root)
        except (OSError, ValueError):
            continue
        if resolved_transcript.is_file() and not resolved_transcript.is_symlink():
            result[session_key] = resolved_transcript
    return result


def _walk_values(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)


def _content_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [
            item.get("text")
            for item in value
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
    return []


def _parse_sqlite_session_marker(value: str) -> tuple[str, Path] | None:
    if not value.startswith("sqlite:"):
        return None
    parts = value.split(":", 3)
    if len(parts) != 4 or not all(parts[1:]):
        return None
    return parts[2], Path(parts[3])


def _load_sqlite_session_messages(
    store_path: Path,
    *,
    session_id: str,
    allowed_state_dir: Path | None,
) -> list[dict[str, Any]]:
    if allowed_state_dir is None:
        return []
    try:
        state_root = allowed_state_dir.resolve(strict=True)
        resolved_store = store_path.resolve(strict=True)
        resolved_store.relative_to(state_root)
    except (OSError, ValueError):
        return []
    if not resolved_store.is_file():
        return []
    try:
        connection = sqlite3.connect(
            f"file:{resolved_store}?mode=ro",
            uri=True,
            timeout=5,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            rows = connection.execute(
                """
                SELECT events.event_json
                FROM session_transcript_active_events AS active
                JOIN transcript_events AS events
                  ON events.session_id = active.session_id
                 AND events.seq = active.event_seq
                WHERE active.session_id = ?
                ORDER BY active.active_position
                """,
                (session_id,),
            ).fetchall()
            if not rows:
                rows = connection.execute(
                    """
                    SELECT event_json
                    FROM transcript_events
                    WHERE session_id = ?
                    ORDER BY seq
                    """,
                    (session_id,),
                ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return []
    records: list[dict[str, Any]] = []
    for (raw,) in rows:
        if not isinstance(raw, str):
            return []
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if (
            isinstance(record, dict)
            and record.get("type") == "message"
            and isinstance(record.get("message"), dict)
        ):
            records.append(record)
    return records


def _load_all_sqlite_session_records(
    store_path: Path,
    *,
    session_id: str,
    allowed_state_dir: Path,
) -> list[dict[str, Any]]:
    try:
        state_root = allowed_state_dir.resolve(strict=True)
        if store_path.is_symlink():
            return []
        resolved_store = store_path.resolve(strict=True)
        resolved_store.relative_to(state_root)
    except (OSError, ValueError):
        return []
    if not resolved_store.is_file():
        return []
    try:
        connection = sqlite3.connect(
            f"file:{resolved_store}?mode=ro",
            uri=True,
            timeout=5,
        )
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            rows = connection.execute(
                """
                SELECT event_json
                FROM transcript_events
                WHERE session_id = ?
                ORDER BY seq
                """,
                (session_id,),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError):
        return []
    records: list[dict[str, Any]] = []
    for (raw,) in rows:
        if not isinstance(raw, str):
            return []
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(record, dict):
            return []
        records.append(record)
    return records


def _messages_for_current_turn(
    records: list[dict[str, Any]],
    final_prompt_text: str | None,
) -> list[dict[str, Any]]:
    if not final_prompt_text:
        return records

    start_index = None
    for index, record in enumerate(records):
        message = record.get("message") or {}
        if message.get("role") != "user":
            continue
        if _message_text(message) == final_prompt_text:
            start_index = index

    if start_index is None:
        return records
    return records[start_index:]


def _message_text(message: dict[str, Any]) -> str:
    return _content_text(message.get("content"))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    texts = []
    for item in content:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts)


def _session_tool_call(record: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "tool_call_id": item.get("id"),
            "tool_name": item.get("name", "unknown"),
            "arguments": item.get("arguments", {}),
            "provider_executed": True,
            "source_message_id": record.get("id"),
        }
    )


def _session_tool_call_from_result(record: dict[str, Any]) -> dict[str, Any]:
    message = record.get("message") or {}
    return _without_none(
        {
            "tool_call_id": message.get("toolCallId"),
            "tool_name": message.get("toolName", "unknown"),
            "arguments": {},
            "provider_executed": True,
        }
    )


def _session_tool_result(record: dict[str, Any]) -> dict[str, Any]:
    message = record.get("message") or {}
    content = _content_text(message.get("content"))
    return _without_none(
        {
            "tool_call_id": message.get("toolCallId"),
            "tool_name": message.get("toolName", "unknown"),
            "content": content,
            "structured_output": _first_result_value(
                message,
                "structured_output",
                "structuredOutput",
            ),
            "error": _first_result_value(message, "error", "is_error", "isError"),
            **_openclaw_result_metadata(message, content),
        }
    )


def _run_started_raw_event(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    execution_trace = meta.get("executionTrace") or {}
    system_prompt_report = meta.get("systemPromptReport") or {}
    return {
        "sessionKey": payload.get("sessionKey"),
        "runId": payload.get("runId"),
        "agentMeta": {
            "sessionId": agent_meta.get("sessionId"),
            "sessionFile": "<runner-private>"
            if agent_meta.get("sessionFile")
            else None,
            "provider": agent_meta.get("provider"),
            "model": agent_meta.get("model"),
            "contextTokens": agent_meta.get("contextTokens"),
        },
        "executionTrace": {
            "winnerProvider": execution_trace.get("winnerProvider"),
            "winnerModel": execution_trace.get("winnerModel"),
            "fallbackUsed": execution_trace.get("fallbackUsed"),
            "runner": execution_trace.get("runner"),
        },
        "systemPromptReport": {
            "sessionKey": system_prompt_report.get("sessionKey"),
            "workspaceDir": system_prompt_report.get("workspaceDir"),
            "provider": system_prompt_report.get("provider"),
            "model": system_prompt_report.get("model"),
        },
    }


def _assistant_raw_event(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("meta") or {}
    return {
        "payloads": payload.get("payloads"),
        "finalAssistantVisibleText": meta.get("finalAssistantVisibleText"),
        "finalAssistantRawText": meta.get("finalAssistantRawText"),
        "completion": meta.get("completion"),
    }


def _usage_raw_event(agent_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "usage": agent_meta.get("usage"),
        "lastCallUsage": agent_meta.get("lastCallUsage"),
        "provider": agent_meta.get("provider"),
        "model": agent_meta.get("model"),
    }


def _complete_input_tokens(
    input_value: Any,
    cache_read: Any,
    cache_write: Any,
) -> int | str:
    if not isinstance(input_value, int) or isinstance(input_value, bool):
        return "unknown"
    return input_value + sum(
        value
        for value in (cache_read, cache_write)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
    )


def _extract_json_payload(stdout: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    last_payload: dict[str, Any] | None = None
    index = 0
    while index < len(stdout):
        start = stdout.find("{", index)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(stdout[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            last_payload = value
        index = start + end
    if last_payload is None:
        raise json.JSONDecodeError("No JSON object found", stdout, 0)
    return last_payload
