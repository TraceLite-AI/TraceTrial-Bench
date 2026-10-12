from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness_tracer.adapters import AdapterResult, adapter_for
from harness_tracer.process import CompletedProcessData, ProcessRunner
from harness_tracer.provider_requests import (
    provider_request_cursor,
    trace_tool_inventory_payloads,
)
from harness_tracer.summary import build_summary, write_last_message, write_summary
from harness_tracer.timeutils import utc_now_iso
from harness_tracer.writer import EventWriter


@dataclass(frozen=True)
class RunConfig:
    harness: str
    model: str
    input_query: str
    output_dir: Path
    harness_path: str | None = None
    timeout: int | None = None
    max_rss_bytes: int | None = None
    max_output_bytes: int | None = None
    session_id: str | None = None
    extra_args: dict[str, str] = field(default_factory=dict)
    cwd: Path = field(default_factory=Path.cwd)
    env: dict[str, str] = field(default_factory=dict)
    command_prefix: tuple[str, ...] = ()
    replace_environment: bool = False
    isolation: dict[str, Any] = field(default_factory=lambda: {"mode": "none"})
    redact_paths: tuple[str, ...] = ()
    redact_values: tuple[str, ...] = field(default=(), repr=False)
    provider_request_events_path: Path | None = None


class TraceRunner:
    def __init__(self, *, process_runner: ProcessRunner | None = None) -> None:
        self.process_runner = process_runner or ProcessRunner()

    def run(self, config: RunConfig) -> dict:
        if not config.input_query:
            raise ValueError("input query must not be empty")

        adapter = adapter_for(config.harness)
        run_id = _make_run_id()
        run_dir = Path(config.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        events_path = run_dir / "events.json"
        summary_path = run_dir / "summary.json"
        last_message_path = run_dir / "last_message.txt"
        started_at = utc_now_iso()
        start_monotonic = time.monotonic()
        command = adapter.build_command(
            model=config.model,
            input_query=config.input_query,
            run_dir=run_dir,
            harness_path=config.harness_path,
            extra_args=config.extra_args,
        )
        process_env = _process_environment(config)
        environment_summary = _environment_summary(
            process_env,
            inherit_environment=not config.replace_environment,
            redact_paths=config.redact_paths,
        )

        writer = EventWriter(
            events_path,
            run_id=run_id,
            redact_values=config.redact_values,
        )
        process = None
        adapter_result = AdapterResult()
        try:
            provider_cursor = provider_request_cursor(
                config.provider_request_events_path
            )
            if config.harness.lower() not in {
                "claude",
                "claudecode",
                "codex",
                "openclaw",
            }:
                writer.write(
                    "run_started",
                    harness=config.harness,
                    model=config.model,
                    input_query=config.input_query,
                    session_id=config.session_id,
                    cwd=str(config.cwd),
                    argv=command,
                    environment=environment_summary,
                    isolation=_safe_isolation_summary(config.isolation),
                )

            if config.harness.lower() == "fake":
                process = CompletedProcessData(
                    args=command,
                    returncode=0,
                    stdout="",
                    stderr="",
                    timed_out=False,
                )
            else:
                process_kwargs: dict[str, Any] = {
                    "cwd": config.cwd,
                    "timeout": config.timeout,
                    "env": process_env,
                }
                if config.max_rss_bytes is not None:
                    process_kwargs["max_rss_bytes"] = config.max_rss_bytes
                if config.max_output_bytes is not None:
                    process_kwargs["max_output_bytes"] = config.max_output_bytes
                # Preserve compatibility with legacy/custom process runners
                # in the default mode by only sending new keywords when the
                # caller explicitly requests isolation behavior.
                if config.command_prefix or config.replace_environment:
                    process_kwargs["command_prefix"] = config.command_prefix
                    process_kwargs["replace_env"] = config.replace_environment
                process = self.process_runner.run(command, **process_kwargs)
            consume_kwargs: dict[str, Any] = {
                "process": process,
                "writer": writer,
                "run_dir": run_dir,
                "env": process_env,
            }
            if config.harness.lower() == "openclaw":
                managed_gateway = (
                    config.extra_args.get("harnessbench-gateway") == "true"
                )
                consume_kwargs["managed_gateway"] = managed_gateway
                if config.replace_environment:
                    consume_kwargs["allowed_state_dir"] = process_env.get(
                        "OPENCLAW_STATE_DIR"
                    )
                if managed_gateway and config.timeout is not None:
                    consume_kwargs["completion_timeout"] = max(
                        0.0,
                        config.timeout - (time.monotonic() - start_monotonic),
                    )
            if config.replace_environment and config.harness.lower() in {
                "claude",
                "claudecode",
            }:
                consume_kwargs["allowed_state_dir"] = process_env.get(
                    "CLAUDE_CONFIG_DIR"
                )
            if config.replace_environment and config.harness.lower() == "codex":
                consume_kwargs["allowed_state_dir"] = process_env.get("CODEX_HOME")
            if config.harness.lower() in {"hermes", "hermes-agent"}:
                consume_kwargs.update(
                    {
                        # Native session export is a second executable phase,
                        # so it must use the same contained process runner and
                        # isolation inputs as the main Hermes invocation.  The
                        # adapter keeps its result diagnostic-only and never
                        # replaces `process`, which remains the scored harness
                        # process below.
                        "process_runner": self.process_runner,
                        "command_prefix": config.command_prefix,
                        "replace_environment": config.replace_environment,
                        "max_rss_bytes": config.max_rss_bytes,
                        "max_output_bytes": config.max_output_bytes,
                    }
                )
                if config.replace_environment:
                    consume_kwargs["allowed_state_dir"] = process_env.get("HERMES_HOME")
            adapter_result = adapter.consume(**consume_kwargs)
            _insert_provider_tool_inventories(
                writer,
                trace_tool_inventory_payloads(
                    config.provider_request_events_path,
                    after_seq=provider_cursor,
                ),
            )
            _redact_writer_events(writer, config.redact_paths)

            status = adapter_result.status
            trace_complete = adapter_result.trace_complete
            error_type = adapter_result.error_type
            error_message = adapter_result.error_message

            # An exporter with unproven descendant cleanup is an isolation
            # failure even when the already-finished main Hermes process also
            # timed out or exited non-zero.  Keep the main exit fields in the
            # summary, but do not let an ordinary agent outcome hide the
            # containment failure that makes the lane infrastructure-invalid.
            infrastructure_process_errors = {
                "process_containment_failure",
                "memory_limit_exceeded",
                "resource_measurement_failure",
                "output_limit_exceeded",
            }
            if process.error_type in infrastructure_process_errors:
                status = "failed"
                trace_complete = False
                error_type = process.error_type
                error_message = process.error_message
            elif error_type not in infrastructure_process_errors:
                if process.timed_out:
                    status = "failed"
                    trace_complete = False
                    error_type = process.error_type or "timeout"
                    error_message = process.error_message or "process timed out"
                elif process.returncode not in (0, None) and not (
                    config.harness.lower() == "claudecode"
                    and config.extra_args.get(
                        "harnessbench-approval-claude-diagnostic-soft-exit"
                    )
                    == "true"
                    and adapter_result.extra.get("soft_process_exit")
                    == "claudecode_completed_tool_turn_diagnostic"
                ):
                    status = "failed"
                    trace_complete = False
                    error_type = "nonzero_exit"
                    error_message = f"process exited with code {process.returncode}"

            if status == "failed":
                if not any(
                    event.get("event_type") == "run_failed" for event in writer.events
                ):
                    writer.write(
                        "run_failed",
                        error_type=error_type,
                        error_message=error_message,
                        exit_code=process.returncode,
                        timed_out=process.timed_out,
                        events_written=writer.seq,
                        recoverable_hint="Inspect events.json for partial trace.",
                    )
            else:
                writer.write(
                    "run_finished",
                    exit_code=process.returncode,
                    summary_path=str(summary_path),
                    trace_complete=trace_complete,
                )

            # Status events are emitted after adapter consumption, so run a
            # final path scrub before summary construction and persistence.
            _redact_writer_events(writer, config.redact_paths)

            finished_at = utc_now_iso()
            duration_ms = int((time.monotonic() - start_monotonic) * 1000)
            summary = build_summary(
                writer=writer,
                run_id=run_id,
                run_dir=run_dir,
                harness=config.harness,
                model=config.model,
                input_query=config.input_query,
                status=status,
                trace_complete=trace_complete,
                structured_trace_status=adapter_result.structured_trace_status,
                exit_code=process.returncode,
                timed_out=process.timed_out,
                error_type=error_type,
                error_message=error_message,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                session_id=config.session_id,
                cwd=str(config.cwd),
                argv=command,
                environment=environment_summary,
                summary_path=summary_path,
            )
            summary["isolation"] = _safe_isolation_summary(config.isolation)
            summary["process_containment"] = process.containment
            summary["resource_usage"] = {
                "peak_rss_bytes": process.peak_rss_bytes,
                "rss_limit_bytes": process.rss_limit_bytes,
                "rss_limit_exceeded": process.rss_limit_exceeded,
                "output_bytes_captured": process.output_bytes_captured,
                "output_limit_bytes": process.output_limit_bytes,
                "output_limit_exceeded": process.output_limit_exceeded,
            }
            write_summary(summary, summary_path)
            write_last_message(summary, last_message_path)
            return summary
        finally:
            _redact_writer_events(writer, config.redact_paths)
            writer.close()


def _make_run_id() -> str:
    return f"run-{uuid.uuid4().hex}"


def _insert_provider_tool_inventories(
    writer: EventWriter,
    payloads: list[dict[str, Any]],
) -> None:
    if not payloads:
        return
    insertion_index = next(
        (
            index + 1
            for index, event in enumerate(writer.events)
            if event.get("event_type") == "run_started"
        ),
        0,
    )
    writer.insert_many(
        insertion_index,
        [("tool_inventory", payload) for payload in payloads],
    )


def _process_environment(config: RunConfig) -> dict[str, str]:
    env = dict(config.env)
    if config.harness.lower() == "openclaw":
        env.setdefault("OPENCLAW_WORKSPACE_DIR", str(config.cwd.resolve()))
    return env


def _environment_summary(
    overrides: dict[str, str] | None = None,
    *,
    inherit_environment: bool = True,
    redact_paths: tuple[str, ...] = (),
) -> dict[str, str | None]:
    overrides = overrides or {}

    def get(name: str) -> str | None:
        value = overrides.get(name)
        if value is None and inherit_environment:
            value = os.environ.get(name)
        return _redact_string(value, redact_paths) if value is not None else None

    return {
        "PATH": get("PATH"),
        "PYTHONPATH": get("PYTHONPATH"),
        "CODEX_HOME": get("CODEX_HOME"),
        "XDG_CONFIG_HOME": get("XDG_CONFIG_HOME"),
        "XDG_DATA_HOME": get("XDG_DATA_HOME"),
        "XDG_STATE_HOME": get("XDG_STATE_HOME"),
        "OPENCLAW_WORKSPACE_DIR": get("OPENCLAW_WORKSPACE_DIR"),
    }


def _safe_isolation_summary(value: dict[str, Any] | None) -> dict[str, Any]:
    value = value or {"mode": "none"}
    allowed = {"mode", "fresh_state", "profile_sha256"}
    return {key: item for key, item in value.items() if key in allowed}


def _redact_writer_events(writer: EventWriter, redact_paths: tuple[str, ...]) -> None:
    if not redact_paths:
        return
    for index, event in enumerate(writer.events):
        writer.events[index] = _redact_value(event, redact_paths)
    # EventWriter flushes on every write.  Rewrite immediately so a raw events
    # artifact never remains on disk with a runner-private or host path while
    # later summary work is in progress.
    writer.path.write_text(
        json.dumps(writer.events, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _redact_value(value: Any, redact_paths: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact_string(value, redact_paths)
    if isinstance(value, list):
        return [_redact_value(item, redact_paths) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item, redact_paths) for key, item in value.items()}
    return value


def _redact_string(value: str, redact_paths: tuple[str, ...]) -> str:
    redacted = value
    for path in sorted((item for item in redact_paths if item), key=len, reverse=True):
        redacted = redacted.replace(path, "<harness-private>")
    return redacted
