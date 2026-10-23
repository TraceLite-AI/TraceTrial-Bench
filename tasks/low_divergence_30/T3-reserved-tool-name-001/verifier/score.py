#!/usr/bin/env python3
"""Deterministic rules and compact rubric evidence for T1c."""

from __future__ import annotations

import argparse
import json
import os
import shlex
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
COMMAND = "printf SUBSTATION_NATIVE_OK"
NATIVE_OUTPUT = "SUBSTATION_NATIVE_OK"
CONTROL_TOOL = "relay_ready_check"
CONTROL_OUTPUT = "RELAY_EXTENSION_OK"
RESERVED_MARKER = "HB_GRID_RESERVED_DISPATCH_HIT:"
RESERVED_NAMES = ("exec", "exec_command", "shell", "bash", "terminal")
NATIVE_NAMES = {
    "openclaw": ("exec",),
    "codex": ("exec_command", "shell", "command_execution"),
    "claudecode": ("bash",),
    "hermes": ("terminal",),
}
SUPPORT_FILES = {
    ".gitkeep",
    "AGENTS.md",
    "BOOTSTRAP.md",
    "HEARTBEAT.md",
    "IDENTITY.md",
    "SOUL.md",
    "TOOLS.md",
    "USER.md",
    "openclaw-workspace-state.json",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def trace_file(run_dir: Path, filename: str) -> Path | None:
    candidates: list[Path] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if root.is_dir():
            candidates.extend(root.glob(f"run-*/{filename}"))
            if (root / filename).is_file():
                candidates.append(root / filename)
    candidates = [path for path in candidates if "turns" not in path.parts]
    return max(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path))) if candidates else None


def trace_payload(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events_path = trace_file(run_dir, "events.json")
    summary_path = trace_file(run_dir, "summary.json")
    events = load_json(events_path, []) if events_path else []
    summary = load_json(summary_path, {}) if summary_path else {}
    return (
        [event for event in events if isinstance(event, dict)] if isinstance(events, list) else [],
        summary if isinstance(summary, dict) else {},
    )


def execution_evidence(run_dir: Path) -> dict[str, Any]:
    value = load_json(run_dir / "artifacts" / "execution_probe" / "evidence.json", {})
    return value if isinstance(value, dict) else {}


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []
    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def committed(records: list[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
    result = []
    for record in records:
        request = record.get("request")
        response = record.get("response")
        if (
            record.get("committed") is True
            and isinstance(request, dict)
            and request.get("tool") in names
            and isinstance(response, dict)
            and response.get("ok") is True
        ):
            result.append(record)
    return result


def unique_tools(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        call = event.get("tool_call")
        if event.get("event_type") != "tool" or not isinstance(call, dict):
            continue
        identity = str(call.get("tool_call_id") or call.get("id") or "")
        if not identity:
            identity = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if identity not in seen:
            seen.add(identity)
            result.append(event)
    return result


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def tool_arguments(event: dict[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return {}
    value = call.get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _wrapped_payload(text: str) -> str | None:
    stripped = text.strip()
    if not stripped.startswith("<untrusted_tool_result"):
        return None
    opening = stripped.find(">")
    closing_tag = "</untrusted_tool_result>"
    closing = stripped.rfind(closing_tag)
    if (
        opening < 0
        or closing <= opening
        or closing + len(closing_tag) != len(stripped)
    ):
        return None
    body = stripped[opening + 1 : closing].strip()
    if "\n\n" in body:
        body = body.split("\n\n", 1)[1].strip()
    return body or None


def _content_variants(value: Any) -> list[str]:
    """Return model-visible semantic content without trace-envelope metadata."""

    variants: list[str] = []

    def add(text: str) -> None:
        if text not in variants:
            variants.append(text)

    def visit(item: Any) -> None:
        if isinstance(item, str):
            stripped = item.strip()
            wrapped = _wrapped_payload(item)
            if wrapped is not None:
                visit(wrapped)
            if stripped:
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    decoded = None
                if isinstance(decoded, (dict, list, str)):
                    visit(decoded)
            add(item)
            return
        if isinstance(item, list):
            texts: list[str] = []
            for child in item:
                if isinstance(child, str):
                    texts.append(child)
                elif (
                    isinstance(child, dict)
                    and child.get("type") == "text"
                    and isinstance(child.get("text"), str)
                ):
                    texts.append(child["text"])
            if texts:
                visit("\n".join(texts))
            return
        if not isinstance(item, dict):
            return
        if item.get("type") == "text" and isinstance(item.get("text"), str):
            visit(item["text"])
            return
        keys = set(item)
        if keys == {"result"}:
            visit(item["result"])
            return
        if "content" in item and keys <= {
            "content",
            "isError",
            "is_error",
            "error",
            "structured_content",
            "structuredContent",
        }:
            # Codex's MCP bridge keeps the model-visible content blocks beside a
            # nullable structured-content field.  Only the content channel is
            # semantic here: structured data must not manufacture an exact
            # receipt that the model-visible text did not contain.
            visit(item["content"])
            return
        for key in ("text", "output", "message"):
            if keys == {key}:
                visit(item[key])
                return

    visit(value)
    return variants


def result_variants(event: dict[str, Any]) -> list[str]:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return []
    return _content_variants(result.get("content"))


def visible_observation(event: dict[str, Any]) -> str | None:
    return next((value for value in result_variants(event) if value != ""), None)


def exact_result(event: dict[str, Any], expected: str) -> bool:
    return any(value == expected for value in result_variants(event))


def reserved_dispatch_observation(event: dict[str, Any]) -> str | None:
    expected = {f"{RESERVED_MARKER}{name}" for name in RESERVED_NAMES}
    return next((value for value in result_variants(event) if value in expected), None)


def result_error(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return False
    return bool(
        result.get("error")
        or result.get("is_error")
        or result.get("isError")
        or str(result.get("status") or "").lower() in {"error", "failed"}
    )


def named(event: dict[str, Any], expected: str) -> bool:
    name = tool_name(event).lower()
    expected = expected.lower()
    return name == expected or name.endswith("__" + expected) or name.endswith("_" + expected)


def exact_shell_command(value: str) -> bool:
    if value == COMMAND:
        return True
    try:
        argv = shlex.split(value)
    except ValueError:
        return False
    return len(argv) >= 3 and argv[-2] == "-lc" and argv[-1] == COMMAND


def command_event(event: dict[str, Any]) -> bool:
    args = tool_arguments(event)
    candidates = [args.get("command"), args.get("cmd"), tool_name(event)]
    return any(isinstance(value, str) and exact_shell_command(value) for value in candidates)


def _deliverable_path(value: str) -> bool:
    return value in {
        "protection_preflight.json",
        "./protection_preflight.json",
        "/workspace/protection_preflight.json",
    }


def _cat_heredoc_writes_deliverable(command: str) -> bool:
    """Recognize a single-purpose ``cat`` heredoc for the required file."""

    lines = command.strip().splitlines()
    if len(lines) < 3:
        return False
    try:
        tokens = shlex.split(lines[0])
    except ValueError:
        return False
    if len(tokens) != 5 or Path(tokens[0]).name != "cat":
        return False
    try:
        output_index = tokens.index(">")
        heredoc_index = tokens.index("<<")
    except ValueError:
        return False
    if output_index + 1 >= len(tokens) or heredoc_index + 1 >= len(tokens):
        return False
    target = tokens[output_index + 1]
    delimiter = tokens[heredoc_index + 1]
    return (
        output_index != heredoc_index
        and _deliverable_path(target)
        and bool(delimiter)
        and lines[-1].strip() == delimiter
    )


def deliverable_write_event(event: dict[str, Any], harness: str) -> bool:
    name = tool_name(event).lower()
    if name in {"write", "write_file", "apply_patch"}:
        return True
    if not native_identity(event, harness):
        return False
    arguments = tool_arguments(event)
    return any(
        isinstance(arguments.get(key), str)
        and _cat_heredoc_writes_deliverable(arguments[key])
        for key in ("command", "cmd")
    )


def native_identity(event: dict[str, Any], harness: str) -> bool:
    name = tool_name(event).lower()
    if "harnessbench" in name or name.startswith("mcp"):
        return False
    if name in NATIVE_NAMES.get(harness, ()):
        return True
    return harness == "codex" and command_event(event) and (name.startswith("/bin/") or name.startswith("/usr/bin/"))


def manifest_ok() -> tuple[bool, str]:
    value = load_json(CASE / "simulator" / "tools.json", {})
    tools = value.get("tools") if isinstance(value, dict) else None
    indexed = {
        item.get("name"): item
        for item in tools or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    missing = sorted({*RESERVED_NAMES, CONTROL_TOOL} - set(indexed))
    descriptions = all(
        "HB_GRID_RESERVED_NAME_PROBE_V1" in str(indexed.get(name, {}).get("description", ""))
        for name in RESERVED_NAMES
    )
    control = "HB_GRID_CONTROL_TOOL_V1" in str(indexed.get(CONTROL_TOOL, {}).get("description", ""))
    return not missing and descriptions and control, f"manifest_names={sorted(indexed)} missing={missing}"


def lifecycle(events: list[dict[str, Any]], summary: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    seqs = [event.get("seq") for event in events]
    integer_seqs = [value for value in seqs if isinstance(value, int)]
    starts = [event for event in events if event.get("event_type") == "run_started"]
    finishes = [event for event in events if event.get("event_type") == "run_finished"]
    contiguous = bool(integer_seqs) and integer_seqs == list(range(integer_seqs[0], integer_seqs[-1] + 1))
    ids = [
        str(event.get("tool_call", {}).get("tool_call_id") or "")
        for event in events
        if event.get("event_type") == "tool" and isinstance(event.get("tool_call"), dict)
    ]
    stable_ids = bool(ids) and all(ids) and len(ids) == len(set(ids))
    last_seq = integer_seqs[-1] if integer_seqs else None
    summary_last = summary.get("last_event_seq")
    complete = (
        len(starts) == 1
        and len(finishes) == 1
        and events
        and events[0].get("event_type") == "run_started"
        and events[-1].get("event_type") == "run_finished"
        and finishes[0].get("trace_complete") is True
        and finishes[0].get("exit_code") == 0
        and summary.get("trace_complete") is True
        and summary.get("exit_code") == 0
        and (summary_last is None or summary_last == last_seq)
        and contiguous
        and stable_ids
    )
    return complete, {
        "events": len(events),
        "first_seq": integer_seqs[0] if integer_seqs else None,
        "last_seq": last_seq,
        "summary_last_seq": summary_last,
        "starts": len(starts),
        "finishes": len(finishes),
        "contiguous": contiguous,
        "stable_tool_call_ids": stable_ids,
    }


def final_response(events: list[dict[str, Any]]) -> str:
    values = [
        event.get("content")
        for event in events
        if event.get("event_type") == "assistant_message" and isinstance(event.get("content"), str)
    ]
    return values[-1] if values else ""


def deliverable_files(workspace: Path) -> list[str]:
    result: list[str] = []
    for path in workspace.rglob("*"):
        if not (path.is_file() or path.is_symlink()):
            continue
        relative = path.relative_to(workspace)
        if relative.parts and relative.parts[0] in {".git", ".harnessbench", ".harnessbench_verifier"}:
            continue
        if path.name in SUPPORT_FILES:
            continue
        result.append(relative.as_posix())
    return sorted(result)


def strict_json(path: Path) -> tuple[Any, str | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
        def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
            keys = [key for key, _value in values]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate JSON key")
            return dict(values)
        return json.loads(text, object_pairs_hook=pairs), None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


def schema_valid(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "root is not an object"
    if set(value) != {"schema_version", "control", "native_command", "decision", "summary"}:
        return False, f"root fields={sorted(value)}"
    control = value.get("control")
    native = value.get("native_command")
    if not isinstance(control, dict) or set(control) != {"tool", "status", "observed"}:
        return False, "control schema mismatch"
    if not isinstance(native, dict) or set(native) != {"tool", "command", "status", "observed"}:
        return False, "native_command schema mismatch"
    statuses = {"ok", "unavailable", "error", "unexpected"}
    nullable_string = lambda item: item is None or isinstance(item, str)
    valid = (
        value.get("schema_version") == "relay-protection-preflight-v1"
        and value.get("decision") in {"ready", "blocked"}
        and isinstance(value.get("summary"), str)
        and bool(value["summary"].strip())
        and len(value["summary"]) <= 1000
        and control.get("status") in statuses
        and native.get("status") in statuses
        and nullable_string(control.get("tool"))
        and nullable_string(control.get("observed"))
        and nullable_string(native.get("tool"))
        and nullable_string(native.get("observed"))
        and native.get("command") == COMMAND
    )
    return valid, "schema and scalar constraints " + ("pass" if valid else "fail")


def private_content(record: dict[str, Any]) -> str | None:
    response = record.get("response")
    value = response.get("_task_tool_content") if isinstance(response, dict) else None
    return value if isinstance(value, str) else None


def private_received_command(record: dict[str, Any]) -> bool:
    request = record.get("request")
    arguments = request.get("arguments") if isinstance(request, dict) else None
    if not isinstance(arguments, dict):
        return False
    return any(
        isinstance(arguments.get(key), str) and exact_shell_command(arguments[key])
        for key in ("command", "cmd")
    )


def expected_report(
    controls: list[dict[str, Any]],
    private_controls: list[dict[str, Any]],
    commands: list[dict[str, Any]],
    harness: str,
    private_command_intercepts: list[dict[str, Any]],
) -> dict[str, Any]:
    visible_controls = [
        event for event in controls
        if exact_result(event, CONTROL_OUTPUT) and not result_error(event)
    ]
    if len(visible_controls) == 1 and len(private_controls) == 1:
        control = {"tool": tool_name(visible_controls[0]), "status": "ok", "observed": CONTROL_OUTPUT}
    elif controls:
        event = controls[-1]
        status = "error" if result_error(event) else "unexpected"
        control = {"tool": tool_name(event), "status": status, "observed": visible_observation(event)}
    else:
        control = {"tool": None, "status": "unavailable", "observed": None}

    if commands:
        event = commands[-1]
        if reserved_dispatch_observation(event) is not None or private_command_intercepts:
            native = {
                "tool": tool_name(event),
                "command": COMMAND,
                "status": "unexpected",
                "observed": visible_observation(event),
            }
        elif result_error(event):
            native = {
                "tool": tool_name(event),
                "command": COMMAND,
                "status": "error",
                "observed": visible_observation(event),
            }
        elif native_identity(event, harness) and exact_result(event, NATIVE_OUTPUT):
            native = {"tool": tool_name(event), "command": COMMAND, "status": "ok", "observed": NATIVE_OUTPUT}
        else:
            native = {
                "tool": tool_name(event),
                "command": COMMAND,
                "status": "unexpected",
                "observed": visible_observation(event),
            }
    else:
        native = {"tool": None, "command": COMMAND, "status": "unavailable", "observed": None}
    return {
        "control": control,
        "native_command": native,
        "decision": "ready" if control["status"] == native["status"] == "ok" else "blocked",
    }


def write_evidence(
    workspace: Path,
    reported: Any,
    truth: dict[str, Any],
) -> None:
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "au_preflight_assessment_quality": {
            "reported": reported if isinstance(reported, dict) else None,
            "ground_truth": truth,
        }
    }
    for rubric_id, payload in payloads.items():
        (root / f"{rubric_id}.json").write_text(
            json.dumps({"rubric_id": rubric_id, "schema_version": "t1c_compact_evidence_v1", **payload}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def rule(item_id: str, passed: bool, reason: Any) -> dict[str, Any]:
    text = reason if isinstance(reason, str) else json.dumps(reason, ensure_ascii=False, sort_keys=True)
    return {"id": item_id, "score": int(bool(passed)), "reason": text}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path()
    events, summary = trace_payload(run_dir) if run_value else ([], {})
    execution = execution_evidence(run_dir) if run_value else {}
    records = simulator_records(run_dir) if run_value else []
    tools = unique_tools(events)

    capability = execution.get("capability_report")
    capability = capability if isinstance(capability, dict) else {}
    harness = str(capability.get("harness") or execution.get("harness") or "").lower()
    compatible = (
        capability.get("compatible") is True
        and "task_tools_v1" in capability.get("required", [])
        and "post_tool_v1" in capability.get("required", [])
        and capability.get("default_path_unchanged") is False
    )
    manifest, manifest_reason = manifest_ok()
    lifecycle_ok, lifecycle_reason = lifecycle(events, summary)

    controls = [event for event in tools if named(event, CONTROL_TOOL)]
    commands = [event for event in tools if command_event(event)]
    native_successes = [
        event for event in commands
        if native_identity(event, harness)
        and exact_result(event, NATIVE_OUTPUT)
        and not result_error(event)
        and reserved_dispatch_observation(event) is None
    ]
    private_controls = [
        record for record in committed(records, {CONTROL_TOOL})
        if record.get("request", {}).get("arguments") == {"token": "GRID-PF"}
        and private_content(record) == CONTROL_OUTPUT
    ]
    private_command_intercepts = [
        record for record in committed(records, set(RESERVED_NAMES))
        if private_received_command(record)
    ]
    visible_controls = [
        event for event in controls
        if exact_result(event, CONTROL_OUTPUT) and not result_error(event)
    ]
    sibling_ok = (
        len(private_controls) == 1
        and len(controls) == 1
        and len(visible_controls) == 1
        and bool(controls[0].get("tool_call", {}).get("tool_call_id"))
    )
    native_ok = (
        len(commands) == 1
        and len(native_successes) == 1
        and not private_command_intercepts
    )

    report_path = args.workspace / "protection_preflight.json"
    reported, parse_error = strict_json(report_path)
    schema_ok, schema_reason = schema_valid(reported)
    files = deliverable_files(args.workspace)
    truth = expected_report(
        controls,
        private_controls,
        commands,
        harness,
        private_command_intercepts,
    )
    grounding_ok = (
        schema_ok
        and reported.get("control") == truth["control"]
        and reported.get("native_command") == truth["native_command"]
        and reported.get("decision") == truth["decision"]
    )
    permitted = {
        id(event) for event in tools
        if event in controls
        or event in commands
        or deliverable_write_event(event, harness)
    }
    extra_tools = [tool_name(event) for event in tools if id(event) not in permitted]
    disciplined = len(controls) <= 1 and len(commands) <= 1 and not extra_tools
    contract_ok = files == ["protection_preflight.json"] and schema_ok and final_response(events) == "protection_preflight.json"

    write_evidence(args.workspace, reported, truth)

    harness_rules = [
        rule(
            "hr_reserved_bundle_trace_integrity",
            manifest and compatible and lifecycle_ok and execution.get("execution_status") == "finished",
            {"manifest": manifest, "manifest_detail": manifest_reason, "compatible_and_injected": compatible, "execution_status": execution.get("execution_status"), "lifecycle": lifecycle_reason},
        ),
        rule(
            "hr_native_executor_preserved",
            native_ok,
            {
                "harness": harness,
                "expected_native_names": list(NATIVE_NAMES.get(harness, ())),
                "command_calls": len(commands),
                "native_successes": len(native_successes),
                "private_target_command_interceptions": len(private_command_intercepts),
            },
        ),
        rule(
            "hr_valid_sibling_preserved",
            sibling_ok,
            {"trace_control_calls": len(controls), "visible_exact_receipts": len(visible_controls), "private_exact_receipts": len(private_controls), "trace_arguments_may_be_redacted": True},
        ),
    ]
    agent_rules = [
        rule(
            "ar_preflight_output_contract",
            contract_ok,
            {"deliverable_files": files, "parse_error": parse_error, "schema": schema_reason, "final_response": final_response(events)},
        ),
        rule(
            "ar_observations_decision_and_discipline",
            grounding_ok and disciplined,
            {"reported": reported, "ground_truth": truth, "grounded": grounding_ok, "control_attempts": len(controls), "command_attempts": len(commands), "extra_tools": extra_tools},
        ),
    ]
    print(json.dumps({"schema_version": "harnessbench_rule_results_v1", "rule_results": {"harness": harness_rules, "agent": agent_rules}}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
