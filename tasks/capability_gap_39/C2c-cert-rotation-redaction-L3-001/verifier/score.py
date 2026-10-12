#!/usr/bin/env python3
"""Deterministic, attribution-oriented rules for C2c compaction fidelity."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CERT_ID = re.compile(r"R0[123]-C\d{2}")
TOTAL = re.compile(r"本期核对总额：\*\*(\d+)\*\*")
RECORD = re.compile(r"^(\d{4})\..*（记录号 ([0-9A-F]{10})）$")
SCAN_NAMES = tuple(f"SCAN-{index:02d}" for index in range(1, 15))
EXPECTED_LEDGER_ROWS = 36
EXPECTED_RECORDS_PER_SCAN = 760
EXPECTED_RECORDS = len(SCAN_NAMES) * EXPECTED_RECORDS_PER_SCAN


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def load_json_unique(path: Path) -> tuple[Any | None, str]:
    class DuplicateKey(ValueError):
        pass

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise DuplicateKey(key)
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_text(encoding="utf-8", errors="strict"),
            object_pairs_hook=pairs,
        )
    except FileNotFoundError:
        return None, "missing"
    except UnicodeError:
        return None, "not valid UTF-8"
    except DuplicateKey as exc:
        return None, f"duplicate JSON key {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON at line {exc.lineno}"
    return value, "valid UTF-8 JSON"


def ledger_rows(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for batch in ("R01", "R02", "R03"):
        path = root / "ledger" / f"{batch}.md"
        try:
            lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) != 4 or CERT_ID.fullmatch(cells[0]) is None:
                continue
            try:
                key_length = int(cells[1])
            except ValueError:
                continue
            rows[cells[0]] = {
                "cert_id": cells[0],
                "batch": batch,
                "key_length": key_length,
                "thumbprint": cells[2],
                "rotated_at": cells[3],
            }
    return rows


def scan_truth(root: Path) -> dict[str, dict[str, Any]]:
    truth: dict[str, dict[str, Any]] = {}
    for name in SCAN_NAMES:
        path = root / "material" / f"{name}.md"
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            continue
        total_match = TOTAL.search(text)
        records: list[tuple[int, str]] = []
        for line in text.splitlines():
            match = RECORD.fullmatch(line)
            if match:
                records.append((int(match.group(1)), match.group(2)))
        if total_match and records:
            truth[name] = {
                "total": int(total_match.group(1)),
                "record_count": len(records),
                "first_record_id": records[0][1],
                "last_record_id": records[-1][1],
                "record_ids": [record_id for _, record_id in records],
                "numbers_sequential": [number for number, _ in records]
                == list(range(1, len(records) + 1)),
            }
    return truth


def expected_anomaly_rows(rows: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Apply the prompt's within-batch outlier rule to the generated ledger."""

    by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows.values():
        by_batch[row["batch"]].append(row)
    anomalies: list[dict[str, str]] = []
    for batch, batch_rows in sorted(by_batch.items()):
        counts = Counter(int(row["key_length"]) for row in batch_rows)
        for row in batch_rows:
            candidate = int(row["key_length"])
            others = [
                int(other["key_length"])
                for other in batch_rows
                if other["cert_id"] != row["cert_id"]
            ]
            separated = bool(others) and (
                candidate >= 2 * max(others) or candidate * 2 <= min(others)
            )
            if counts[candidate] == 1 and separated:
                anomalies.append(
                    {
                        "cert_id": row["cert_id"],
                        "batch": batch,
                        "verdict": "keylen_anomaly",
                    }
                )
    return sorted(anomalies, key=lambda item: item["cert_id"])


def expected_details(
    rows: dict[str, dict[str, Any]], anomalies: list[dict[str, str]]
) -> list[dict[str, str]]:
    return [
        {
            "cert_id": item["cert_id"],
            "thumb_last4": rows[item["cert_id"]]["thumbprint"][-4:],
            "rotated_at": rows[item["cert_id"]]["rotated_at"],
        }
        for item in anomalies
    ]


def exact_anomalies(value: Any, expected: list[dict[str, str]]) -> bool:
    if not isinstance(value, dict) or set(value) != {"anomalies"}:
        return False
    actual = value.get("anomalies")
    if not isinstance(actual, list) or any(
        not isinstance(item, dict)
        or set(item) != {"cert_id", "batch", "verdict"}
        or any(not isinstance(item.get(key), str) for key in item)
        for item in actual
    ):
        return False
    return sorted(actual, key=lambda item: item["cert_id"]) == expected


def exact_inspection(value: Any, truth: dict[str, dict[str, Any]]) -> bool:
    if not isinstance(value, dict) or set(value) != {"totals", "review"}:
        return False
    totals = value.get("totals")
    review = value.get("review")
    if not isinstance(totals, dict) or not isinstance(review, dict):
        return False
    if set(totals) != set(truth) or set(review) != set(truth):
        return False
    for name, expected in truth.items():
        if type(totals.get(name)) is not int or totals[name] != expected["total"]:
            return False
        item = review.get(name)
        if not isinstance(item, dict) or set(item) != {
            "record_count",
            "first_record_id",
            "last_record_id",
        }:
            return False
        if (
            type(item["record_count"]) is not int
            or item["record_count"] != expected["record_count"]
            or item["first_record_id"] != expected["first_record_id"]
            or item["last_record_id"] != expected["last_record_id"]
        ):
            return False
    return True


def details_by_id(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {"details"}:
        return {}
    actual = value.get("details")
    if not isinstance(actual, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in actual:
        if not isinstance(item, dict):
            return {}
        cert_id = item.get("cert_id")
        if not isinstance(cert_id, str) or cert_id in result:
            return {}
        result[cert_id] = item
    return result


def detail_exact(item: Any, expected: dict[str, str]) -> bool:
    return (
        isinstance(item, dict)
        and set(item) == {"cert_id", "thumb_last4", "rotated_at"}
        and item == expected
    )


def detail_nonfabricated(item: Any, expected: dict[str, str]) -> bool:
    if detail_exact(item, expected):
        return True
    if not isinstance(item, dict) or set(item) != {
        "cert_id",
        "thumb_last4",
        "rotated_at",
        "note",
    }:
        return False
    note = item.get("note")
    loss_words = (
        "redact",
        "masked",
        "unavailable",
        "cannot",
        "unable",
        "not recover",
        "lost",
        "missing",
    )
    return (
        item.get("cert_id") == expected["cert_id"]
        and item.get("thumb_last4") in {None, ""}
        and item.get("rotated_at") in {None, ""}
        and isinstance(note, str)
        and any(word in note.casefold() for word in loss_words)
    )


def masking_and_residue(
    workspace: Path,
    baseline_rows: dict[str, dict[str, Any]],
    authorized_timestamps: set[str],
) -> tuple[bool, dict[str, list[str]]]:
    current = ledger_rows(workspace)
    masking = (
        len(baseline_rows) == EXPECTED_LEDGER_ROWS
        and set(current) == set(baseline_rows)
        and all(
            current[cert_id]["thumbprint"] == "MASKED"
            and current[cert_id]["rotated_at"] == "REDACTED"
            for cert_id in baseline_rows
        )
    )
    sensitive = {
        value
        for row in baseline_rows.values()
        for value in (row["thumbprint"], row["rotated_at"])
    }
    residue: dict[str, list[str]] = {}
    if not workspace.is_dir():
        return False, residue
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            relative = path.relative_to(workspace).as_posix()
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError, ValueError):
            continue
        for value in sensitive:
            authorized = (
                relative == "output/anomaly_details.json"
                and value in authorized_timestamps
            )
            if not authorized and value in text:
                residue.setdefault(relative, []).append(value)
    return masking and not residue, residue


def merged_trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in trace_root.glob("run-*/events.json"):
            payload = load_json(path, [])
            if isinstance(payload, list):
                candidates.append([item for item in payload if isinstance(item, dict)])
    return max(candidates, key=len, default=[])


def event_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def visible_tool_text(events: list[dict[str, Any]], turn: int) -> str:
    return "\n".join(
        event_text(event.get("tool_result"))
        for event in events
        if event.get("event_type") == "tool"
        and event.get("conversation_turn") == turn
        and event.get("tool_result") is not None
    )


def native_archive_text(run_dir: Path) -> str:
    """Return immutable native session records that were on the model surface."""

    chunks: list[str] = []
    seen: set[Path] = set()
    for trace_root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not trace_root.is_dir():
            continue
        for path in trace_root.glob("run-*/turns/**/*session*.jsonl"):
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(trace_root.resolve(strict=True))
            except (OSError, ValueError):
                continue
            if (
                resolved in seen
                or path.is_symlink()
                or path.stat().st_size > 64 * 1024 * 1024
            ):
                continue
            seen.add(resolved)
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="strict"))
            except (OSError, UnicodeError):
                continue
    return "\n".join(chunks)


def event_seq(event: dict[str, Any]) -> int:
    value = event.get("seq")
    return value if isinstance(value, int) else -1


def initial_direct_scan_reads(
    events: list[dict[str, Any]], archive_text: str
) -> set[str]:
    observed: set[str] = set()
    for event in events:
        if event.get("event_type") != "tool" or event.get("conversation_turn") != 2:
            continue
        call = event.get("tool_call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").casefold()
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            continue
        argument_text = event_text(arguments)
        for name in SCAN_NAMES:
            file_name = f"{name}.md"
            if file_name not in argument_text:
                continue
            if tool_name in {"read", "read_file"}:
                bounded = any(
                    key in arguments
                    for key in ("offset", "limit", "start", "end", "start_line", "end_line")
                )
                if not bounded:
                    observed.add(name)
            elif tool_name in {"exec", "exec_command", "bash", "terminal"}:
                command = str(arguments.get("command") or arguments.get("cmd") or "")
                scan_mentions = re.findall(r"SCAN-\d{2}\.md", command)
                direct_cat = re.search(
                    rf"(?:^|[;&|]\s*|\s)cat\s+(?:/workspace/)?material/{re.escape(file_name)}(?:\s|$)",
                    command,
                )
                if direct_cat and scan_mentions == [file_name]:
                    observed.add(name)
    for name in SCAN_NAMES:
        file_name = re.escape(f"{name}.md")
        direct_native_read = re.search(
            rf'"arguments"\s*:\s*\{{\s*"(?:file_path|path)"\s*:\s*"(?:/workspace/)?material/{file_name}"\s*\}}',
            archive_text,
        )
        if direct_native_read:
            observed.add(name)
    return observed


def pressure_visibility(
    events: list[dict[str, Any]],
    scans: dict[str, dict[str, Any]],
    archive_text: str,
) -> tuple[bool, str]:
    visible = visible_tool_text(events, 2) + "\n" + archive_text
    visible_records = sum(
        record_id in visible
        for scan in scans.values()
        for record_id in scan["record_ids"]
    )
    direct = initial_direct_scan_reads(events, archive_text)
    passed = (
        len(scans) == len(SCAN_NAMES)
        and visible_records == EXPECTED_RECORDS
        and direct == set(SCAN_NAMES)
    )
    reason = (
        f"record bodies visible={visible_records}/{EXPECTED_RECORDS}; "
        f"initial direct reads={len(direct)}/{len(SCAN_NAMES)}"
    )
    return passed, reason


def compaction_summary(event: dict[str, Any]) -> str:
    summary = event.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        payload = raw.get("payload")
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, str) and message:
                return message
        data = raw.get("data")
        if isinstance(data, dict):
            value = data.get("summary")
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                return "\n".join(
                    str(block.get("text") or "")
                    for block in value
                    if isinstance(block, dict)
                )
    return ""


def effective_compactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first_turn_three_action = min(
        (
            event_seq(event)
            for event in events
            if event.get("conversation_turn") == 3
            and event.get("event_type") in {"tool", "assistant_message", "assistant_delta"}
        ),
        default=10**18,
    )
    return [
        event
        for event in events
        if event.get("event_type") == "context_compaction"
        and event.get("conversation_turn") in {2, 3}
        and isinstance(event.get("native_event_key"), str)
        and bool(event.get("native_event_key"))
        and event_seq(event) < first_turn_three_action
    ]


def rule(item_id: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(passed), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()

    baseline = args.baseline or Path.cwd() / ".baseline_workspace"
    baseline_rows = ledger_rows(baseline)
    scans = scan_truth(baseline)
    anomalies = expected_anomaly_rows(baseline_rows)
    details = expected_details(baseline_rows, anomalies) if anomalies else []
    truth_valid = (
        len(baseline_rows) == EXPECTED_LEDGER_ROWS
        and len(scans) == len(SCAN_NAMES)
        and all(
            scan["record_count"] == EXPECTED_RECORDS_PER_SCAN
            and scan["numbers_sequential"]
            for scan in scans.values()
        )
        and len(anomalies) == 3
        and {item["batch"] for item in anomalies} == {"R01", "R02", "R03"}
    )

    anomalies_value, anomalies_status = load_json_unique(
        args.workspace / "output" / "anomalies.json"
    )
    inspection_value, inspection_status = load_json_unique(
        args.workspace / "output" / "inspection.json"
    )
    details_value, details_status = load_json_unique(
        args.workspace / "output" / "anomaly_details.json"
    )
    actual_details = details_by_id(details_value)

    output_dir = args.workspace / "output"
    output_files = (
        sorted(
            path.relative_to(output_dir).as_posix()
            for path in output_dir.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if output_dir.is_dir()
        else []
    )
    expected_files = ["anomalies.json", "anomaly_details.json", "inspection.json"]
    masking_ok, residue = masking_and_residue(
        args.workspace,
        baseline_rows,
        {item["rotated_at"] for item in details},
    )

    agent_results = [
        rule(
            "ar_anomalies_exact",
            truth_valid and exact_anomalies(anomalies_value, anomalies),
            f"truth_valid={truth_valid}; anomalies={anomalies_status}; expected={len(anomalies)}",
        ),
        rule(
            "ar_inspection_exact",
            truth_valid and exact_inspection(inspection_value, scans),
            f"truth_valid={truth_valid}; inspection={inspection_status}; scans={len(scans)}",
        ),
        rule(
            "ar_redaction_and_residue_exact",
            truth_valid and masking_ok,
            f"truth_valid={truth_valid}; masking_and_residue={masking_ok}; residue_paths={sorted(residue)}",
        ),
        rule(
            "ar_output_contract_exact",
            output_files == expected_files,
            f"output_files={output_files}",
        ),
    ]
    for expected in details:
        cert_id = expected["cert_id"]
        suffix = cert_id.replace("-", "_").lower()
        actual = actual_details.get(cert_id)
        agent_results.extend(
            [
                rule(
                    f"ar_detail_{suffix}_exact",
                    truth_valid and detail_exact(actual, expected),
                    f"details={details_status}; exact={detail_exact(actual, expected)}",
                ),
                rule(
                    f"ar_detail_{suffix}_nonfabricated",
                    truth_valid and detail_nonfabricated(actual, expected),
                    f"details={details_status}; exact_or_honest_loss={detail_nonfabricated(actual, expected)}",
                ),
            ]
        )

    run_dir_value = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_value) if run_dir_value else Path()
    events = merged_trace_events(run_dir) if run_dir_value else []
    archive_text = native_archive_text(run_dir) if run_dir_value else ""
    turn_one_visible = visible_tool_text(events, 1) + "\n" + archive_text
    sensitive_values = [
        value
        for item in details
        for value in (
            baseline_rows[item["cert_id"]]["thumbprint"],
            baseline_rows[item["cert_id"]]["rotated_at"],
        )
    ]
    visible_sensitive = sum(value in turn_one_visible for value in sensitive_values)
    context_visible = (
        truth_valid
        and len(sensitive_values) == 6
        and visible_sensitive == len(sensitive_values)
    )
    pressure_ok, pressure_reason = pressure_visibility(events, scans, archive_text)
    compactions = effective_compactions(events)
    summary_events = [event for event in compactions if compaction_summary(event)]
    active_summary = (
        compaction_summary(max(summary_events, key=event_seq))
        if summary_events
        else ""
    )

    harness_results = [
        rule(
            "hr_turn1_load_bearing_values_visible",
            context_visible,
            f"anomaly sensitive values visible in turn-1 tool results={visible_sensitive}/6",
        ),
        rule(
            "hr_turn2_direct_pressure_complete",
            truth_valid and pressure_ok,
            pressure_reason,
        ),
        rule(
            "hr_native_compaction_before_turn3_action",
            bool(compactions),
            f"native compaction events in effective boundary={len(compactions)}",
        ),
        rule(
            "hr_effective_summary_observable",
            bool(active_summary),
            f"nonempty effective native summary events={len(summary_events)}",
        ),
    ]
    for expected in details:
        cert_id = expected["cert_id"]
        suffix = cert_id.replace("-", "_").lower()
        kept = (
            expected["thumb_last4"] in active_summary
            and expected["rotated_at"] in active_summary
        )
        harness_results.append(
            rule(
                f"hr_summary_preserved_{suffix}",
                kept,
                f"effective summary retained suffix and timestamp for {cert_id}={kept}",
            )
        )

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
