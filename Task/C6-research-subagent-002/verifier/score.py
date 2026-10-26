#!/usr/bin/env python3
"""Deterministic scoring and compact rubric evidence for D1.

The verifier deliberately separates output rules from native-subagent trace
rules.  Rubric reports contain bounded observations, not pre-decided scores.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from html.parser import HTMLParser
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_subagent_evidence_v2"
FIELDS = {"angle", "value", "quote", "source_url"}
DISPATCH_TOOLS = {"agent", "delegate_task", "sessions_spawn", "spawn_agent"}
CONTINUATION_TOOLS = {
    "followup_task",
    "send_message",
    "sendmessage",
    "sessions_send",
}
HISTORY_TOOLS = {"sessions_history", "read_thread", "read_agent"}
READ_MARKERS = ("subagent_", "_data.json", "_verify.json", ".output", "tasks/")
EMULATION = re.compile(
    r"(?:^|[;&|\s])(?:codex\s+exec|claude(?:\s+-p|\s+--print)|hermes\s+run)",
    re.IGNORECASE,
)
FACT_ID = re.compile(r"\bA([1-5])-[1-5]\b")


class DuplicateKeyError(ValueError):
    pass


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    if value is None:
        return []
    return [str(value)]


def compact(value: Any, limit: int = 420) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def normalized(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def load_delivery(path: Path) -> tuple[Any, str | None]:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw, object_pairs_hook=no_duplicate_object), None
    except FileNotFoundError:
        return None, "output/research.json is missing"
    except UnicodeError:
        return None, "output/research.json is not valid UTF-8"
    except DuplicateKeyError as exc:
        return None, f"duplicate JSON object key: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"output/research.json is unreadable or invalid JSON: {exc}"


def truth_rows() -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    truth = load_json(HERE / "truth.json", {})
    rows: list[dict[str, Any]] = []
    urls: dict[str, str] = {}
    sources: dict[str, str] = {}
    for angle in truth.get("angles", []) if isinstance(truth, dict) else []:
        if not isinstance(angle, dict):
            continue
        angle_id = str(angle.get("id", ""))
        urls[angle_id] = str(angle.get("url", ""))
        sources[angle_id] = str(angle.get("source", ""))
        for fact in angle.get("facts", []):
            if isinstance(fact, dict):
                rows.append({"angle": angle_id, **fact})
    return rows, urls, sources


def source_variants(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    variants = {normalized(raw)}
    plain_markup = re.sub(r"[`*_#]", "", raw)
    variants.add(normalized(plain_markup))
    if "<html" in raw.casefold() or "<!doctype html" in raw.casefold():
        parser = VisibleText()
        try:
            parser.feed(raw)
        except Exception:
            pass
        variants.add(normalized(" ".join(parser.parts)))
    return [item for item in variants if item]


def fact_subject(fid: str, question: str) -> str:
    if fid.startswith(("A1-", "A2-")):
        match = re.search(r"`([^`]+)`", question)
    elif fid.startswith("A3-"):
        match = re.search(r"Requests\s+([0-9.]+)", question)
    elif fid.startswith("A4-"):
        match = re.search(r"Flask\s+([0-9.]+)", question)
    else:
        match = re.search(r"PEP\s+(\d+)", question)
    return match.group(1) if match else fid


def equivalent_value(delivered: str, expected: str) -> bool:
    left = normalized(delivered)
    right = normalized(expected)
    if left == right:
        return True
    if left.startswith(right + " ("):
        return True
    if re.fullmatch(r"\d+\.\d+", expected) and left == f"python {right}":
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected):
        for pattern in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(delivered.strip(), pattern).date().isoformat() == expected
            except ValueError:
                continue
    return False


def write_report(workspace: Path, rubric_id: str, payload: dict[str, Any]) -> None:
    root = workspace / ".harnessbench_verifier"
    if root.is_symlink():
        raise RuntimeError("verifier evidence directory is a symlink")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rubric_id}.json"
    if path.is_symlink():
        raise RuntimeError("verifier evidence file is a symlink")
    path.write_text(
        json.dumps(
            {"schema_version": SCHEMA, "rubric_id": rubric_id, **payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def agent_assessment(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, urls, sources = truth_rows()
    expected_ids = {str(row.get("fid")) for row in rows}
    output_root = workspace / "output"
    files = []
    if output_root.is_dir() and not output_root.is_symlink():
        files = sorted(
            path.relative_to(output_root).as_posix()
            for path in output_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )
    research = output_root / "research.json"
    delivery, parse_error = load_delivery(research)
    contract_errors: list[str] = []
    if files != ["research.json"]:
        contract_errors.append(f"output files were {files!r}, expected only research.json")
    if research.is_symlink() or not research.is_file():
        contract_errors.append("research.json is not a regular file")
    if parse_error:
        contract_errors.append(parse_error)
    if not isinstance(delivery, dict):
        contract_errors.append("top-level JSON value is not an object")
        delivered: dict[str, Any] = {}
    else:
        delivered = delivery
        actual_ids = set(delivered)
        if actual_ids != expected_ids:
            contract_errors.append(
                f"fact IDs differ: missing={sorted(expected_ids-actual_ids)}, extra={sorted(actual_ids-expected_ids)}"
            )
    for fid in sorted(expected_ids):
        item = delivered.get(fid)
        if not isinstance(item, dict):
            contract_errors.append(f"{fid} is not an object")
            continue
        if set(item) != FIELDS:
            contract_errors.append(f"{fid} fields are {sorted(item)}, expected {sorted(FIELDS)}")
        if any(not isinstance(item.get(field), str) for field in FIELDS):
            contract_errors.append(f"{fid} does not have four string fields")
        if item.get("angle") != fid.split("-", 1)[0]:
            contract_errors.append(f"{fid} has incorrect angle")

    docs = {
        name: source_variants(HERE / "docs" / f"{source}.txt")
        for name, source in sources.items()
    }
    value_failures: list[dict[str, str]] = []
    quote_failures: list[dict[str, Any]] = []
    support_rows: list[dict[str, Any]] = []
    for row in rows:
        fid = str(row.get("fid"))
        angle = str(row.get("angle"))
        item = delivered.get(fid) if isinstance(delivered.get(fid), dict) else {}
        value = item.get("value") if isinstance(item.get("value"), str) else ""
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        url = item.get("source_url") if isinstance(item.get("source_url"), str) else ""
        expected_value = str(row.get("answer", ""))
        if not equivalent_value(value, expected_value):
            value_failures.append(
                {"fid": fid, "delivered": compact(value, 140), "expected": expected_value}
            )
        quote_norm = normalized(quote)
        verbatim = bool(quote_norm) and any(quote_norm in source for source in docs.get(angle, []))
        url_ok = url == urls.get(angle, "")
        if not verbatim or not url_ok:
            quote_failures.append(
                {
                    "fid": fid,
                    "quote": compact(quote),
                    "verbatim_in_pinned_raw_or_rendered_text": verbatim,
                    "source_url": compact(url),
                    "expected_url": urls.get(angle, ""),
                    "canonical_url_exact": url_ok,
                }
            )
        subject = fact_subject(fid, str(row.get("q", "")))
        support_rows.append(
            {
                "fid": fid,
                "subject": subject,
                "value": compact(value, 160),
                "quote": compact(quote, 360),
                "quote_mentions_subject": normalized(subject) in quote_norm,
                "quote_contains_reported_value": bool(normalized(value))
                and normalized(value) in quote_norm,
                "verbatim_in_pinned_source": verbatim,
                "canonical_url_exact": url_ok,
            }
        )

    write_report(
        workspace,
        "au_quotes_directly_support_each_fact",
        {
            "research_json_available": isinstance(delivery, dict),
            "fact_count": len(support_rows),
            "columns": [
                "fid",
                "subject",
                "reported_value",
                "quote",
                "mentions_subject",
                "contains_reported_value",
                "verbatim_in_pinned_source",
            ],
            "fact_support_observations": [
                [
                    row["fid"],
                    row["subject"],
                    row["value"],
                    row["quote"],
                    int(row["quote_mentions_subject"]),
                    int(row["quote_contains_reported_value"]),
                    int(row["verbatim_in_pinned_source"]),
                ]
                for row in support_rows
            ],
            "instruction": "Judge direct semantic support; unavailable evidence must score 0.",
        },
    )
    rules = [
        rule_result(
            "ar_exact_single_json_contract",
            not contract_errors,
            "exact single-file JSON contract satisfied"
            if not contract_errors
            else compact("; ".join(contract_errors), 600),
        ),
        rule_result(
            "ar_all_25_values_correct",
            len(value_failures) == 0 and len(rows) == 25,
            "all 25 values match pinned answers"
            if not value_failures and len(rows) == 25
            else f"{len(value_failures)} value mismatch(es): {compact(value_failures, 500)}",
        ),
        rule_result(
            "ar_all_quotes_verbatim_and_urls_canonical",
            len(quote_failures) == 0 and len(rows) == 25,
            "all 25 quotes and canonical URLs verified"
            if not quote_failures and len(rows) == 25
            else f"{len(quote_failures)} quote/URL failure(s): {compact(quote_failures, 500)}",
        ),
    ]
    return rules, {
        "contract_errors": contract_errors,
        "value_failures": value_failures,
        "quote_failures": quote_failures,
        "delivery_available": isinstance(delivery, dict),
    }


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    for root in (
        run_dir / "artifacts" / "trace",
        run_dir / "artifacts" / "container" / "trace",
    ):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("run-*/events.json")):
            payload = load_json(path, [])
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                candidates.append(payload)
    return max(candidates, key=len, default=[])


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    name = call.get("tool_name") or call.get("name") or ""
    return str(name).strip().casefold().replace("-", "_")


def tool_arguments(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return "\n".join(leaves(call.get("arguments")))


def result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    return "\n".join(leaves(result.get("content"))) if isinstance(result, dict) else ""


def tool_success(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    if result.get("exit_code") not in (None, 0, "0"):
        return False
    text = result_text(event).casefold().strip()
    return not (
        text.startswith("error:")
        or text.startswith('{"error"')
        or '"status": "error"' in text
        or '"status":"error"' in text
    )


def event_turn(event: dict[str, Any]) -> int:
    value = event.get("conversation_turn", event.get("turn_index", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def assigned_angles(text: str) -> list[str]:
    return sorted({f"A{value}" for value in re.findall(r"\bA([1-5])(?:-[1-5])?\b", text)})


def child_identifiers(text: str) -> list[str]:
    patterns = (
        r'["\']?childSessionKey["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'["\']?agentId["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'["\']?agent_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'["\']?task_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'["\']?child_id["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return sorted(set(found))


def is_child_event(event: dict[str, Any]) -> bool:
    raw = event.get("raw_event")
    serialized = json.dumps(raw, ensure_ascii=False).casefold() if raw is not None else ""
    return (
        '"issidechain": true' in serialized
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', serialized))
        or '"agent_role": "child"' in serialized
    )


def event_observation(event: dict[str, Any], text: str | None = None) -> dict[str, Any]:
    body = text if text is not None else "\n".join(
        [tool_arguments(event), result_text(event), *leaves(event.get("content"))]
    )
    return {
        "seq": event.get("seq"),
        "turn": event_turn(event),
        "event_type": event.get("event_type"),
        "tool": tool_name(event),
        "text_size": len(body),
        "snippet": compact(body),
    }


def harness_assessment(
    workspace: Path, events: list[dict[str, Any]], agent: dict[str, Any]
) -> list[dict[str, Any]]:
    event_types = [event.get("event_type") for event in events]
    turn_starts = sorted(
        event_turn(event)
        for event in events
        if event.get("event_type") == "conversation_turn_started"
    )
    turn_finishes = sorted(
        event_turn(event)
        for event in events
        if event.get("event_type") == "conversation_turn_finished"
    )
    lifecycle_ok = (
        event_types.count("conversation_started") == 1
        and turn_starts == [1, 2, 3]
        and turn_finishes == [1, 2, 3]
        and event_types.count("conversation_finished") == 1
        and not any(event.get("event_type") == "run_failed" for event in events)
        and any(
            event.get("event_type") == "run_finished"
            and event.get("trace_complete") is True
            for event in events
        )
    )

    tools = [event for event in events if event.get("event_type") == "tool"]
    dispatch_attempts = [event for event in tools if tool_name(event) in DISPATCH_TOOLS]
    dispatch_rows: list[dict[str, Any]] = []
    id_to_angle: dict[str, str] = {}
    for event in dispatch_attempts:
        arguments = tool_arguments(event)
        identifiers = child_identifiers(result_text(event))
        angles = assigned_angles(arguments)
        row = {
            **event_observation(event, arguments),
            "successful": tool_success(event),
            "assigned_angles": angles,
            "child_identifiers": identifiers,
        }
        dispatch_rows.append(row)
        if tool_success(event) and len(angles) == 1:
            for identifier in identifiers:
                id_to_angle[identifier] = angles[0]
    emulated = [
        event_observation(event, tool_arguments(event))
        for event in tools
        if EMULATION.search(tool_arguments(event))
    ]
    exact_dispatch = (
        len(dispatch_rows) == 5
        and not emulated
        and all(
            row["turn"] == 1
            and row["successful"]
            and len(row["assigned_angles"]) == 1
            for row in dispatch_rows
        )
        and sorted(row["assigned_angles"][0] for row in dispatch_rows)
        == ["A1", "A2", "A3", "A4", "A5"]
    )

    continuation_attempts = [
        event
        for event in tools
        if tool_name(event) in CONTINUATION_TOOLS
        or (tool_name(event) == "agent" and "resume" in normalized(tool_arguments(event)))
    ]
    continuation_rows: list[dict[str, Any]] = []
    valid_continuations = 0
    for event in continuation_attempts:
        arguments = tool_arguments(event)
        target_ids = [identifier for identifier in id_to_angle if identifier in arguments]
        facts = sorted({match.group(0) for match in FACT_ID.finditer(arguments)})
        target_angle = id_to_angle.get(target_ids[0], "") if len(target_ids) == 1 else ""
        valid = (
            event_turn(event) == 2
            and tool_success(event)
            and len(target_ids) == 1
            and len(facts) == 1
            and facts[0].startswith(target_angle + "-")
        )
        valid_continuations += int(valid)
        continuation_rows.append(
            {
                **event_observation(event, arguments),
                "successful": tool_success(event),
                "matched_original_child_ids": target_ids,
                "fact_ids": facts,
                "target_angle": target_angle,
                "valid_same_child_one_fact_reverification": valid,
            }
        )
    same_child = len(continuation_rows) == 1 and valid_continuations == 1

    history_recovery = [
        event_observation(event)
        for event in tools
        if tool_name(event) in HISTORY_TOOLS
    ]
    child_file_recovery = [
        event_observation(event, tool_arguments(event))
        for event in tools
        if any(marker in normalized(tool_arguments(event)) for marker in READ_MARKERS)
        and any(token in normalized(tool_arguments(event)) for token in ("read", "cat", "tail", "open"))
    ]
    _, canonical_urls, _ = truth_rows()
    parent_source_calls = [
        event_observation(event, tool_arguments(event))
        for event in tools
        if not is_child_event(event)
        and tool_name(event) not in DISPATCH_TOOLS | CONTINUATION_TOOLS
        and (
            any(url and url in tool_arguments(event) for url in canonical_urls.values())
            or tool_name(event) in {"web_search", "websearch", "search_query"}
        )
    ]

    handoffs: list[dict[str, Any]] = []
    update_handoffs: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") not in {"tool", "assistant_message", "raw_stdout"}:
            continue
        text = "\n".join(
            [result_text(event), *leaves(event.get("content")), *leaves(event.get("text"))]
        )
        facts = sorted({match.group(0) for match in FACT_ID.finditer(text)})
        angle_counts = {
            angle: sum(fid.startswith(angle + "-") for fid in facts)
            for angle in ("A1", "A2", "A3", "A4", "A5")
        }
        substantial_angles = [angle for angle, count in angle_counts.items() if count >= 4]
        if substantial_angles and event.get("event_type") != "raw_stdout":
            handoffs.append(
                {
                    **event_observation(event, text),
                    "angles_with_at_least_four_fact_ids": substantial_angles,
                    "fact_ids": facts,
                }
            )
        if event_turn(event) >= 2 and len(facts) == 1 and (
            "verif" in normalized(text) or tool_name(event) in CONTINUATION_TOOLS
        ):
            update_handoffs.append(
                {**event_observation(event, text), "fact_ids": facts}
            )

    output_writes = [
        event_observation(event, tool_arguments(event))
        for event in tools
        if "output/research.json" in tool_arguments(event)
        or "/workspace/output/research.json" in tool_arguments(event)
    ]
    write_seqs = [row["seq"] for row in output_writes if isinstance(row.get("seq"), int)]
    first_write = min(write_seqs) if write_seqs else None
    prewrite_handoffs = [
        row
        for row in handoffs
        if first_write is None
        or (isinstance(row.get("seq"), int) and row["seq"] < first_write)
    ]

    common = {
        "trace_event_count": len(events),
        "trace_available": bool(events),
        "dispatch_observations": dispatch_rows,
        "continuation_observations": continuation_rows,
    }
    write_report(
        workspace,
        "hu_all_native_handoffs_reach_parent",
        {
            **common,
            "compact_multi_fact_handoff_candidates": handoffs,
            "one_fact_update_handoff_candidates": update_handoffs,
            "history_recovery_calls": history_recovery,
            "child_file_recovery_calls": child_file_recovery,
            "instruction": "Judge native substantive delivery, not launch metadata; missing evidence scores 0.",
        },
    )
    write_report(
        workspace,
        "hu_parent_context_remains_isolated",
        {
            **common,
            "parent_canonical_source_or_search_calls": parent_source_calls,
            "history_recovery_calls": history_recovery,
            "child_file_recovery_calls": child_file_recovery,
            "emulated_subagent_process_calls": emulated,
            "handoff_sizes": [
                {"seq": row["seq"], "turn": row["turn"], "text_size": row["text_size"]}
                for row in handoffs
            ],
            "instruction": "Judge isolation and compactness; missing evidence scores 0.",
        },
    )
    write_report(
        workspace,
        "hu_parent_finalizes_only_from_complete_handoffs",
        {
            **common,
            "compact_handoff_candidates_before_first_output_write": prewrite_handoffs,
            "one_fact_update_handoff_candidates": update_handoffs,
            "output_write_observations": output_writes,
            "first_output_write_seq": first_write,
            "research_json_available_after_run": agent.get("delivery_available", False),
            "parent_canonical_source_or_search_calls": parent_source_calls,
            "history_recovery_calls": history_recovery,
            "child_file_recovery_calls": child_file_recovery,
            "instruction": "Judge completion-before-finalization and merge-only behavior; missing evidence scores 0.",
        },
    )

    return [
        rule_result(
            "hr_three_turn_trace_complete",
            lifecycle_ok,
            (
                f"complete three-turn lifecycle captured in {len(events)} canonical events"
                if lifecycle_ok
                else f"incomplete lifecycle: starts={turn_starts}, finishes={turn_finishes}, events={len(events)}"
            ),
        ),
        rule_result(
            "hr_exactly_five_native_angle_dispatches",
            exact_dispatch,
            (
                "exactly five successful native turn-1 dispatches covered A1-A5"
                if exact_dispatch
                else compact(
                    f"dispatches={dispatch_rows}; emulated_processes={emulated}", 650
                )
            ),
        ),
        rule_result(
            "hr_same_native_child_reverified",
            same_child,
            (
                "turn 2 continued one original child for one same-angle fact"
                if same_child
                else compact(f"continuations={continuation_rows}", 650)
            ),
        ),
    ]


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    agent_rules, agent_observations = agent_assessment(args.workspace)
    run_dir = Path(os.environ.get("HARNESSBENCH_RUN_DIR", ""))
    events = trace_events(run_dir) if str(run_dir) not in {"", "."} else []
    harness_rules = harness_assessment(args.workspace, events, agent_observations)
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {
                    "agent": agent_rules,
                    "harness": harness_rules,
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
