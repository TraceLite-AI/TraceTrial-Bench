from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness_tracer import SCHEMA_VERSION
from harness_tracer.writer import EventWriter

UNKNOWN = "unknown"
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
TOKEN_ALIASES = {
    "cache_write_input_tokens": (
        "cache_write_tokens",
        "cache_creation_input_tokens",
    ),
}
STEP_EVENT_TYPES = {
    "model_input",
    "tool_inventory",
    "tool",
    "tool_call",
    "tool_result",
    "assistant_delta",
    "assistant_message",
    "token_usage",
    "raw_stdout",
    "raw_stderr",
    "run_failed",
    "context_compaction",
}
PREVIEW_LIMIT = 240
CLI_STREAM_TIMING_SCHEMA = "harnessbench_cli_stream_timing_v1"


def _empty_token_usage() -> dict[str, Any]:
    return {
        "input_tokens": UNKNOWN,
        "cached_input_tokens": "N/A",
        "cache_write_input_tokens": UNKNOWN,
        "output_tokens": UNKNOWN,
        "reasoning_output_tokens": UNKNOWN,
        "total_tokens": UNKNOWN,
        "cache_hit_rate": "N/A",
        "call_count": 0,
    }


def _merge_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _empty_token_usage()
    known: dict[str, int] = {}
    aggregate_usage: dict[str, Any] | None = None
    calls: list[dict[str, Any]] = []
    cache_complete = True
    input_complete = True
    output_complete = True
    reported_cost_usd: float | int | None = None

    for event in events:
        if event.get("event_type") != "token_usage":
            continue
        event_cost = event.get("reported_cost_usd")
        if (
            isinstance(event_cost, (int, float))
            and not isinstance(event_cost, bool)
            and event_cost >= 0
        ):
            reported_cost_usd = event_cost
        usage = event.get("usage") or {}
        if event.get("usage_aggregate") is True and isinstance(usage, dict):
            aggregate_usage = usage
            continue
        input_tokens = _usage_integer(usage, "input_tokens")
        output_tokens = _usage_integer(usage, "output_tokens")
        cached_input_tokens = _usage_integer(usage, "cached_input_tokens")
        # Reasoning-only telemetry (for example Claude Code's estimated
        # thinking deltas) describes an existing model call and must not create
        # an additional call in the per-call accounting.
        if input_tokens is not None or output_tokens is not None:
            call_total = (
                input_tokens + output_tokens
                if input_tokens is not None and output_tokens is not None
                else UNKNOWN
            )
            calls.append(
                _without_none(
                    {
                        "call_index": len(calls) + 1,
                        "event_seq": event.get("seq"),
                        "conversation_turn": event.get("conversation_turn"),
                        "source": event.get("source"),
                        "usage_scope": event.get("usage_scope", "unknown"),
                        "input_tokens": (
                            input_tokens if input_tokens is not None else UNKNOWN
                        ),
                        "cached_input_tokens": (
                            cached_input_tokens
                            if cached_input_tokens is not None
                            else "N/A"
                        ),
                        "output_tokens": (
                            output_tokens if output_tokens is not None else UNKNOWN
                        ),
                        "total_tokens": call_total,
                    }
                )
            )
            if cached_input_tokens is None:
                cache_complete = False
            if input_tokens is None:
                input_complete = False
            if output_tokens is None:
                output_complete = False
        for key in TOKEN_KEYS:
            value = _usage_integer(usage, key)
            if value is not None:
                known[key] = known.get(key, 0) + value

    if aggregate_usage is not None:
        input_complete = _usage_integer(aggregate_usage, "input_tokens") is not None
        output_complete = _usage_integer(aggregate_usage, "output_tokens") is not None
        cache_complete = (
            _usage_integer(aggregate_usage, "cached_input_tokens") is not None
        )
        for key in TOKEN_KEYS:
            value = _usage_integer(aggregate_usage, key)
            if value is not None:
                totals[key] = value
    else:
        for key in TOKEN_KEYS:
            if key in known:
                totals[key] = known[key]

    totals["call_count"] = len(calls)
    totals["calls"] = calls
    if reported_cost_usd is not None:
        totals["reported_cost_usd"] = reported_cost_usd

    # Cached input and reasoning are breakdowns of input and output.  They are
    # deliberately not additive so this total has the same meaning across
    # harnesses: all model input plus all model output.
    input_total = totals["input_tokens"]
    output_total = totals["output_tokens"]
    if (
        calls
        and input_complete
        and output_complete
        and isinstance(input_total, int)
        and isinstance(output_total, int)
    ):
        totals["total_tokens"] = input_total + output_total
    elif not input_complete or not output_complete:
        totals["input_tokens"] = input_total if input_complete else UNKNOWN
        totals["output_tokens"] = output_total if output_complete else UNKNOWN

    if not calls or not cache_complete:
        totals["cached_input_tokens"] = "N/A"
        totals["cache_hit_rate"] = "N/A"
    elif not input_complete:
        totals["cache_hit_rate"] = "N/A"
    elif isinstance(input_total, int) and input_total > 0:
        cached_total = totals["cached_input_tokens"]
        if isinstance(cached_total, int) and cached_total <= input_total:
            totals["cache_hit_rate"] = round(cached_total / input_total, 8)
        else:
            totals["cached_input_tokens"] = "N/A"
            totals["cache_hit_rate"] = "N/A"
    elif input_total == 0 and totals["cached_input_tokens"] == 0:
        totals["cache_hit_rate"] = 0.0
    return totals


def _cli_stream_timing(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    starts = [
        event
        for event in events
        if event.get("event_type") == "model_input"
        and _event_offset_ms(event) is not None
    ]
    if not starts:
        return None

    ttfts: list[float] = []
    decodes: list[float] = []
    observed_calls = 0
    for index, start_event in enumerate(starts):
        start_seq = _event_seq(start_event)
        start_offset = _event_offset_ms(start_event)
        if start_seq is None or start_offset is None:
            continue
        next_start_seq = (
            _event_seq(starts[index + 1]) if index + 1 < len(starts) else None
        )
        window = [
            event
            for event in events
            if _in_seq_window(event, start_seq=start_seq, end_seq=next_start_seq)
        ]
        first_output_offset = _first_cli_output_offset_ms(window)
        completed_offset = _last_event_offset_ms(window, event_type="token_usage")
        if first_output_offset is None:
            continue
        observed_calls += 1
        if first_output_offset >= start_offset:
            ttfts.append(first_output_offset - start_offset)
        if completed_offset is not None and completed_offset > first_output_offset:
            decodes.append(completed_offset - first_output_offset)

    if observed_calls == 0:
        return None
    return {
        "schema_version": CLI_STREAM_TIMING_SCHEMA,
        "source": "cli_json_stdout_line_observation",
        "status": "available" if len(ttfts) == len(starts) else "partial",
        "request_count": len(starts),
        "ttft_mean_ms": round(sum(ttfts) / len(ttfts), 3) if ttfts else "N/A",
        "ttft_observed_count": len(ttfts),
        "decode_duration_ms": round(sum(decodes), 3) if decodes else "N/A",
        "decode_observed_count": len(decodes),
    }


def _event_seq(event: dict[str, Any]) -> int | None:
    value = event.get("seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _event_offset_ms(event: dict[str, Any]) -> float | None:
    value = event.get("stream_observed_offset_ms")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _in_seq_window(
    event: dict[str, Any], *, start_seq: int, end_seq: int | None
) -> bool:
    seq = _event_seq(event)
    return seq is not None and seq >= start_seq and (
        end_seq is None or seq < end_seq
    )


def _first_cli_output_offset_ms(events: list[dict[str, Any]]) -> float | None:
    for event in events:
        event_type = event.get("event_type")
        if event_type == "tool":
            tool_start = event.get("tool_call_stream_observed")
            if isinstance(tool_start, dict):
                value = _event_offset_ms(tool_start)
                if value is not None:
                    return value
            return _event_offset_ms(event)
        if event_type == "assistant_delta" and (
            event.get("content_delta") or event.get("reasoning_delta")
        ):
            return _event_offset_ms(event)
        if event_type == "assistant_message" and (
            event.get("content") or event.get("text") or event.get("reasoning")
        ):
            return _event_offset_ms(event)
    return None


def _first_event_offset_ms(
    events: list[dict[str, Any]], *, event_type: str
) -> float | None:
    for event in events:
        if event.get("event_type") == event_type:
            return _event_offset_ms(event)
    return None


def _last_event_offset_ms(
    events: list[dict[str, Any]], *, event_type: str
) -> float | None:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            value = _event_offset_ms(event)
            if value is not None:
                return value
    return None


def _usage_integer(usage: Any, key: str) -> int | None:
    if not isinstance(usage, dict):
        return None
    for candidate in (key, *TOKEN_ALIASES.get(key, ())):
        value = usage.get(candidate, UNKNOWN)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _tool_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "total_tool_calls": 0,
        "total_tool_results": 0,
        "by_name": {},
    }
    for event in events:
        event_type = event.get("event_type")
        if event_type == "tool":
            tool_call = event.get("tool_call") or {}
            tool_result = event.get("tool_result")
            name = tool_call.get("tool_name") or UNKNOWN
            stats["total_tool_calls"] += 1
            by_name = stats["by_name"].setdefault(name, {"calls": 0, "results": 0})
            by_name["calls"] += 1
            if tool_result is not None:
                stats["total_tool_results"] += 1
                by_name["results"] += 1
            continue
        if event_type in {"tool_call", "tool_result"}:
            name = event.get("tool_name") or event.get("name") or UNKNOWN
            by_name = stats["by_name"].setdefault(name, {"calls": 0, "results": 0})
            if event_type == "tool_call":
                stats["total_tool_calls"] += 1
                by_name["calls"] += 1
            else:
                stats["total_tool_results"] += 1
                by_name["results"] += 1
    return stats


def _final_answer(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if (
            event.get("event_type") == "assistant_message"
            and event.get("content_kind") != "thinking"
        ):
            return event.get("content") or event.get("text")
    return None


def _steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps = []
    for event in events:
        if event.get("event_type") not in STEP_EVENT_TYPES:
            continue
        step = _step_for_event(len(steps) + 1, event)
        if step is not None:
            steps.append(step)
    return steps


def _step_for_event(step_number: int, event: dict[str, Any]) -> dict[str, Any] | None:
    event_type = event.get("event_type")
    base = {
        "step": step_number,
        "event_seq": event.get("seq"),
        "event_type": event_type,
    }

    if event_type == "tool":
        tool_call = event.get("tool_call") or {}
        tool_result = event.get("tool_result") or {}
        tool_name = tool_call.get("tool_name") or UNKNOWN
        step = {
            **base,
            "action": "tool_use",
            "description": f"tool_use: {tool_name}",
            "tool_name": tool_name,
            "tool_call_id": tool_call.get("tool_call_id"),
            "arguments": tool_call.get("arguments", {}),
            "result_preview": _preview(tool_result.get("content")),
            "error": tool_result.get("error"),
        }
        return _without_none(step)

    if event_type == "tool_call":
        tool_name = event.get("tool_name") or event.get("name") or UNKNOWN
        return _without_none(
            {
                **base,
                "action": "tool_use",
                "description": f"tool_use: {tool_name}",
                "tool_name": tool_name,
                "tool_call_id": event.get("tool_call_id"),
                "arguments": event.get("arguments", {}),
            }
        )

    if event_type == "tool_result":
        tool_name = event.get("tool_name") or event.get("name") or UNKNOWN
        return _without_none(
            {
                **base,
                "action": "tool_result",
                "description": f"tool_result: {tool_name}",
                "tool_name": tool_name,
                "tool_call_id": event.get("tool_call_id"),
                "result_preview": _preview(event.get("content")),
                "error": event.get("error"),
            }
        )

    if event_type == "model_input":
        return _without_none(
            {
                **base,
                "action": "model_input",
                "description": "model_input",
                "model": event.get("model"),
                "provider": event.get("provider"),
                "messages_count": _len_or_none(event.get("messages")),
                "tools_count": _len_or_none(event.get("tools")),
            }
        )

    if event_type == "tool_inventory":
        return _without_none(
            {
                **base,
                "action": "tool_inventory",
                "description": "tool_inventory",
                "source": event.get("source"),
                "scope": event.get("scope"),
                "model_visible": event.get("model_visible"),
                "complete": event.get("complete"),
                "tool_count": _len_or_none(event.get("tools")),
                "request_count": event.get("request_count"),
            }
        )

    if event_type in {"assistant_message", "assistant_delta"}:
        content = event.get("content") or event.get("text") or event.get("delta")
        return _without_none(
            {
                **base,
                "action": event_type,
                "description": event_type,
                "content_preview": _preview(content),
                "finish_reason": event.get("finish_reason"),
            }
        )

    if event_type == "token_usage":
        return {
            **base,
            "action": "token_usage",
            "description": "token_usage",
            "usage": event.get("usage") or {},
        }

    if event_type in {"raw_stdout", "raw_stderr"}:
        return _without_none(
            {
                **base,
                "action": event_type,
                "description": event_type,
                "content_preview": _preview(event.get("text")),
                "parseable": event.get("parseable"),
            }
        )

    if event_type == "run_failed":
        return _without_none(
            {
                **base,
                "action": "run_failed",
                "description": "run_failed",
                "error_type": event.get("error_type"),
                "error_message": event.get("error_message"),
            }
        )

    if event_type == "context_compaction":
        return _without_none(
            {
                **base,
                "action": "context_compaction",
                "description": "context_compaction",
                "source": event.get("source"),
                "native_event_type": event.get("native_event_type"),
                "summary_present": event.get("summary_present"),
                "summary_preview": _preview(event.get("summary")),
            }
        )

    return None


def _preview(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = text.strip()
    if len(text) <= PREVIEW_LIMIT:
        return text
    return text[: PREVIEW_LIMIT - 3].rstrip() + "..."


def _len_or_none(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def build_summary(
    *,
    writer: EventWriter,
    run_id: str,
    run_dir: Path,
    harness: str,
    model: str,
    input_query: str,
    status: str,
    trace_complete: bool,
    structured_trace_status: str,
    exit_code: int | None,
    timed_out: bool,
    error_type: str | None,
    error_message: str | None,
    started_at: str,
    finished_at: str,
    duration_ms: int,
    session_id: str | None,
    cwd: str,
    argv: list[str],
    environment: dict[str, str | None],
    summary_path: Path,
) -> dict[str, Any]:
    events = writer.events
    summary = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "harness": harness,
        "model": model,
        "input_query": input_query,
        "status": status,
        "trace_complete": trace_complete,
        "structured_trace_status": structured_trace_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "error_type": error_type,
        "error_message": error_message,
        "last_event_seq": writer.seq,
        "events_path": str(run_dir / "events.json"),
        "summary_path": str(summary_path),
        "cwd": cwd,
        "argv": argv,
        "environment": environment,
        "token_usage": _merge_usage(events),
        "cli_stream_timing": _cli_stream_timing(events),
        "tool_stats": _tool_stats(events),
        "tool_inventory": _tool_inventory_summary(events),
        "steps": _steps(events),
        "compaction_stats": _compaction_stats(events),
        "final_answer": _final_answer(events),
    }
    return summary


def _tool_inventory_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    inventories = [
        event for event in events if event.get("event_type") == "tool_inventory"
    ]
    complete = [
        event
        for event in inventories
        if event.get("model_visible") is True and event.get("complete") is True
    ]
    names = sorted(
        {
            str(tool["name"])
            for event in complete
            for tool in (event.get("tools") or [])
            if isinstance(tool, dict)
            and isinstance(tool.get("name"), str)
            and tool["name"]
        }
    )
    return {
        "event_count": len(inventories),
        "complete_model_visible_event_count": len(complete),
        "tool_names_from_complete_inventories": names,
        "sources": sorted(
            {
                str(event["source"])
                for event in inventories
                if isinstance(event.get("source"), str) and event["source"]
            }
        ),
    }


def _compaction_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    compactions = [
        event for event in events if event.get("event_type") == "context_compaction"
    ]
    archives = [
        event for event in events if event.get("event_type") == "native_session_archive"
    ]
    return {
        "count": len(compactions),
        "sources": sorted(
            {
                str(event.get("source"))
                for event in compactions
                if event.get("source")
            }
        ),
        "native_session_statuses": [
            {
                "source": event.get("source"),
                "status": event.get("status"),
                "artifact": event.get("artifact"),
            }
            for event in archives
        ],
    }


def write_summary(summary: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_last_message(summary: dict[str, Any], path: Path) -> None:
    final_answer = summary.get("final_answer")
    path.write_text(final_answer if isinstance(final_answer, str) else "", encoding="utf-8")
