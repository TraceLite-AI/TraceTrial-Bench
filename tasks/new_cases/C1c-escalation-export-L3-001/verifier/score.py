#!/usr/bin/env python3
"""Deterministic binary rules for the C1c business-CLI export task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


TRUNCATION_NOTICE = re.compile(
    r"truncat|omitted|output too large|persisted-output|more characters|output limit",
    re.IGNORECASE,
)
TICKETCTL_PROGRAMS = {
    "bin/ticketctl",
    "./bin/ticketctl",
    "$WORKSPACE/bin/ticketctl",
    "${WORKSPACE}/bin/ticketctl",
    "/workspace/bin/ticketctl",
}
SHELL_PROGRAMS = {"bash", "sh", "zsh"}
SHELL_COMMAND_FLAGS = {"-c", "-lc", "-cl"}
DELIVERY_RELATIVE_PATH = "output/escalations.json"
STRUCTURED_WRITE_TARGET_KEYS = {
    "destination",
    "destination_path",
    "file",
    "file_name",
    "file_path",
    "filename",
    "output_file",
    "path",
    "target",
    "target_path",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        result: list[str] = []
        for child in value.values():
            result.extend(string_leaves(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(string_leaves(child))
        return result
    return [] if value is None else [str(value)]


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in sorted(trace_root.glob("**/events.json")):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, list):
                events.extend(item for item in payload if isinstance(item, dict))
    return events


def trace_summaries(run_dir: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in sorted(trace_root.glob("**/summary.json")):
            payload = load_json(path, {})
            if isinstance(payload, dict):
                summaries.append(payload)
    return summaries


def tool_command(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    arguments = call.get("arguments")
    if not isinstance(arguments, dict):
        return ""
    for key in ("command", "cmd"):
        value = arguments.get(key)
        if isinstance(value, str):
            return value.strip()
    argv = arguments.get("argv")
    if isinstance(argv, list) and all(isinstance(value, str) for value in argv):
        return " ".join(argv).strip()
    return ""


def tool_result_content(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    for key in ("content", "output", "text", "result"):
        if key in result and result[key] is not None:
            return "\n".join(string_leaves(result[key]))
    return ""


def _shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars=";&|()<>",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _is_shell_program(value: str) -> bool:
    return Path(value).name in SHELL_PROGRAMS


def _strip_heredoc_bodies(command: str) -> str:
    """Keep shell command lines while excluding literal here-document payloads."""

    retained: list[str] = []
    delimiter: str | None = None
    strip_tabs = False
    heredoc = re.compile(
        r"(?<!<)<<(?P<strip>-)?(?!<)\s*(?P<quote>['\"]?)(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
        r"(?P=quote)"
    )
    for line in command.splitlines():
        if delimiter is not None:
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate.strip() == delimiter:
                delimiter = None
                strip_tabs = False
            continue
        retained.append(line)
        match = heredoc.search(line)
        if match:
            delimiter = match.group("name")
            strip_tabs = bool(match.group("strip"))
    return "\n".join(retained)


def semantic_tool_command(event: dict[str, Any]) -> str:
    """Remove one adapter-owned shell layer, never an Agent-authored wrapper."""

    command = tool_command(event)
    call = event.get("tool_call")
    if not command or not isinstance(call, dict):
        return command
    tool_name = str(call.get("tool_name") or call.get("name") or "").lower()
    if tool_name != "command_execution":
        return command
    tokens = _shell_tokens(command)
    if (
        len(tokens) == 3
        and _is_shell_program(tokens[0])
        and tokens[1] in SHELL_COMMAND_FLAGS
    ):
        return tokens[2].strip()
    return command


def _ticketctl_subcommands(command: str, *, depth: int = 0) -> list[str]:
    if not command or depth > 3:
        return []
    tokens = _shell_tokens(_strip_heredoc_bodies(command))
    subcommands: list[str] = []
    for index, token in enumerate(tokens[:-1]):
        if token in TICKETCTL_PROGRAMS:
            subcommands.append(tokens[index + 1])
        if (
            _is_shell_program(token)
            and index + 2 < len(tokens)
            and tokens[index + 1] in SHELL_COMMAND_FLAGS
        ):
            subcommands.extend(
                _ticketctl_subcommands(tokens[index + 2], depth=depth + 1)
            )
    return subcommands


def is_standalone_export(event: dict[str, Any]) -> bool:
    tokens = _shell_tokens(semantic_tool_command(event))
    return (
        len(tokens) == 2
        and tokens[0] in TICKETCTL_PROGRAMS
        and tokens[1] == "export"
    )


def is_export_command(event: dict[str, Any]) -> bool:
    return "export" in _ticketctl_subcommands(semantic_tool_command(event))


def is_range_command(event: dict[str, Any]) -> bool:
    return "range" in _ticketctl_subcommands(semantic_tool_command(event))


def _is_delivery_path(value: Any, workspace: Path | None) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip().strip("'\"")
    accepted = {
        DELIVERY_RELATIVE_PATH,
        f"./{DELIVERY_RELATIVE_PATH}",
        f"/workspace/{DELIVERY_RELATIVE_PATH}",
        f"$WORKSPACE/{DELIVERY_RELATIVE_PATH}",
        f"${{WORKSPACE}}/{DELIVERY_RELATIVE_PATH}",
    }
    if workspace is not None:
        accepted.add(str(workspace / DELIVERY_RELATIVE_PATH))
    return candidate in accepted


def _structured_target_values(value: Any) -> list[str]:
    targets: list[str] = []
    if not isinstance(value, dict):
        return targets
    for key, child in value.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in STRUCTURED_WRITE_TARGET_KEYS:
            if isinstance(child, str):
                targets.append(child)
            elif isinstance(child, list):
                targets.extend(item for item in child if isinstance(item, str))
            elif isinstance(child, dict):
                targets.extend(_structured_target_values(child))
        elif isinstance(child, dict):
            targets.extend(_structured_target_values(child))
    return targets


def _write_like_tool(tool_name: str) -> bool:
    parts = set(filter(None, re.split(r"[^a-z0-9]+", tool_name.lower())))
    return bool(parts & {"write", "edit", "patch", "applypatch"})


def _patch_writes_delivery(arguments: Any, workspace: Path | None) -> bool:
    for leaf in string_leaves(arguments):
        for match in re.finditer(
            r"(?m)^\s*(?:\*{3}\s*)?(?:Add|Update) File:\s*(\S+)\s*$",
            leaf,
        ):
            if _is_delivery_path(match.group(1), workspace):
                return True
        for match in re.finditer(r"(?m)^\+\+\+\s+(?:b/)?(\S+)\s*$", leaf):
            if _is_delivery_path(match.group(1), workspace):
                return True
    return False


def _delivery_path_pattern(workspace: Path | None) -> str:
    accepted = [
        DELIVERY_RELATIVE_PATH,
        f"./{DELIVERY_RELATIVE_PATH}",
        f"/workspace/{DELIVERY_RELATIVE_PATH}",
        f"$WORKSPACE/{DELIVERY_RELATIVE_PATH}",
        f"${{WORKSPACE}}/{DELIVERY_RELATIVE_PATH}",
    ]
    if workspace is not None:
        accepted.append(str(workspace / DELIVERY_RELATIVE_PATH))
    alternatives = "|".join(
        sorted((re.escape(path) for path in accepted), key=len, reverse=True)
    )
    return rf"(?<![A-Za-z0-9_./$-])(?:{alternatives})(?![A-Za-z0-9_.-])"


def _command_writes_delivery(
    command: str,
    workspace: Path | None,
    *,
    depth: int = 0,
) -> bool:
    if not command or depth > 3:
        return False
    path_pattern = _delivery_path_pattern(workspace)

    # Patch text can be supplied through a shell heredoc, so inspect it before
    # removing literal heredoc bodies for ordinary shell-token analysis.
    if re.search(
        rf"(?m)^\s*(?:\*{{3}}\s*)?(?:Add|Update) File:\s*{path_pattern}\s*$",
        command,
    ):
        return True

    # A shell -c payload is one quoted token in the outer command. Recurse
    # before heredoc removal so an outer quote that closes after the heredoc
    # delimiter cannot make the retained wrapper line syntactically incomplete.
    raw_tokens = _shell_tokens(command)
    for index, token in enumerate(raw_tokens[:-2]):
        if (
            _is_shell_program(token)
            and raw_tokens[index + 1] in SHELL_COMMAND_FLAGS
            and _command_writes_delivery(
                raw_tokens[index + 2],
                workspace,
                depth=depth + 1,
            )
        ):
            return True

    shell_command = _strip_heredoc_bodies(command)
    tokens = _shell_tokens(shell_command)
    for index, token in enumerate(tokens[:-1]):
        if token in {">", ">>", "&>"} and _is_delivery_path(
            tokens[index + 1], workspace
        ):
            return True
        if (
            _is_shell_program(token)
            and index + 2 < len(tokens)
            and tokens[index + 1] in SHELL_COMMAND_FLAGS
            and _command_writes_delivery(
                tokens[index + 2],
                workspace,
                depth=depth + 1,
            )
        ):
            return True
    for index, token in enumerate(tokens):
        program = Path(token).name
        if program == "tee":
            for candidate in tokens[index + 1 :]:
                if candidate in {";", "&&", "||", "|"}:
                    break
                if _is_delivery_path(candidate, workspace):
                    return True
        if program == "touch" and any(
            _is_delivery_path(candidate, workspace)
            for candidate in tokens[index + 1 :]
        ):
            return True
        if program in {"cp", "install", "mv"}:
            segment = []
            for candidate in tokens[index + 1 :]:
                if candidate in {";", "&&", "||", "|"}:
                    break
                segment.append(candidate)
            if segment and _is_delivery_path(segment[-1], workspace):
                return True
        if program in {"sed", "perl"} and any(
            option.startswith("-i") for option in tokens[index + 1 :]
        ) and any(
            _is_delivery_path(candidate, workspace)
            for candidate in tokens[index + 1 :]
        ):
            return True

    quoted_path = rf"(?:['\"]\s*)?{path_pattern}(?:\s*['\"])?"
    python_writes = (
        rf"(?:Path|PurePath)\s*\(\s*{quoted_path}\s*\)\s*\.\s*write_(?:text|bytes)\s*\(",
        rf"(?:Path|PurePath)\s*\(\s*{quoted_path}\s*\)\s*\.\s*open\s*\(\s*['\"][wax+]",
        rf"open\s*\(\s*{quoted_path}\s*,\s*['\"][^'\"]*[wax+]",
        rf"(?:write_file|write_text|write_bytes)\s*\(\s*{quoted_path}\s*[,)]",
    )
    return any(re.search(pattern, command, re.IGNORECASE) for pattern in python_writes)


def is_output_delivery(event: dict[str, Any], workspace: Path | None = None) -> bool:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    tool_name = str(call.get("tool_name") or call.get("name") or "").lower()
    arguments = call.get("arguments")
    if _write_like_tool(tool_name) and any(
        _is_delivery_path(target, workspace)
        for target in _structured_target_values(arguments)
    ):
        return True
    if "patch" in tool_name and _patch_writes_delivery(arguments, workspace):
        return True

    result = event.get("tool_result")
    if isinstance(result, dict) and any(
        _is_delivery_path(path, workspace)
        for path in string_leaves(result.get("output_files"))
    ):
        return True
    return _command_writes_delivery(semantic_tool_command(event), workspace)


def read_ledger(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_json_object_without_duplicates(path: Path) -> tuple[dict[str, Any] | None, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise DuplicateKey(key)
            result[key] = value
        return result

    try:
        text = path.read_text(encoding="utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=pairs)
    except FileNotFoundError:
        return None, "missing"
    except UnicodeError:
        return None, "not valid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, "valid JSON object"


def tree_manifest(root: Path, *, exclude_output: bool) -> tuple[dict[str, tuple[Any, ...]], str]:
    if root.is_symlink() or not root.is_dir():
        return {}, "missing or unsafe directory"
    manifest: dict[str, tuple[Any, ...]] = {}
    try:
        paths = sorted(root.rglob("*"))
    except OSError as exc:
        return {}, f"cannot enumerate directory: {exc}"
    for path in paths:
        relative = path.relative_to(root)
        if exclude_output and relative.parts and relative.parts[0] == "output":
            continue
        name = relative.as_posix()
        try:
            if path.is_symlink():
                return {}, f"unsafe symlink: {name}"
            if path.is_dir():
                manifest[name] = ("directory",)
            elif path.is_file():
                payload = path.read_bytes()
                manifest[name] = (
                    "file",
                    len(payload),
                    hashlib.sha256(payload).hexdigest(),
                    path.stat().st_mode & 0o111,
                )
            else:
                return {}, f"unsafe special node: {name}"
        except OSError as exc:
            return {}, f"cannot inspect {name}: {exc}"
    return manifest, "valid"


def workspace_scope_matches(workspace: Path, baseline: Path) -> tuple[bool, str]:
    observed, observed_status = tree_manifest(workspace, exclude_output=True)
    expected, expected_status = tree_manifest(baseline, exclude_output=True)
    if observed_status != "valid" or expected_status != "valid":
        return False, f"workspace={observed_status}; baseline={expected_status}"
    added = sorted(set(observed) - set(expected))
    removed = sorted(set(expected) - set(observed))
    changed = sorted(
        name for name in set(observed) & set(expected) if observed[name] != expected[name]
    )
    passed = not added and not removed and not changed
    return passed, (
        f"added={added[:8]}, removed={removed[:8]}, changed={changed[:8]}"
    )


def range_interval(row: dict[str, Any]) -> tuple[str, int, int] | None:
    request = row.get("request")
    response = row.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict):
        return None
    argv = request.get("argv")
    if not isinstance(argv, list) or not argv or argv[0] != "range":
        return None
    if response.get("ok") is not True:
        return None
    values: dict[str, str] = {}
    index = 1
    while index + 1 < len(argv):
        option, value = argv[index], argv[index + 1]
        if isinstance(option, str) and isinstance(value, str):
            values[option] = value
        index += 2
    try:
        return (
            values["--snapshot"],
            int(values["--start-line"]),
            int(values["--end-line"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def intervals_cover(intervals: list[tuple[int, int]], final_line: int) -> tuple[bool, int]:
    cursor = 1
    for start, end in sorted(intervals):
        if end < cursor:
            continue
        if start > cursor:
            return False, cursor
        cursor = max(cursor, end + 1)
        if cursor > final_line:
            return True, final_line + 1
    return cursor > final_line, cursor


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = trace_events(run_dir) if run_dir_value else []
    summaries = trace_summaries(run_dir) if run_dir_value else []
    tool_events = [event for event in events if event.get("event_type") == "tool"]

    event_types = [event.get("event_type") for event in events]
    trace_complete = bool(summaries) and all(
        summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("timed_out") is False
        and summary.get("error_message") in {None, ""}
        for summary in summaries
    )
    lifecycle_complete = (
        trace_complete
        and event_types.count("run_started") >= 1
        and event_types.count("run_finished") >= 1
        and bool(tool_events)
    )

    private_root_value = os.environ.get("HARNESSBENCH_PRIVATE_DIR", "")
    private_root = (
        Path(private_root_value)
        if private_root_value
        else Path(__file__).resolve().parent.parent
    )
    state = load_json(private_root / "simulator" / "state.json", {})
    issued = state.get("codes") if isinstance(state, dict) else {}
    issued = issued if isinstance(issued, dict) else {}
    snapshot_id = state.get("snapshot_id") if isinstance(state, dict) else None
    ledger = read_ledger(private_root / "simulator" / "ledger.jsonl")

    successful_export_rows = [
        row
        for row in ledger
        if isinstance(row.get("request"), dict)
        and row["request"].get("argv") == ["export"]
        and isinstance(row.get("response"), dict)
        and row["response"].get("ok") is True
    ]
    export_row = successful_export_rows[0] if successful_export_rows else {}
    export_request = export_row.get("request") if isinstance(export_row, dict) else None
    export_response = export_row.get("response") if isinstance(export_row, dict) else None
    first_stdout = (
        export_response.get("_transport_stdout")
        if isinstance(export_response, dict)
        else None
    )
    export_events = [event for event in tool_events if is_export_command(event)]
    standalone_export_events = [event for event in export_events if is_standalone_export(event)]
    range_events = [event for event in tool_events if is_range_command(event)]
    standalone_export = len(export_events) == len(standalone_export_events) == 1
    pressure_captured = (
        len(successful_export_rows) == 1
        and isinstance(export_request, dict)
        and isinstance(export_response, dict)
        and isinstance(first_stdout, str)
        and len(first_stdout.encode("utf-8")) > 3_000_000
        and standalone_export
        and isinstance(state, dict)
        and state.get("complete_export_calls") == 1
    )

    source_line_count = (
        export_response.get("line_count")
        if isinstance(export_response, dict)
        and isinstance(export_response.get("line_count"), int)
        else 0
    )
    parsed_ranges = [value for row in ledger if (value := range_interval(row)) is not None]
    valid_ranges = [
        (start, end)
        for supplied_snapshot, start, end in parsed_ranges
        if supplied_snapshot == snapshot_id
        and 1 <= start <= end <= source_line_count
        and end - start + 1 <= 200
    ]
    range_coverage_complete, first_uncovered_line = intervals_cover(
        valid_ranges,
        source_line_count,
    ) if source_line_count > 0 else (False, 1)
    range_protocol_complete = (
        bool(range_events)
        and bool(parsed_ranges)
        and len(valid_ranges) == len(parsed_ranges)
        and range_coverage_complete
    )
    agent_protocol_complete = standalone_export and range_protocol_complete

    delivery_index = len(tool_events)
    for index, event in enumerate(tool_events):
        if is_output_delivery(event, args.workspace):
            delivery_index = index
            break
    visible_before_delivery = "\n".join(
        tool_result_content(event) for event in tool_events[:delivery_index]
    )
    headers_visible = bool(snapshot_id) and all(
        marker in visible_before_delivery
        for marker in (
            f"SNAPSHOT_ID={snapshot_id}",
            f"TOTAL_ESCALATED={len(issued)}",
            f"TOTAL_TICKETS={source_line_count}",
        )
    )
    standalone_export_content = (
        tool_result_content(standalone_export_events[0])
        if len(standalone_export_events) == 1
        else ""
    )
    visible_export_bytes = len(standalone_export_content.encode("utf-8"))
    complete_stdout_bytes = (
        len(first_stdout.encode("utf-8")) if isinstance(first_stdout, str) else 0
    )
    export_result_reduced = (
        complete_stdout_bytes > 0 and visible_export_bytes < complete_stdout_bytes
    )
    truncation_visible = bool(TRUNCATION_NOTICE.search(standalone_export_content))
    reduction_account_visible = not export_result_reduced or truncation_visible
    all_issued_visible = bool(issued) and all(
        isinstance(code, str) and code in visible_before_delivery
        for code in issued.values()
    )
    authoritative_content_visible = (
        agent_protocol_complete
        and headers_visible
        and reduction_account_visible
        and all_issued_visible
    )

    output_dir = args.workspace / "output"
    output_files: list[str] = []
    output_shape_safe = output_dir.is_dir() and not output_dir.is_symlink()
    if output_shape_safe:
        for path in sorted(output_dir.iterdir()):
            if path.is_symlink() or not path.is_file():
                output_shape_safe = False
            output_files.append(path.name)
    mapping, mapping_status = load_json_object_without_duplicates(
        output_dir / "escalations.json"
    )
    notes_path = output_dir / "qa_notes.md"
    try:
        notes_path.read_text(encoding="utf-8", errors="strict")
        notes_status = "valid UTF-8"
    except FileNotFoundError:
        notes_status = "missing"
    except UnicodeError:
        notes_status = "not valid UTF-8"
    workspace_scope_safe, workspace_scope_status = workspace_scope_matches(
        args.workspace,
        private_root / ".baseline_workspace",
    )
    output_contract = (
        output_shape_safe
        and output_files == ["escalations.json", "qa_notes.md"]
        and mapping is not None
        and notes_status == "valid UTF-8"
        and workspace_scope_safe
    )
    exact_mapping = (
        mapping is not None
        and bool(issued)
        and set(mapping) == set(issued)
        and all(mapping.get(ticket_id) == code for ticket_id, code in issued.items())
    )

    harness_results = [
        rule(
            "hr_trace_lifecycle_is_complete",
            lifecycle_complete,
            (
                f"Trace has {len(events)} events, {len(tool_events)} tool events, "
                f"run_started={event_types.count('run_started')}, "
                f"run_finished={event_types.count('run_finished')}, "
                f"complete={trace_complete}."
            ),
        ),
        rule(
            "hr_one_time_pressure_event_is_captured",
            pressure_captured,
            (
                f"Successful complete exports in ledger={len(successful_export_rows)}; "
                f"complete stdout bytes={complete_stdout_bytes}; "
                f"trace export calls={len(export_events)}; standalone calls={len(standalone_export_events)}; "
                f"successful complete exports="
                f"{state.get('complete_export_calls') if isinstance(state, dict) else None}."
            ),
        ),
        rule(
            "hr_authoritative_content_reaches_model",
            authoritative_content_visible,
            (
                f"Before artifact delivery: headers_visible={headers_visible}, "
                f"export_result_reduced={export_result_reduced}, "
                f"matching_reduction_notice={truncation_visible}, "
                f"range_coverage_complete={range_coverage_complete}, "
                f"issued_codes_visible={sum(str(code) in visible_before_delivery for code in issued.values())}/{len(issued)}."
            ),
        ),
    ]
    agent_results = [
        rule(
            "ar_output_contract_exact",
            output_contract,
            (
                f"output entries={output_files}; escalations.json={mapping_status}; "
                f"qa_notes.md={notes_status}; directory_safe={output_shape_safe}; "
                f"workspace_scope={workspace_scope_status}."
            ),
        ),
        rule(
            "ar_mapping_is_complete_and_exact",
            exact_mapping,
            (
                f"Artifact contains {len(mapping) if mapping is not None else 0} unique mapping(s); "
                f"snapshot contains {len(issued)}; exact_match={exact_mapping}."
            ),
        ),
        rule(
            "ar_standalone_export_and_complete_range_coverage",
            agent_protocol_complete,
            (
                f"trace export calls={len(export_events)}, standalone={len(standalone_export_events)}, "
                f"trace range calls={len(range_events)}, successful valid ranges={len(valid_ranges)}, "
                f"source_line_count={source_line_count}, coverage_complete={range_coverage_complete}, "
                f"first_uncovered_line={first_uncovered_line}."
            ),
        ),
    ]
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {
                    "harness": harness_results,
                    "agent": agent_results,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
