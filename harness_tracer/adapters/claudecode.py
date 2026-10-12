from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import (
    archive_claudecode_session,
    emit_context_compaction,
    extract_summary,
    write_archive_event,
)
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


class ClaudeCodeAdapter:
    def __init__(self, name: str = "claudecode") -> None:
        self.name = name
        self.allow_diagnostic_soft_exit = False
        self.native_child_delegation = False
        self.native_child_return_boundary = False

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        resume_session = extra_args.get("harnessbench-session-id")
        approval_probe = extra_args.get("harnessbench-approval-policy") == "ask_always"
        artifact_tools = [
            item
            for item in extra_args.get(
                "harnessbench-approval-artifact-tools", ""
            ).split(",")
            if item
        ]
        self.allow_diagnostic_soft_exit = (
            extra_args.get(
                "harnessbench-approval-claude-diagnostic-soft-exit"
            )
            == "true"
        )
        self.native_child_delegation = (
            extra_args.get("harnessbench-native-child-delegation") == "true"
        )
        self.native_child_return_boundary = (
            extra_args.get("harnessbench-native-child-return-boundary") == "true"
        )
        task_tools = extra_args.get("harnessbench-task-tools") == "true"
        task_tool_config = extra_args.get("harnessbench-task-tool-config")
        command = [
            harness_path or "claude",
            "-p",
            input_query,
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            model,
        ]
        if self.native_child_return_boundary:
            # Partial stream events are the only task-scoped source that can
            # expose an otherwise contentless native parent ``end_turn`` at
            # the time it occurs.  The ordinary Claude command stays byte-for-
            # byte unchanged when the L3d capability switch is absent.
            command.append("--include-partial-messages")
        if approval_probe:
            command.extend(
                ["--permission-mode", "manual", "--forward-subagent-text"]
            )
            if artifact_tools:
                command.extend(["--allowedTools", *artifact_tools])
        else:
            command.append("--dangerously-skip-permissions")
        if resume_session:
            command.extend(["--resume", resume_session])
        if task_tools:
            if not task_tool_config:
                raise ValueError("task tool MCP config path is missing")
            command.extend(
                ["--mcp-config", task_tool_config, "--strict-mcp-config"]
            )
        for key, value in sorted(extra_args.items()):
            if key.startswith("harnessbench-approval-") or key in {
                "harnessbench-conversation",
                "harnessbench-session-id",
                "harnessbench-task-tools",
                "harnessbench-task-tool-bridge",
                "harnessbench-task-tool-config",
                "harnessbench-native-child-delegation",
                "harnessbench-native-child-return-boundary",
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
    ) -> AdapterResult:
        result = AdapterResult(structured_trace_status="available")
        state = _StreamState()
        parse_failed = False
        stdout_line_times = process.stdout_line_observed_ns
        first_stdout_ns = stdout_line_times[0] if stdout_line_times else None

        for line_seq, line in enumerate(process.stdout.splitlines(), start=1):
            if not line.strip():
                continue
            stream_observed = _stream_observed_payload(
                line_seq=line_seq,
                observed_ns=(
                    stdout_line_times[line_seq - 1]
                    if line_seq <= len(stdout_line_times)
                    else None
                ),
                first_observed_ns=first_stdout_ns,
            )
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                parse_failed = True
                writer.write(
                    "raw_stdout",
                    text=line,
                    stream_seq=line_seq,
                    parseable=False,
                    parse_error=f"JSON parse error: {exc.msg}",
                    **stream_observed,
                )
                continue
            if not isinstance(payload, dict) or not self._map_payload(
                payload,
                process=process,
                writer=writer,
                result=result,
                state=state,
                stream_observed=stream_observed,
            ):
                writer.write(
                    "raw_stdout",
                    text=line,
                    stream_seq=line_seq,
                    parseable=True,
                    parse_error=None,
                    raw_event=payload,
                    **stream_observed,
                )

        if state.reasoning_output_tokens:
            writer.write(
                "token_usage",
                source="claudecode.thinking_tokens",
                usage_scope="internal",
                usage={
                    "reasoning_output_tokens": state.reasoning_output_tokens,
                },
                sample_count=state.reasoning_samples,
            )

        for pending in state.pending_tools.values():
            writer.write(
                "tool",
                tool_call=pending["tool_call"],
                tool_result=None,
                raw_event={"tool_call": pending["raw_event"], "tool_result": None},
                **pending["principal"],
            )

        if allowed_state_dir is not None:
            archive = archive_claudecode_session(
                allowed_state_dir=allowed_state_dir,
                session_id=state.session_id,
                run_dir=run_dir,
            )
            write_archive_event(
                writer,
                harness=self.name,
                session_id=state.session_id,
                archive=archive,
            )
            summary_artifact = (
                archive.path.relative_to(run_dir).as_posix()
                if archive.path is not None
                else None
            )
            for record in archive.records:
                if (
                    record.get("type") != "system"
                    or record.get("subtype") != "compact_boundary"
                ):
                    continue
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="claudecode.native_session",
                    native_event_type="system.compact_boundary",
                    record=record,
                    session_id=state.session_id,
                    summary=extract_summary(record),
                    summary_artifact=summary_artifact,
                    seen=state.seen_compactions,
                    notice_visible_to_model=True,
                )
            if self.native_child_return_boundary:
                _insert_native_child_return_boundaries(
                    writer,
                    records=archive.records,
                    session_id=state.session_id,
                )

        for index, line in enumerate(process.stderr.splitlines(), start=1):
            writer.write(
                "raw_stderr",
                text=line,
                stream_seq=index,
                severity="error" if process.returncode else "info",
            )

        if parse_failed or not state.mapped_any:
            result.structured_trace_status = "unavailable"
        return result

    def _map_payload(
        self,
        payload: dict[str, Any],
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        result: AdapterResult,
        state: _StreamState,
        stream_observed: dict[str, Any] | None = None,
    ) -> bool:
        record_type = payload.get("type")
        observed = stream_observed or {}
        if record_type == "system":
            mapped = self._map_system(payload, process, writer, state, observed)
        elif record_type == "assistant":
            mapped = self._map_assistant(payload, writer, state, observed)
        elif record_type == "user":
            mapped = self._map_user(payload, writer, state, observed)
        elif record_type == "result":
            mapped = self._map_result(payload, writer, result, state, observed)
        else:
            mapped = False
        state.mapped_any = state.mapped_any or mapped
        return mapped

    def _map_system(
        self,
        payload: dict[str, Any],
        process: CompletedProcessData,
        writer: EventWriter,
        state: _StreamState,
        stream_observed: dict[str, Any],
    ) -> bool:
        subtype = payload.get("subtype")
        if self.native_child_delegation and subtype in {
            "task_started",
            "task_notification",
        }:
            task_id = _string(payload.get("task_id"))
            tool_use_id = _string(payload.get("tool_use_id"))
            task_type = _string(payload.get("task_type"))
            if subtype == "task_started" and task_type == "local_agent" and task_id:
                principal_id = f"claudecode-child:{tool_use_id or task_id}"
                state.child_principals[task_id] = principal_id
                writer.write(
                    "subagent_lifecycle",
                    source="claudecode.stream_json",
                    action="started",
                    principal_id=principal_id,
                    child_principal_id=principal_id,
                    parent_principal_id=(
                        state.session_id
                        or _string(payload.get("session_id"))
                        or "main"
                    ),
                    principal_role="child",
                    native_task_id=task_id,
                    tool_call_id=tool_use_id,
                    content=payload.get("prompt"),
                    raw_event=payload,
                    **stream_observed,
                )
                return True
            principal_id = state.child_principals.get(task_id or "")
            if subtype == "task_notification" and principal_id:
                writer.write(
                    "task_completion",
                    source="claudecode.stream_json",
                    status=payload.get("status"),
                    content=payload.get("summary"),
                    principal_id=principal_id,
                    parent_principal_id=(
                        state.session_id
                        or _string(payload.get("session_id"))
                        or "main"
                    ),
                    principal_role="child",
                    parent_reactivated=True,
                    native_task_id=task_id,
                    tool_call_id=tool_use_id,
                    raw_event=payload,
                    **stream_observed,
                )
                return True
        if subtype == "compact_boundary":
            emit_context_compaction(
                writer,
                harness=self.name,
                source="claudecode.stream_json",
                native_event_type="system.compact_boundary",
                record=payload,
                session_id=state.session_id or _string(payload.get("session_id")),
                summary=extract_summary(payload),
                summary_artifact=None,
                seen=state.seen_compactions,
                notice_visible_to_model=True,
            )
            # Keep the pre-existing raw_stdout evidence for formerly unknown
            # system subtypes; the normalized event is strictly additive.
            return False
        if subtype == "thinking_tokens":
            delta = payload.get("estimated_tokens_delta")
            if isinstance(delta, int) and delta > 0:
                state.reasoning_output_tokens += delta
                state.reasoning_samples += 1
            return True
        if subtype != "init":
            return False

        raw = {"raw_event": payload, **stream_observed}
        session_id = _string(payload.get("session_id"))
        state.session_id = session_id or state.session_id
        if state.run_started:
            return True
        state.run_started = True
        model = _string(payload.get("model")) or _extract_arg(
            process.args,
            "--model",
        )
        writer.write(
            "run_started",
            source=self.name,
            session_id=session_id,
            claude_code_version=payload.get("claude_code_version"),
            cwd=payload.get("cwd"),
            **raw,
        )
        prompt = _extract_arg(process.args, "-p")
        writer.write(
            "model_input",
            turn_id=payload.get("uuid"),
            model=model,
            provider="claudecode",
            messages=(
                [{"role": "user", "content": prompt}]
                if prompt is not None
                else []
            ),
            tools=payload.get("tools")
            if isinstance(payload.get("tools"), list)
            else [],
            request_params={
                "permission_mode": payload.get("permissionMode"),
                "output_style": payload.get("output_style"),
            },
            **stream_observed,
        )
        return True

    def _map_assistant(
        self,
        payload: dict[str, Any],
        writer: EventWriter,
        state: _StreamState,
        stream_observed: dict[str, Any],
    ) -> bool:
        message = payload.get("message")
        if not isinstance(message, dict):
            return False
        mapped = False
        raw = {"raw_event": payload, **stream_observed}
        message_id = _string(message.get("id")) or _string(payload.get("uuid"))
        usage = _result_usage(message.get("usage"))
        if usage and message_id not in state.seen_assistant_usage:
            state.seen_assistant_usage.add(
                message_id or f"usage-{len(state.seen_assistant_usage) + 1}"
            )
            writer.write(
                "token_usage",
                source="claudecode.assistant",
                usage_scope="call",
                message_id=message_id,
                usage=usage,
                **raw,
            )
            mapped = True
        for block in _content_blocks(message.get("content")):
            block_type = block.get("type")
            if block_type == "tool_use":
                mapped = True
                call_id = _string(block.get("id"))
                if call_id is None:
                    call_id = f"claudecode-tool-{len(state.seen_tool_calls) + 1}"
                if call_id in state.seen_tool_calls:
                    continue
                state.seen_tool_calls.add(call_id)
                state.pending_tools[call_id] = {
                    "tool_call": {
                        "tool_call_id": call_id,
                        "tool_name": _string(block.get("name")) or "unknown",
                        "arguments": block.get("input")
                        if isinstance(block.get("input"), dict)
                        else {},
                        "provider_executed": True,
                    },
                    "raw_event": payload,
                    "stream_observed": stream_observed,
                    "principal": _principal_metadata(payload, state.session_id),
                }
                continue
            if block_type not in {"text", "thinking"}:
                continue
            mapped = True
            content = _string(block.get(block_type))
            if not content:
                continue
            block_key = (
                _string(message.get("id")),
                block_type,
                content,
            )
            if block_key in state.seen_assistant_blocks:
                continue
            state.seen_assistant_blocks.add(block_key)
            if block_type == "text":
                state.assistant_texts.add(content)
            writer.write(
                "assistant_message",
                message_id=message.get("id") or payload.get("uuid"),
                content=content,
                content_kind=block_type,
                model=message.get("model"),
                finish_reason=message.get("stop_reason"),
                **raw,
            )
        return mapped

    def _map_user(
        self,
        payload: dict[str, Any],
        writer: EventWriter,
        state: _StreamState,
        stream_observed: dict[str, Any],
    ) -> bool:
        message = payload.get("message")
        if not isinstance(message, dict):
            return False
        mapped = False
        for block in _content_blocks(message.get("content")):
            if block.get("type") != "tool_result":
                continue
            mapped = True
            call_id = _string(block.get("tool_use_id"))
            if call_id is None:
                call_id = f"claudecode-result-{len(state.seen_tool_results) + 1}"
            if call_id in state.seen_tool_results:
                continue
            state.seen_tool_results.add(call_id)
            pending = state.pending_tools.pop(call_id, None)
            tool_call = (
                pending["tool_call"]
                if pending is not None
                else {
                    "tool_call_id": call_id,
                    "tool_name": "unknown",
                    "arguments": {},
                    "provider_executed": True,
                }
            )
            principal = (
                pending["principal"]
                if pending
                else _principal_metadata(payload, state.session_id)
            )
            tool_result = {
                "tool_call_id": call_id,
                "tool_name": tool_call["tool_name"],
                "content": block.get("content"),
                "error": bool(block.get("is_error", False)),
            }
            writer.write(
                "tool",
                tool_call=tool_call,
                tool_result=tool_result,
                raw_event={
                    "tool_call": pending["raw_event"] if pending else None,
                    "tool_result": payload,
                },
                tool_call_stream_observed=(
                    pending.get("stream_observed") if pending else None
                ),
                **stream_observed,
                **principal,
            )
            meta = _tool_result_meta(payload, call_id)
            non_execution_kind = (
                _string(meta.get("non_execution_kind")) if meta else None
            )
            if non_execution_kind is not None:
                denied = non_execution_kind in {
                    "user-rejected",
                    "permission-denied",
                    "approval-unavailable",
                }
                writer.write(
                    "policy_event",
                    policy_kind="approval",
                    source="claudecode.tool_result_meta",
                    tool_call_id=call_id,
                    tool_name=tool_call["tool_name"],
                    decision="blocked" if denied else "unknown",
                    reason_code=("APPROVAL_DENIED" if denied else None),
                    reason_visible_to_model=bool(block.get("content")),
                    execution_started=False if denied else None,
                    native_policy_event=meta,
                    model_visible_result=tool_result,
                    **principal,
                )
        return mapped

    def _map_result(
        self,
        payload: dict[str, Any],
        writer: EventWriter,
        result: AdapterResult,
        state: _StreamState,
        stream_observed: dict[str, Any],
    ) -> bool:
        final_answer = _string(payload.get("result"))
        raw = {"raw_event": payload, **stream_observed}
        if self.native_child_return_boundary:
            _insert_completed_tool_turn_return_boundary(
                writer,
                payload=payload,
                session_id=state.session_id or _string(payload.get("session_id")),
                stream_observed=stream_observed,
            )
        if final_answer and final_answer not in state.assistant_texts:
            state.assistant_texts.add(final_answer)
            writer.write(
                "assistant_message",
                message_id=payload.get("uuid"),
                content=final_answer,
                content_kind="text",
                finish_reason=payload.get("stop_reason"),
                **raw,
            )

        model_usage = _model_usage_aggregate(payload.get("modelUsage"))
        usage = model_usage or _result_usage(payload.get("usage"))
        if usage:
            writer.write(
                "token_usage",
                source="claudecode.result",
                usage_scope="run",
                usage_aggregate=True,
                native_usage_source="modelUsage" if model_usage else "usage",
                usage=usage,
                reported_cost_usd=_nonnegative_number(payload.get("total_cost_usd")),
                **raw,
            )

        if (
            self.allow_diagnostic_soft_exit
            and _is_completed_tool_turn_diagnostic(payload)
        ):
            writer.write(
                "native_diagnostic",
                source="claudecode.result",
                diagnostic_type="completed_tool_turn_ede_diagnostic",
                process_exit_may_be_softened=True,
                **raw,
            )
            result.extra["soft_process_exit"] = "claudecode_completed_tool_turn_diagnostic"
        elif payload.get("is_error") is True or payload.get("subtype") in {
            "error",
            "failed",
        }:
            result.status = "failed"
            result.trace_complete = False
            result.error_type = "claudecode_result_error"
            result.error_message = final_answer or "Claude Code reported an error"
        else:
            # Claude Code can emit intermediate result records for failed child
            # turns before a later, successful root result. AdapterResult is an
            # aggregate, so the last ordinary result record is authoritative;
            # otherwise one child diagnostic permanently poisons a completed
            # root run and makes an intact trace unscorable.
            result.status = "finished"
            result.trace_complete = True
            result.error_type = None
            result.error_message = None
        return True


@dataclass(frozen=True)
class _NativeChildReturnBoundary:
    message_id: str
    tool_call_ids: tuple[str, ...]
    native_child_ids: tuple[str, ...]
    record_index: int
    raw_event: dict[str, Any]


def _insert_native_child_return_boundaries(
    writer: EventWriter,
    *,
    records: tuple[dict[str, Any], ...],
    session_id: str | None,
) -> None:
    """Insert archive-confirmed parent ``end_turn`` events in stream order.

    Claude's stream-json feed can omit ``message.stop_reason`` from incremental
    assistant records even though the bound native session persists
    ``end_turn``.  The archive is therefore the authority for the boundary;
    the matching stream message only determines where that native record sits
    in the normalized trace.  This projection is enabled solely by the
    task-scoped return-boundary switch.
    """

    for boundary in _native_child_return_boundaries(records):
        if not all(
            _has_normalized_async_agent_acceptance(writer.events, call_id)
            for call_id in boundary.tool_call_ids
        ):
            continue
        if any(
            _has_normalized_return_boundary(writer.events, call_id)
            for call_id in boundary.tool_call_ids
        ):
            continue

        matching_message_indexes = [
            index
            for index, event in enumerate(writer.events)
            if _event_message_id(event) == boundary.message_id
            and event.get("principal_role") != "child"
        ]
        completion_indexes = [
            index
            for index, event in enumerate(writer.events)
            if event.get("event_type") == "task_completion"
            and event.get("tool_call_id") in boundary.tool_call_ids
        ]
        if matching_message_indexes:
            insert_at = max(matching_message_indexes) + 1
            if completion_indexes and insert_at > min(completion_indexes):
                insert_at = min(completion_indexes)
        elif completion_indexes:
            insert_at = min(completion_indexes)
        else:
            continue

        writer.insert_many(
            insert_at,
            [
                (
                    "native_event",
                    {
                        "source": "claudecode.native_session",
                        "native_event_type": "assistant.end_turn",
                        "action": "returned",
                        "boundary_kind": "parent_turn_end",
                        "session_id": session_id,
                        "principal_id": session_id or "main",
                        "parent_principal_id": None,
                        "principal_role": "main",
                        "pending_child_ids": [
                            f"claudecode-child:{call_id}"
                            for call_id in boundary.tool_call_ids
                        ],
                        "native_child_ids": list(boundary.native_child_ids),
                        "tool_call_ids": list(boundary.tool_call_ids),
                        "parent_reactivated": False,
                        "native_record_index": boundary.record_index,
                        "source_timestamp": boundary.raw_event.get("timestamp"),
                        "raw_event": boundary.raw_event,
                    },
                )
            ],
        )


def _insert_completed_tool_turn_return_boundary(
    writer: EventWriter,
    *,
    payload: dict[str, Any],
    session_id: str | None,
    stream_observed: dict[str, Any],
) -> None:
    """Project Claude's explicit pre-notification empty-turn result.

    In print/SDK mode Claude can batch this native ``result`` record after the
    task notification that reactivates the session.  The record is still
    unambiguously the preceding invocation: it has no task-notification
    origin, reports a completed ``end_turn``, and carries the SDK's exact
    empty-assistant EDE diagnostic.  Insert it at its semantic boundary, never
    from the later notification alone.
    """

    if not _is_empty_completed_tool_turn_diagnostic(payload):
        return
    completion_by_call: dict[str, int] = {}
    for index, event in enumerate(writer.events):
        if (
            event.get("event_type") == "task_completion"
            and event.get("source") == "claudecode.stream_json"
            and event.get("parent_reactivated") is True
        ):
            call_id = _string(event.get("tool_call_id"))
            if call_id is not None:
                completion_by_call.setdefault(call_id, index)
    accepted_calls = {
        _string((event.get("tool_call") or {}).get("tool_call_id"))
        for event in writer.events
        if event.get("event_type") == "tool"
        and isinstance(event.get("tool_call"), dict)
    }
    calls = tuple(
        sorted(
            call_id
            for call_id in accepted_calls
            if call_id is not None
            and _has_normalized_async_agent_acceptance(writer.events, call_id)
            and not _has_normalized_return_boundary(writer.events, call_id)
        )
    )
    if not calls:
        return
    completed_calls = tuple(call_id for call_id in calls if call_id in completion_by_call)
    if completed_calls:
        insert_at = min(completion_by_call[call_id] for call_id in completed_calls)
        semantic_order_source = "pre_notification_completed_tool_turn_result"
        observed_after_child_notification = True
    else:
        insert_at = len(writer.events)
        semantic_order_source = "direct_completed_tool_turn_result"
        observed_after_child_notification = False
    writer.insert_many(
        insert_at,
        [
            (
                "native_event",
                {
                    "source": "claudecode.result",
                    "native_event_type": "result.completed_empty_end_turn",
                    "action": "returned",
                    "boundary_kind": "parent_turn_end",
                    "session_id": session_id,
                    "principal_id": session_id or "main",
                    "parent_principal_id": None,
                    "principal_role": "main",
                    "pending_child_ids": [
                        f"claudecode-child:{call_id}" for call_id in calls
                    ],
                    "tool_call_ids": list(calls),
                    "parent_reactivated": False,
                    "semantic_order_source": semantic_order_source,
                    "observed_after_child_notification": (
                        observed_after_child_notification
                    ),
                    **stream_observed,
                    "raw_event": payload,
                },
            )
        ],
    )


def _native_child_return_boundaries(
    records: tuple[dict[str, Any], ...],
) -> tuple[_NativeChildReturnBoundary, ...]:
    requested_calls: set[str] = set()
    active_calls: dict[str, str] = {}
    returned_calls: set[str] = set()
    seen_messages: set[str] = set()
    boundaries: list[_NativeChildReturnBoundary] = []

    for record_index, record in enumerate(records, start=1):
        for completed_call in _archive_task_notification_call_ids(record):
            active_calls.pop(completed_call, None)

        # Forwarded child transcript records share the parent session file but
        # carry parent_tool_use_id.  Only the root coordinator can establish
        # this boundary.
        if _string(record.get("parent_tool_use_id")) is not None:
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue

        if record.get("type") == "assistant":
            for block in _content_blocks(message.get("content")):
                if block.get("type") != "tool_use":
                    continue
                if str(block.get("name") or "").casefold() != "agent":
                    continue
                arguments = block.get("input")
                call_id = _string(block.get("id"))
                if (
                    call_id is not None
                    and isinstance(arguments, dict)
                    and arguments.get("run_in_background") is True
                ):
                    requested_calls.add(call_id)

            if message.get("stop_reason") != "end_turn":
                continue
            message_id = _string(message.get("id")) or _string(record.get("uuid"))
            pending = tuple(
                sorted(
                    call_id
                    for call_id in active_calls
                    if call_id not in returned_calls
                )
            )
            if message_id is None or message_id in seen_messages or not pending:
                continue
            seen_messages.add(message_id)
            returned_calls.update(pending)
            boundaries.append(
                _NativeChildReturnBoundary(
                    message_id=message_id,
                    tool_call_ids=pending,
                    native_child_ids=tuple(active_calls[call_id] for call_id in pending),
                    record_index=record_index,
                    raw_event=record,
                )
            )
            continue

        if record.get("type") != "user":
            continue
        tool_use_result = record.get("tool_use_result")
        if not isinstance(tool_use_result, dict):
            tool_use_result = record.get("toolUseResult")
        metadata_child_id = None
        if (
            isinstance(tool_use_result, dict)
            and tool_use_result.get("isAsync") is True
            and str(tool_use_result.get("status") or "").casefold()
            == "async_launched"
        ):
            metadata_child_id = _string(tool_use_result.get("agentId"))
        for block in _content_blocks(message.get("content")):
            call_id = _string(block.get("tool_use_id"))
            child_id = metadata_child_id or _async_agent_id(block.get("content"))
            if (
                block.get("type") == "tool_result"
                and block.get("is_error") is not True
                and call_id in requested_calls
                and child_id is not None
            ):
                active_calls[call_id] = child_id

    return tuple(boundaries)


def _archive_task_notification_call_ids(record: dict[str, Any]) -> tuple[str, ...]:
    content: Any = None
    if (
        record.get("type") == "queue-operation"
        and record.get("operation") == "enqueue"
    ):
        content = record.get("content")
    elif record.get("type") == "user":
        origin = record.get("origin")
        if isinstance(origin, dict) and origin.get("kind") == "task-notification":
            message = record.get("message")
            if isinstance(message, dict):
                content = message.get("content")
    text = "\n".join(_text_leaves(content))
    if "<task-notification>" not in text:
        return ()
    return tuple(
        match.group(1).strip()
        for match in re.finditer(r"<tool-use-id>([^<]+)</tool-use-id>", text)
        if match.group(1).strip()
    )


def _async_agent_id(content: Any) -> str | None:
    text = "\n".join(_text_leaves(content))
    if (
        "Async agent launched successfully." not in text
        or "working in the background" not in text
    ):
        return None
    match = re.search(r"(?:^|\n)agentId:\s*([^\s]+)", text)
    return match.group(1) if match is not None else None


def _text_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _text_leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in _text_leaves(child)]
    return []


def _has_normalized_async_agent_acceptance(
    events: list[dict[str, Any]], call_id: str
) -> bool:
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        result = event.get("tool_result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            continue
        arguments = call.get("arguments")
        if (
            call.get("tool_call_id") == call_id
            and str(call.get("tool_name") or "").casefold() == "agent"
            and isinstance(arguments, dict)
            and arguments.get("run_in_background") is True
            and result.get("error") in (None, False)
        ):
            return True
    return False


def _has_normalized_return_boundary(
    events: list[dict[str, Any]], call_id: str
) -> bool:
    for event in events:
        if (
            event.get("event_type") == "native_event"
            and event.get("action") == "returned"
            and event.get("boundary_kind") == "parent_turn_end"
        ):
            call_ids = event.get("tool_call_ids")
            if isinstance(call_ids, list) and call_id in call_ids:
                return True
    return False


def _event_message_id(event: dict[str, Any]) -> str | None:
    raw = event.get("raw_event")
    if not isinstance(raw, dict):
        return None
    message = raw.get("message")
    if not isinstance(message, dict):
        return None
    return _string(message.get("id")) or _string(raw.get("uuid"))


def _is_completed_tool_turn_diagnostic(payload: dict[str, Any]) -> bool:
    if payload.get("terminal_reason") != "completed":
        return False
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors:
        return False
    joined = "\n".join(str(error) for error in errors)
    if "ede_diagnostic" not in joined or any(
        marker in joined.lower()
        for marker in ("timeout", "authentication", "rate limit", "overloaded")
    ):
        return False
    if "response_format type is unavailable" in joined:
        return True
    return all(
        str(error).lstrip().startswith("[ede_diagnostic]") for error in errors
    )


def _is_empty_completed_tool_turn_diagnostic(payload: dict[str, Any]) -> bool:
    if (
        payload.get("type") != "result"
        or payload.get("subtype") != "error_during_execution"
        or payload.get("is_error") is not True
        or payload.get("stop_reason") != "end_turn"
        or not _is_completed_tool_turn_diagnostic(payload)
    ):
        return False
    origin = payload.get("origin")
    if isinstance(origin, dict) and origin.get("kind") == "task-notification":
        return False
    errors = payload.get("errors")
    assert isinstance(errors, list)
    return any(
        re.search(
            r"\[ede_diagnostic\].*\bresult_type=assistant\b.*"
            r"\blast_content_type=none\b.*\bstop_reason=end_turn\b",
            str(error),
        )
        is not None
        for error in errors
    )


@dataclass
class _StreamState:
    pending_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    seen_tool_calls: set[str] = field(default_factory=set)
    seen_tool_results: set[str] = field(default_factory=set)
    seen_assistant_blocks: set[tuple[str | None, str, str]] = field(
        default_factory=set
    )
    assistant_texts: set[str] = field(default_factory=set)
    seen_assistant_usage: set[str] = field(default_factory=set)
    reasoning_output_tokens: int = 0
    reasoning_samples: int = 0
    mapped_any: bool = False
    session_id: str | None = None
    seen_compactions: set[str] = field(default_factory=set)
    child_principals: dict[str, str] = field(default_factory=dict)
    run_started: bool = False


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _tool_result_meta(payload: dict[str, Any], call_id: str) -> dict[str, Any] | None:
    rows = payload.get("tool_result_meta")
    if not isinstance(rows, list):
        return None
    return next(
        (
            row
            for row in rows
            if isinstance(row, dict) and _string(row.get("id")) == call_id
        ),
        None,
    )


def _stream_observed_payload(
    *,
    line_seq: int,
    observed_ns: int | None,
    first_observed_ns: int | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"stream_line_seq": line_seq}
    if observed_ns is not None and first_observed_ns is not None:
        result["stream_observed_offset_ms"] = round(
            (observed_ns - first_observed_ns) / 1_000_000, 3
        )
    return result


def _principal_metadata(
    payload: dict[str, Any], session_id: str | None
) -> dict[str, Any]:
    """Preserve Claude's native subagent-to-parent tool-use linkage."""

    parent_tool_use_id = _string(payload.get("parent_tool_use_id"))
    if parent_tool_use_id is None:
        return {
            "principal_id": session_id or _string(payload.get("session_id")) or "main",
            "parent_principal_id": None,
            "principal_role": "main",
        }
    return {
        "principal_id": f"claudecode-child:{parent_tool_use_id}",
        "parent_principal_id": session_id or _string(payload.get("session_id")) or "main",
        "parent_tool_use_id": parent_tool_use_id,
        "principal_role": "child",
    }


def _result_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    usage = {}
    mappings = {
        "input_tokens": "input_tokens",
        "cache_read_input_tokens": "cached_input_tokens",
        "output_tokens": "output_tokens",
        "cache_creation_input_tokens": "cache_creation_input_tokens",
    }
    for source, target in mappings.items():
        amount = value.get(source)
        if isinstance(amount, int):
            usage[target] = amount
            if source == "cache_creation_input_tokens":
                usage["cache_write_input_tokens"] = amount
    # Claude reports uncached input, cache reads, and cache creation as
    # separate components.  Normalize input_tokens to the complete model input
    # so it is directly comparable with Codex/OpenAI usage.
    raw_input = usage.get("input_tokens")
    if isinstance(raw_input, int):
        usage["input_tokens"] = raw_input + sum(
            usage.get(key, 0)
            for key in ("cached_input_tokens", "cache_write_input_tokens")
            if isinstance(usage.get(key, 0), int)
        )
    return usage


def _model_usage_aggregate(value: Any) -> dict[str, int]:
    """Normalize Claude Code's per-model, full-run usage aggregate.

    Current Claude Code result events expose both ``usage`` (the terminal model
    call) and ``modelUsage`` (the whole run, grouped by model).  The latter is
    the only complete total for multi-call agent runs.  Require every model row
    to provide a field before publishing its aggregate so an absent cache field
    remains unknown instead of silently becoming zero.
    """

    if not isinstance(value, dict) or not value:
        return {}
    rows = list(value.values())
    if any(not isinstance(row, dict) for row in rows):
        return {}

    mappings = {
        "inputTokens": "input_tokens",
        "cacheReadInputTokens": "cache_read_input_tokens",
        "cacheCreationInputTokens": "cache_creation_input_tokens",
        "outputTokens": "output_tokens",
    }
    aggregate: dict[str, int] = {}
    for source, target in mappings.items():
        amounts = [row.get(source) for row in rows]
        if all(
            isinstance(amount, int)
            and not isinstance(amount, bool)
            and amount >= 0
            for amount in amounts
        ):
            aggregate[target] = sum(amounts)

    if "input_tokens" not in aggregate or "output_tokens" not in aggregate:
        return {}
    return _result_usage(aggregate)


def _extract_arg(args: list[str], flag: str) -> str | None:
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _nonnegative_number(value: Any) -> int | float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return value
    return None
