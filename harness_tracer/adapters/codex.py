from __future__ import annotations

import json
import hashlib
import shlex
from pathlib import Path
from typing import Any

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import (
    archive_codex_child_session,
    archive_codex_session,
    discover_codex_child_sessions,
    emit_context_compaction,
    extract_summary,
    write_archive_event,
)
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter

TOOL_TYPES = {"command_execution", "mcp_tool_call", "collab_tool_call", "web_search"}
_CODEX_EXECUTOR_SHELL = "/usr/bin/bash"
_CODEX_EXECUTOR_SHELL_FLAG = "-lc"
_STREAM_COMPACTION_TYPES = {
    "context.compacted",
    "context_compacted",
    "thread.compacted",
    "turn.compacted",
}


class CodexAdapter:
    name = "codex"

    def __init__(self) -> None:
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
        executable = harness_path or "codex"
        native_child_delegation = (
            extra_args.get("harnessbench-native-child-delegation") == "true"
        )
        self.native_child_delegation = native_child_delegation
        conversation = (
            extra_args.get("harnessbench-conversation") == "true"
            or native_child_delegation
        )
        resume_session = extra_args.get("harnessbench-session-id")
        approval_probe = extra_args.get("harnessbench-approval-policy") == "ask_always"
        task_tools = extra_args.get("harnessbench-task-tools") == "true"
        task_tools_preconfigured = (
            extra_args.get("harnessbench-task-tools-preconfigured") == "true"
        )
        task_tool_bridge = extra_args.get("harnessbench-task-tool-bridge")
        command = [executable]
        if approval_probe:
            # Approval is a top-level Codex option. Passing the legacy config
            # value after `exec` is silently normalized to `never` by 0.145.
            command.extend(["--ask-for-approval", "on-request"])
        command.append("exec")
        if resume_session:
            command.append("resume")
        command.extend(
            [
            "--json",
            "--model",
            model,
            "--skip-git-repo-check",
            "--output-last-message",
            str(run_dir / "last_message.txt"),
            ]
        )
        if approval_probe:
            # The benchmark process already runs inside the hardened Docker
            # boundary. Codex' bwrap workspace sandbox cannot create user
            # namespaces there. The resume subcommand does not accept the
            # top-level --sandbox flag, so express the same setting as config.
            if resume_session:
                command.extend(["-c", 'sandbox_mode="danger-full-access"'])
            else:
                command.extend(["--sandbox", "danger-full-access"])
        else:
            command.append("--dangerously-bypass-approvals-and-sandbox")
        if not conversation:
            command.append("--ephemeral")
        if native_child_delegation:
            # Codex 0.145 keeps the legacy collaboration implementation on by
            # default, while its exec surface exposes native spawn/wait tools
            # through multi_agent_v2. Goals are a same-thread continuation
            # mechanism, not child delegation; hiding them prevents an
            # ambiguous fallback that can loop until the outer timeout.
            command.extend(
                [
                    "--enable",
                    "multi_agent",
                    "--enable",
                    "multi_agent_v2",
                    "--disable",
                    "goals",
                ]
            )
        if task_tools and not task_tools_preconfigured:
            if not task_tool_bridge:
                raise ValueError("task tool bridge path is missing")
            command.extend(
                [
                    "-c",
                    "mcp_servers.harnessbench.command=\"python3\"",
                    "-c",
                    "mcp_servers.harnessbench.args="
                    + json.dumps([task_tool_bridge]),
                    "-c",
                    "mcp_servers.harnessbench.startup_timeout_sec=10",
                    "-c",
                    "mcp_servers.harnessbench.tool_timeout_sec=300",
                ]
            )
        for key, value in sorted(extra_args.items()):
            if key.startswith("harnessbench-approval-") or key in {
                "harnessbench-conversation",
                "harnessbench-session-id",
                "harnessbench-task-tools",
                "harnessbench-task-tool-bridge",
                "harnessbench-task-tool-config",
                "harnessbench-task-tools-preconfigured",
                "harnessbench-native-child-delegation",
            }:
                continue
            command.extend([f"--{key}", value])
        if resume_session:
            command.append(resume_session)
        command.append(input_query)
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
        parsed_any = False
        parse_failed = False
        pending_tools: dict[str, dict[str, Any]] = {}
        session_id: str | None = None
        child_thread_ids: set[str] = set()
        seen_compactions: set[str] = set()
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
            parsed_any = True
            if payload.get("type") == "thread.started":
                session_id = _first_string(
                    payload.get("thread_id"), payload.get("session_id")
                )
            child_thread_ids.update(_completed_child_thread_ids(payload))
            if payload.get("type") in _STREAM_COMPACTION_TYPES:
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="codex.stream_json",
                    native_event_type=str(payload.get("type")),
                    record=payload,
                    session_id=session_id,
                    summary=extract_summary(payload),
                    summary_artifact=None,
                    seen=seen_compactions,
                    notice_visible_to_model=None,
                )
            self._map_payload(payload, writer, result, pending_tools, stream_observed)

        for index, line in enumerate(process.stderr.splitlines(), start=1):
            writer.write("raw_stderr", text=line, stream_seq=index, severity="error")

        for pending in pending_tools.values():
            writer.write(
                "tool",
                tool_call=pending["tool_call"],
                tool_result=None,
                raw_event={"tool_call": pending["raw_event"], "tool_result": None},
            )

        if allowed_state_dir is not None:
            archive = archive_codex_session(
                allowed_state_dir=allowed_state_dir,
                session_id=session_id,
                run_dir=run_dir,
                prefer_latest=self.native_child_delegation,
                exclude_subagents=self.native_child_delegation,
            )
            write_archive_event(
                writer,
                harness=self.name,
                session_id=session_id,
                archive=archive,
            )
            main_principal = {
                "principal_id": session_id or "main",
                "parent_principal_id": None,
                "principal_role": "main",
            }
            _emit_unstreamed_native_tools(
                archive.records,
                writer,
                include_exec=self.native_child_delegation,
                current_invocation=self.native_child_delegation,
                skip_existing=self.native_child_delegation,
                principal=main_principal,
            )
            _emit_codex_native_policy_events(
                archive.records,
                writer,
                principal=main_principal,
            )
            summary_artifact = (
                archive.path.relative_to(run_dir).as_posix()
                if archive.path is not None
                else None
            )
            for record in archive.records:
                native_type = _native_compaction_type(record)
                if native_type is None:
                    continue
                emit_context_compaction(
                    writer,
                    harness=self.name,
                    source="codex.native_session",
                    native_event_type=native_type,
                    record=record,
                    session_id=session_id,
                    summary=extract_summary(record),
                    summary_artifact=summary_artifact,
                    seen=seen_compactions,
                    notice_visible_to_model=None,
                )
            child_bindings = discover_codex_child_sessions(
                allowed_state_dir=allowed_state_dir,
                parent_session_id=session_id,
            ) if self.native_child_delegation else ()
            child_targets: list[tuple[str, str, Any]] = [
                (binding.principal_id, binding.session_id, binding)
                for binding in child_bindings
            ]
            bound_native_ids = {binding.session_id for binding in child_bindings}
            child_targets.extend(
                (child_id, child_id, None)
                for child_id in sorted(child_thread_ids - bound_native_ids)
            )
            if self.native_child_delegation:
                _emit_codex_child_completions(
                    archive.records,
                    writer,
                    parent_session_id=session_id or "main",
                    child_bindings=child_bindings,
                )
            for index, (child_id, native_child_id, binding) in enumerate(
                child_targets, start=1
            ):
                writer.write(
                    "subagent_lifecycle",
                    source="codex.native_session_meta",
                    action="spawned",
                    principal_id=child_id,
                    child_principal_id=child_id,
                    parent_principal_id=(
                        binding.parent_session_id
                        if binding is not None
                        else session_id or "main"
                    ),
                    principal_role="child",
                    agent_path=binding.agent_path if binding is not None else None,
                    identity_source=(
                        "session_meta.source.subagent.thread_spawn"
                        if binding is not None
                        else "codex_exec_stream"
                    ),
                )
                child_archive = (
                    archive_codex_child_session(
                        child=binding,
                        allowed_state_dir=allowed_state_dir,
                        run_dir=run_dir,
                        destination_name=f"codex-child-{index}.trace.jsonl",
                    )
                    if binding is not None
                    else archive_codex_session(
                        allowed_state_dir=allowed_state_dir,
                        session_id=native_child_id,
                        run_dir=run_dir,
                        destination_name=f"codex-child-{index}.trace.jsonl",
                        prefer_latest=self.native_child_delegation,
                    )
                )
                write_archive_event(
                    writer,
                    harness=self.name,
                    session_id=native_child_id,
                    archive=child_archive,
                )
                child_principal = {
                    "principal_id": child_id,
                    "parent_principal_id": session_id or "main",
                    "principal_role": "child",
                }
                _emit_unstreamed_native_tools(
                    child_archive.records,
                    writer,
                    include_exec=True,
                    all_turns=True,
                    principal=child_principal,
                )
                _emit_codex_native_policy_events(
                    child_archive.records,
                    writer,
                    principal=child_principal,
                )

        if parse_failed or not parsed_any:
            result.structured_trace_status = "unavailable"
        return result

    def _map_payload(
        self,
        payload: dict[str, Any],
        writer: EventWriter,
        result: AdapterResult,
        pending_tools: dict[str, dict[str, Any]],
        stream_observed: dict[str, Any] | None = None,
    ) -> None:
        event_type = payload.get("type")
        observed = stream_observed or {}
        raw = {"raw_event": payload, **observed}

        if event_type == "thread.started":
            writer.write(
                "run_started",
                source="codex",
                thread_id=payload.get("thread_id"),
                session_id=payload.get("session_id"),
                **raw,
            )
            return

        if event_type == "turn.started":
            writer.write(
                "model_input",
                turn_id=payload.get("turn_id"),
                model=payload.get("model"),
                provider=payload.get("provider", "codex"),
                messages=payload.get("input_messages") or payload.get("messages"),
                tools=payload.get("tools", []),
                request_params=payload.get("request_params", {}),
                **raw,
            )
            return

        if event_type in {"item.started", "item.updated", "item.completed"}:
            self._map_item(event_type, payload, writer, pending_tools, observed)
            return

        if event_type == "turn.completed":
            usage = payload.get("usage")
            if usage:
                writer.write(
                    "token_usage",
                    turn_id=payload.get("turn_id"),
                    source="codex.turn.completed",
                    usage_scope="turn",
                    usage={
                        "input_tokens": usage.get("input_tokens", "unknown"),
                        "cached_input_tokens": usage.get("cached_input_tokens", "unknown"),
                        "cache_write_input_tokens": usage.get(
                            "cache_write_input_tokens", "unknown"
                        ),
                        "output_tokens": usage.get("output_tokens", "unknown"),
                        "reasoning_output_tokens": usage.get("reasoning_output_tokens", "unknown"),
                    },
                    **raw,
                )
            return

        if event_type in {"turn.failed", "error"}:
            error = payload.get("error") or {}
            error_type = error.get("type") or payload.get("error_type") or event_type
            error_message = error.get("message") or payload.get("message") or "Codex run failed"
            writer.write(
                "run_failed",
                error_type=error_type,
                error_message=error_message,
                turn_id=payload.get("turn_id"),
                exit_code=None,
                timed_out=False,
                **raw,
            )
            result.status = "failed"
            result.trace_complete = False
            result.error_type = error_type
            result.error_message = error_message
            return

        writer.write("raw_stdout", text=json.dumps(payload, ensure_ascii=False), parseable=True, raw_event=payload)

    def _map_item(
        self,
        codex_event_type: str,
        payload: dict[str, Any],
        writer: EventWriter,
        pending_tools: dict[str, dict[str, Any]],
        stream_observed: dict[str, Any] | None = None,
    ) -> None:
        item = payload.get("item") or {}
        item_type = item.get("type")
        observed = stream_observed or {}
        raw = {"raw_event": payload, **observed}

        if item_type in {"agent_message", "reasoning"}:
            if codex_event_type == "item.updated":
                writer.write(
                    "assistant_delta",
                    turn_id=payload.get("turn_id"),
                    message_id=item.get("id"),
                    content_delta=item.get("delta"),
                    reasoning_delta=item.get("delta") if item_type == "reasoning" else None,
                    delta_type=item_type,
                    fragment_seq=item.get("sequence"),
                    **raw,
                )
            elif codex_event_type == "item.completed":
                writer.write(
                    "assistant_message",
                    turn_id=payload.get("turn_id"),
                    message_id=item.get("id"),
                    content=item.get("text") or item.get("content"),
                    reasoning=item.get("reasoning"),
                    finish_reason=item.get("finish_reason"),
                    **raw,
                )
            return

        if item_type in TOOL_TYPES:
            tool_call_id = item.get("id")
            if codex_event_type == "item.started":
                if tool_call_id is not None:
                    pending_tools[tool_call_id] = {
                        "tool_call": _tool_call_payload(item, payload),
                        "raw_event": payload,
                        "stream_observed": observed,
                    }
            elif codex_event_type == "item.completed":
                pending = pending_tools.pop(tool_call_id, None) if tool_call_id is not None else None
                tool_call = pending["tool_call"] if pending else _tool_call_payload(item, payload)
                writer.write(
                    "tool",
                    tool_call=tool_call,
                    tool_result=_tool_result_payload(item, payload),
                    raw_event={
                        "tool_call": pending["raw_event"] if pending else None,
                        "tool_result": payload,
                    },
                    tool_call_stream_observed=(
                        pending.get("stream_observed") if pending else None
                    ),
                    **observed,
                )
            return

        writer.write(
            "raw_stdout",
            text=json.dumps(payload, ensure_ascii=False),
            parseable=True,
            raw_event=payload,
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


def _tool_name(item: dict[str, Any]) -> str:
    """Map Codex stream item kinds back to provider-visible tool identities."""

    explicit = item.get("name")
    if isinstance(explicit, str) and explicit:
        return explicit
    item_type = item.get("type")
    if item_type == "command_execution":
        return "exec_command"
    if item_type == "mcp_tool_call":
        server = item.get("server")
        tool = item.get("tool")
        if isinstance(tool, str) and tool:
            # Codex' own resource helpers are ordinary top-level declarations;
            # external MCP calls use the reversible legacy flat name that the
            # Responses namespace compatibility bridge presents upstream.
            if server == "codex":
                return tool
            if isinstance(server, str) and server:
                prefix = server if server.startswith("mcp__") else f"mcp__{server}"
                return f"{prefix}__{tool}"
            return tool
    if item_type == "collab_tool_call":
        tool = item.get("tool")
        if isinstance(tool, str) and tool:
            return f"collaboration__{tool}"
    return str(item_type or "unknown")


def _tool_arguments(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("command"):
        return {"command": item.get("command")}
    if item.get("query"):
        return {"query": item.get("query")}
    return {}


def _semantic_tool_arguments(item: dict[str, Any]) -> dict[str, Any]:
    native_arguments = item.get("arguments")
    arguments = (
        dict(native_arguments)
        if isinstance(native_arguments, dict) and native_arguments
        else _tool_arguments(item)
    )
    if item.get("type") == "collab_tool_call":
        for key in (
            "prompt",
            "sender_thread_id",
            "receiver_thread_ids",
            "agents_states",
            "status",
        ):
            if key in item and item.get(key) is not None:
                arguments[key] = item.get(key)
    if item.get("type") != "command_execution":
        return arguments

    for key in ("command", "cmd"):
        command = arguments.get(key)
        if not isinstance(command, str):
            continue
        arguments[key] = _unwrap_codex_executor_shell(command)
        break
    return arguments


def _unwrap_codex_executor_shell(command: str) -> str:
    """Return the script passed through Codex's one known executor shell.

    Codex 0.145 reports command executions as ``/usr/bin/bash -lc SCRIPT`` even
    when the Agent supplied only ``SCRIPT``.  Strip exactly that one
    executor-owned layer so normalized arguments represent Agent intent.  An
    Agent-authored shell wrapper remains inside ``SCRIPT`` and therefore stays
    visible to policy checks.  The untouched native command remains available
    in the event's ``raw_event``.
    """

    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return command
    if (
        len(tokens) == 3
        and tokens[0] == _CODEX_EXECUTOR_SHELL
        and tokens[1] == _CODEX_EXECUTOR_SHELL_FLAG
    ):
        return tokens[2]
    return command


def _tool_call_payload(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    return _without_none(
        {
            "tool_call_id": item.get("id"),
            "tool_name": _tool_name(item),
            "arguments": _semantic_tool_arguments(item),
            "provider_executed": True,
            "source_message_id": item.get("source_message_id"),
            "turn_id": payload.get("turn_id"),
        }
    )


def _tool_result_payload(item: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    content = _first_present(item, "aggregated_output", "output", "result", "content", "text")
    result = {
        "tool_call_id": item.get("id"),
        "tool_name": _tool_name(item),
        "content": content,
        "structured_output": (
            item.get("result")
            if isinstance(item.get("result"), dict)
            else {
                key: item.get(key)
                for key in (
                    "sender_thread_id",
                    "receiver_thread_ids",
                    "agents_states",
                    "status",
                    "prompt",
                )
                if item.get(key) is not None
            }
            if item.get("type") == "collab_tool_call"
            else None
        ),
        "error": item.get("error"),
        "exit_code": item.get("exit_code"),
        "duration_ms": item.get("duration_ms"),
        "output_files": item.get("output_files", []),
        "turn_id": payload.get("turn_id"),
    }
    return _without_none(result)


def _first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _native_compaction_type(record: dict[str, Any]) -> str | None:
    record_type = record.get("type")
    if record_type == "compacted":
        return "compacted"
    payload = record.get("payload")
    if isinstance(payload, dict) and record_type == "event_msg":
        payload_type = payload.get("type")
        if payload_type in {"context_compacted", "thread_compacted"}:
            return f"event_msg.{payload_type}"
    return None


def _emit_unstreamed_native_tools(
    records: list[dict[str, Any]],
    writer: EventWriter,
    *,
    include_exec: bool = False,
    all_turns: bool = False,
    current_invocation: bool = False,
    skip_existing: bool = False,
    principal: dict[str, Any] | None = None,
) -> None:
    """Recover Codex function calls omitted by the exec JSON event stream.

    A resumed rollout archive is cumulative. The default recovers the most
    recently completed native turn; task-scoped callers can instead recover the
    complete invocation after the latest native user message, or all child turns.
    Shell execution remains excluded unless the caller explicitly requests it.
    """
    current_turn_id = _latest_native_turn_id(records)
    if current_turn_id is None:
        return

    invocation_start = (
        _latest_native_user_message_index(records)
        if current_invocation and not all_turns
        else -1
    )
    existing_fingerprints: dict[tuple[str, str], int] = {}
    if skip_existing:
        for event in writer.events:
            if event.get("event_type") != "tool":
                continue
            fingerprint = _tool_call_fingerprint(event.get("tool_call"))
            if fingerprint is not None:
                existing_fingerprints[fingerprint] = (
                    existing_fingerprints.get(fingerprint, 0) + 1
                )
    calls: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {}
    for record_index, record in enumerate(records):
        payload = record.get("payload")
        record_turn_id = _native_turn_id(record)
        if (
            not isinstance(payload, dict)
            or (
                not all_turns
                and not current_invocation
                and record_turn_id != current_turn_id
            )
            or (current_invocation and record_index <= invocation_start)
        ):
            continue
        if record.get("type") != "response_item":
            continue
        if payload.get("type") == "function_call":
            call_id = _first_string(payload.get("call_id"), payload.get("id"))
            name = _first_string(payload.get("name"))
            if (
                call_id is None
                or name is None
                or (name == "exec_command" and not include_exec)
            ):
                continue
            calls[call_id] = (record, payload, record_turn_id or current_turn_id)
        elif payload.get("type") == "function_call_output":
            call_id = _first_string(payload.get("call_id"))
            pending = calls.pop(call_id, None) if call_id is not None else None
            if pending is None:
                continue
            call_record, call_payload, call_turn_id = pending
            tool_call = _native_tool_call_payload(call_payload, call_turn_id)
            fingerprint = _tool_call_fingerprint(tool_call)
            if (
                fingerprint is not None
                and existing_fingerprints.get(fingerprint, 0) > 0
            ):
                existing_fingerprints[fingerprint] -= 1
                continue
            writer.write(
                "tool",
                tool_call=tool_call,
                tool_result=_native_tool_result_payload(
                    call_payload, payload, call_turn_id
                ),
                raw_event={"tool_call": call_record, "tool_result": record},
                **(principal or {}),
            )

    for call_record, call_payload, call_turn_id in calls.values():
        tool_call = _native_tool_call_payload(call_payload, call_turn_id)
        fingerprint = _tool_call_fingerprint(tool_call)
        if (
            fingerprint is not None
            and existing_fingerprints.get(fingerprint, 0) > 0
        ):
            existing_fingerprints[fingerprint] -= 1
            continue
        writer.write(
            "tool",
            tool_call=tool_call,
            tool_result=None,
            raw_event={"tool_call": call_record, "tool_result": None},
            **(principal or {}),
        )


def _tool_call_fingerprint(tool_call: Any) -> tuple[str, str] | None:
    if not isinstance(tool_call, dict):
        return None
    name = str(tool_call.get("tool_name") or tool_call.get("name") or "")
    arguments = tool_call.get("arguments")
    if not name or not isinstance(arguments, dict):
        return None
    for key in ("command", "cmd"):
        command = arguments.get(key)
        if isinstance(command, str):
            return name, command
    return name, json.dumps(arguments, ensure_ascii=False, sort_keys=True)


def _latest_native_user_message_index(records: list[dict[str, Any]]) -> int:
    latest = -1
    for index, record in enumerate(records):
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if (
            record.get("type") == "response_item"
            and payload.get("type") == "message"
            and payload.get("role") == "user"
        ) or (
            record.get("type") == "event_msg"
            and payload.get("type") == "user_message"
        ):
            latest = index
    return latest


def _completed_child_thread_ids(payload: dict[str, Any]) -> set[str]:
    if payload.get("type") != "item.completed":
        return set()
    item = payload.get("item")
    if not isinstance(item, dict) or item.get("type") != "collab_tool_call":
        return set()
    if item.get("tool") != "spawn_agent":
        return set()
    values: set[str] = set()
    receivers = item.get("receiver_thread_ids")
    if isinstance(receivers, list):
        values.update(value for value in receivers if isinstance(value, str) and value)
    states = item.get("agents_states")
    if isinstance(states, dict):
        values.update(str(value) for value in states if value)
    return values


def _emit_codex_child_completions(
    records: list[dict[str, Any]],
    writer: EventWriter,
    *,
    parent_session_id: str,
    child_bindings: tuple[Any, ...],
) -> None:
    """Expose native child messages pushed into the parent rollout.

    Codex persists the runtime-origin ``agent_message`` after the parent's
    collaboration wait, but the exec JSON stream omits that message.  Emit a
    typed completion only when its author exactly matches a discovered native
    child binding; arbitrary parent or model-authored text is never promoted.
    """

    by_author = {
        binding.agent_path: binding.principal_id
        for binding in child_bindings
        if isinstance(binding.agent_path, str) and binding.agent_path
    }
    if not by_author:
        return
    seen: set[tuple[str, str]] = set()
    for record in records:
        payload = record.get("payload")
        if (
            record.get("type") != "response_item"
            or not isinstance(payload, dict)
            or payload.get("type") != "agent_message"
        ):
            continue
        author = _first_string(payload.get("author"))
        principal_id = by_author.get(author or "")
        if principal_id is None:
            continue
        content = payload.get("content")
        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = _first_string(block.get("text"), block.get("content"))
                if text:
                    text_parts.append(text)
        text = "\n".join(text_parts)
        identity = (principal_id, text)
        if not text or identity in seen:
            continue
        seen.add(identity)
        writer.write(
            "task_completion",
            source="codex.native_parent_session",
            status="completed",
            content=text,
            principal_id=principal_id,
            parent_principal_id=parent_session_id,
            principal_role="child",
            parent_reactivated=True,
            native_author=author,
            raw_event=record,
        )


def _emit_codex_native_policy_events(
    records: list[dict[str, Any]],
    writer: EventWriter,
    *,
    principal: dict[str, Any],
) -> None:
    for record in records:
        payload = record.get("payload")
        if record.get("type") != "event_msg" or not isinstance(payload, dict):
            continue
        native_type = _first_string(payload.get("type"))
        if native_type not in {
            "exec_approval_request",
            "exec_approval_decision",
            "approval_request",
            "approval_decision",
        }:
            continue
        native_decision = _first_string(
            payload.get("decision"), payload.get("outcome"), payload.get("status")
        )
        decision = {
            "approved": "allowed",
            "allow": "allowed",
            "allowed": "allowed",
            "denied": "blocked",
            "deny": "blocked",
            "rejected": "blocked",
            "cancelled": "cancelled",
            "unavailable": "unavailable",
        }.get(native_decision or "", "pending")
        command = _first_string(payload.get("command"), payload.get("cmd"))
        writer.write(
            "policy_event",
            policy_kind="approval",
            source="codex.native_session",
            native_event_type=native_type,
            approval_id=_first_string(payload.get("id"), payload.get("approval_id")),
            tool_call_id=_first_string(payload.get("call_id"), payload.get("tool_call_id")),
            tool_name="exec_command",
            decision=decision,
            reason_code=(
                "APPROVAL_DENIED"
                if decision == "blocked"
                else "APPROVAL_CLIENT_UNAVAILABLE"
                if decision == "unavailable"
                else "APPROVAL_REQUIRED"
                if decision == "pending"
                else None
            ),
            reason_visible_to_model=decision in {"blocked", "cancelled", "unavailable"},
            execution_started=False if decision != "allowed" else None,
            command_sha256=(
                hashlib.sha256(command.encode("utf-8")).hexdigest()
                if command
                else None
            ),
            native_policy_event=payload,
            raw_event=record,
            **principal,
        )


def _native_tool_call_payload(
    payload: dict[str, Any], turn_id: str
) -> dict[str, Any]:
    arguments = payload.get("arguments")
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = {"raw_arguments": arguments}
        arguments = parsed_arguments if isinstance(parsed_arguments, dict) else {
            "value": parsed_arguments
        }
    if not isinstance(arguments, dict):
        arguments = {}
    return _without_none(
        {
            "tool_call_id": _first_string(payload.get("call_id"), payload.get("id")),
            "tool_name": _native_tool_name(payload),
            "arguments": arguments,
            "provider_executed": True,
            "turn_id": turn_id,
        }
    )


def _native_tool_result_payload(
    call_payload: dict[str, Any], result_payload: dict[str, Any], turn_id: str
) -> dict[str, Any]:
    return _without_none(
        {
            "tool_call_id": _first_string(
                result_payload.get("call_id"), call_payload.get("call_id")
            ),
            "tool_name": _native_tool_name(call_payload),
            "content": _first_present(
                result_payload, "output", "result", "content", "text"
            ),
            "turn_id": turn_id,
        }
    )


def _native_tool_name(payload: dict[str, Any]) -> str | None:
    name = _first_string(payload.get("name"))
    namespace = _first_string(payload.get("namespace"))
    if name is None:
        return None
    return f"{namespace}__{name}" if namespace is not None else name


def _latest_native_turn_id(records: list[dict[str, Any]]) -> str | None:
    for record in reversed(records):
        payload = record.get("payload")
        if (
            record.get("type") == "event_msg"
            and isinstance(payload, dict)
            and payload.get("type") in {"task_complete", "task_failed"}
        ):
            turn_id = _first_string(payload.get("turn_id"))
            if turn_id is not None:
                return turn_id
    for record in reversed(records):
        turn_id = _native_turn_id(record)
        if turn_id is not None:
            return turn_id
    return None


def _native_turn_id(record: dict[str, Any]) -> str | None:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    return _first_string(
        payload.get("turn_id"),
        metadata.get("turn_id") if isinstance(metadata, dict) else None,
    )


def _first_string(*values: Any) -> str | None:
    return next((value for value in values if isinstance(value, str) and value), None)
