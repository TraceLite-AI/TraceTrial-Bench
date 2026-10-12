#!/usr/bin/env python3
"""Binary scoring and compact evidence packets for the dense submersible case."""

from __future__ import annotations

import argparse
import collections
import json
import os
from pathlib import Path
from typing import Any, Mapping


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
SUFFIXES = ("_draft", "_dry_run", "_legacy")
RELEASE_TURN = {
    **{f"DS-2026-08-{index:03d}": 2 for index in range(1, 5)},
    **{f"DS-2026-08-{index:03d}": 4 for index in range(5, 9)},
    **{f"DS-2026-08-{index:03d}": 7 for index in range(9, 13)},
}
NOTES_TITLE = "# Submersible Dive Action Notes"
CALIBRATION_HEADER = "## Channel Calibration"
ITEM_HEADER = "## Item Evidence"
TABLE_HEADER = "| Item | Brief basis | Selected action | Effective evidence | Submission evidence |"
TABLE_SEPARATOR = "|---|---|---|---|---|"
RECALL_TURNS = (3, 6, 8)


class DuplicateKey(ValueError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise DuplicateKey(key)
        value[key] = child
    return value


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_output_json(path: Path, default: Any) -> Any:
    if path.is_symlink() or not path.is_file():
        return default
    try:
        return json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DuplicateKey):
        return default


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[tuple[Path, list[dict[str, Any]]]] = []
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
            if isinstance(payload, list) and all(isinstance(row, dict) for row in payload):
                candidates.append((resolved, payload))
    if not candidates:
        return []

    # A fixed-turn run stores one authoritative composite conversation trace
    # beside nested per-turn traces. Combining both levels double-counts every
    # imported tool event, so select exactly one composite trace.
    composite = [
        candidate
        for candidate in candidates
        if any(row.get("event_type") == "conversation_started" for row in candidate[1])
    ]
    pool = composite or candidates

    def rank(candidate: tuple[Path, list[dict[str, Any]]]) -> tuple[int, int, int, int]:
        path, payload = candidate
        completed = max(
            (
                int(row.get("turns_completed") or 0)
                for row in payload
                if row.get("event_type") == "conversation_finished"
            ),
            default=0,
        )
        started = len(
            {
                row.get("turn_index")
                for row in payload
                if row.get("event_type") == "conversation_turn_started"
                and isinstance(row.get("turn_index"), int)
            }
        )
        return completed, started, len(payload), -len(path.parts)

    return max(pool, key=rank)[1]


def unique_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool" or not isinstance(event.get("tool_call"), Mapping):
            continue
        call = event["tool_call"]
        key = str(call.get("tool_call_id") or call.get("id") or "")
        if not key:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            by_id.append(event)

    # Codex can expose the same MCP call twice in one normalized trace: once
    # from its live ``item.*`` stream and once from the native session archive.
    # Their IDs differ (``item_*`` versus ``call_*``), so ID-only de-duplication
    # is insufficient.  Collapse only complete, one-to-one cross-source groups.
    # Keeping the multiplicity within each source means a real repeated call is
    # still counted twice and remains visible to the one-shot checks.
    tagged = [
        (event, codex_export_source(event), codex_export_signature(event))
        for event in by_id
    ]
    stream_counts = collections.Counter(
        signature
        for _, source, signature in tagged
        if source == "stream" and signature is not None
    )
    archive_counts = collections.Counter(
        signature
        for _, source, signature in tagged
        if source == "archive" and signature is not None
    )
    paired = {
        signature
        for signature, count in stream_counts.items()
        if count > 0 and archive_counts[signature] == count
    }
    return [
        event
        for event, source, signature in tagged
        if not (source == "archive" and signature in paired)
    ]


def codex_export_source(event: Mapping[str, Any]) -> str | None:
    raw = event.get("raw_event")
    if not isinstance(raw, Mapping):
        return None
    call = raw.get("tool_call")
    result = raw.get("tool_result")
    raw_types = {
        value.get("type")
        for value in (call, result)
        if isinstance(value, Mapping)
    }
    if any(isinstance(value, str) and value.startswith("item.") for value in raw_types):
        return "stream"
    if isinstance(call, Mapping) and call.get("type") == "response_item":
        payload = call.get("payload")
        if isinstance(payload, Mapping) and payload.get("type") == "function_call":
            return "archive"
    return None


def codex_content_texts(value: Any) -> list[str]:
    """Extract user-visible text without provider metadata such as ``type=text``."""

    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for child in value for text in codex_content_texts(child)]
    if not isinstance(value, Mapping):
        return []
    texts = [value["text"]] if isinstance(value.get("text"), str) else []
    for key, child in value.items():
        if key != "text" and isinstance(child, (Mapping, list)):
            texts.extend(codex_content_texts(child))
    return texts


def codex_export_result(event: Mapping[str, Any], source: str) -> str | None:
    raw = event.get("raw_event")
    if not isinstance(raw, Mapping):
        return None
    result = raw.get("tool_result")
    if not isinstance(result, Mapping):
        return None
    if source == "stream":
        item = result.get("item")
        value = item.get("result") if isinstance(item, Mapping) else None
        text = "\n".join(codex_content_texts(value)).strip()
        return text or None
    if source == "archive" and result.get("type") == "response_item":
        payload = result.get("payload")
        output = payload.get("output") if isinstance(payload, Mapping) else None
        if not isinstance(output, str) or not output.strip():
            return None
        candidate = output.partition("\nOutput:\n")[2] or output
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        text = "\n".join(codex_content_texts(decoded)).strip()
        return text or None
    return None


def codex_export_signature(event: Mapping[str, Any]) -> tuple[int, str, str, str] | None:
    source = codex_export_source(event)
    turn = event.get("conversation_turn")
    call = event.get("tool_call")
    if source is None or not isinstance(turn, int) or not isinstance(call, Mapping):
        return None
    name = str(call.get("tool_name") or call.get("name") or "").strip()
    arguments = call.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if not name or not isinstance(arguments, Mapping):
        return None
    result = codex_export_result(event, source)
    if result is None:
        return None
    return (
        turn,
        name,
        json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        result,
    )


def tool_name(event: Mapping[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


def tool_arguments(event: Mapping[str, Any]) -> dict[str, Any]:
    call = event.get("tool_call")
    if not isinstance(call, Mapping):
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


def result_text(event: Mapping[str, Any]) -> str:
    return "\n".join(leaves(event.get("tool_result")))


def content_visible(content: str, observed: str) -> bool:
    """Accept intact plain text or the same text JSON-escaped inside a safety wrapper."""

    escaped = json.dumps(content, ensure_ascii=False)[1:-1]
    return bool(content) and (content in observed or escaped in observed)


def matches_name(observed: str, expected: str) -> bool:
    lowered = observed.lower()
    target = expected.lower()
    return (
        lowered == target
        or lowered.endswith("__" + target)
        or lowered.endswith("_" + target)
        or lowered.endswith("." + target)
    )


def canonical_name(observed: str, expected_names: list[str]) -> str | None:
    matches = [name for name in expected_names if matches_name(observed, name)]
    return matches[0] if len(matches) == 1 else None


def normalize_tool(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw.strip() or None
    if not isinstance(raw, Mapping):
        return None
    core: Mapping[str, Any] = raw
    for key in ("function", "toolSpec", "tool_spec"):
        if isinstance(raw.get(key), Mapping):
            core = raw[key]
            break
    name = core.get("name") or core.get("tool_name") or raw.get("name") or raw.get("tool_name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def tool_snapshots(events: list[dict[str, Any]], expected_names: list[str]) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") not in {"model_input", "model_request"}:
            continue
        candidates = (
            event.get("tools"),
            nested(event, "request", "tools"),
            nested(event, "request_body", "tools"),
            nested(event, "raw_event", "tools"),
            nested(event, "raw_event", "request", "tools"),
            nested(event, "raw_event", "request_body", "tools"),
        )
        for candidate in candidates:
            if not isinstance(candidate, list) or not candidate:
                continue
            observed = [name for raw in candidate if (name := normalize_tool(raw))]
            mapped = [canonical_name(name, expected_names) for name in observed]
            mapped = [name for name in mapped if name]
            if not mapped:
                continue
            counts = collections.Counter(mapped)
            reports.append(
                {
                    "turn": event.get("conversation_turn"),
                    "present": len(counts),
                    "missing": [name for name in expected_names if counts[name] == 0],
                    "duplicates": [name for name in expected_names if counts[name] != 1 and counts[name] > 0],
                    "complete": len(counts) == len(expected_names) and all(counts[name] == 1 for name in expected_names),
                }
            )
    return reports


def simulator_records(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "artifacts" / "private_evidence" / "business_simulator" / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError):
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("committed") is True:
            records.append(value)
    return records


def provider_tool_snapshots(run_dir: Path) -> list[dict[str, Any]]:
    paths = (
        run_dir / "artifacts" / "model_call_budget" / "events.jsonl",
        run_dir / "artifacts" / "container" / "model_call_budget" / "events.jsonl",
    )
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        reports: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError):
            return []
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            snapshot = event.get("provider_tool_snapshot") if isinstance(event, dict) else None
            if isinstance(snapshot, dict) and event.get("admitted") is True:
                reports.append(snapshot)
        return reports
    return []


def record_tool(record: Mapping[str, Any]) -> str:
    request = record.get("request")
    return str(request.get("tool") or "") if isinstance(request, Mapping) else ""


def record_arguments(record: Mapping[str, Any]) -> dict[str, Any]:
    request = record.get("request")
    value = request.get("arguments") if isinstance(request, Mapping) else None
    return value if isinstance(value, dict) else {}


def record_content(record: Mapping[str, Any]) -> str:
    response = record.get("response")
    return str(response.get("_task_tool_content") or "") if isinstance(response, Mapping) else ""


def action_base(name: str, action_names: set[str]) -> tuple[str | None, str | None]:
    if name in action_names:
        return name, "production"
    for suffix in SUFFIXES:
        if name.endswith(suffix) and name[: -len(suffix)] in action_names:
            return name[: -len(suffix)], suffix[1:]
    return None, None


def signature(name: str, arguments: Mapping[str, Any], content: str) -> tuple[str, str, str]:
    return name, json.dumps(arguments, ensure_ascii=False, sort_keys=True), content


def native_rows(
    events: list[dict[str, Any]], expected_names: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in unique_tool_events(events):
        name = canonical_name(tool_name(event), expected_names)
        if not name:
            continue
        rows.append(
            {
                "event": event,
                "name": name,
                "arguments": tool_arguments(event),
                "result": result_text(event),
                "turn": event.get("conversation_turn"),
            }
        )
    return rows


def output_files(workspace: Path) -> list[str]:
    root = workspace / "output"
    if not root.is_dir() or root.is_symlink():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    )


def read_text(path: Path) -> tuple[str, str]:
    if path.is_symlink() or not path.is_file():
        return "", "missing or non-regular"
    try:
        return path.read_text(encoding="utf-8", errors="strict"), "valid UTF-8"
    except UnicodeError:
        return "", "invalid UTF-8"
    except OSError as exc:
        return "", f"unreadable: {type(exc).__name__}"


def note_document(
    text: str, item_order: list[str]
) -> tuple[bool, list[str], list[str], dict[str, list[str]]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    expected_line_count = 8 + len(item_order)
    calibration = lines[2:5] if len(lines) >= 5 else []
    table_lines = lines[8:] if len(lines) >= 8 else []
    order: list[str] = []
    rows: dict[str, list[str]] = {}
    rows_valid = len(table_lines) == len(item_order)
    for line in table_lines:
        if not line.startswith("|") or not line.endswith("|"):
            rows_valid = False
            continue
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 5 or cells[0] not in item_order or cells[0] in rows:
            rows_valid = False
            continue
        order.append(cells[0])
        rows[cells[0]] = cells
    shape = (
        len(lines) == expected_line_count
        and lines[:2] == [NOTES_TITLE, CALIBRATION_HEADER]
        and len(calibration) == 3
        and all(line.startswith("- ") for line in calibration)
        and lines[5:8] == [ITEM_HEADER, TABLE_HEADER, TABLE_SEPARATOR]
        and rows_valid
        and order == item_order
        and set(rows) == set(item_order)
    )
    return shape, calibration, order, rows


def exact_cell(value: str, expected: str) -> bool:
    value = value.strip()
    return value in {
        expected,
        f"`{expected}`",
        f'"{expected}"',
        f"'{expected}'",
    }


def calibration_bullet_matches(line: str, tool: str, result: str) -> bool:
    if not line.startswith("- "):
        return False
    body = line[2:].replace("`", "").replace("**", "").strip()
    if body.count(tool) != 1 or body.count(result) != 1:
        return False
    remainder = body.replace(tool, "", 1).replace(result, "", 1)
    return not remainder.strip(" \t:;,.=-–—\"'“”‘’()[]")


def assistant_answers(events: list[dict[str, Any]]) -> dict[int, str]:
    answers: dict[int, str] = {}
    for event in events:
        turn = event.get("conversation_turn")
        if event.get("event_type") != "assistant_message" or not isinstance(turn, int):
            continue
        content = event.get("content")
        if content is None:
            content = event.get("text")
        text = "\n".join(leaves(content)).strip()
        if text:
            answers[turn] = text
    return answers


def recall_required_tokens(turn: int, expectation: Mapping[str, Any]) -> list[str]:
    if turn == 3:
        return [
            str(expectation.get("item") or ""),
            str(expectation.get("action") or ""),
            str(expectation.get("receipt") or ""),
        ]
    if turn == 6:
        values = expectation.get("items")
        return [str(value) for value in values] if isinstance(values, list) else []
    if turn == 8:
        values = expectation.get("receipts")
        if not isinstance(values, Mapping):
            return []
        return [
            token
            for item, receipt in values.items()
            for token in (str(item), str(receipt))
        ]
    return []


def prohibited_access(events: list[dict[str, Any]], expected_names: list[str]) -> list[str]:
    markers = (
        ".harnessbench",
        "simulator-cli",
        "task_tool_mcp",
        "/simulator/",
        "/verifier/",
        "/artifacts/",
        "private_evidence",
    )
    network = {"websearch", "webfetch", "web_search", "web_fetch"}
    failures: list[str] = []
    for event in unique_tool_events(events):
        name = tool_name(event)
        if canonical_name(name, expected_names):
            continue
        if name.lower() in network:
            failures.append(f"seq={event.get('seq')}:network:{name}")
            continue
        arguments = "\n".join(leaves(tool_arguments(event))).lower()
        hits = [marker for marker in markers if marker in arguments]
        if hits:
            failures.append(f"seq={event.get('seq')}:hidden:{name}:{','.join(hits[:2])}")
    return failures


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(passed)), "reason": reason}


def write_packet(workspace: Path, rubric_id: str, evidence_text: str, facts: Mapping[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    payload = {
        "schema_version": "harnessbench_submersible_dense_tools_evidence_v2",
        "rubric_id": rubric_id,
        "evidence": f"EVIDENCE_START {evidence_text} EVIDENCE_END",
        "facts": facts,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    truth = load_json(HERE / "truth.json", {})
    item_truth = truth.get("items", {}) if isinstance(truth, dict) else {}
    item_order = list(item_truth)
    action_names = {row["action"] for row in item_truth.values() if isinstance(row, dict)}
    scenario = load_json(CASE / "simulator" / "scenario.json", {})
    scenario_items = scenario.get("items", {}) if isinstance(scenario, dict) else {}
    manifest = load_json(CASE / "simulator" / "tools.json", {})
    declared = manifest.get("tools", []) if isinstance(manifest, dict) else []
    expected_names = [row["name"] for row in declared if isinstance(row, dict) and isinstance(row.get("name"), str)]

    run_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_value) if run_value else Path()
    events = trace_events(run_dir) if run_value else []
    records = simulator_records(run_dir) if run_value else []
    native = native_rows(events, expected_names)

    # Agent output contract.
    receipts_path = args.workspace / "output" / "dive_action_receipts.json"
    receipts_raw = load_output_json(receipts_path, None)
    receipts = receipts_raw if isinstance(receipts_raw, dict) else {}
    notes, notes_status = read_text(args.workspace / "output" / "dive_action_notes.md")
    notes_document_ok, calibration_bullets, row_order, note_map = note_document(
        notes, item_order
    )
    receipt_shape = (
        receipts_path.is_file()
        and not receipts_path.is_symlink()
        and isinstance(receipts_raw, dict)
        and list(receipts) == item_order
        and all(isinstance(receipts[item], dict) and list(receipts[item]) == ["state", "action", "receipt"] for item in item_order)
    )
    notes_shape = (
        notes_status == "valid UTF-8"
        and notes_document_ok
    )
    output_contract = output_files(args.workspace) == ["dive_action_notes.md", "dive_action_receipts.json"] and receipt_shape and notes_shape

    # Exact dive-control results are deterministic and belong in an agent rule.
    item_facts: dict[str, dict[str, Any]] = {}
    for item, target in item_truth.items():
        briefs = [r for r in records if record_tool(r) == "dive_brief" and record_arguments(r) == {"item": item}]
        actions = [r for r in records if record_arguments(r).get("ref") == item and action_base(record_tool(r), action_names)[0]]
        submits = [r for r in records if record_tool(r) == "dive_submit" and record_arguments(r).get("item") == item]
        effect = f"EFFECTIVE item={item} action={target['action']} receipt_code={target['receipt']}"
        accepted = f"ACCEPTED item={item}"
        workflow_exact = (
            len(briefs) == 1
            and len(actions) == 1
            and record_tool(actions[0]) == target["action"]
            and record_content(actions[0]) == effect
            and len(submits) == 1
            and record_arguments(submits[0]).get("value") == target["receipt"]
            and record_content(submits[0]) == accepted
        )
        note_row = note_map.get(item, [])
        artifact_exact = (
            receipts.get(item) == {"state": "effective", "action": target["action"], "receipt": target["receipt"]}
            and len(note_row) == 5
            and exact_cell(note_row[2], target["action"])
            and exact_cell(note_row[3], effect)
            and exact_cell(note_row[4], accepted)
        )
        item_facts[item] = {
            "briefs": len(briefs),
            "action_tools": [record_tool(r) for r in actions],
            "submits": len(submits),
            "workflow_exact": workflow_exact,
            "artifact_exact": artifact_exact,
            "exact": workflow_exact and artifact_exact,
        }
    workflow_dive = len(item_facts) == 12 and all(
        row["workflow_exact"] for row in item_facts.values()
    )

    # Release timing and one-shot behavior require native trajectory association.
    calibration_records = [r for r in records if record_arguments(r).get("ref") == truth.get("calibration_ref") and action_base(record_tool(r), action_names)[0]]
    calibration_exact = [record_tool(r) for r in calibration_records] == truth.get("calibration_tools")
    calibration_grounded = (
        calibration_exact
        and len(calibration_records) == len(calibration_bullets) == 3
        and all(
            calibration_bullet_matches(
                bullet,
                record_tool(record),
                record_content(record),
            )
            for bullet, record in zip(calibration_bullets, calibration_records)
        )
    )
    exact_dive = (
        workflow_dive
        and calibration_grounded
        and all(row["artifact_exact"] for row in item_facts.values())
    )
    private_counts: collections.Counter[tuple[str, str, str]] = collections.Counter(
        signature(record_tool(r), record_arguments(r), record_content(r)) for r in records
    )
    native_counts: collections.Counter[tuple[str, str, str]] = collections.Counter()
    native_turns: dict[tuple[str, str, str], list[int]] = collections.defaultdict(list)
    for row in native:
        for key in private_counts:
            if row["name"] == key[0] and json.dumps(row["arguments"], ensure_ascii=False, sort_keys=True) == key[1] and content_visible(key[2], row["result"]):
                native_counts[key] += 1
                if isinstance(row["turn"], int):
                    native_turns[key].append(row["turn"])
                break
    staged = calibration_exact and private_counts == native_counts
    expected_turn_violations: list[str] = []
    if staged:
        for record in records:
            key = signature(record_tool(record), record_arguments(record), record_content(record))
            turns = native_turns.get(key, [])
            turn = turns.pop(0) if turns else None
            ref = record_arguments(record).get("ref")
            item = record_arguments(record).get("item")
            expected_turn = 2 if ref == truth.get("calibration_ref") else RELEASE_TURN.get(ref or item)
            if turn != expected_turn:
                expected_turn_violations.append(f"{record_tool(record)}:{ref or item}:turn={turn}:expected={expected_turn}")
    hidden_access = prohibited_access(events, expected_names)
    release_one_shot = staged and workflow_dive and not expected_turn_violations and not hidden_access

    agent_rules = [
        rule("ar_output_contract", output_contract, f"files={output_files(args.workspace)}; receipt_shape={receipt_shape}; notes_shape={notes_shape}"),
        rule(
            "ar_exact_validated_dive_results",
            exact_dive,
            f"exact_items={sum(row['exact'] for row in item_facts.values())}/12; "
            f"workflow_items={sum(row['workflow_exact'] for row in item_facts.values())}/12; "
            f"artifact_items={sum(row['artifact_exact'] for row in item_facts.values())}/12; "
            f"calibration_grounded={calibration_grounded}",
        ),
        rule(
            "ar_released_turns_and_one_shot",
            release_one_shot,
            f"native_private_match={private_counts == native_counts}; calibration_exact={calibration_exact}; turn_violations={expected_turn_violations[:4]}; hidden_access={hidden_access[:3]}",
        ),
    ]

    # Complete conversation/tool trajectory rule.
    started = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_started" and isinstance(row.get("turn_index"), int)}
    finished = {row.get("turn_index") for row in events if row.get("event_type") == "conversation_turn_finished" and row.get("status") == "finished" and isinstance(row.get("turn_index"), int)}
    run_turns = {row.get("conversation_turn") for row in events if row.get("event_type") == "run_started" and isinstance(row.get("conversation_turn"), int)}
    successful_run_turns = {
        row.get("conversation_turn")
        for row in events
        if row.get("event_type") == "run_finished" and row.get("exit_code") == 0 and row.get("trace_complete") is True and isinstance(row.get("conversation_turn"), int)
    }
    assistant_turns = {row.get("conversation_turn") for row in events if row.get("event_type") == "assistant_message" and isinstance(row.get("conversation_turn"), int)}
    conversation_done = any(row.get("event_type") == "conversation_finished" and row.get("status") == "finished" and row.get("turns_completed") == 10 for row in events)
    expected_turns = set(range(1, 11))
    task_results_intact = all(row["event"].get("tool_result") is not None and bool((row["event"].get("tool_call") or {}).get("tool_call_id")) for row in native)
    trace_complete = (
        started == expected_turns
        and finished == expected_turns
        and run_turns == expected_turns
        and successful_run_turns == expected_turns
        and assistant_turns == expected_turns
        and conversation_done
        and task_results_intact
        and not any(row.get("event_type") == "run_failed" for row in events)
    )

    provider_snapshots = provider_tool_snapshots(run_dir) if run_value else []
    snapshot_reports: list[dict[str, Any]] = []
    for snapshot in provider_snapshots:
        definitions = snapshot.get("tools") if isinstance(snapshot.get("tools"), list) else []
        complete_definitions = all(
            isinstance(row, dict)
            and isinstance(row.get("description"), str)
            and bool(row["description"].strip())
            and isinstance(row.get("input_schema"), dict)
            and row["input_schema"].get("type") == "object"
            for row in definitions
        )
        complete = (
            snapshot.get("request_body_valid") is True
            and snapshot.get("declared_task_tool_count") == len(expected_names)
            and snapshot.get("observed_task_tool_count") == len(expected_names)
            and snapshot.get("present_names") == expected_names
            and snapshot.get("missing_names") == []
            and snapshot.get("duplicate_names") == []
            and len(definitions) == len(expected_names)
            and complete_definitions
        )
        snapshot_reports.append(
            {
                "observed": snapshot.get("observed_task_tool_count"),
                "provider_total": snapshot.get("provider_tool_count"),
                "complete": complete,
            }
        )
    surface_complete = bool(snapshot_reports) and all(row["complete"] for row in snapshot_reports)
    native_roundtrip = bool(records) and len(native) == len(records) and private_counts == native_counts
    bounded_trace = trace_complete and bool(provider_snapshots) and len(provider_snapshots) <= 25
    harness_rules = [
        rule(
            "hr_complete_bounded_trajectory",
            bounded_trace,
            f"trace_complete={trace_complete}; provider_requests={len(provider_snapshots)}/25; started={sorted(started)}; finished={sorted(finished)}; run_finished={sorted(successful_run_turns)}; assistant={sorted(assistant_turns)}; task_results_intact={task_results_intact}",
        ),
        rule(
            "hr_all_fifty_tool_definitions_visible",
            surface_complete,
            f"declared={len(expected_names)}; provider_snapshots={len(snapshot_reports)}; coverage={[row['observed'] for row in snapshot_reports]}; complete_definitions={surface_complete}",
        ),
        rule(
            "hr_native_call_result_roundtrip",
            native_roundtrip,
            f"private_calls={len(records)}; native_task_calls={len(native)}; exact_multiset={private_counts == native_counts}",
        ),
    ]

    # Rubric evidence stays compact and appears immediately after the rubric id.
    for rubric_id, selected_items in (
        ("ar_audit_note_grounding_first_half", item_order[:6]),
        ("ar_audit_note_grounding_second_half", item_order[6:]),
    ):
        note_parts: list[str] = []
        for item in selected_items:
            expected = str((scenario_items.get(item) or {}).get("brief") or "unavailable")
            observed = note_map.get(item, ["", "unavailable"])[1] if len(note_map.get(item, [])) == 5 else "unavailable"
            note_parts.append(f"{item} authoritative={expected} output_cell={observed}")
        write_packet(
            args.workspace,
            rubric_id,
            " ; ".join(note_parts),
            {
                "output_cells_available": sum(len(note_map.get(item, [])) == 5 for item in selected_items),
                "expected_cells": len(selected_items),
            },
        )

    answers = assistant_answers(events)
    recall_expectations = (
        truth.get("recall_expectations", {}) if isinstance(truth, dict) else {}
    )
    recall_packet: dict[str, Any] = {}
    for turn in RECALL_TURNS:
        key = f"turn_{turn}"
        expectation = recall_expectations.get(key, {})
        if not isinstance(expectation, Mapping):
            expectation = {}
        answer = answers.get(turn)
        task_calls = [row["name"] for row in native if row.get("turn") == turn]
        required_tokens = recall_required_tokens(turn, expectation)
        recall_packet[key] = {
            "authoritative_expectation": expectation,
            "assistant_response": answer,
            "task_tool_calls": task_calls,
            "required_tokens_present": bool(answer)
            and bool(required_tokens)
            and all(token and token in answer for token in required_tokens),
        }
    write_packet(
        args.workspace,
        "ar_cross_turn_recall_is_exact",
        json.dumps(recall_packet, ensure_ascii=False, separators=(",", ":")),
        {
            "recall_turns": list(RECALL_TURNS),
            "answers_available": [turn for turn in RECALL_TURNS if answers.get(turn)],
            "recall_turn_task_tool_call_count": sum(
                len(row["task_tool_calls"]) for row in recall_packet.values()
            ),
        },
    )

    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {"harness": harness_rules, "agent": agent_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
