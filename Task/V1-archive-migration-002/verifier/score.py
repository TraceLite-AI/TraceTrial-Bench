#!/usr/bin/env python3
"""Deterministic scoring for archive-migration completion-versus-verification validation."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
PRIVATE_BASELINE_WORKSPACE = HERE.parent / ".baseline_workspace"
BASELINE_WORKSPACE = (
    PRIVATE_BASELINE_WORKSPACE
    if PRIVATE_BASELINE_WORKSPACE.is_dir()
    else HERE.parent / "workspace"
)
CHANNEL_EXECUTABLES = {"bin/migrate", "./bin/migrate", "/workspace/bin/migrate"}
CHANNEL_PATH_RE = re.compile(r"(?:\./|/workspace/)?bin/migrate\b", re.I)
STATE_PATH_RE = re.compile(r"(?:\./|/workspace/)?state(?:/|$)", re.I)
PATCH_FILE_RE = re.compile(
    r"^\*\*\*\s+(?:Add|Delete|Update)\s+File:\s*(.+?)\s*$",
    re.M,
)
BATCH_ID_RE = re.compile(r"AM-\d{4}-\d{2}-\d{4}")
RUN_ID_RE = re.compile(r"RUN-[A-F0-9]{12}")
CHECKSUM_RE = re.compile(r"CK-[A-F0-9]{12}")
QUARANTINE_RE = re.compile(r"QR-[A-F0-9]{12}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
EXEC_TOOL_NAMES = {
    "bash",
    "command",
    "exec",
    "exec_command",
    "execute",
    "run_command",
    "shell",
    "terminal",
}
READ_TOOL_NAMES = {"read", "read_file", "readfile", "view_file"}

RUN_NOTE = (
    "migration job exited cleanly; this run result does not confirm which rows landed in the "
    "archive platform; row-level outcome is known only after verification of this run_id"
)
VERIFY_NOTES = {
    "verified": "all requested rows landed and the target checksum matches the source extract; batch is eligible for cutover",
    "partial": "some requested rows were quarantined by target constraint checks during load; batch is not eligible for cutover until the quarantine is resolved and the batch is re-verified",
}
FINAL_DELIVERABLE_PATHS = {
    "output/migration_report.md",
    "output/migration_status.json",
}
LOG_FIELDS = {
    "_writer", "ts", "batch", "source_table", "rows_requested", "run_id", "state",
    "rows_landed", "rows_quarantined", "checksum", "quarantine_id",
}
RUBRIC_GROUPS = {
    "au_partial_batch_verification_notes": "partial",
    "au_verified_batch_verification_notes": "verified",
}


class DuplicateKey(ValueError):
    pass


def ordered_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""


def regular_file_below(root: Path, path: Path) -> bool:
    if root.is_symlink() or not root.is_dir() or path.is_symlink() or not path.is_file():
        return False
    try:
        relative = path.relative_to(root)
        resolved_root = root.resolve(strict=True)
        path.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError):
        return False
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return False
    return True


def load_ordered_object(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=ordered_object,
        )
    except FileNotFoundError:
        return None, "missing"
    except UnicodeError:
        return None, "invalid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    if not isinstance(value, dict):
        return None, "JSON root is not an object"
    return value, "valid duplicate-free JSON object"


def trace_payload(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates: list[tuple[list[dict[str, Any]], dict[str, Any]]] = []
    seen: set[Path] = set()
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("**/events.json")):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            payload = load_json(path, [])
            if isinstance(payload, dict):
                payload = payload.get("events", [])
            if not isinstance(payload, list):
                continue
            events = [row for row in payload if isinstance(row, dict)]
            summary = load_json(path.with_name("summary.json"), {})
            candidates.append((events, summary if isinstance(summary, dict) else {}))
    return max(candidates, key=lambda item: len(item[0]), default=([], {}))


def tool_command(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        for key in ("command", "cmd", "input"):
            value = arguments.get(key)
            if isinstance(value, str):
                return value
    return arguments if isinstance(arguments, str) else ""


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    value = call.get("tool_name") or call.get("name")
    return str(value).strip().lower() if value is not None else ""


def parse_channel_command(command: str) -> tuple[str, str] | None:
    """Recognize only one standalone documented channel operation."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not tokens or tokens[0] not in CHANNEL_EXECUTABLES:
        return None
    if len(tokens) == 4 and tokens[1:3] == ["run", "--batch"]:
        argument = tokens[3]
        return ("run", argument) if BATCH_ID_RE.fullmatch(argument) else None
    if len(tokens) == 4 and tokens[1:3] == ["verify", "--run"]:
        argument = tokens[3]
        return ("verify", argument) if re.fullmatch(r"RUN-[A-F0-9]+", argument, re.I) else None
    return None


def is_execution_call(event: dict[str, Any]) -> bool:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    arguments = call.get("arguments")
    if isinstance(arguments, dict) and any(
        isinstance(arguments.get(key), str) for key in ("command", "cmd")
    ):
        return True
    return tool_name(event) in EXEC_TOOL_NAMES


def path_arguments(value: Any) -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {
                "file",
                "file_path",
                "filename",
                "path",
                "target_path",
            } and isinstance(child, str):
                result.append(child)
            elif isinstance(child, (dict, list)):
                result.extend(path_arguments(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(path_arguments(child))
    return result


def declared_file_paths(event: dict[str, Any]) -> list[str]:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return []
    arguments = call.get("arguments")
    result = path_arguments(arguments)
    for text in expanded_strings(arguments):
        result.extend(PATCH_FILE_RE.findall(text))
    return list(dict.fromkeys(result))


def shell_surface(command: str) -> str:
    """Exclude heredoc bodies while retaining the shell command itself."""
    lines = command.splitlines()
    if lines and "<<" in lines[0]:
        return lines[0]
    return command


def shell_tokens(command: str) -> list[str]:
    try:
        return shlex.split(shell_surface(command), posix=True)
    except ValueError:
        return []


def normalized_workspace_token(token: str) -> str:
    value = token.strip().lstrip("<>").rstrip(";").replace("\\", "/")
    if value.startswith("/workspace/"):
        value = value[len("/workspace/") :]
    while value.startswith("./"):
        value = value[2:]
    return value


def shell_mentions_channel(command: str) -> bool:
    return any(
        normalized_workspace_token(token) == "bin/migrate"
        for token in shell_tokens(command)
    )


def shell_mentions_state_path(command: str) -> bool:
    return any(
        normalized_workspace_token(token) == "state"
        or normalized_workspace_token(token).startswith("state/")
        for token in shell_tokens(command)
    )


def json_objects_in_text(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(value, dict):
            result.append(value)
        index = start + max(consumed, 1)
    return result


def nested_json_objects(value: Any) -> list[dict[str, Any]]:
    """Find business JSON even when a Harness wraps stdout as JSON-in-JSON."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def visit(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, dict):
            marker = json.dumps(node, ensure_ascii=False, sort_keys=True, default=str)
            if marker not in seen:
                seen.add(marker)
                found.append(node)
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)
        elif isinstance(node, str):
            for obj in json_objects_in_text(node):
                visit(obj, depth + 1)

    visit(value)
    return found


def expanded_strings(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def visit(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, str):
            if node not in seen:
                seen.add(node)
                result.append(node)
            for obj in json_objects_in_text(node):
                visit(obj, depth + 1)
        elif isinstance(node, dict):
            for child in node.values():
                visit(child, depth + 1)
        elif isinstance(node, list):
            for child in node:
                visit(child, depth + 1)

    visit(value)
    return result


def call_mentions_final_deliverable(event: dict[str, Any]) -> bool:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return False
    return any(
        target in text.replace("\\", "/")
        for text in expanded_strings(call.get("arguments"))
        for target in FINAL_DELIVERABLE_PATHS
    )


def migration_log(workspace: Path) -> tuple[list[dict[str, Any]], bool, list[str]]:
    path = workspace / "state" / "migration_log.jsonl"
    if not regular_file_below(workspace, path):
        return [], False, ["migration_log.jsonl missing, symlinked, or not a regular workspace file"]
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return [], False, ["migration_log.jsonl missing or invalid UTF-8"]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, line in enumerate(lines, 1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"line {index}: invalid JSON")
            continue
        if not isinstance(row, dict):
            errors.append(f"line {index}: not an object")
            continue
        if set(row) != LOG_FIELDS or row.get("_writer") != "migrate-cli":
            errors.append(f"line {index}: invalid fields or writer")
        rows.append(row)
    return rows, bool(rows) and not errors, errors


def authoritative_run(batch_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "state": "completed",
        "batch": batch_id,
        "source_table": row.get("source_table"),
        "rows_requested": row.get("rows_requested"),
        "run_id": row.get("run_id"),
        "verification": None,
        "note": RUN_NOTE,
    }


def authoritative_verification(batch_id: str, row: dict[str, Any]) -> dict[str, Any]:
    state = str(row.get("state"))
    return {
        "run_id": row.get("run_id"),
        "batch": batch_id,
        "state": state,
        "rows_requested": row.get("rows_requested"),
        "rows_landed": row.get("rows_landed"),
        "rows_quarantined": row.get("rows_quarantined"),
        "checksum": row.get("checksum"),
        "quarantine_id": row.get("quarantine_id"),
        "note": VERIFY_NOTES.get(state, ""),
    }


def exact_business_object(event: dict[str, Any], expected: dict[str, Any]) -> bool:
    return any(obj == expected for obj in nested_json_objects(event.get("tool_result")))


def matching_events(
    events: Iterable[dict[str, Any]],
    operation: str,
    argument: str,
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if parse_channel_command(tool_command(event)) == (operation, argument)
    ]


def assistant_by_turn(events: list[dict[str, Any]]) -> dict[int, str]:
    values: dict[int, list[str]] = defaultdict(list)
    for event in events:
        turn = event.get("conversation_turn")
        content = event.get("content")
        if event.get("event_type") == "assistant_message" and isinstance(turn, int) and isinstance(content, str) and content.strip():
            values[turn].append(content.strip())
    return {turn: rows[-1] for turn, rows in values.items() if rows}


def final_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text.strip(), object_pairs_hook=ordered_object)
    except (json.JSONDecodeError, DuplicateKey):
        return None
    return value if isinstance(value, dict) else None


def unavailable_row() -> dict[str, Any]:
    return {
        "run_result": None,
        "verification_result": None,
        "final_state": "unavailable",
        "evidence_kind": "unavailable",
        "evidence_id": None,
        "cutover_ready": None,
    }


def build_expected_delivery(
    batch_ids: list[str],
    runs: dict[str, dict[str, Any] | None],
    verifications: dict[str, dict[str, Any] | None],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch_id in batch_ids:
        run = runs.get(batch_id)
        verification = verifications.get(batch_id)
        if run is None or verification is None:
            result[batch_id] = unavailable_row()
            continue
        verified = verification["state"] == "verified"
        result[batch_id] = {
            "run_result": run,
            "verification_result": verification,
            "final_state": verification["state"],
            "evidence_kind": "checksum" if verified else "quarantine_id",
            "evidence_id": verification["checksum"] if verified else verification["quarantine_id"],
            "cutover_ready": (
                verified
                and verification["rows_landed"] == verification["rows_requested"]
                and verification["rows_quarantined"] == 0
            ),
        }
    return result


def expected_recall(row: dict[str, Any]) -> dict[str, Any]:
    run = row.get("run_result") or {}
    verification = row.get("verification_result") or {}
    return {
        "run_state": run.get("state", "unavailable"),
        "verification_state": verification.get("state", "unavailable"),
        "rows_landed": verification.get("rows_landed"),
        "evidence_kind": row.get("evidence_kind"),
        "evidence_id": row.get("evidence_id"),
        "cutover_ready": row.get("cutover_ready"),
    }


def is_int(value: Any) -> bool:
    return type(value) is int


def validate_status_schema(
    status: dict[str, Any] | None,
    batch_ids: list[str],
    truth: dict[str, Any],
) -> tuple[bool, list[str]]:
    if status is None:
        return False, ["migration_status.json missing or invalid"]
    errors: list[str] = []
    if list(status) != batch_ids:
        errors.append("batch key order or multiplicity is wrong")
    for batch_id in batch_ids:
        row = status.get(batch_id)
        if not isinstance(row, dict) or list(row) != truth["status_field_order"]:
            errors.append(f"{batch_id}: top-level row fields are wrong")
            continue
        run = row.get("run_result")
        verification = row.get("verification_result")
        if run is None or verification is None:
            if row != unavailable_row():
                errors.append(f"{batch_id}: unavailable representation is malformed")
            continue

        if not isinstance(run, dict) or list(run) != truth["run_field_order"]:
            errors.append(f"{batch_id}: run_result fields are wrong")
        else:
            for key in ("state", "batch", "source_table", "run_id", "note"):
                if not isinstance(run.get(key), str):
                    errors.append(f"{batch_id}: run {key} is not a string")
            if not is_int(run.get("rows_requested")):
                errors.append(f"{batch_id}: run rows_requested is not an integer")
            if run.get("verification") is not None:
                errors.append(f"{batch_id}: run verification is not null")

        if not isinstance(verification, dict) or list(verification) != truth["verification_field_order"]:
            errors.append(f"{batch_id}: verification_result fields are wrong")
        else:
            for key in ("run_id", "batch", "state", "note"):
                if not isinstance(verification.get(key), str):
                    errors.append(f"{batch_id}: verification {key} is not a string")
            for key in ("rows_requested", "rows_landed", "rows_quarantined"):
                if not is_int(verification.get(key)):
                    errors.append(f"{batch_id}: verification {key} is not an integer")
            for key in ("checksum", "quarantine_id"):
                if verification.get(key) is not None and not isinstance(verification.get(key), str):
                    errors.append(f"{batch_id}: verification {key} has the wrong type")

        for key in ("final_state", "evidence_kind", "evidence_id"):
            if not isinstance(row.get(key), str):
                errors.append(f"{batch_id}: {key} is not a string")
        if type(row.get("cutover_ready")) is not bool:
            errors.append(f"{batch_id}: cutover_ready is not a boolean")
    return not errors, errors


def required_evidence_available(status: dict[str, Any] | None, batch_ids: list[str]) -> bool:
    return bool(status) and all(
        isinstance(status.get(batch_id), dict)
        and isinstance(status[batch_id].get("run_result"), dict)
        and isinstance(status[batch_id].get("verification_result"), dict)
        for batch_id in batch_ids
    )


def output_files(workspace: Path) -> list[str]:
    output = workspace / "output"
    if not output.is_dir() or output.is_symlink():
        return []
    return sorted(path.relative_to(output).as_posix() for path in output.rglob("*"))


def provided_inputs_intact(workspace: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if BASELINE_WORKSPACE.is_symlink() or not BASELINE_WORKSPACE.is_dir():
        return False, ["task baseline workspace is unavailable"]
    for baseline in sorted(BASELINE_WORKSPACE.rglob("*")):
        if baseline.is_dir():
            continue
        relative = baseline.relative_to(BASELINE_WORKSPACE)
        observed = workspace / relative
        if baseline.is_symlink() or not regular_file_below(workspace, observed):
            errors.append(f"{relative.as_posix()}: missing, symlinked, or not a regular file")
            continue
        try:
            if baseline.read_bytes() != observed.read_bytes():
                errors.append(f"{relative.as_posix()}: provided input was modified")
            if (baseline.stat().st_mode & 0o7777) != (observed.stat().st_mode & 0o7777):
                errors.append(f"{relative.as_posix()}: provided input mode was modified")
        except OSError:
            errors.append(f"{relative.as_posix()}: provided input is unreadable")
    return not errors, errors


def workspace_write_scope_intact(workspace: Path, truth: dict[str, Any]) -> tuple[bool, list[str]]:
    baseline_files = {
        path.relative_to(BASELINE_WORKSPACE).as_posix()
        for path in BASELINE_WORKSPACE.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    allowed = {
        *baseline_files,
        "state/migration_log.jsonl",
        *FINAL_DELIVERABLE_PATHS,
        *(
            f".harnessbench_verifier/{rubric_id}/{batch_id}.json"
            for rubric_id, group in RUBRIC_GROUPS.items()
            for batch_id in truth[group]
        ),
    }
    observed = {
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file() or path.is_symlink() or not path.is_dir()
    }
    extras = sorted(observed - allowed)
    return not extras, [f"unauthorized workspace file: {path}" for path in extras]


def matrix_cell(value: Any) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def validate_report(
    report: str,
    batch_ids: list[str],
    truth: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[bool, list[str], dict[str, str]]:
    lines = [line.rstrip() for line in report.splitlines()]
    errors: list[str] = []
    if not lines or lines[0] != "# Archive Migration Verification":
        errors.append("wrong report title")
    headings = [
        "## Summary",
        "## Verification Matrix",
        "## Verification Notes",
        "## Quarantine Follow-up",
    ]
    positions: list[int] = []
    for heading in headings:
        if lines.count(heading) != 1:
            errors.append(f"heading count for {heading}={lines.count(heading)}")
        else:
            positions.append(lines.index(heading))
    ordered = len(positions) == len(headings) and positions == sorted(positions)
    if len(positions) == len(headings) and not ordered:
        errors.append("headings out of order")
    if [line for line in lines if line.startswith("## ")] != headings:
        errors.append("report headings are missing, extra, duplicated, or out of order")

    available = [expected[b] for b in batch_ids if expected[b]["final_state"] != "unavailable"]
    verified_rows = [row for row in available if row["final_state"] == "verified"]
    partial_rows = [row for row in available if row["final_state"] == "partial"]
    summary = [
        f"- Runs completed: {len(available)}",
        f"- Verified batches: {len(verified_rows)}",
        f"- Partial batches: {len(partial_rows)}",
        f"- Rows landed: {sum(row['verification_result']['rows_landed'] for row in available)}",
        f"- Rows quarantined: {sum(row['verification_result']['rows_quarantined'] for row in available)}",
        f"- Batches ready for cutover: {sum(1 for row in available if row['cutover_ready'])}",
    ]
    header = "| Batch | Run state | Verification state | Rows requested | Rows landed | Rows quarantined | Evidence kind | Evidence ID | Cutover ready |"
    separator = "|---|---|---|---|---|---|---|---|---|"
    expected_rows: list[str] = []
    for batch_id in batch_ids:
        row = expected[batch_id]
        run = row.get("run_result") or {}
        verification = row.get("verification_result") or {}
        cells = [
            batch_id,
            matrix_cell(run.get("state")),
            matrix_cell(verification.get("state")),
            matrix_cell(verification.get("rows_requested", truth["batches"][batch_id]["rows_requested"])),
            matrix_cell(verification.get("rows_landed")),
            matrix_cell(verification.get("rows_quarantined")),
            matrix_cell(row.get("evidence_kind")),
            matrix_cell(row.get("evidence_id")),
            matrix_cell(row.get("cutover_ready")),
        ]
        expected_rows.append("| " + " | ".join(cells) + " |")

    notes: dict[str, str] = {}
    if ordered:
        if [line for line in lines[1 : positions[0]] if line.strip()]:
            errors.append("unexpected content appears before Summary")
        if [line for line in lines[positions[0] + 1 : positions[1]] if line.strip()] != summary:
            errors.append("Summary must contain exactly the six required count lines")
        matrix_lines = [line.strip() for line in lines[positions[1] + 1 : positions[2]] if line.strip()]
        if matrix_lines != [header, separator, *expected_rows]:
            errors.append("verification matrix must contain only the exact header and 12 evidence rows")
        note_lines = [line for line in lines[positions[2] + 1 : positions[3]] if line.strip()]
        if len(note_lines) != len(batch_ids):
            errors.append("Verification Notes must contain exactly 12 nonempty bullets")
        for line in note_lines:
            match = re.fullmatch(
                r"\s*-\s+(?:\*\*)?(AM-\d{4}-\d{2}-\d{4})(?:\*\*)?\s*[:\-]\s*(.+?)\s*",
                line,
            )
            if not match or match.group(1) in notes:
                errors.append("verification note is malformed or duplicated")
                continue
            notes[match.group(1)] = match.group(2)
    if list(notes) != batch_ids or any(not text.strip() for text in notes.values()):
        errors.append("verification notes are missing, duplicated, empty, or out of order")
    for batch_id, text in notes.items():
        row = expected.get(batch_id, {})
        if row.get("final_state") == "partial" and "run state=completed" not in text:
            errors.append(f"{batch_id}: partial-batch note omits run state=completed")
        if row.get("final_state") in {"verified", "partial"} and str(row.get("evidence_id")) not in text:
            errors.append(f"{batch_id}: note omits its exact evidence ID")

    followup = "\n".join(lines[positions[3] + 1 :]) if ordered else ""
    lowered = followup.lower()
    for phrase in ("not eligible for cutover", "quarantine", "re-verif"):
        if phrase not in lowered:
            errors.append(f"quarantine follow-up omitted {phrase}")
    for batch_id in truth["partial"]:
        row = expected[batch_id]
        if batch_id not in followup:
            errors.append(f"quarantine follow-up omitted {batch_id}")
        verification = row.get("verification_result") or {}
        if row["final_state"] == "partial" and (
            str(row["evidence_id"]) not in followup
            or str(verification.get("rows_quarantined")) not in followup
        ):
            errors.append(f"quarantine follow-up omitted the quarantine ID or count for {batch_id}")
    for batch_id in truth["verified"]:
        if batch_id in followup:
            errors.append(f"quarantine follow-up incorrectly includes {batch_id}")
    if re.search(
        r"(?:all\s+12|every\s+batch|all\s+batches)[^.\n]{0,40}"
        r"(?:verified|migrated|complete|successful|ready\s+for\s+cutover|eligible\s+for\s+cutover)",
        report,
        re.I,
    ):
        errors.append("report contains a blanket-success claim")
    return bool(report.strip()) and not errors, errors, notes


def validate_recalls(
    assistant: dict[int, str],
    tool_events: list[dict[str, Any]],
    truth: dict[str, Any],
    expected: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for turn_text, batch_ids in truth["recall_turns"].items():
        turn = int(turn_text)
        if any(event.get("conversation_turn") == turn for event in tool_events):
            errors.append(f"turn {turn}: tool call made during no-tool check")
        observed = final_json_object(assistant.get(turn, ""))
        wanted = {batch_id: expected_recall(expected[batch_id]) for batch_id in batch_ids}
        if observed != wanted or list(observed or {}) != batch_ids or any(
            list(observed[batch_id]) != truth["recall_field_order"] for batch_id in batch_ids
        ):
            errors.append(f"turn {turn}: retained result check is wrong")
    return not errors, errors


def validate_agent_protocol(
    tool_events: list[dict[str, Any]],
    truth: dict[str, Any],
    by_batch: dict[str, dict[str, Any]],
    workspace: Path,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    release_for = {
        batch_id: int(turn)
        for turn, batch_ids in truth["release_turns"].items()
        for batch_id in batch_ids
    }
    known_runs = {
        run_id
        for batch_id in truth["batches"]
        if isinstance(run_id := by_batch.get(batch_id, {}).get("run_id"), str)
    }
    unexpected = [
        (event.get("conversation_turn"), event.get("seq"), operation, argument)
        for event in tool_events
        if (parsed := parse_channel_command(tool_command(event))) is not None
        for operation, argument in [parsed]
        if (operation == "run" and argument not in release_for)
        or (operation == "verify" and argument not in known_runs)
    ]
    if unexpected:
        errors.append(f"documented channel calls outside the released batch={unexpected}")
    for batch_id in truth["batches"]:
        calls = matching_events(tool_events, "run", batch_id)
        turns = [event.get("conversation_turn") for event in calls]
        if len(calls) != 1 or turns != [release_for[batch_id]]:
            errors.append(f"{batch_id}: run calls={len(calls)} turns={turns}")
        run_id = by_batch.get(batch_id, {}).get("run_id")
        verifies = matching_events(tool_events, "verify", run_id) if isinstance(run_id, str) else []
        verify_turns = [event.get("conversation_turn") for event in verifies]
        if len(verifies) != 1 or verify_turns != [release_for[batch_id]]:
            errors.append(f"{batch_id}: verify calls={len(verifies)} turns={verify_turns}")
        elif calls and (verifies[0].get("seq") or 0) <= (calls[0].get("seq") or 0):
            errors.append(f"{batch_id}: verify was not issued after its run")

    no_tool_turns = {int(turn) for turn in truth["recall_turns"]}
    forbidden = [
        (event.get("conversation_turn"), event.get("seq"))
        for event in tool_events
        if event.get("conversation_turn") in no_tool_turns | {9, 10}
        and parse_channel_command(tool_command(event)) is not None
    ]
    if forbidden:
        errors.append(f"migration calls in no-tool/final turns={forbidden}")

    early = [
        (event.get("conversation_turn"), event.get("seq"))
        for event in tool_events
        if isinstance(event.get("conversation_turn"), int)
        and event["conversation_turn"] < 9
        and call_mentions_final_deliverable(event)
    ]
    if early:
        errors.append(f"final deliverable accessed before finalization={early}")

    unauthorized_channel: list[tuple[Any, Any]] = []
    unauthorized_state: list[tuple[Any, Any]] = []
    for event in tool_events:
        command = tool_command(event)
        marker = (event.get("conversation_turn"), event.get("seq"))
        if is_execution_call(event):
            if shell_mentions_channel(command) and parse_channel_command(command) is None:
                unauthorized_channel.append(marker)
            if shell_mentions_state_path(command):
                unauthorized_state.append(marker)
        for path in declared_file_paths(event):
            normalized = path.strip().replace("\\", "/")
            if CHANNEL_PATH_RE.search(normalized):
                unauthorized_channel.append(marker)
            if STATE_PATH_RE.search(normalized):
                unauthorized_state.append(marker)
    if unauthorized_channel:
        errors.append(
            "undocumented, wrapped, combined, or implementation-level migration access="
            f"{sorted(set(unauthorized_channel), key=str)}"
        )
    if unauthorized_state:
        errors.append(f"unauthorized private-state access={sorted(set(unauthorized_state), key=str)}")

    inputs_ok, input_errors = provided_inputs_intact(workspace)
    errors.extend(input_errors)
    scope_ok, scope_errors = workspace_write_scope_intact(workspace, truth)
    errors.extend(scope_errors)
    return not errors, errors


def write_rubric_evidence(
    workspace: Path,
    truth: dict[str, Any],
    expected: dict[str, dict[str, Any]],
    notes: dict[str, str],
) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    for rubric_id, group in RUBRIC_GROUPS.items():
        target = root / rubric_id
        target.mkdir(parents=True, exist_ok=True)
        for batch_id in truth[group]:
            row = expected[batch_id]
            run = row.get("run_result") or {}
            verification = row.get("verification_result") or {}
            evidence = {
                "schema_version": "harnessbench_archive_migration_rubric_evidence_v1",
                "rubric_id": rubric_id,
                "source_artifact": "output/migration_report.md",
                "batch": batch_id,
                "observed_verification_note": notes.get(batch_id, ""),
                "expected_run_state": run.get("state"),
                "expected_run_id": run.get("run_id"),
                "expected_verification_state": verification.get("state"),
                "expected_rows_requested": verification.get("rows_requested"),
                "expected_rows_landed": verification.get("rows_landed"),
                "expected_rows_quarantined": verification.get("rows_quarantined"),
                "expected_final_state": row.get("final_state"),
                "expected_evidence_kind": row.get("evidence_kind"),
                "expected_evidence_id": row.get("evidence_id"),
                "expected_cutover_ready": row.get("cutover_ready"),
            }
            (target / f"{batch_id}.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason[:2400]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace
    truth = load_json(HERE / "truth.json", {})
    batch_ids = list(truth.get("batches", {}))

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run_dir__")
    events, summary = trace_payload(run_dir) if run_value else ([], {})
    tool_events = [event for event in events if event.get("event_type") == "tool"]
    assistant = assistant_by_turn(events)

    counts = Counter(str(event.get("event_type")) for event in events)
    seqs = [event.get("seq") for event in events]
    conversation = summary.get("conversation", {}) if isinstance(summary, dict) else {}
    if not isinstance(conversation, dict):
        conversation = {}
    native_session_id = conversation.get("native_session_id")
    finished_session_ids = [
        event.get("native_session_id")
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
    ]
    same_session_ok = (
        isinstance(native_session_id, str)
        and bool(native_session_id)
        and conversation.get("native_session_ids") == [native_session_id]
        and conversation.get("new_session_before_turns") == []
        and finished_session_ids == [native_session_id] * 10
    )
    tool_ids_ok = all(
        isinstance(event.get("tool_call"), dict)
        and isinstance(event["tool_call"].get("tool_call_id"), str)
        and bool(event["tool_call"]["tool_call_id"])
        and isinstance(event.get("tool_result"), dict)
        and event["tool_result"].get("tool_call_id") == event["tool_call"]["tool_call_id"]
        for event in tool_events
    )
    lifecycle_ok = (
        bool(events)
        and seqs == list(range(1, len(events) + 1))
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("timed_out") is False
        and counts["conversation_started"] == 1
        and counts["conversation_turn_started"] == 10
        and counts["conversation_turn_finished"] == 10
        and counts["conversation_finished"] == 1
        and set(assistant) == set(range(1, 11))
        and conversation.get("mode") == "fixed_turns"
        and conversation.get("turns_completed") == 10
        and same_session_ok
        and tool_ids_ok
    )

    rows, log_valid, log_errors = migration_log(workspace)
    by_batch = {str(row.get("batch")): row for row in rows}
    ledger_ok = log_valid and [row.get("batch") for row in rows] == batch_ids
    ledger_errors = list(log_errors)
    if not ledger_ok:
        ledger_errors.append("ledger order, count, or validity is wrong")
    for batch_id in batch_ids:
        static = truth["batches"][batch_id]
        row = by_batch.get(batch_id, {})
        partial = static["final_state"] == "partial"
        if not (
            row.get("source_table") == static["source_table"]
            and is_int(row.get("rows_requested"))
            and row.get("rows_requested") == static["rows_requested"]
            and row.get("rows_landed") == static["rows_landed"]
            and row.get("rows_quarantined") == static["rows_quarantined"]
            and row.get("state") == static["final_state"]
            and isinstance(row.get("ts"), str)
            and TIMESTAMP_RE.fullmatch(row["ts"])
            and isinstance(row.get("run_id"), str)
            and RUN_ID_RE.fullmatch(row["run_id"])
            and (
                (partial and row.get("checksum") is None
                 and isinstance(row.get("quarantine_id"), str)
                 and QUARANTINE_RE.fullmatch(row["quarantine_id"]))
                or (not partial and row.get("quarantine_id") is None
                    and isinstance(row.get("checksum"), str)
                    and CHECKSUM_RE.fullmatch(row["checksum"]))
            )
        ):
            ledger_ok = False
            ledger_errors.append(f"{batch_id}: channel ledger conflicts with source truth")

    runs: dict[str, dict[str, Any] | None] = {}
    run_errors: list[str] = []
    verifications: dict[str, dict[str, Any] | None] = {}
    verify_errors: list[str] = []
    for batch_id in batch_ids:
        row = by_batch.get(batch_id, {})
        expected_run = authoritative_run(batch_id, row) if row else {}
        matches = matching_events(tool_events, "run", batch_id)
        exact = [event for event in matches if expected_run and exact_business_object(event, expected_run)]
        runs[batch_id] = expected_run if len(matches) == 1 and len(exact) == 1 else None
        if runs[batch_id] is None:
            run_errors.append(f"{batch_id}: calls={len(matches)} exact_results={len(exact)}")

        run_id = row.get("run_id")
        expected_verification = authoritative_verification(batch_id, row) if row else {}
        matches = matching_events(tool_events, "verify", run_id) if isinstance(run_id, str) else []
        exact = [
            event for event in matches
            if expected_verification and exact_business_object(event, expected_verification)
        ]
        verifications[batch_id] = (
            expected_verification if matches and len(exact) == len(matches) else None
        )
        if verifications[batch_id] is None:
            verify_errors.append(f"{batch_id}: calls={len(matches)} exact_results={len(exact)}")

    run_fidelity_ok = ledger_ok and not run_errors
    verify_fidelity_ok = ledger_ok and not verify_errors
    expected = build_expected_delivery(batch_ids, runs, verifications)

    status, status_parse = load_ordered_object(workspace / "output" / "migration_status.json")
    schema_ok, schema_errors = validate_status_schema(status, batch_ids, truth)
    exact_status_ok = schema_ok and status == expected
    files = output_files(workspace)
    files_ok = files == ["migration_report.md", "migration_status.json"] and all(
        (workspace / "output" / name).is_file()
        and not (workspace / "output" / name).is_symlink()
        and bool(read_text(workspace / "output" / name).strip())
        for name in files
    )
    evidence_available = required_evidence_available(status, batch_ids)
    contract_schema_ok = files_ok and schema_ok and evidence_available

    report = read_text(workspace / "output" / "migration_report.md")
    report_ok, report_errors, notes = validate_report(report, batch_ids, truth, expected)
    recalls_ok, recall_errors = validate_recalls(assistant, tool_events, truth, expected)
    protocol_ok, protocol_errors = validate_agent_protocol(tool_events, truth, by_batch, workspace)
    write_rubric_evidence(workspace, truth, expected, notes)

    harness_results = [
        rule(
            "hr_complete_attributable_multiturn_trace",
            lifecycle_ok,
            f"events={len(events)} contiguous={seqs == list(range(1, len(events) + 1))} "
            f"turns={counts['conversation_turn_started']}/{counts['conversation_turn_finished']} "
            f"assistant_turns={sorted(assistant)} tool_events={len(tool_events)} "
            f"stable_call_result_ids={tool_ids_ok} same_session={same_session_ok} "
            f"summary={summary.get('status')}/{conversation.get('turns_completed')}",
        ),
        rule(
            "hr_run_results_reach_model_exactly",
            run_fidelity_ok,
            f"ledger_valid={ledger_ok} exact_run_results={len(batch_ids) - len(run_errors)}/{len(batch_ids)} "
            f"errors={(ledger_errors + run_errors)[:10]}",
        ),
        rule(
            "hr_verification_results_reach_model_exactly",
            verify_fidelity_ok,
            f"exact_verification_results={len(batch_ids) - len(verify_errors)}/{len(batch_ids)} "
            f"errors={(ledger_errors + verify_errors)[:10]}",
        ),
    ]
    agent_results = [
        rule(
            "ar_output_contract_and_nested_schema",
            contract_schema_ok,
            f"files={files} json_parse={status_parse} required_evidence_available={evidence_available} "
            f"schema_errors={schema_errors[:10]}",
        ),
        rule(
            "ar_exact_direct_result_reconciliation",
            exact_status_ok,
            f"rows={len(status or {})}/{len(batch_ids)} "
            f"direct_run_available={sum(v is not None for v in runs.values())} "
            f"direct_verification_available={sum(v is not None for v in verifications.values())} "
            f"exact={exact_status_ok}",
        ),
        rule(
            "ar_cross_turn_checks_and_report_are_consistent",
            report_ok and recalls_ok and protocol_ok,
            f"recalls_ok={recalls_ok} report_ok={report_ok} protocol_ok={protocol_ok} "
            f"recall_errors={recall_errors[:6]} report_errors={report_errors[:6]} "
            f"protocol_errors={protocol_errors[:6]}",
        ),
    ]
    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness_results, "agent": agent_results},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
