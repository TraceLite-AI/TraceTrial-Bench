from __future__ import annotations

import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import emit_context_compaction, extract_summary
from harness_tracer.process import CompletedProcessData, ProcessRunner
from harness_tracer.writer import EventWriter


_SESSION_ID_LINE = re.compile(
    r"^session_id: (?P<session_id>[0-9]{8}_[0-9]{6}_[0-9a-f]{6})\r?$",
    re.MULTILINE,
)
_EXPORT_TIMEOUT_SECONDS = 30
_STATELESS_ARG = "harnessbench-stateless"
_STATELESS_BOOTSTRAP = (
    "from gateway.session_context import declare_stateless_channel;"
    "declare_stateless_channel();"
    "from hermes_cli.main import main;"
    "main()"
)
_DELEGATION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_DELEGATION_TOOL_LINE = re.compile(
    r"^(?:\d+\|)?(?P<time>\d\d:\d\d:\d\d)\s+tool\s+\|\s+->\s+"
    r"(?P<tool>[A-Za-z0-9_.-]+)\((?P<arguments>.*)\)\s*$"
)
_DELEGATION_RESULT_LINE = re.compile(
    r"^(?:\d+\|)?(?P<time>\d\d:\d\d:\d\d)\s+result\s+\|\s+"
    r"(?P<tool>[A-Za-z0-9_.-]+)\s+(?P<status>[A-Z]+)\b[^:]*:\s*(?P<content>.*)$"
)


class HermesAdapter:
    """Run Hermes and recover a redacted structured trace from its session store."""

    name = "hermes"

    def __init__(self) -> None:
        self._executable = "hermes"
        self._source: str | None = None

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        self._executable = harness_path or "hermes"
        self._source = f"harnessbench:{run_dir.name}"
        resume_session = extra_args.get("harnessbench-session-id")
        approval_probe = extra_args.get("harnessbench-approval-policy") == "ask_always"
        if extra_args.get(_STATELESS_ARG) == "true":
            command = [sys.executable, "-c", _STATELESS_BOOTSTRAP, "chat", "-q"]
        else:
            command = [self._executable, "chat", "-q"]
        command.extend(
            [
                input_query,
                "--model",
                model,
                "--quiet",
                # Benchmark runs are non-interactive and execute only inside the
                # runner's synthetic workspace.  Avoid approval prompts that can
                # stall a run, and suppress user-level rules, memories, personas,
                # and preloaded skills so the task prompt is the only injected
                # benchmark instruction.
                "--ignore-rules",
                "--source",
                self._source,
            ]
        )
        if not approval_probe:
            command.append("--yolo")
        if resume_session:
            command.extend(["--resume", resume_session])
        for key, value in sorted(extra_args.items()):
            # The source is a runner-owned session tag; trace selection uses the
            # exact session ID emitted after the run, not this broad filter.
            # Hermes' --pass-session-id alters model context, so the runner never
            # adds or forwards it. Credentials belong in the subprocess env and
            # must never become argv/trace data.
            if key in {
                _STATELESS_ARG,
                "harnessbench-conversation",
                "harnessbench-session-id",
                "harnessbench-approval-policy",
                "harnessbench-approval-fallback",
                "harnessbench-task-tools",
                "harnessbench-task-tool-bridge",
                "harnessbench-task-tool-config",
                "model",
                "pass-session-id",
                "query",
                "source",
            } or _is_sensitive_key(key):
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
        command_prefix: tuple[str, ...] = (),
        replace_environment: bool = False,
        allowed_state_dir: str | None = None,
        process_runner: ProcessRunner | None = None,
        max_rss_bytes: int | None = None,
        max_output_bytes: int | None = None,
    ) -> AdapterResult:
        trace_path = run_dir / "hermes-session.trace.jsonl"
        export_path = trace_path
        state_root: Path | None = None
        if allowed_state_dir is not None:
            try:
                state_root = Path(allowed_state_dir).resolve(strict=True)
            except OSError:
                return AdapterResult(
                    trace_complete=False, structured_trace_status="unavailable"
                )
            export_dir = state_root / f"trace-export-{uuid.uuid4().hex}"
            export_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
            export_path = export_dir / f"{run_dir.name}.trace.jsonl"
        session_id = _session_id_from_runner_stderr(process.stderr)
        if session_id is None:
            # Hermes' source-filter export omits an active session.  The quiet
            # one-shot CLI emits one machine-readable session-id line to its
            # runner-captured stderr, so fail closed if that binding is absent
            # or ambiguous instead of exporting a different recent session.
            writer.write(
                "trace_export",
                source="hermes.sessions.export",
                status="failed",
                redacted=True,
            )
            return AdapterResult(
                trace_complete=False, structured_trace_status="unavailable"
            )
        export_command = [
            self._executable,
            "sessions",
            "export",
            str(export_path),
            "--format",
            "trace",
            "--session-id",
            session_id,
            "--redact",
        ]
        export_runner = process_runner or ProcessRunner()

        try:
            export_kwargs: dict[str, Any] = {
                "cwd": state_root or run_dir,
                "timeout": _EXPORT_TIMEOUT_SECONDS,
                "env": env,
            }
            # Keep legacy/custom runners usable outside formal mode.  In the
            # formal path these keywords are mandatory and exactly mirror the
            # main process invocation.
            if command_prefix or replace_environment:
                export_kwargs["command_prefix"] = command_prefix
                export_kwargs["replace_env"] = replace_environment
            if max_rss_bytes is not None:
                export_kwargs["max_rss_bytes"] = max_rss_bytes
            if max_output_bytes is not None:
                export_kwargs["max_output_bytes"] = max_output_bytes
            exported = export_runner.run(export_command, **export_kwargs)
        except RuntimeError as exc:
            if _is_unproven_process_cleanup_error(exc):
                return _containment_failure(writer)
            writer.write(
                "trace_export",
                source="hermes.sessions.export",
                status="failed",
                redacted=True,
            )
            return AdapterResult(
                trace_complete=False, structured_trace_status="unavailable"
            )
        except OSError:
            writer.write(
                "trace_export",
                source="hermes.sessions.export",
                status="failed",
                redacted=True,
            )
            return AdapterResult(
                trace_complete=False, structured_trace_status="unavailable"
            )

        if exported.error_type in {
            "process_containment_failure",
            "memory_limit_exceeded",
            "resource_measurement_failure",
            "output_limit_exceeded",
        }:
            return _infrastructure_failure(
                writer,
                error_type=exported.error_type,
                error_message=exported.error_message,
            )

        if exported.returncode != 0 or not export_path.is_file():
            # Do not copy exporter or agent stderr into artifacts: native trace
            # export is the only Hermes output path with forced secret redaction.
            writer.write(
                "trace_export",
                source="hermes.sessions.export",
                status="failed",
                exit_code=exported.returncode,
                redacted=True,
            )
            return AdapterResult(
                trace_complete=False, structured_trace_status="unavailable"
            )

        if not _trace_is_bound_to_session(export_path, session_id):
            writer.write(
                "trace_export",
                source="hermes.sessions.export",
                status="failed",
                redacted=True,
            )
            return AdapterResult(
                trace_complete=False, structured_trace_status="unavailable"
            )

        if state_root is not None:
            try:
                resolved_export = export_path.resolve(strict=True)
                resolved_export.relative_to(state_root)
            except (OSError, ValueError):
                return AdapterResult(
                    trace_complete=False, structured_trace_status="unavailable"
                )
            if not resolved_export.is_file():
                return AdapterResult(
                    trace_complete=False, structured_trace_status="unavailable"
                )
            shutil.copyfile(resolved_export, trace_path)

        parsed_any, parse_failed, exported_session_id = _consume_native_trace(
            trace_path, writer
        )
        descendant_expected = 0
        descendant_found = 0
        descendant_parse_failed = False
        if state_root is not None:
            (
                descendant_expected,
                descendant_found,
                descendant_parse_failed,
            ) = _consume_delegation_traces(
                trace_path,
                writer,
                state_root=state_root,
                parent_session_id=exported_session_id or session_id,
            )
        writer.write(
            "trace_export",
            source="hermes.sessions.export",
            status="available" if parsed_any else "failed",
            format="claude-code-jsonl",
            session_id=exported_session_id,
            path=str(trace_path),
            redacted=True,
            descendant_sessions_expected=descendant_expected,
            descendant_sessions_found=descendant_found,
        )
        return AdapterResult(
            trace_complete=(
                parsed_any
                and not parse_failed
                and not descendant_parse_failed
                and descendant_expected == descendant_found
            ),
            structured_trace_status="available" if parsed_any else "unavailable",
        )


def _is_unproven_process_cleanup_error(error: RuntimeError) -> bool:
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "process containment tag was removed",
            "process containment tag identity changed",
            "process containment failed",
        )
    )


def _containment_failure(writer: EventWriter) -> AdapterResult:
    # A native trace is diagnostic, but an exporter whose descendants cannot
    # be proven dead is an isolation failure.  Surface that condition without
    # replacing the already-captured main harness process result or persisting
    # exporter output.
    writer.write(
        "trace_export",
        source="hermes.sessions.export",
        status="failed",
        redacted=True,
        containment="unproven",
    )
    return AdapterResult(
        status="failed",
        trace_complete=False,
        structured_trace_status="unavailable",
        error_type="process_containment_failure",
        error_message="Hermes trace exporter process cleanup could not be proven",
    )


def _infrastructure_failure(
    writer: EventWriter,
    *,
    error_type: str,
    error_message: str | None,
) -> AdapterResult:
    if error_type == "process_containment_failure":
        return _containment_failure(writer)
    writer.write(
        "trace_export",
        source="hermes.sessions.export",
        status="failed",
        redacted=True,
        resource_gate="failed",
    )
    return AdapterResult(
        status="failed",
        trace_complete=False,
        structured_trace_status="unavailable",
        error_type=error_type,
        error_message=(
            error_message
            or "Hermes trace exporter resource gate could not be proven"
        ),
    )


def _session_id_from_runner_stderr(stderr: str) -> str | None:
    """Parse the sole canonical ID line emitted by Hermes' quiet CLI path."""

    matches = [match.group("session_id") for match in _SESSION_ID_LINE.finditer(stderr)]
    return matches[0] if len(matches) == 1 else None


def _trace_is_bound_to_session(trace_path: Path, expected_session_id: str) -> bool:
    """Reject empty, malformed, mixed, or incorrectly selected native exports."""

    found = False
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(record, dict):
            return False
        session_id = _string(record.get("sessionId"))
        if session_id != expected_session_id:
            return False
        found = True
    return found


def _consume_native_trace(
    trace_path: Path, writer: EventWriter
) -> tuple[bool, bool, str | None]:
    parsed_any = False
    parse_failed = False
    session_id: str | None = None
    pending_tools: dict[str, dict[str, Any]] = {}
    seen_compactions: set[str] = set()

    for line in trace_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            parse_failed = True
            continue
        if not isinstance(record, dict):
            parse_failed = True
            continue

        parsed_any = True
        session_id = session_id or _string(record.get("sessionId"))
        if record.get("type") == "compaction":
            emit_context_compaction(
                writer,
                harness="hermes",
                source="hermes.native_session",
                native_event_type="compaction",
                record=record,
                session_id=session_id,
                summary=extract_summary(record),
                summary_artifact=trace_path.name,
                seen=seen_compactions,
                notice_visible_to_model=None,
            )
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            parse_failed = True
            continue
        role = message.get("role") or record.get("type")
        content = message.get("content")

        if role == "assistant":
            text_parts: list[str] = []
            for block in _content_blocks(content):
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    text_parts.append(block["text"])
                elif block_type == "tool_use":
                    call_id = (
                        _string(block.get("id"))
                        or f"hermes-tool-{len(pending_tools) + 1}"
                    )
                    pending_tools[call_id] = {
                        "tool_call": {
                            "tool_call_id": call_id,
                            "tool_name": _string(block.get("name")) or "unknown",
                            "arguments": block.get("input")
                            if isinstance(block.get("input"), dict)
                            else {},
                            "provider_executed": True,
                        },
                        "raw_event": record,
                    }
            visible_text = "\n".join(part for part in text_parts if part)
            if visible_text:
                writer.write(
                    "assistant_message",
                    message_id=record.get("uuid"),
                    content=visible_text,
                    model=message.get("model"),
                    finish_reason=message.get("stop_reason"),
                    raw_event=record,
                )
            _write_usage(message.get("usage") or record.get("usage"), writer, record)
            continue

        tool_results = [
            block
            for block in _content_blocks(content)
            if block.get("type") == "tool_result"
        ]
        if tool_results:
            for block in tool_results:
                call_id = _string(block.get("tool_use_id")) or "unknown"
                pending = pending_tools.pop(call_id, None)
                writer.write(
                    "tool",
                    tool_call=(
                        pending["tool_call"]
                        if pending
                        else {
                            "tool_call_id": call_id,
                            "tool_name": "unknown",
                            "arguments": {},
                            "provider_executed": True,
                        }
                    ),
                    tool_result={
                        "tool_call_id": call_id,
                        "tool_name": (
                            pending["tool_call"]["tool_name"] if pending else "unknown"
                        ),
                        "content": block.get("content"),
                        "error": _tool_result_is_error(block),
                    },
                    raw_event={
                        "tool_call": pending["raw_event"] if pending else None,
                        "tool_result": record,
                    },
                )
            continue

        if role == "user":
            writer.write(
                "model_input",
                turn_id=record.get("uuid"),
                model=None,
                provider="hermes",
                messages=[{"role": "user", "content": content}],
                tools=[],
                request_params={},
                raw_event=record,
            )

    for pending in pending_tools.values():
        writer.write(
            "tool",
            tool_call=pending["tool_call"],
            tool_result=None,
            raw_event={"tool_call": pending["raw_event"], "tool_result": None},
        )

    return parsed_any, parse_failed, session_id


def _consume_delegation_traces(
    trace_path: Path,
    writer: EventWriter,
    *,
    state_root: Path,
    parent_session_id: str,
) -> tuple[int, int, bool]:
    """Import Hermes' native append-only child logs with explicit lineage.

    Hermes' redacted session exporter currently covers only the parent session.
    `delegate_task` returns a stable delegation ID and writes each child operation
    to an append-only log under HERMES_HOME.  We bind logs to IDs present in this
    exported parent trace, reject links and paths outside the allowed state root,
    and emit only normalized child tool lifecycles (the raw log is not archived).
    """

    delegation_ids = _delegation_ids_from_trace(trace_path)
    if not delegation_ids:
        return 0, 0, False
    try:
        allowed_root = state_root.resolve(strict=True)
    except OSError:
        return len(delegation_ids), 0, True

    found = 0
    parse_failed = False
    for delegation_id in sorted(delegation_ids):
        if _DELEGATION_ID.fullmatch(delegation_id) is None:
            parse_failed = True
            continue
        directory = allowed_root / "cache" / "delegation" / "live" / delegation_id
        try:
            if directory.is_symlink():
                raise ValueError("delegation directory is a symlink")
            resolved_directory = directory.resolve(strict=True)
            resolved_directory.relative_to(allowed_root)
            logs = sorted(resolved_directory.glob("task-*.log"))
        except (OSError, ValueError):
            continue
        valid_logs = 0
        for log_path in logs:
            try:
                if log_path.is_symlink():
                    raise ValueError("delegation log is a symlink")
                resolved_log = log_path.resolve(strict=True)
                resolved_log.relative_to(allowed_root)
                if not resolved_log.is_file():
                    raise ValueError("delegation log is not a regular file")
                rows = _delegation_tool_rows(resolved_log)
            except (OSError, UnicodeError, ValueError):
                parse_failed = True
                continue
            if rows:
                valid_logs += 1
            if any(row["result_content"] is None for row in rows):
                parse_failed = True
            task_name = resolved_log.stem
            principal_id = f"hermes-child:{delegation_id}:{task_name}"
            for index, row in enumerate(rows, start=1):
                call_id = f"hermes-delegation:{delegation_id}:{task_name}:{index}"
                writer.write(
                    "tool",
                    principal_id=principal_id,
                    parent_principal_id=parent_session_id,
                    principal_role="child",
                    tool_call={
                        "tool_call_id": call_id,
                        "tool_name": row["tool_name"],
                        "arguments": {"command": row["arguments"]},
                        "provider_executed": True,
                    },
                    tool_result={
                        "tool_call_id": call_id,
                        "tool_name": row["tool_name"],
                        "content": row["result_content"],
                        "error": row["result_error"],
                    }
                    if row["result_content"] is not None
                    else None,
                    raw_event={
                        "source": "hermes.delegation.live",
                        "delegation_id": delegation_id,
                        "task": task_name,
                        "tool_time": row["tool_time"],
                        "result_time": row["result_time"],
                    },
                )
        if valid_logs:
            found += 1
    return len(delegation_ids), found, parse_failed


def _delegation_ids_from_trace(trace_path: Path) -> set[str]:
    pending: set[str] = set()
    delegation_ids: set[str] = set()
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return set()
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        for block in _content_blocks(message.get("content")):
            if block.get("type") == "tool_use" and block.get("name") == "delegate_task":
                call_id = _string(block.get("id"))
                if call_id:
                    pending.add(call_id)
            if block.get("type") != "tool_result" or block.get("tool_use_id") not in pending:
                continue
            payload = _structured_tool_result_payload(block.get("content"))
            if not isinstance(payload, dict):
                continue
            delegation_id = payload.get("delegation_id")
            if isinstance(delegation_id, str) and _DELEGATION_ID.fullmatch(delegation_id):
                delegation_ids.add(delegation_id)
    return delegation_ids


def _delegation_tool_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending: dict[str, str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        tool_match = _DELEGATION_TOOL_LINE.match(line)
        if tool_match:
            if pending is not None:
                rows.append(
                    {
                        **pending,
                        "result_time": None,
                        "result_content": None,
                        "result_error": True,
                    }
                )
            pending = {
                "tool_name": tool_match.group("tool"),
                "arguments": tool_match.group("arguments"),
                "tool_time": tool_match.group("time"),
            }
            continue
        result_match = _DELEGATION_RESULT_LINE.match(line)
        if result_match and pending is not None:
            rows.append(
                {
                    **pending,
                    "result_time": result_match.group("time"),
                    "result_content": result_match.group("content"),
                    "result_error": result_match.group("status") != "OK",
                }
            )
            pending = None
    if pending is not None:
        rows.append(
            {
                **pending,
                "result_time": None,
                "result_content": None,
                "result_error": True,
            }
        )
    return rows


def _content_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, list):
        return [block for block in content if isinstance(block, dict)]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return []


def _tool_result_is_error(block: dict[str, Any]) -> bool:
    """Recover MCP error state that Hermes may preserve only in its wrapper."""

    if any(block.get(name) is True for name in ("is_error", "isError", "error")):
        return True
    payload = _structured_tool_result_payload(block.get("content"))
    if not isinstance(payload, dict):
        return False
    if payload.get("harnessbench_task_tool_error") is True:
        return True
    if payload.get("ok") is False or payload.get("isError") is True:
        return True
    error = payload.get("error")
    return isinstance(error, str) and error.startswith("Task tool failed:")


def _structured_tool_result_payload(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    candidates = [content.strip()]
    match = re.search(
        r"<untrusted_tool_result\b[^>]*>.*?\n\n(?P<payload>.*)\n"
        r"</untrusted_tool_result>\s*$",
        content,
        re.DOTALL,
    )
    if match:
        wrapped = match.group("payload").strip()
        candidates.insert(0, wrapped)
        first_line = next((line.strip() for line in wrapped.splitlines() if line.strip()), "")
        if first_line:
            candidates.insert(0, first_line)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
    return None


def _write_usage(usage: Any, writer: EventWriter, record: dict[str, Any]) -> None:
    if not isinstance(usage, dict):
        return
    input_tokens = usage.get("input_tokens")
    cached_input_tokens = usage.get("cache_read_input_tokens")
    if isinstance(input_tokens, int) and not isinstance(input_tokens, bool):
        input_tokens += (
            cached_input_tokens
            if isinstance(cached_input_tokens, int)
            and not isinstance(cached_input_tokens, bool)
            else 0
        )
    writer.write(
        "token_usage",
        source="hermes.sessions.export",
        usage_scope="call",
        usage={
            "input_tokens": (
                input_tokens if isinstance(input_tokens, int) else "unknown"
            ),
            "cached_input_tokens": (
                cached_input_tokens
                if isinstance(cached_input_tokens, int)
                else "unknown"
            ),
            "output_tokens": usage.get("output_tokens", "unknown"),
            "reasoning_output_tokens": usage.get("reasoning_tokens", "unknown"),
        },
        raw_event=record,
    )


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return (
        normalized == "key"
        or normalized.endswith("_key")
        or any(
            fragment in normalized
            for fragment in (
                "api_key",
                "apikey",
                "authorization",
                "credential",
                "password",
                "secret",
                "token",
            )
        )
    )
