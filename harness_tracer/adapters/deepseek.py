from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import (
    archive_records,
    emit_context_compaction,
    write_archive_event,
)
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter


class DeepSeekAdapter:
    """Normalize the official DeepSeek Harness SDK notification stream."""

    name = "deepseek"

    def __init__(self) -> None:
        # SDK notifications are incremental.  Keep the native model-visible
        # surface and lineage across calls so a resumed turn's request/header
        # can be reconstructed from the same state as the Harness runtime.
        # A surface node can intentionally project to no model message (the
        # runtime uses an empty assistant/message to carry usage). Retaining
        # that node's seq is still necessary because a later replace operation
        # can name it as a range boundary.
        self._surfaces: dict[
            str, list[tuple[int, dict[str, Any] | None]]
        ] = {}
        self._parents: dict[str, str] = {}
        self._headers: dict[str, dict[str, Any]] = {}
        self._emitted_requests: set[tuple[str, int | None, int | None]] = set()
        self._active_children: dict[str, set[str]] = {}
        self._returned_children: set[tuple[str, str]] = set()
        self.continuable_subagents = False

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        del input_query, run_dir
        self.continuable_subagents = (
            extra_args.get("harnessbench-deepseek-continuable-subagents") == "true"
        )
        session_id = extra_args.get("harnessbench-session-id")
        command = [harness_path or "dsh-jsonrpc-agent", "sdk/run", "--model", model]
        if session_id:
            command.extend(["--session-id", session_id])
        return command

    def consume(
        self,
        *,
        process: CompletedProcessData,
        writer: EventWriter,
        run_dir: Path,
        env: dict[str, str] | None = None,
    ) -> AdapterResult:
        """Compatibility entry point for a one-shot JSON SDK wrapper.

        Docker runs use :class:`tracetrial_bench.deepseek_runner.DeepSeekTraceRunner`
        so one SDK runtime survives every conversation turn.  This method keeps
        the adapter independently testable and accepts the same result object on
        stdout when a caller supplies its own wrapper executable.
        """

        del env
        try:
            payload = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            writer.write(
                "raw_stdout",
                text=process.stdout,
                parseable=False,
                parse_error=f"JSON parse error: {exc}",
            )
            return AdapterResult(
                status="failed",
                trace_complete=False,
                structured_trace_status="unavailable",
                error_type="deepseek_sdk_protocol",
                error_message="DeepSeek SDK wrapper returned invalid JSON",
            )
        if not isinstance(payload, dict):
            return AdapterResult(
                status="failed",
                trace_complete=False,
                structured_trace_status="unavailable",
                error_type="deepseek_sdk_protocol",
                error_message="DeepSeek SDK wrapper returned a non-object result",
            )
        return self.consume_result(payload, writer=writer, run_dir=run_dir)

    def consume_result(
        self,
        result_payload: dict[str, Any],
        *,
        writer: EventWriter,
        run_dir: Path,
    ) -> AdapterResult:
        session_id = _first_string(
            result_payload.get("sessionId"), result_payload.get("session_id")
        )
        notifications = _notifications(result_payload, session_id)
        archive = archive_records(
            harness=self.name,
            session_id=session_id,
            records=notifications,
            run_dir=run_dir,
            destination_name="deepseek-session.trace.jsonl",
        )
        write_archive_event(
            writer,
            harness=self.name,
            session_id=session_id,
            archive=archive,
        )

        self._parents.update(_parent_sessions(notifications))
        parents = self._parents
        surfaces = self._surfaces
        pending_tools: dict[tuple[str, str], dict[str, Any]] = {}
        pending_approvals: dict[tuple[str, str], dict[str, Any]] = {}
        finish_by_step: dict[tuple[str, int | None, int | None], Any] = {}
        seen_compactions: set[str] = set()
        root_finish_reason: Any = result_payload.get("finishReason")
        parsed_events = 0

        for notification_index, envelope in enumerate(notifications, start=1):
            method = envelope.get("method")
            params = envelope.get("params")
            if not isinstance(params, dict):
                writer.write(
                    "native_event",
                    source="deepseek.sdk",
                    native_method=method,
                    notification_index=notification_index,
                    raw_event=envelope,
                )
                continue

            if method in {"subagent.started", "subagent.finished"}:
                child_id = _first_string(params.get("childSessionId"))
                parent_id = _first_string(params.get("parentSessionId"))
                if self.continuable_subagents and child_id and parent_id:
                    active = self._active_children.setdefault(parent_id, set())
                    if method == "subagent.started":
                        active.add(child_id)
                    else:
                        active.discard(child_id)
                writer.write(
                    "subagent_lifecycle",
                    source="deepseek.sdk",
                    action="started" if method == "subagent.started" else "finished",
                    principal_id=child_id,
                    parent_principal_id=parent_id,
                    principal_role="child",
                    status=params.get("status"),
                    stop_reason=params.get("stopReason"),
                    provider=params.get("provider"),
                    last_assistant_message=params.get("lastAssistantMessage"),
                    raw_event=envelope,
                )
                continue

            if method == "session.status":
                current_session = _first_string(params.get("sessionId"))
                origin = _origin(current_session, session_id, parents)
                writer.write(
                    "native_event",
                    source="deepseek.sdk",
                    native_method=method,
                    status=params.get("status"),
                    **origin,
                    raw_event=envelope,
                )
                continue

            if method != "session.event":
                writer.write(
                    "native_event",
                    source="deepseek.sdk",
                    native_method=method,
                    raw_event=envelope,
                )
                continue

            current_session = _first_string(params.get("sessionId")) or session_id
            native = params.get("event")
            if not isinstance(native, dict):
                writer.write(
                    "native_event",
                    source="deepseek.sdk",
                    native_method=method,
                    raw_event=envelope,
                )
                continue
            parsed_events += 1
            native_type = str(native.get("type") or "unknown")
            data = native.get("data") if isinstance(native.get("data"), dict) else {}
            native_seq = native.get("seq")
            seq = native_seq if isinstance(native_seq, int) else notification_index
            origin = _origin(current_session, session_id, parents)
            raw = {"raw_event": native, **origin}

            if native_type == "user/message":
                _apply_surface(
                    surfaces,
                    current_session,
                    native,
                    seq,
                    _history_message("user", data),
                )
                settlement = (
                    _subagent_settlement(data, current_session, parents)
                    if self.continuable_subagents
                    else None
                )
                if settlement is not None:
                    writer.write(
                        "task_completion",
                        source="deepseek.native_session",
                        status=settlement["status"],
                        content=settlement["content"],
                        model_visible_content=settlement["model_visible_content"],
                        principal_id=settlement["child_id"],
                        parent_principal_id=current_session,
                        principal_role="child",
                        parent_reactivated=True,
                        native_task_id=settlement["child_id"],
                        session_id=current_session,
                        raw_event=native,
                    )
                writer.write(
                    "native_event",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    **raw,
                )
                continue

            if native_type == "request/header":
                header = data.get("header") if isinstance(data.get("header"), dict) else {}
                self._headers[current_session or ""] = {
                    "header": deepcopy(header),
                    "raw_event": deepcopy(native),
                }
                writer.write(
                    "native_event",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    **raw,
                )
                continue

            if native_type == "assistant/chunk":
                self._emit_model_input(
                    writer,
                    session_id=current_session,
                    turn=data.get("turn"),
                    step=data.get("step"),
                    dispatch_event=native,
                    origin=origin,
                )
                chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
                chunk_type = chunk.get("type")
                if chunk_type in {"text-delta", "reasoning-delta"}:
                    writer.write(
                        "assistant_delta",
                        session_id=current_session,
                        turn_id=data.get("turn"),
                        step_id=data.get("step"),
                        delta=chunk.get("text", ""),
                        content_kind=(
                            "thinking" if chunk_type == "reasoning-delta" else "text"
                        ),
                        **raw,
                    )
                elif chunk_type == "finish":
                    finish_by_step[
                        (current_session or "", data.get("turn"), data.get("step"))
                    ] = chunk.get("reason")
                    writer.write(
                        "native_event",
                        source="deepseek.native_session",
                        native_event_type=native_type,
                        session_id=current_session,
                        **raw,
                    )
                else:
                    writer.write(
                        "native_event",
                        source="deepseek.native_session",
                        native_event_type=native_type,
                        session_id=current_session,
                        **raw,
                    )
                continue

            if native_type == "step/end":
                self._emit_model_input(
                    writer,
                    session_id=current_session,
                    turn=data.get("turn"),
                    step=data.get("step"),
                    dispatch_event=native,
                    origin=origin,
                )
                writer.write(
                    "native_event",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    **raw,
                )
                continue

            if native_type == "assistant/message":
                # Normal streams emit assistant/chunk first. Keep the complete
                # message as a fallback dispatch boundary for providers or
                # fixtures that do not expose deltas; emit before folding the
                # response into the next request's surface.
                self._emit_model_input(
                    writer,
                    session_id=current_session,
                    turn=data.get("turn"),
                    step=data.get("step"),
                    dispatch_event=native,
                    origin=origin,
                )
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                blocks = message.get("content") if isinstance(message.get("content"), list) else []
                _apply_surface(
                    surfaces,
                    current_session,
                    native,
                    seq,
                    _history_message("assistant", message) if blocks else None,
                )
                finish = finish_by_step.get(
                    (current_session or "", data.get("turn"), data.get("step"))
                )
                finish_kind = _finish_kind(finish)
                message_id = _first_string(message.get("id")) or (
                    f"deepseek:{current_session}:{seq}"
                )
                emitted = 0
                for block_index, block in enumerate(blocks):
                    if not isinstance(block, dict) or block.get("type") not in {
                        "text",
                        "reasoning",
                    }:
                        continue
                    writer.write(
                        "assistant_message",
                        session_id=current_session,
                        turn_id=data.get("turn"),
                        step_id=data.get("step"),
                        message_id=(
                            message_id
                            if emitted == 0
                            else f"{message_id}:block:{block_index}"
                        ),
                        content=str(block.get("text") or ""),
                        content_kind=(
                            "thinking" if block.get("type") == "reasoning" else "text"
                        ),
                        finish_reason=finish_kind,
                        **raw,
                    )
                    emitted += 1
                usage = data.get("usage")
                if isinstance(usage, dict):
                    writer.write(
                        "token_usage",
                        session_id=current_session,
                        turn_id=data.get("turn"),
                        step_id=data.get("step"),
                        source="deepseek.assistant_message",
                        usage_scope="call",
                        usage=_usage(usage),
                        **raw,
                    )
                continue

            if native_type == "tool/call":
                call_id = _first_string(data.get("callId")) or f"deepseek-tool:{seq}"
                pending_tools[(current_session or "", call_id)] = {
                    "tool_call": _tool_call(data, call_id),
                    "raw_event": native,
                    "origin": origin,
                    "session_id": current_session,
                    "turn_id": data.get("turn"),
                    "step_id": data.get("step"),
                }
                continue

            if native_type == "tool/result":
                message = data.get("message") if isinstance(data.get("message"), dict) else {}
                call_id = _tool_result_call_id(message) or f"deepseek-tool:{seq}"
                pending = pending_tools.pop((current_session or "", call_id), None)
                tool_call = (
                    pending["tool_call"]
                    if pending is not None
                    else {
                        "tool_call_id": call_id,
                        "tool_name": "unknown",
                        "arguments": {},
                    }
                )
                blocks = message.get("content") if isinstance(message.get("content"), list) else []
                result = {
                    "tool_call_id": call_id,
                    "tool_name": tool_call.get("tool_name"),
                    "content": _content_text(blocks),
                    "content_blocks": blocks,
                    "error": _tool_result_error(blocks, data),
                    "error_info": data.get("error"),
                    "metadata": data.get("meta"),
                }
                writer.write(
                    "tool",
                    session_id=current_session,
                    turn_id=data.get("turn"),
                    step_id=data.get("step"),
                    tool_call=tool_call,
                    tool_result=result,
                    raw_event={
                        "tool_call": pending["raw_event"] if pending else None,
                        "tool_result": native,
                    },
                    **(pending["origin"] if pending else origin),
                )
                _apply_surface(
                    surfaces,
                    current_session,
                    native,
                    seq,
                    _history_message("user", message),
                )
                continue

            if native_type == "compaction/summary":
                summary = _content_text(data.get("summary"))
                compaction_record = {**native, "id": data.get("compactionId")}
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    record=compaction_record,
                    session_id=current_session,
                    summary=summary or None,
                    summary_artifact=None,
                    seen=seen_compactions,
                    notice_visible_to_model=True,
                )
                usage = data.get("usage")
                if isinstance(usage, dict):
                    writer.write(
                        "token_usage",
                        session_id=current_session,
                        source="deepseek.compaction_summary",
                        usage_scope="internal",
                        compaction_id=data.get("compactionId"),
                        provider=data.get("provider"),
                        model=data.get("model"),
                        usage=_usage(usage),
                        **raw,
                    )
                continue

            if native_type == "compaction/prune":
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    record=native,
                    session_id=current_session,
                    summary=None,
                    summary_artifact=None,
                    seen=seen_compactions,
                    notice_visible_to_model=True,
                )
                continue

            if native_type == "turn/end":
                reason = data.get("reason")
                if current_session == session_id:
                    root_finish_reason = reason
                pending_children = (
                    sorted(
                        child_id
                        for child_id in self._active_children.get(
                            current_session or "", set()
                        )
                        if (current_session or "", child_id)
                        not in self._returned_children
                    )
                    if self.continuable_subagents
                    else []
                )
                boundary: dict[str, Any] = {}
                if current_session == session_id and pending_children:
                    boundary = {
                        "action": "returned",
                        "boundary_kind": "parent_turn_end",
                        "pending_child_ids": pending_children,
                        "parent_reactivated": False,
                    }
                    self._returned_children.update(
                        (current_session or "", child_id)
                        for child_id in pending_children
                    )
                writer.write(
                    "native_event",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    finish_reason=_finish_kind(reason),
                    **boundary,
                    **raw,
                )
                continue

            if native_type == "approval/asked":
                approval_id = _first_string(data.get("id"))
                if approval_id is not None:
                    pending_approvals[(current_session or "", approval_id)] = {
                        "data": data,
                        "origin": origin,
                    }
                writer.write(
                    "policy_event",
                    policy_kind="approval",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    approval_id=approval_id,
                    tool_call_id=_first_string(data.get("callId")),
                    tool_name=_first_string(data.get("toolName")),
                    decision="pending",
                    reason_code="APPROVAL_REQUIRED",
                    reason_visible_to_model=False,
                    execution_started=False,
                    native_policy_event=data,
                    **raw,
                )
                continue

            if native_type == "approval/decided":
                approval_id = _first_string(data.get("id"))
                asked = pending_approvals.pop(
                    (current_session or "", approval_id or ""), None
                )
                asked_data = asked.get("data", {}) if asked else {}
                outcome = _first_string(data.get("outcome"))
                decision = {
                    "allowed": "allowed",
                    "rejected": "blocked",
                    "cancelled": "cancelled",
                    "unavailable": "unavailable",
                }.get(outcome or "", "unknown")
                reason_code = {
                    "rejected": "APPROVAL_DENIED",
                    "cancelled": "APPROVAL_CANCELLED",
                    "unavailable": "APPROVAL_CLIENT_UNAVAILABLE",
                }.get(outcome or "")
                writer.write(
                    "policy_event",
                    policy_kind="approval",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    approval_id=approval_id,
                    tool_call_id=_first_string(asked_data.get("callId")),
                    tool_name=_first_string(asked_data.get("toolName")),
                    decision=decision,
                    reason_code=reason_code,
                    reason_visible_to_model=decision in {
                        "blocked", "cancelled", "unavailable"
                    },
                    execution_started=(False if decision != "allowed" else None),
                    native_policy_event={"asked": asked_data, "decided": data},
                    **raw,
                )
                continue

            if native_type.startswith("approval/") or native_type.startswith(
                "sandbox/"
            ) or native_type.startswith("permissionPresets/"):
                writer.write(
                    "policy_event",
                    source="deepseek.native_session",
                    native_event_type=native_type,
                    session_id=current_session,
                    policy=data,
                    **raw,
                )
                continue

            writer.write(
                "native_event",
                source="deepseek.native_session",
                native_event_type=native_type,
                session_id=current_session,
                **raw,
            )

        for (_, _), pending in pending_tools.items():
            writer.write(
                "tool",
                session_id=pending["session_id"],
                turn_id=pending["turn_id"],
                step_id=pending["step_id"],
                tool_call=pending["tool_call"],
                tool_result=None,
                raw_event={"tool_call": pending["raw_event"], "tool_result": None},
                **pending["origin"],
            )

        finish_kind = _finish_kind(root_finish_reason)
        failed = finish_kind in {"error", "aborted", "blocked", "interrupted"}
        error_message = _finish_error(root_finish_reason) if failed else None
        if failed and not error_message:
            error_message = f"DeepSeek turn ended: {finish_kind}"
        if failed:
            writer.write(
                "run_failed",
                error_type=f"deepseek_turn_{finish_kind}",
                error_message=error_message,
                exit_code=0,
                timed_out=False,
                session_id=session_id,
            )

        structured_available = parsed_events > 0 and archive.status == "available"
        return AdapterResult(
            status="failed" if failed else "finished",
            trace_complete=structured_available and not failed,
            structured_trace_status=(
                "available" if structured_available else "unavailable"
            ),
            error_type=f"deepseek_turn_{finish_kind}" if failed else None,
            error_message=error_message,
            extra={
                "session_id": session_id,
                "finish_reason": finish_kind,
                "final_response": result_payload.get("finalResponse"),
                "native_notification_count": len(notifications),
            },
        )

    def _emit_model_input(
        self,
        writer: EventWriter,
        *,
        session_id: str | None,
        turn: Any,
        step: Any,
        dispatch_event: dict[str, Any],
        origin: dict[str, Any],
    ) -> None:
        key = (session_id or "", turn, step)
        if key in self._emitted_requests:
            return
        header_state = self._headers.get(session_id or "")
        if header_state is None:
            return
        header = header_state["header"]
        call_config = (
            header.get("config") if isinstance(header.get("config"), dict) else {}
        )
        self._emitted_requests.add(key)
        writer.write(
            "model_input",
            turn_id=f"{session_id}:turn:{turn}:step:{step}",
            session_id=session_id,
            native_turn=turn,
            native_step=step,
            model=call_config.get("model"),
            provider=call_config.get("provider", "deepseek-official"),
            messages=[
                deepcopy(message)
                for _, message in self._surfaces.get(session_id or "", [])
                if message is not None
            ],
            system=header.get("system"),
            tools=deepcopy(header.get("tools", [])),
            request_params=deepcopy(call_config),
            adapter_defaults=deepcopy(header.get("adapterDefaults")),
            reconstruction="deepseek_native_surface",
            raw_event={
                "request_header": header_state["raw_event"],
                "dispatch_boundary": dispatch_event,
            },
            **origin,
        )


def _notifications(
    payload: dict[str, Any], session_id: str | None
) -> list[dict[str, Any]]:
    raw = payload.get("notifications")
    if isinstance(raw, list):
        notifications = [item for item in raw if isinstance(item, dict)]
        if notifications:
            return notifications
    events = payload.get("events")
    if not isinstance(events, list):
        return []
    return [
        {
            "method": "session.event",
            "params": {"sessionId": session_id, "event": event},
        }
        for event in events
        if isinstance(event, dict)
    ]


def _parent_sessions(notifications: Iterable[dict[str, Any]]) -> dict[str, str]:
    parents: dict[str, str] = {}
    for envelope in notifications:
        if envelope.get("method") != "subagent.started":
            continue
        params = envelope.get("params")
        if not isinstance(params, dict):
            continue
        child = _first_string(params.get("childSessionId"))
        parent = _first_string(params.get("parentSessionId"))
        if child and parent:
            parents[child] = parent
    return parents


def _origin(
    current: str | None, root: str | None, parents: dict[str, str]
) -> dict[str, Any]:
    return {
        "principal_id": current or root or "main",
        "parent_principal_id": parents.get(current or ""),
        "principal_role": "main" if current == root or current is None else "child",
    }


def _subagent_settlement(
    data: dict[str, Any],
    current_session: str | None,
    parents: dict[str, str],
) -> dict[str, str] | None:
    """Project only the runtime-owned continuable-child settlement notice.

    The source object is minted by ``@deepseek-ai/dsh-subagent``.  Requiring
    its typed provenance and the SDK's child-to-parent lineage prevents an
    ordinary user message (or child-authored prose) from becoming completion
    evidence.
    """

    source = data.get("source")
    if not isinstance(source, dict):
        return None
    child_id = _first_string(source.get("senderSessionId"))
    if (
        source.get("kind") != "subagent-settled"
        or source.get("form") != "notice"
        or child_id is None
        or current_session is None
        or parents.get(child_id) != current_session
    ):
        return None

    blocks = data.get("content")
    model_visible = _content_text(blocks)
    closing = _closing_message_text(blocks) or model_visible
    summary = _first_string(source.get("summary")) or model_visible
    lowered = summary.casefold()
    if "finished" in lowered:
        status = "completed"
    elif "failed" in lowered:
        status = "failed"
    elif "declined" in lowered:
        status = "declined"
    elif "out of room" in lowered or "token" in lowered:
        status = "max_tokens"
    elif "stopped" in lowered or "cancel" in lowered:
        status = "cancelled"
    else:
        status = "settled"
    return {
        "child_id": child_id,
        "status": status,
        "content": closing,
        "model_visible_content": model_visible,
    }


def _closing_message_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    marker_index: int | None = None
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        if str(block.get("text") or "").strip() == "Its closing message:":
            marker_index = index
            break
    if marker_index is None:
        return ""
    return _content_text(content[marker_index + 1 :])


def _apply_surface(
    surfaces: dict[str, list[tuple[int, dict[str, Any] | None]]],
    session_id: str | None,
    event: dict[str, Any],
    seq: int,
    message: dict[str, Any] | None,
) -> None:
    key = session_id or ""
    surface = surfaces.setdefault(key, [])
    operation = event.get("surfaceOp")
    if isinstance(operation, dict) and operation.get("op") == "replace":
        start = operation.get("start")
        end = operation.get("end")
        start_index = next(
            (index for index, (value, _) in enumerate(surface) if value == start), None
        )
        end_index = next(
            (index for index, (value, _) in enumerate(surface) if value == end), None
        )
        if (
            start_index is not None
            and end_index is not None
            and start_index <= end_index
        ):
            surface[start_index : end_index + 1] = [(seq, message)]
            return
    surface.append((seq, message))


def _history_message(role: str, value: dict[str, Any]) -> dict[str, Any]:
    message = deepcopy(value)
    message.setdefault("role", role)
    if not isinstance(message.get("content"), list):
        message["content"] = []
    return message


def _tool_call(data: dict[str, Any], call_id: str) -> dict[str, Any]:
    raw_arguments = data.get("arguments")
    arguments: Any = {}
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            arguments = parsed
    return {
        "tool_call_id": call_id,
        "tool_name": str(data.get("name") or "unknown"),
        "arguments": arguments,
        "raw_arguments": raw_arguments,
    }


def _tool_result_call_id(message: dict[str, Any]) -> str | None:
    source = message.get("source") if isinstance(message.get("source"), dict) else {}
    direct = _first_string(source.get("callId"), message.get("toolCallId"))
    if direct:
        return direct
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                found = _first_string(block.get("toolCallId"), block.get("callId"))
                if found:
                    return found
    return None


def _tool_result_error(blocks: list[Any], data: dict[str, Any]) -> bool:
    if data.get("error") is not None:
        return True
    return any(
        isinstance(block, dict) and block.get("isError") is True for block in blocks
    )


def _usage(value: dict[str, Any]) -> dict[str, int | str]:
    cache_write = _integer_or_unknown(value.get("cacheWriteTokens"))
    cached_input = _integer_or_unknown(value.get("cacheReadTokens"))
    raw_input = _integer_or_unknown(value.get("inputTokens"))
    input_tokens = (
        raw_input
        + (cached_input if isinstance(cached_input, int) else 0)
        + (cache_write if isinstance(cache_write, int) else 0)
        if isinstance(raw_input, int)
        else "unknown"
    )
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input,
        "cache_write_input_tokens": cache_write,
        # Retain the original DeepSeek-specific key for existing trace consumers.
        "cache_write_tokens": cache_write,
        "output_tokens": _integer_or_unknown(value.get("outputTokens")),
        "reasoning_output_tokens": _integer_or_unknown(value.get("reasoningTokens")),
    }


def _integer_or_unknown(value: Any) -> int | str:
    return value if isinstance(value, int) and not isinstance(value, bool) else "unknown"


def _finish_kind(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        kind = value.get("kind")
        return str(kind) if isinstance(kind, str) else None
    return None


def _finish_error(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    error = value.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    reason = value.get("reason")
    if isinstance(reason, dict):
        nested = reason.get("error")
        if isinstance(nested, dict) and isinstance(nested.get("message"), str):
            return nested["message"]
    return None


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if value.get("type") in {"text", "reasoning"}:
            return str(value.get("text") or "")
        if value.get("type") == "tool-result":
            return _content_text(value.get("content"))
        return "\n".join(_content_text(item) for item in value.values()).strip()
    if isinstance(value, list):
        return "\n".join(
            text for item in value if (text := _content_text(item)).strip()
        )
    return ""


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None
