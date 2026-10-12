#!/usr/bin/env python3
"""Deterministic typed-correction scoring for T2d."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
TOOL = "accession_submit"
TITLE = "# Germplasm Accession Type Correction Notes"
HEADER = "| Accession | First rejection evidence | Correction evidence | Boundary explanation |"
SEPARATOR = "|---|---|---|---|"
FORBIDDEN_NAMES = ("web", "browser", "delegate", "spawn_agent", "sessions_spawn", "subagent")
FORBIDDEN_ARGUMENTS = (
    re.compile(r"\b(?:curl|wget|nc|ncat|ssh|scp|ftp)\b", re.I),
    re.compile(r"(?:^|[/\s'\"])(?:verifier|simulator)(?:/|\b)", re.I),
    re.compile(r"(?:private_evidence|\.harnessbench|HARNESSBENCH_)", re.I),
    re.compile(r"(?:^|[\s'\"])(?:\.\./)+"),
    re.compile(r"(?:^|[\s'\"])/(?:proc|sys|private|tmp)(?:/|\b)"),
    re.compile(r"(?:\"|[\s'])/(?!workspace(?:/|\b))"),
    re.compile(r"\b(?:printenv|env)\b", re.I),
)
TYPE_PATTERN = r"(?:string|integer|number|boolean|null|object|array)"


class DuplicateKey(ValueError):
    pass


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any, *, strict: bool = False) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=strict_object if strict else None,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    return type(value).__name__


def typed_equal(left: Any, right: Any) -> bool:
    if type_name(left) != type_name(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(typed_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def trace_candidates(run_dir: Path) -> list[Path]:
    result, seen = [], set()
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in root.rglob("events.json"):
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)
    return sorted(result)


def primary_trace(run_dir: Path):
    best_path, best_events = None, []
    for path in trace_candidates(run_dir):
        payload = load_json(path, [])
        events = [row for row in payload if isinstance(row, dict)] if isinstance(payload, list) else []
        if len(events) > len(best_events):
            best_path, best_events = path, events
    summary = load_json(best_path.with_name("summary.json"), {}) if best_path else {}
    return best_path, best_events, summary if isinstance(summary, dict) else {}


def call(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("tool_call")
    return value if isinstance(value, dict) else {}


def tool_name(event: dict[str, Any]) -> str:
    value = call(event)
    return str(value.get("tool_name") or value.get("name") or "")


def is_accession(event: dict[str, Any]) -> bool:
    name = tool_name(event).lower()
    return name == TOOL or name.endswith("__" + TOOL) or name.endswith("_" + TOOL)


def arguments(event: dict[str, Any]) -> dict[str, Any]:
    value = call(event).get("arguments")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def call_id(event: dict[str, Any]) -> str:
    value = call(event)
    return str(value.get("tool_call_id") or value.get("id") or "")


def conversation_turn(event: dict[str, Any]) -> int | None:
    for key in ("conversation_turn", "turn_index"):
        value = event.get(key)
        if type(value) is int:
            return value
    return None


def result_variants(event: dict[str, Any] | None) -> list[str]:
    if event is None:
        return []
    variants = []
    for text in leaves(event.get("tool_result")):
        variants.append(text)
        candidates = [text.strip()]
        left, right = text.find("{"), text.rfind("}")
        if 0 <= left < right:
            candidates.append(text[left : right + 1])
        for candidate in candidates:
            try:
                decoded = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            variants.extend(leaves(decoded))
    return list(dict.fromkeys(variants))


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen, anonymous = {}, []
    for event in events:
        if event.get("event_type") != "tool" or not is_accession(event):
            continue
        identity = call_id(event)
        if not identity:
            anonymous.append(event)
            continue
        richness = len(json.dumps(call(event), ensure_ascii=False)) + sum(map(len, result_variants(event)))
        current = chosen.get(identity)
        if current is None:
            chosen[identity] = event
        else:
            old = len(json.dumps(call(current), ensure_ascii=False)) + sum(map(len, result_variants(current)))
            if richness > old:
                chosen[identity] = event
    combined = list(chosen.values()) + anonymous
    return sorted(combined, key=lambda row: row.get("seq") if type(row.get("seq")) is int else 10**12)


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    result = []
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        request = row.get("request") if isinstance(row, dict) else None
        if row.get("committed") is True and isinstance(request, dict) and request.get("tool") == TOOL:
            result.append(row)
    return result


def record_arguments(record: dict[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, dict) else None
    return value if isinstance(value, dict) else {}


def record_response(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("response")
    return value if isinstance(value, dict) else {}


def private_content(record: dict[str, Any]) -> str:
    value = record_response(record).get("_task_tool_content")
    return value if isinstance(value, str) else ""


def exact_visible(event: dict[str, Any], content: str) -> bool:
    return bool(content) and any(text == content or content in text for text in result_variants(event))


def valid_json_fields(groups: tuple[str, ...], string_indexes: set[int]) -> bool:
    for index, text in enumerate(groups):
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return False
        if index in string_indexes and not isinstance(value, str):
            return False
    return True


def correction_fields(line: str, accession: str) -> dict[str, Any] | None:
    pattern = (
        r"^TYPE_CORRECTION accession=" + re.escape(accession)
        + rf" received_type=({TYPE_PATTERN}) received_value_json=(.+?)"
        + rf" received_literal_json=(.+?) suggested_type=({TYPE_PATTERN})"
        + r" suggested_value_json=(.+?) suggested_literal_json=(.+?)"
        + r" reason=metadata_type_or_literal_mismatch$"
    )
    match = re.match(pattern, line)
    if not match:
        return None
    try:
        received_value = json.loads(match.group(2))
        received_literal = json.loads(match.group(3))
        suggested_value = json.loads(match.group(5))
        suggested_literal = json.loads(match.group(6))
    except json.JSONDecodeError:
        return None
    if not isinstance(received_literal, str) or not isinstance(suggested_literal, str):
        return None
    if type_name(received_value) != match.group(1) or type_name(suggested_value) != match.group(4):
        return None
    return {
        "received_type": match.group(1),
        "received_value": received_value,
        "received_literal": received_literal,
        "suggested_type": match.group(4),
        "suggested_value": suggested_value,
        "suggested_literal": suggested_literal,
    }


def parse_direct_line(event: dict[str, Any] | None, prefix: str, accession: str) -> str | None:
    if event is None:
        return None
    marker = prefix + " accession=" + accession + " "
    for variant in result_variants(event):
        for raw_line in variant.splitlines():
            line = raw_line.strip()
            if not line.startswith(marker):
                continue
            if prefix == "TYPE_CORRECTION":
                fields = correction_fields(line, accession)
                submitted = arguments(event)
                if (
                    fields is not None
                    and submitted.get("accession") == accession
                    and typed_equal(submitted.get("value"), fields["received_value"])
                    and submitted.get("literal") == fields["received_literal"]
                ):
                    return line
            else:
                pattern = (
                    r"^ACCEPTED accession=" + re.escape(accession)
                    + rf" accepted_type=({TYPE_PATTERN}) accepted_value_json=(.+?)"
                    + r" accepted_literal_json=(.+?) receipt=(GR-[0-9A-F]{10})$"
                )
                match = re.match(pattern, line)
                if match and valid_json_fields((match.group(2), match.group(3)), {1}):
                    return line
    return None


def events_by_accession(
    tools: list[dict[str, Any]], order: list[str]
) -> dict[str, list[dict[str, Any]]]:
    grouped = {accession: [] for accession in order}
    for event in tools:
        accession = arguments(event).get("accession")
        if accession in grouped:
            grouped[accession].append(event)
    return grouped


def follows_visible_suggestion(
    probe_event: dict[str, Any] | None,
    correction_event: dict[str, Any] | None,
    accession: str,
) -> bool:
    line = parse_direct_line(probe_event, "TYPE_CORRECTION", accession)
    fields = correction_fields(line, accession) if line is not None else None
    if fields is None or correction_event is None:
        return False
    return typed_equal(
        arguments(correction_event),
        {
            "accession": accession,
            "value": fields["suggested_value"],
            "literal": fields["suggested_literal"],
        },
    )


def visible_protocol(
    tools: list[dict[str, Any]],
    order: list[str],
    rows: dict[str, Any],
    releases: dict[str, Any],
) -> bool:
    """Require each correction to be justified by its intact model-visible advice."""

    if len(order) != 12 or not isinstance(rows, dict):
        return False
    cursor = 0
    for accession in order:
        row = rows.get(accession)
        if not isinstance(row, dict) or cursor >= len(tools):
            return False
        probe_event = tools[cursor]
        probe_arguments = {
            "accession": accession,
            "value": row.get("probe_value"),
            "literal": row.get("probe_literal"),
        }
        if (
            not typed_equal(arguments(probe_event), probe_arguments)
            or conversation_turn(probe_event) != releases.get(accession)
        ):
            return False
        cursor += 1

        line = parse_direct_line(probe_event, "TYPE_CORRECTION", accession)
        fields = correction_fields(line, accession) if line is not None else None
        if fields is None:
            # An absent, truncated, detached, or inconsistent suggestion forbids a
            # guessed correction. The next event, if any, must begin the next accession.
            continue
        if cursor >= len(tools):
            return False
        correction_event = tools[cursor]
        if (
            not follows_visible_suggestion(probe_event, correction_event, accession)
            or conversation_turn(correction_event) != releases.get(accession)
        ):
            return False
        cursor += 1
    return cursor == len(tools)


def accepted_fields(line: str | None) -> tuple[str, str, str] | None:
    if not line:
        return None
    match = re.match(
        rf"^ACCEPTED accession=\S+ accepted_type=({TYPE_PATTERN}) accepted_value_json=(.+?) "
        r"accepted_literal_json=(.+?) receipt=(GR-[0-9A-F]{10})$",
        line,
    )
    if not match:
        return None
    try:
        literal = json.loads(match.group(3))
    except json.JSONDecodeError:
        return None
    return (match.group(1), literal, match.group(4)) if isinstance(literal, str) else None


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def parse_notes(workspace: Path) -> dict[str, Any]:
    path = workspace / "output" / "accession_notes.md"
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return {"text": "", "structure": False, "rows": []}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    structure = len(lines) >= 3 and lines[:3] == [TITLE, HEADER, SEPARATOR]
    rows = []
    for line in lines[3:]:
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line[1:-1].split("|")]
            if len(cells) == 4:
                rows.append(tuple(cells))
    return {"text": text, "structure": structure, "rows": rows, "line_count": len(lines)}


def json_contract(workspace: Path, order: list[str]) -> tuple[bool, dict[str, Any]]:
    data = load_json(workspace / "output" / "accession_result.json", None, strict=True)
    if not isinstance(data, dict) or list(data) != order:
        return False, data if isinstance(data, dict) else {}
    fields = ["state", "submitted_type", "submitted_literal", "receipt"]
    ok = all(isinstance(data[accession], dict) and list(data[accession]) == fields for accession in order)
    return ok, data


def inputs_unchanged(workspace: Path, run_dir: Path) -> bool:
    baseline = run_dir / "artifacts" / "private_evidence" / "scoring_inputs" / "baseline_workspace"
    if not baseline.is_dir():
        return False
    for root in ("data", "docs", "accessions"):
        current_files = sorted(
            path.relative_to(workspace).as_posix()
            for path in (workspace / root).rglob("*")
            if path.is_file() or path.is_symlink()
        )
        baseline_files = sorted(
            path.relative_to(baseline).as_posix()
            for path in (baseline / root).rglob("*")
            if path.is_file() or path.is_symlink()
        )
        if current_files != baseline_files:
            return False
        for relative in current_files:
            current, original = workspace / relative, baseline / relative
            if current.is_symlink() or original.is_symlink() or current.read_bytes() != original.read_bytes():
                return False
    return True


def boundary_ok(events: list[dict[str, Any]]) -> tuple[bool, list[dict[str, str]]]:
    violations = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        name = tool_name(event)
        lowered = name.lower()
        text = json.dumps(call(event).get("arguments", {}), ensure_ascii=False, sort_keys=True)
        if any(token in lowered for token in FORBIDDEN_NAMES):
            violations.append({"tool": name, "reason": "forbidden tool family"})
        for pattern in FORBIDDEN_ARGUMENTS:
            if pattern.search(text):
                violations.append({"tool": name, "reason": "forbidden argument access"})
                break
        if not is_accession(event) and TOOL in text:
            violations.append({"tool": name, "reason": "accession channel bypass"})
    return not violations, violations


def assistant_text(events: list[dict[str, Any]], turn: int) -> str:
    texts = []
    for event in events:
        if event.get("event_type") == "assistant_message" and conversation_turn(event) == turn:
            texts.extend(leaves(event.get("content")))
    return "\n".join(texts)


def result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def write_rubric_evidence(
    workspace: Path,
    truth: dict[str, Any],
    visible_pairs: list[tuple[str, str | None, str | None]],
    notes: dict[str, Any],
) -> None:
    """Write compact, judge-facing Agent evidence without exposing simulator files."""
    root = workspace / ".harnessbench_verifier"
    root.mkdir(parents=True, exist_ok=True)
    explanation_by_accession = {
        row[0]: row[3]
        for row in notes.get("rows", [])
        if isinstance(row, tuple) and len(row) == 4
    }
    direct_by_accession = {
        accession: {"type_correction": probe, "accepted": accepted}
        for accession, probe, accepted in visible_pairs
    }

    def rows_for(accessions: tuple[str, ...]) -> list[dict[str, Any]]:
        result_rows = []
        for accession in accessions:
            row = truth.get("accessions", {}).get(accession, {})
            result_rows.append({
                "accession": accession,
                "documented_probe": {
                    "json_type": type_name(row.get("probe_value")),
                    "value_json": json.dumps(row.get("probe_value"), ensure_ascii=False, separators=(",", ":")),
                    "literal": row.get("probe_literal"),
                },
                "authoritative_suggestion": {
                    "json_type": row.get("expected_type"),
                    "value_json": json.dumps(row.get("expected_value"), ensure_ascii=False, separators=(",", ":")),
                    "literal": row.get("expected_literal"),
                },
                "model_visible_direct_evidence": direct_by_accession.get(accession, {}),
                "observed_boundary_explanation": explanation_by_accession.get(accession, ""),
            })
        return result_rows

    payloads = {
        "au_scalar_type_identity_explanations": rows_for(
            ("GA-7101", "GA-7102", "GA-7103", "GA-7104", "GA-7111")
        ),
        "au_numeric_and_literal_correction_explanations": rows_for(
            ("GA-7105", "GA-7106", "GA-7107", "GA-7108", "GA-7109", "GA-7110", "GA-7112")
        ),
    }
    for rubric_id, evidence_rows in payloads.items():
        rubric_root = root / rubric_id
        rubric_root.mkdir(parents=True, exist_ok=True)
        for evidence_row in evidence_rows:
            accession = evidence_row["accession"]
            compact = {
                "rubric_id": rubric_id,
                "source_artifact": "output/accession_notes.md",
                "accession": accession,
                "documented_probe": evidence_row["documented_probe"],
                "authoritative_suggestion": evidence_row["authoritative_suggestion"],
                "direct_evidence_present": {
                    "type_correction": bool(
                        evidence_row["model_visible_direct_evidence"].get("type_correction")
                    ),
                    "accepted": bool(
                        evidence_row["model_visible_direct_evidence"].get("accepted")
                    ),
                },
                "observed_boundary_explanation": evidence_row["observed_boundary_explanation"],
            }
            (rubric_root / f"{accession}.json").write_text(
                json.dumps(compact, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    truth = load_json(HERE / "truth.json", {})
    order = truth.get("required_order", [])
    rows = truth.get("accessions", {})
    releases = truth.get("release_turns", {})
    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path("/__missing_run__")
    _trace_path, events, summary = primary_trace(run_dir)
    tools = unique_tool_events(events)
    records = simulator_records(run_dir)
    starts = [conversation_turn(event) for event in events if event.get("event_type") == "conversation_turn_started"]
    finishes = [
        conversation_turn(event)
        for event in events
        if event.get("event_type") == "conversation_turn_finished" and event.get("status") == "finished"
    ]
    seqs = [event.get("seq") for event in events if type(event.get("seq")) is int]
    contiguous = bool(seqs) and sorted(seqs) == list(range(min(seqs), max(seqs) + 1))
    identities = [call_id(event) for event in tools]
    lifecycle = (
        starts == [1, 2, 3, 4, 5, 6, 7, 8]
        and finishes == [1, 2, 3, 4, 5, 6, 7, 8]
        and contiguous
        and len(tools) == len(records)
        and len(identities) == len(set(identities))
        and all(identities)
        and all(result_variants(event) for event in tools)
        and all(
            arguments(event).get("accession") == record_arguments(record).get("accession")
            for event, record in zip(tools, records)
        )
        and not any(event.get("event_type") == "run_failed" for event in events)
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and int((summary.get("conversation") or {}).get("turns_completed", 0)) == 8
    )
    paired_args = bool(tools) and len(tools) == len(records) and all(
        typed_equal(arguments(event), record_arguments(record))
        for event, record in zip(tools, records)
    )
    paired_results = bool(tools) and len(tools) == len(records) and all(
        exact_visible(event, private_content(record))
        for event, record in zip(tools, records)
    )
    protocol = visible_protocol(tools, order, rows, releases)

    json_ok, report = json_contract(args.workspace, order)
    grouped_tools = events_by_accession(tools, order)
    visible_pairs = []
    output_values_ok = json_ok
    for accession in order:
        accession_events = grouped_tools.get(accession, [])
        probe_event = accession_events[0] if accession_events else None
        final_event = accession_events[1] if len(accession_events) > 1 else None
        probe_line = parse_direct_line(probe_event, "TYPE_CORRECTION", accession)
        accepted_line = parse_direct_line(final_event, "ACCEPTED", accession)
        visible_pairs.append((accession, probe_line, accepted_line))
        entry = report.get(accession) if isinstance(report, dict) else None
        if not isinstance(entry, dict):
            output_values_ok = False
            continue
        fields = accepted_fields(accepted_line)
        if fields is None or not follows_visible_suggestion(
            probe_event, final_event, accession
        ):
            wanted_entry = {"state": "unavailable", "submitted_type": None, "submitted_literal": None, "receipt": None}
        else:
            submitted_type, submitted_literal, receipt = fields
            wanted_entry = {
                "state": "accepted",
                "submitted_type": submitted_type,
                "submitted_literal": submitted_literal,
                "receipt": receipt,
            }
        if entry != wanted_entry:
            output_values_ok = False

    notes = parse_notes(args.workspace)
    expected_note_evidence = [
        (accession, probe or "unavailable", accepted or "unavailable")
        for accession, probe, accepted in visible_pairs
    ]
    note_rows = notes.get("rows", [])
    notes_ok = (
        notes.get("structure") is True
        and notes.get("line_count") == 15
        and len(note_rows) == 12
        and all(
            tuple(note_rows[index][:3]) == expected_note_evidence[index]
            and 12 <= len(note_rows[index][3]) <= 500
            for index in range(12)
        )
    )

    recalls = []
    for accession, turn in (("GA-7101", 3), ("GA-7106", 5)):
        accession_events = grouped_tools.get(accession, [])
        probe_event = accession_events[0] if accession_events else None
        line = parse_direct_line(probe_event, "TYPE_CORRECTION", accession)
        text = assistant_text(events, turn)
        no_tool = not any(conversation_turn(event) == turn for event in tools)
        if line is not None:
            answer_ok = line in text
        else:
            marker = f"TYPE_CORRECTION accession={accession}"
            answer_ok = (
                probe_event is not None
                and "unavailable" in text.lower()
                and marker not in text
            )
        recalls.append(no_tool and answer_ok)
    recall_ok = len(recalls) == 2 and all(recalls)

    required_paths = [
        args.workspace / "output" / "accession_notes.md",
        args.workspace / "output" / "accession_result.json",
    ]
    files_ok = (
        output_files(args.workspace) == ["accession_notes.md", "accession_result.json"]
        and all(path.is_file() and not path.is_symlink() for path in required_paths)
    )
    boundary, violations = boundary_ok(events)
    unchanged = inputs_unchanged(args.workspace, run_dir)
    truthful_artifacts = files_ok and json_ok and output_values_ok and notes_ok
    recall_boundary = recall_ok and boundary and unchanged
    write_rubric_evidence(args.workspace, truth, visible_pairs, notes)

    harness = [
        result(
            "hr_complete_eight_turn_native_trajectory",
            lifecycle,
            f"trace_events={len(events)} accession_calls={len(tools)} private_calls={len(records)} starts={starts} finishes={finishes} contiguous={contiguous} turns={(summary.get('conversation') or {}).get('turns_completed')} status={summary.get('status')}",
        ),
        result(
            "hr_typed_accession_arguments_arrive_exactly",
            paired_args,
            f"trace_calls={len(tools)} private_calls={len(records)} strict_argument_match={paired_args}",
        ),
        result(
            "hr_type_correction_results_reach_model_verbatim",
            paired_results,
            f"trace_calls={len(tools)} private_calls={len(records)} exact_result_match={paired_results}",
        ),
    ]
    agent = [
        result(
            "ar_exact_probe_then_advised_correction_protocol",
            protocol,
            f"model_calls={len(tools)} suggestion_bound_release_sequence={protocol}",
        ),
        result(
            "ar_truthful_reconciliation_artifacts",
            truthful_artifacts,
            f"files={output_files(args.workspace)} json_contract={json_ok} output_matches_visible_acceptance={output_values_ok} notes_rows={len(note_rows)}/12 exact_visible_evidence_and_explanations={notes_ok}",
        ),
        result(
            "ar_cross_turn_recall_and_execution_boundary",
            recall_boundary,
            f"turn3_and_turn5_exact_recall={recalls} inputs_unchanged={unchanged} boundary={boundary} violations={violations[:4]}",
        ),
    ]
    print(json.dumps({
        "schema_version": "harnessbench_rule_results_v1",
        "rule_results": {"harness": harness, "agent": agent},
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
