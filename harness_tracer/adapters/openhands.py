from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harness_tracer.adapters.base import AdapterResult
from harness_tracer.native_sessions import (
    archive_records,
    emit_context_compaction,
    write_archive_event,
)
from harness_tracer.process import CompletedProcessData
from harness_tracer.writer import EventWriter

_CONVERSATION_ID_RE = re.compile(
    r"Conversation ID:\s*([0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12})"
)
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class OpenHandsAdapter:
    """Run OpenHands CLI in headless JSON mode and normalize its event stream."""

    name = "openhands"

    def build_command(
        self,
        *,
        model: str,
        input_query: str,
        run_dir: Path,
        harness_path: str | None,
        extra_args: dict[str, str],
    ) -> list[str]:
        del model, run_dir
        executable = harness_path or "openhands"
        resume_session = extra_args.get("harnessbench-session-id")
        approval_probe = extra_args.get("harnessbench-approval-policy") == "ask_always"
        command = [
            executable,
            "--headless",
            "--json",
            "--override-with-envs",
            "--exit-without-confirmation",
        ]
        if approval_probe:
            command.append("--llm-approve")
        else:
            command.append("--always-approve")
        if resume_session:
            command.extend(["--resume", resume_session])
        command.extend(["-t", input_query])
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
        del env
        persistence_dir = _resolve_persistence_dir(allowed_state_dir)
        session_id = _extract_conversation_id(process.stdout, process.stderr)
        records, resolved_session_id = _load_session_events(
            persistence_dir=persistence_dir,
            session_id=session_id,
            stdout=process.stdout,
        )
        session_id = resolved_session_id or session_id

        writer.write(
            "run_started",
            harness=self.name,
            model="",
            input_query="",
            session_id=session_id,
            cwd=str(run_dir),
            argv=process.args,
            environment={},
            isolation={"mode": "docker", "fresh_state": True},
        )

        if not records:
            for index, line in enumerate(process.stdout.splitlines(), start=1):
                writer.write(
                    "raw_stdout",
                    text=line,
                    stream_seq=index,
                    parseable=False,
                    parse_error="no structured OpenHands events found",
                )
            for index, line in enumerate(process.stderr.splitlines(), start=1):
                writer.write(
                    "raw_stderr",
                    text=line,
                    stream_seq=index,
                    severity="error" if process.returncode else "info",
                )
            return AdapterResult(
                status="failed" if process.returncode else "finished",
                trace_complete=False,
                structured_trace_status="unavailable",
            )

        archive = archive_records(
            harness=self.name,
            session_id=session_id,
            records=records,
            run_dir=run_dir,
            destination_name="openhands-session.trace.jsonl",
        )
        write_archive_event(
            writer,
            harness=self.name,
            session_id=session_id,
            archive=archive,
        )

        _emit_normalized_events(
            records=records,
            writer=writer,
            session_id=session_id,
            persistence_dir=persistence_dir,
        )
        _emit_usage_from_base_state(
            persistence_dir=persistence_dir,
            session_id=session_id,
            writer=writer,
        )

        return AdapterResult(
            status="finished",
            trace_complete=True,
            structured_trace_status="available",
        )


def _resolve_persistence_dir(allowed_state_dir: str | None) -> Path | None:
    if not allowed_state_dir:
        return None
    try:
        return Path(allowed_state_dir).resolve(strict=True)
    except OSError:
        return None


def _extract_conversation_id(stdout: str, stderr: str) -> str | None:
    for text in (stdout, stderr):
        matches = list(_CONVERSATION_ID_RE.finditer(text or ""))
        if matches:
            return matches[-1].group(1).replace("-", "")
    return None


def _load_session_events(
    *,
    persistence_dir: Path | None,
    session_id: str | None,
    stdout: str,
) -> tuple[list[dict[str, Any]], str | None]:
    if persistence_dir is not None:
        conv_root = persistence_dir / "conversations"
        if conv_root.is_dir():
            target_dir: Path | None = None
            if session_id:
                normalized = session_id.replace("-", "")
                candidate = conv_root / normalized
                if candidate.is_dir():
                    target_dir = candidate
            if target_dir is None:
                dirs = [d for d in conv_root.iterdir() if d.is_dir() and (d / "events").is_dir()]
                if dirs:
                    target_dir = max(dirs, key=lambda d: d.stat().st_mtime_ns)
                    session_id = target_dir.name
            if target_dir is not None:
                events_dir = target_dir / "events"
                if events_dir.is_dir():
                    loaded: list[dict[str, Any]] = []
                    for event_file in sorted(events_dir.glob("event-*.json")):
                        try:
                            data = json.loads(event_file.read_text(encoding="utf-8"))
                            if isinstance(data, dict):
                                loaded.append(data)
                        except (OSError, UnicodeError, json.JSONDecodeError):
                            continue
                    if loaded:
                        return loaded, session_id

    # Fallback: parse JSON lines from stdout
    stream_events: list[dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            continue
        try:
            data = json.loads(stripped)
            if isinstance(data, dict) and "kind" in data:
                stream_events.append(data)
        except json.JSONDecodeError:
            continue
    return stream_events, session_id


def _emit_normalized_events(
    *,
    records: list[dict[str, Any]],
    writer: EventWriter,
    session_id: str | None,
    persistence_dir: Path | None,
) -> None:
    pending_actions: dict[str, dict[str, Any]] = {}
    pending_by_call_id: dict[str, dict[str, Any]] = {}
    seen_compactions: set[str] = set()
    system_tools: list[dict[str, Any]] = []

    for record in records:
        kind = record.get("kind")
        if kind == "SystemPromptEvent":
            raw_tools = record.get("tools")
            if isinstance(raw_tools, list):
                system_tools = [t for t in raw_tools if isinstance(t, dict)]
            continue

        if kind == "Condensation":
            emit_context_compaction(
                writer,
                harness="openhands",
                source="openhands.native_session",
                native_event_type="Condensation",
                record=record,
                session_id=session_id,
                summary=record.get("summary") if isinstance(record.get("summary"), str) else None,
                summary_artifact="openhands-session.trace.jsonl",
                seen=seen_compactions,
                notice_visible_to_model=bool(record.get("summary")),
            )
            continue

        if kind == "MessageEvent":
            source = record.get("source")
            llm_msg = record.get("llm_message")
            if not isinstance(llm_msg, dict):
                continue
            text = _extract_content_text(llm_msg.get("content"))
            if source == "user":
                writer.write(
                    "model_input",
                    turn_id=record.get("id"),
                    model=None,
                    provider="openhands",
                    messages=[{"role": "user", "content": text}],
                    tools=system_tools,
                    request_params={},
                    raw_event=record,
                )
            elif source == "agent" and text:
                writer.write(
                    "assistant_message",
                    message_id=record.get("id"),
                    content=text,
                    reasoning=llm_msg.get("reasoning_content"),
                    raw_event=record,
                )
            continue

        if kind == "ActionEvent":
            action_id = str(record.get("id") or "")
            tool_name = str(record.get("tool_name") or "unknown")
            call_id = str(
                record.get("tool_call_id")
                or (record.get("tool_call") or {}).get("id")
                or action_id
                or f"oh-call-{len(pending_actions) + 1}"
            )
            args = _extract_action_arguments(record)
            thought = _extract_content_text(record.get("thought"))
            reasoning = record.get("reasoning_content")
            if thought or (isinstance(reasoning, str) and reasoning.strip()):
                writer.write(
                    "assistant_message",
                    message_id=f"{action_id}-thought",
                    content=thought or reasoning,
                    content_kind="thinking",
                    raw_event=record,
                )
            entry = {
                "action_id": action_id,
                "tool_name": tool_name,
                "tool_call": {
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "name": tool_name,
                    "arguments": args,
                    "provider_executed": True,
                },
                "raw_action": record,
            }
            if action_id:
                pending_actions[action_id] = entry
            pending_by_call_id[call_id] = entry
            continue

        if kind == "ObservationEvent":
            action_id = str(record.get("action_id") or "")
            call_id = str(record.get("tool_call_id") or "")
            pending = pending_actions.pop(action_id, None) if action_id else None
            if pending is None and call_id:
                pending = pending_by_call_id.pop(call_id, None)
            elif pending is not None:
                pending_by_call_id.pop(pending["tool_call"]["tool_call_id"], None)

            obs = record.get("observation")
            obs = obs if isinstance(obs, dict) else {}
            tool_name = str(
                record.get("tool_name")
                or (pending["tool_name"] if pending else "unknown")
            )
            effective_call_id = str(
                call_id
                or (pending["tool_call"]["tool_call_id"] if pending else record.get("id") or "unknown")
            )
            content_text = _render_observation_llm_content(obs)
            is_error = bool(obs.get("is_error"))
            exit_code = obs.get("exit_code")
            if exit_code is None and isinstance(obs.get("metadata"), dict):
                exit_code = obs["metadata"].get("exit_code")
            if isinstance(exit_code, int) and exit_code not in (0, -1):
                is_error = True

            if tool_name == "finish":
                finish_msg = ""
                if pending and isinstance(pending["tool_call"]["arguments"].get("message"), str):
                    finish_msg = pending["tool_call"]["arguments"]["message"]
                elif content_text:
                    finish_msg = content_text
                if finish_msg:
                    writer.write(
                        "assistant_message",
                        message_id=record.get("id"),
                        content=finish_msg,
                        finish_reason="stop",
                        raw_event=record,
                    )
                continue

            tool_result: dict[str, Any] = {
                "tool_call_id": effective_call_id,
                "tool_name": tool_name,
                "content": content_text,
                "error": is_error,
            }
            if isinstance(exit_code, int):
                tool_result["exit_code"] = exit_code
            if isinstance(obs.get("timeout"), bool):
                tool_result["timed_out"] = obs["timeout"]
            if isinstance(obs.get("full_output_save_dir"), str):
                tool_result["full_output_save_dir"] = obs["full_output_save_dir"]
            if obs.get("kind") == "TaskObservation":
                for field_name in ("task_id", "subagent", "status"):
                    if isinstance(obs.get(field_name), str):
                        tool_result[field_name] = obs[field_name]

            writer.write(
                "tool",
                tool_call=(
                    pending["tool_call"]
                    if pending
                    else {
                        "tool_call_id": effective_call_id,
                        "tool_name": tool_name,
                        "name": tool_name,
                        "arguments": {},
                        "provider_executed": True,
                    }
                ),
                tool_result=tool_result,
                raw_event={
                    "tool_call": pending["raw_action"] if pending else None,
                    "tool_result": record,
                },
            )

    for pending in pending_by_call_id.values():
        if pending["tool_name"] == "finish":
            msg = pending["tool_call"]["arguments"].get("message")
            if isinstance(msg, str) and msg:
                writer.write(
                    "assistant_message",
                    message_id=pending["action_id"],
                    content=msg,
                    finish_reason="stop",
                    raw_event=pending["raw_action"],
                )
            continue
        writer.write(
            "tool",
            tool_call=pending["tool_call"],
            tool_result=None,
            raw_event={"tool_call": pending["raw_action"], "tool_result": None},
        )


def _emit_usage_from_base_state(
    *,
    persistence_dir: Path | None,
    session_id: str | None,
    writer: EventWriter,
) -> None:
    if persistence_dir is None or not session_id:
        return
    base_state_path = persistence_dir / "conversations" / session_id.replace("-", "") / "base_state.json"
    if not base_state_path.is_file():
        return
    try:
        state = json.loads(base_state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    usage_metrics = ((state.get("stats") or {}).get("usage_to_metrics") or {})
    if not isinstance(usage_metrics, dict):
        return
    for scope, metric_data in usage_metrics.items():
        if not isinstance(metric_data, dict):
            continue
        token_usages = metric_data.get("token_usages")
        if not isinstance(token_usages, list):
            continue
        for item in token_usages:
            if not isinstance(item, dict):
                continue
            prompt_tokens = item.get("prompt_tokens")
            completion_tokens = item.get("completion_tokens")
            cache_read = item.get("cache_read_tokens", 0)
            cache_write = item.get("cache_write_tokens", 0)
            reasoning = item.get("reasoning_tokens", 0)
            if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
                continue
            writer.write(
                "token_usage",
                source="openhands.base_state",
                usage_scope=str(scope),
                usage={
                    "input_tokens": prompt_tokens,
                    "cached_input_tokens": cache_read if isinstance(cache_read, int) else 0,
                    "cache_write_input_tokens": cache_write if isinstance(cache_write, int) else 0,
                    "output_tokens": completion_tokens,
                    "reasoning_output_tokens": reasoning if isinstance(reasoning, int) else 0,
                },
                raw_event=item,
            )


def _extract_action_arguments(record: dict[str, Any]) -> dict[str, Any]:
    tool_call = record.get("tool_call")
    if isinstance(tool_call, dict):
        raw_args = tool_call.get("arguments")
        if isinstance(raw_args, str) and raw_args.strip():
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass
        elif isinstance(raw_args, dict):
            return dict(raw_args)
    action = record.get("action")
    if isinstance(action, dict):
        return {k: v for k, v in action.items() if k != "kind"}
    return {}


def _extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _clean_terminal_text(text: str) -> str:
    return _ANSI_CSI_RE.sub("", text).strip()


def _render_observation_llm_content(obs: dict[str, Any]) -> str:
    raw_text = _ANSI_CSI_RE.sub("", _extract_content_text(obs.get("content")))
    kind = obs.get("kind")
    if kind == "TaskObservation":
        header = (
            f"Task ID: {obs.get('task_id', '')}\n"
            f"Subagent: {obs.get('subagent', '')}\n"
            f"Status: {obs.get('status', '')}"
        )
        return f"{header}\n{raw_text}".strip()
    if kind == "TerminalObservation":
        meta = obs.get("metadata") if isinstance(obs.get("metadata"), dict) else {}
        prefix = str(meta.get("prefix") or "")
        suffix = str(meta.get("suffix") or "")
        ret = f"{prefix}{raw_text}{suffix}"
        if meta.get("working_dir"):
            ret += f"\n[Current working directory: {meta['working_dir']}]"
        if meta.get("py_interpreter_path"):
            ret += f"\n[Python interpreter: {meta['py_interpreter_path']}]"
        exit_code = meta.get("exit_code")
        if isinstance(exit_code, int) and exit_code != -1:
            ret += f"\n[Command finished with exit code {exit_code}]"
        if len(ret) > 30000:
            notice = (
                "\n<response clipped><NOTE>Due to the max output limit, only part "
                "of this output has been shown to you.</NOTE>\n"
            )
            save_dir = obs.get("full_output_save_dir")
            if isinstance(save_dir, str) and save_dir:
                notice = (
                    f"\n<response clipped><NOTE>Due to the max output limit, only "
                    f"part of this output has been shown to you. Full output saved "
                    f"in {save_dir}.</NOTE>\n"
                )
            budget = max(0, 30000 - len(notice))
            head = budget // 2 + (budget % 2)
            tail = budget - head
            ret = ret[:head] + notice + (ret[-tail:] if tail > 0 else "")
        return ret.strip()
    return raw_text.strip()

