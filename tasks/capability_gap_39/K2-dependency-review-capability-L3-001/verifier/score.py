#!/usr/bin/env python3
"""Deterministic rules and bounded aggregation evidence for K2."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_component_aggregation_evidence_v4"
FIELDS = {"verdict", "rule", "evidence"}
EVIDENCE_FIELDS = {"r1", "r2", "r3"}
VENDOR_ID = re.compile(r"(?<![A-Za-z0-9])V(?:0[1-9]|1[0-9]|2[0-3])")
VENDOR_RANGE = re.compile(r"V(\d{2})\s*[-–]\s*V(\d{2})")
VENDOR_FILE = re.compile(r"(?:^|[\s'\"`])(?:/workspace/)?components/[^\s'\"`]+\.md(?:$|[\s'\"`])", re.I)
DISPATCH_TOOLS = {"agent", "delegate_task", "sessions_spawn", "spawn_agent", "subagent"}
HISTORY_TOOLS = {"sessions_history", "read_thread", "read_agent", "task_output"}
READ_TOOLS = {
    "read", "read_file", "open_file", "view", "cat", "exec", "shell", "bash",
    "terminal", "run_command", "run", "command_execution", "exec_command",
}
WRITE_TOOLS = {"write", "write_file", "edit", "apply_patch", "notebook_edit"}
EMULATION = re.compile(
    r"(?:^|[;&|\s'\"])(?:codex\s+(?:exec|e)|claude(?:\s+-p|\s+--print)|hermes\s+run)",
    re.IGNORECASE,
)
GROUPS = (
    frozenset(f"V{i:02d}" for i in range(1, 5)),
    frozenset(f"V{i:02d}" for i in range(5, 9)),
    frozenset(f"V{i:02d}" for i in range(9, 13)),
    frozenset(f"V{i:02d}" for i in range(13, 19)),
    frozenset(f"V{i:02d}" for i in range(19, 24)),
)


class DuplicateKeyError(ValueError):
    pass


def leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in leaves(child)]
    if isinstance(value, list):
        return [text for child in value for text in leaves(child)]
    return [] if value is None else [str(value)]


def compact(value: Any, limit: int = 520) -> str:
    # Keep empty containers visible. The score schema requires a non-empty reason,
    # and `{}` is materially more diagnostic than silently collapsing it to "".
    text = " ".join(str("" if value is None else value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def quote_normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def component_ids(text: str) -> set[str]:
    found = set(VENDOR_ID.findall(text))
    for left, right in VENDOR_RANGE.findall(text):
        start, end = int(left), int(right)
        if 1 <= start <= end <= 23:
            found.update(f"V{value:02d}" for value in range(start, end + 1))
    return found


def group_number(ids: set[str] | frozenset[str]) -> int | None:
    for index, group in enumerate(GROUPS, 1):
        if frozenset(ids) == group:
            return index
    return None


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def parse_json_text(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def load_delivery(path: Path) -> tuple[Any, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_object), None
    except FileNotFoundError:
        return None, "output/review.json is missing"
    except UnicodeError:
        return None, "output/review.json is not valid UTF-8"
    except DuplicateKeyError as exc:
        return None, f"duplicate JSON key: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"output/review.json is unreadable or invalid JSON: {exc}"


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


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def source_for_component(workspace: Path, component_id: str) -> Path | None:
    matches = sorted((workspace / "components").glob(f"{component_id}_*.md"))
    return matches[0] if len(matches) == 1 else None


def quote_is_verbatim(source: str, quote: str) -> bool:
    quote_norm = quote_normalized(quote)
    return bool(quote_norm) and quote_norm in quote_normalized(source)


def agent_assessment(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    truth = load_json(HERE / "truth.json", {})
    truth = truth if isinstance(truth, dict) else {}
    expected = set(truth)
    output_root = workspace / "output"
    output_files: list[str] = []
    if output_root.is_dir() and not output_root.is_symlink():
        output_files = sorted(
            path.relative_to(output_root).as_posix()
            for path in output_root.rglob("*")
            if path.is_file() or path.is_symlink()
        )

    path = output_root / "review.json"
    raw, parse_error = load_delivery(path)
    delivered = raw if isinstance(raw, dict) else {}
    contract_errors: list[str] = []
    if output_files != ["review.json"]:
        contract_errors.append(f"output files were {output_files!r}; expected only review.json")
    if path.is_symlink() or not path.is_file():
        contract_errors.append("review.json is not a regular file")
    if parse_error:
        contract_errors.append(parse_error)
    if not isinstance(raw, dict):
        contract_errors.append("top-level JSON value is not an object")
    elif set(raw) != expected:
        contract_errors.append(
            f"component IDs differ: missing={sorted(expected-set(raw))}, extra={sorted(set(raw)-expected)}"
        )

    decision_errors: list[dict[str, Any]] = []
    quote_errors: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for component_id in sorted(expected):
        item = delivered.get(component_id)
        target = truth[component_id]
        if not isinstance(item, dict):
            contract_errors.append(f"{component_id} is not an object")
            item = {}
        elif set(item) != FIELDS:
            contract_errors.append(
                f"{component_id} fields are {sorted(item)}; expected {sorted(FIELDS)}"
            )

        verdict = item.get("verdict")
        rule = item.get("rule")
        evidence = item.get("evidence")
        if verdict not in {"pass", "fail"}:
            contract_errors.append(f"{component_id}.verdict is not pass or fail")
        if verdict == "pass" and rule is not None:
            contract_errors.append(f"{component_id}.rule is not null for pass")
        if verdict == "fail" and (type(rule) is not int or rule not in {1, 2, 3}):
            contract_errors.append(f"{component_id}.rule is not integer 1, 2, or 3 for fail")
        if not isinstance(evidence, dict):
            contract_errors.append(f"{component_id}.evidence is not an object")
            evidence = {}
        elif set(evidence) != EVIDENCE_FIELDS:
            contract_errors.append(
                f"{component_id}.evidence fields are {sorted(evidence)}; expected {sorted(EVIDENCE_FIELDS)}"
            )

        if verdict != target.get("verdict") or rule != target.get("rule"):
            decision_errors.append(
                {
                    "component": component_id,
                    "delivered": {"verdict": verdict, "rule": rule},
                    "expected": target,
                }
            )

        source_path = source_for_component(workspace, component_id)
        try:
            source_text = source_path.read_text(encoding="utf-8") if source_path else ""
        except (OSError, UnicodeError):
            source_text = ""
        row: dict[str, Any] = {
            "component": component_id,
            "delivered_verdict": verdict,
            "delivered_rule": rule,
            "expected_verdict": target.get("verdict"),
            "expected_rule": target.get("rule"),
            "checklist": source_path.name if source_path else None,
            "quotes": {},
        }
        for evidence_key in ("r1", "r2", "r3"):
            quote = evidence.get(evidence_key)
            valid_string = (
                isinstance(quote, str)
                and quote == quote.strip()
                and 0 < len(quote) <= 300
            )
            if not valid_string:
                contract_errors.append(
                    f"{component_id}.evidence.{evidence_key} is not a non-empty trimmed string <=300 characters"
                )
                quote = quote if isinstance(quote, str) else ""
            verbatim = quote_is_verbatim(source_text, quote)
            if not verbatim:
                quote_errors.append(
                    {"component": component_id, "rule": evidence_key, "quote": compact(quote, 180)}
                )
            row["quotes"][evidence_key] = {
                "delivered_quote": quote,
                "verbatim_in_same_component_checklist": verbatim,
            }
        evidence_rows.append(row)

    policy_text = (workspace / "docs" / "POLICY.md").read_text(
        encoding="utf-8", errors="replace"
    )
    rubric_anchors = {
        "au_r1_v04_evidence_supports_review": "V04",
        "au_r2_v10_evidence_supports_review": "V10",
        "au_r2_v21_dormant_source_supports_pass": "V21",
    }
    for rubric_id, component_id in rubric_anchors.items():
        write_report(
            workspace,
            rubric_id,
            {
                "review_json_available": isinstance(raw, dict),
                "policy_text": policy_text,
                "required_component_id": component_id,
                "component_evidence_rows": [
                    row for row in evidence_rows if row["component"] == component_id
                ],
                "instruction": (
                    "Judge this source-labelled high-difficulty component. Each r1/r2/r3 quote must be "
                    "relevant to that rule and the three quotes must jointly support the delivered "
                    "decision and pinned expected decision/rule. A missing row or source proof scores 0."
                ),
            },
        )

    contract_ok = not contract_errors and len(expected) == 23
    decisions_ok = not decision_errors and len(expected) == 23
    quotes_ok = not quote_errors and len(evidence_rows) == 23
    rules = [
        rule_result(
            "ar_exact_single_json_contract",
            contract_ok,
            "exact one-file 23-dependency review contract satisfied"
            if contract_ok else compact(contract_errors, 760),
        ),
        rule_result(
            "ar_all_decisions_exact",
            decisions_ok,
            "all 23 verdict/rule pairs match pinned truth"
            if decisions_ok else compact(decision_errors, 760),
        ),
        rule_result(
            "ar_all_evidence_verbatim",
            quotes_ok,
            "all 69 evidence strings are verbatim in their own checklists"
            if quotes_ok else compact(quote_errors, 760),
        ),
    ]
    return rules, {
        "delivery_available": isinstance(raw, dict),
        "delivery": delivered if isinstance(raw, dict) else None,
        "contract_errors": contract_errors,
        "decision_errors": decision_errors,
        "quote_errors": quote_errors,
    }


def trace_events(run_dir: Path) -> list[dict[str, Any]]:
    candidates: list[list[dict[str, Any]]] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
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
    name = str(call.get("tool_name") or call.get("name") or "")
    normalized_name = name.strip().casefold().replace("-", "_")
    # Codex namespaces native collaboration tools as collaboration__spawn_agent.
    # Score the normalized operation, not a harness-specific namespace prefix.
    return normalized_name.rsplit("__", 1)[-1]


def tool_args(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def tool_arguments(event: dict[str, Any]) -> str:
    text = "\n".join(leaves(tool_args(event)))
    if text.strip():
        return text
    raw = event.get("raw_event")
    raw_call = raw.get("tool_call") if isinstance(raw, dict) else None
    item = raw_call.get("item") if isinstance(raw_call, dict) else None
    if isinstance(item, dict):
        return "\n".join(leaves(item.get("prompt")))
    return ""


def result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    text = "\n".join(leaves(result.get("content"))) if isinstance(result, dict) else ""
    if text.strip():
        return text
    raw = event.get("raw_event")
    raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
    item = raw_result.get("item") if isinstance(raw_result, dict) else None
    return json.dumps(item, ensure_ascii=False) if item is not None else ""


def raw_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return json.dumps(raw, ensure_ascii=False) if raw is not None else ""


def event_text(event: dict[str, Any]) -> str:
    return "\n".join(
        [
            result_text(event),
            *leaves(event.get("content")),
            *leaves(event.get("text")),
            raw_text(event),
        ]
    )


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


def is_child_event(event: dict[str, Any]) -> bool:
    serialized = raw_text(event).casefold()
    return (
        str(event.get("principal_role") or "").casefold() == "child"
        or bool(event.get("parent_principal_id"))
        or '"issidechain": true' in serialized
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', serialized))
        or '"agent_role": "child"' in serialized
    )


def observation(event: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "seq": event.get("seq"),
        "event_type": event.get("event_type"),
        "tool": tool_name(event),
        "component_ids": sorted(component_ids(text)),
        "text_size": len(text),
        "snippet": compact(text),
    }


def child_identifiers(text: str) -> list[str]:
    patterns = (
        r'childSessionKey["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'agentId["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'agent_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.I))
    return sorted(set(found))


def event_child_identifiers(event: dict[str, Any]) -> list[str]:
    found = set(child_identifiers(result_text(event)))
    raw = event.get("raw_event")
    raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
    item = raw_result.get("item") if isinstance(raw_result, dict) else None
    if isinstance(item, dict):
        receivers = item.get("receiver_thread_ids")
        if isinstance(receivers, list):
            found.update(str(value) for value in receivers if str(value).strip())
        states = item.get("agents_states")
        if isinstance(states, dict):
            found.update(str(value) for value in states if str(value).strip())
    return sorted(found)


def extract_component_object(text: str, expected_group: frozenset[str]) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        stack = [candidate]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if set(value) == set(expected_group):
                    return value
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)
    return None


def complete_component_payload(payload: Any, group: frozenset[str]) -> bool:
    if not isinstance(payload, dict) or set(payload) != set(group):
        return False
    for component in group:
        item = payload.get(component)
        if not isinstance(item, dict) or set(item) != FIELDS:
            return False
        if item.get("verdict") not in {"pass", "fail"}:
            return False
        verdict = item.get("verdict")
        rule = item.get("rule")
        if verdict == "pass" and rule is not None:
            return False
        if verdict == "fail" and (type(rule) is not int or rule not in {1, 2, 3}):
            return False
        evidence = item.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_FIELDS:
            return False
        if not all(
            isinstance(evidence.get(key), str)
            and evidence.get(key) == evidence.get(key).strip()
            and 0 < len(evidence.get(key)) <= 300
            for key in EVIDENCE_FIELDS
        ):
            return False
    return True


def session_rows_for_dispatch(event: dict[str, Any]) -> list[dict[str, Any]]:
    name = tool_name(event)
    arguments = tool_args(event)
    call = event.get("tool_call") if isinstance(event.get("tool_call"), dict) else {}
    call_id = str(call.get("tool_call_id") or "")
    if name != "delegate_task":
        text = tool_arguments(event)
        ids = component_ids(text)
        row = {
            **observation(event, text),
            "group": group_number(ids),
            "successful": tool_success(event),
            "identity": event_child_identifiers(event),
            "dispatch_call_id": call_id,
            "completion_payload": None,
            "terminal_in_dispatch_result": bool(
                re.search(r"subagent completed|child completed", result_text(event), re.I)
            ),
            "prompt_text": text,
        }
        return [row]

    tasks = arguments.get("tasks") if isinstance(arguments, dict) else None
    result_payload = parse_json_text(result_text(event))
    results = result_payload.get("results") if isinstance(result_payload, dict) else None
    if not isinstance(tasks, list):
        return [{
            **observation(event, tool_arguments(event)),
            "group": None,
            "successful": False,
            "identity": [],
            "dispatch_call_id": call_id,
            "completion_payload": None,
            "terminal_in_dispatch_result": False,
            "prompt_text": tool_arguments(event),
        }]
    results = results if isinstance(results, list) else []
    by_index = {
        item.get("task_index"): item
        for item in results
        if isinstance(item, dict) and type(item.get("task_index")) is int
    }
    rows: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        task_text = "\n".join(leaves(task))
        item = by_index.get(index, {})
        success = isinstance(item, dict) and str(item.get("status", "")).casefold() == "completed"
        summary = str(item.get("summary") or "")
        rows.append(
            {
                **observation(event, task_text),
                "group": group_number(component_ids(task_text)),
                "successful": success,
                "identity": [f"{call_id}:task-{index}"],
                "dispatch_call_id": call_id,
                "completion_payload": summary,
                "terminal_in_dispatch_result": success,
                "prompt_text": task_text,
            }
        )
    return rows


def pure_component_object(text: str, group: frozenset[str]) -> dict[str, Any] | None:
    parsed = parse_json_text(text.strip())
    return parsed if isinstance(parsed, dict) and set(parsed) == set(group) else None


def handoff_row(
    event: dict[str, Any],
    group_index: int,
    identity: list[str],
    payload_text: str,
    channel: str,
) -> dict[str, Any]:
    group = GROUPS[group_index - 1]
    extracted = extract_component_object(payload_text, group)
    pure = pure_component_object(payload_text, group)
    truncated = bool(re.search(r"\btruncat(?:ed|ion)\b|output_limit_exceeded", payload_text, re.I))
    return {
        "seq": event.get("seq"),
        "group": group_index,
        "assigned_component_ids": sorted(group),
        "identity": identity,
        "completion_channel": channel,
        "payload_substantive": bool(
            component_ids(payload_text) & set(group)
            and re.search(r'"(?:verdict|evidence)"', payload_text)
        ),
        "payload_complete_shape": complete_component_payload(extracted, group) and not truncated,
        "payload_pure_json_shape": complete_component_payload(pure, group) and not truncated,
        "extracted_payload": extracted,
        "payload_text": payload_text[:24000],
        "payload_truncated_for_evidence": len(payload_text) > 24000,
        "transport_truncation_marker": truncated,
    }


def completion_text_candidates(event: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    result = event.get("tool_result")
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, str):
        candidates.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                candidates.append(block["text"])
            elif isinstance(block, str):
                candidates.append(block)
    raw = event.get("raw_event")
    raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
    item = raw_result.get("item") if isinstance(raw_result, dict) else None
    if isinstance(item, dict):
        states = item.get("agents_states")
        if isinstance(states, dict):
            for state in states.values():
                if isinstance(state, dict) and isinstance(state.get("message"), str):
                    candidates.append(state["message"])
    return [value for value in candidates if value.strip()]


def first_required_rows(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda value: value.get("seq") or 0):
        group = row.get("group")
        if type(group) is int and group not in result:
            result[group] = row
    return result


def child_prompt_is_scoped(row: dict[str, Any]) -> bool:
    group_index = row.get("group")
    if type(group_index) is not int or not 1 <= group_index <= len(GROUPS):
        return False
    group = GROUPS[group_index - 1]
    text = str(row.get("prompt_text") or "")
    path_ids = set(re.findall(r"components/(V(?:0[1-9]|1[0-9]|2[0-3]))_[^\s,'\"`]+\.md", text, re.I))
    lower = text.casefold()
    has_contract = all(
        token in lower for token in ("r1", "r2", "r3", "verdict", "rule", "evidence")
    )
    forbids_write = bool(re.search(r"(?:do not|must not|never)\s+write|不得写", lower))
    no_component_glob = not re.search(r"components/[^\s,'\"`]*[*?]", text)
    return (
        component_ids(text) == set(group)
        and path_ids == set(group)
        and "docs/policy.md" in lower
        and has_contract
        and forbids_write
        and no_component_glob
    )


def output_write_event(event: dict[str, Any]) -> bool:
    if event.get("event_type") != "tool" or is_child_event(event) or not tool_success(event):
        return False
    text = tool_arguments(event)
    if not re.search(r"(?:^|[\s'\"/])output/review\.json\b", text):
        return False
    if tool_name(event) in WRITE_TOOLS:
        return True
    if tool_name(event) in READ_TOOLS:
        return bool(
            re.search(
                r"(?:>>?|\btee\b|\bwrite_text\b|\bwriteFile\b|\bcp\b|\bmv\b|"
                r"open\([^\n]{0,200}['\"]w[b+]?['\"])",
                text,
                re.I,
            )
        )
    return False


def harness_assessment(
    workspace: Path, events: list[dict[str, Any]], agent: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run_starts = [event for event in events if event.get("event_type") == "run_started"]
    run_ids = {str(event.get("run_id")) for event in events if event.get("run_id")}
    run_finishes = [event for event in events if event.get("event_type") == "run_finished"]
    lifecycle_ok = (
        len(run_starts) >= 1
        and len(run_ids) == 1
        and run_starts[0].get("seq") == min(
            (event.get("seq") for event in events if isinstance(event.get("seq"), int)),
            default=None,
        )
        and not any(event.get("event_type") == "run_failed" for event in events)
        and len(run_finishes) == 1
        and run_finishes[0].get("trace_complete") is True
        and run_finishes[0].get("exit_code") in (None, 0, "0")
    )

    tools = [event for event in events if event.get("event_type") == "tool"]
    dispatch_events = [event for event in tools if tool_name(event) in DISPATCH_TOOLS]
    session_rows: list[dict[str, Any]] = []
    for event in dispatch_events:
        session_rows.extend(session_rows_for_dispatch(event))

    # DeepSeek's native `subagent` result carries the child completion but not the
    # child session id. The normalized trace separately exposes typed lifecycle
    # events with both the principal id and final assistant message. Join those
    # two native surfaces by the exact assigned component group; do not infer an
    # identity merely from call order.
    lifecycle_by_group: dict[int, dict[str, Any]] = {}
    for event in events:
        if (
            event.get("event_type") != "subagent_lifecycle"
            or str(event.get("action") or "").casefold() != "finished"
            or str(event.get("principal_role") or "").casefold() != "child"
        ):
            continue
        message = "\n".join(leaves(event.get("last_assistant_message")))
        group = group_number(component_ids(message))
        identity = str(event.get("principal_id") or "")
        if group is not None and identity:
            lifecycle_by_group[group] = {
                "identity": identity,
                "terminal": (
                    str(event.get("status") or "").casefold() in {"ok", "completed"}
                    and str(event.get("stop_reason") or "").casefold() == "completed"
                ),
            }
    for row in session_rows:
        group = row.get("group")
        lifecycle = lifecycle_by_group.get(group) if type(group) is int else None
        if row.get("tool") == "subagent" and lifecycle:
            row["identity"] = [lifecycle["identity"]]
            row["terminal_in_dispatch_result"] = bool(lifecycle["terminal"])

    required_rows = first_required_rows(session_rows)
    required_issued = len(required_rows)
    required_accepted = sum(bool(row.get("successful")) for row in required_rows.values())
    accepted_required_rows = {
        group: row for group, row in required_rows.items() if row.get("successful")
    }

    emulated = [
        observation(event, tool_arguments(event))
        for event in tools
        if EMULATION.search(tool_arguments(event))
    ]
    successful_sessions = [row for row in session_rows if row.get("successful")]
    actual_groups = [row.get("group") for row in successful_sessions]
    exact_assignments = (
        len(successful_sessions) == 5
        and not emulated
        and sorted(actual_groups) == [1, 2, 3, 4, 5]
        and all(row.get("successful") for row in successful_sessions)
    )

    parent_component_reads = [
        observation(event, tool_arguments(event))
        for event in tools
        if not is_child_event(event)
        and tool_name(event) not in DISPATCH_TOOLS
        and tool_name(event) in READ_TOOLS
        and VENDOR_FILE.search(" " + tool_arguments(event) + " ")
    ]
    history_recovery = [
        observation(event, tool_arguments(event))
        for event in tools
        if not is_child_event(event) and tool_name(event) in HISTORY_TOOLS
    ]
    child_writes = [
        observation(event, tool_arguments(event))
        for event in tools
        if is_child_event(event)
        and (
            tool_name(event) in WRITE_TOOLS
            or (
                tool_name(event) in READ_TOOLS
                and re.search(
                    r"(?:(?:^|[\s;&|])>{1,2}\s*(?!/dev/null)|\btee\b(?!\s+/dev/null)|"
                    r"\btouch\b|\bmkdir\b|\bcp\b|\bmv\b)",
                    tool_arguments(event),
                )
            )
        )
    ]
    isolated = bool(events) and not (
        parent_component_reads or history_recovery or emulated or child_writes
    )

    dispatch_by_call: dict[str, dict[str, Any]] = {}
    identity_to_group: dict[str, int] = {}
    for group, row in accepted_required_rows.items():
        if row.get("dispatch_call_id") and row.get("group"):
            dispatch_by_call[str(row["dispatch_call_id"])] = row
        for identity in row.get("identity") or []:
            identity_to_group[str(identity)] = group

    required_identities = [
        str(identity)
        for row in accepted_required_rows.values()
        for identity in (row.get("identity") or [])
        if str(identity)
    ]
    identities_ok = (
        required_accepted == 5
        and len(required_identities) == 5
        and len(set(required_identities)) == 5
    )

    terminal_identities: set[str] = set()
    for row in accepted_required_rows.values():
        if row.get("terminal_in_dispatch_result"):
            terminal_identities.update(str(value) for value in row.get("identity") or [])
    for event in events:
        raw = event.get("raw_event")
        raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
        item = raw_result.get("item") if isinstance(raw_result, dict) else None
        states = item.get("agents_states") if isinstance(item, dict) else None
        if isinstance(states, dict):
            for identity, state in states.items():
                if (
                    str(identity) in identity_to_group
                    and isinstance(state, dict)
                    and str(state.get("status") or "").casefold() == "completed"
                ):
                    terminal_identities.add(str(identity))
        if isinstance(raw, dict) and raw.get("subtype") == "task_notification":
            if str(raw.get("status") or "").casefold() == "completed":
                task_id = str(raw.get("task_id") or "")
                if task_id in identity_to_group:
                    terminal_identities.add(task_id)
                dispatch = dispatch_by_call.get(str(raw.get("tool_use_id") or ""))
                if dispatch:
                    terminal_identities.update(str(value) for value in dispatch.get("identity") or [])
    terminal_groups = {
        identity_to_group[identity]
        for identity in terminal_identities
        if identity in identity_to_group
    }

    handoffs: list[dict[str, Any]] = []
    for event in events:
        raw = event.get("raw_event")
        raw_result = raw.get("tool_result") if isinstance(raw, dict) else None
        item = raw_result.get("item") if isinstance(raw_result, dict) else None
        states = item.get("agents_states") if isinstance(item, dict) else None
        if not isinstance(states, dict):
            continue
        for identity, state in states.items():
            group_index = identity_to_group.get(str(identity))
            payload = state.get("message") if isinstance(state, dict) else None
            if group_index is not None and isinstance(payload, str) and payload.strip():
                handoffs.append(
                    handoff_row(
                        event,
                        group_index,
                        [str(identity)],
                        payload,
                        "native child-state completion",
                    )
                )
    for row in accepted_required_rows.values():
        payload = row.get("completion_payload")
        if isinstance(payload, str) and payload:
            source_event = next(
                (event for event in events if event.get("seq") == row.get("seq")), {}
            )
            handoffs.append(
                handoff_row(
                    source_event,
                    int(row["group"]),
                    list(row.get("identity") or []),
                    payload,
                    "delegate_task result",
                )
            )
        source_event = next(
            (event for event in events if event.get("seq") == row.get("seq")), {}
        )
        for candidate in completion_text_candidates(source_event):
            if row.get("group") and (
                component_ids(candidate) & set(GROUPS[int(row["group"]) - 1])
            ):
                handoffs.append(
                    handoff_row(
                        source_event,
                        int(row["group"]),
                        list(row.get("identity") or []),
                        candidate,
                        "native dispatch result",
                    )
                )

    for event in events:
        raw = event.get("raw_event")
        if not isinstance(raw, dict):
            continue
        if raw.get("subtype") != "task_notification" or raw.get("status") != "completed":
            continue
        identity = str(raw.get("task_id") or "")
        dispatch = dispatch_by_call.get(str(raw.get("tool_use_id") or ""))
        group_index = identity_to_group.get(identity)
        if group_index is None and dispatch and dispatch.get("group"):
            group_index = int(dispatch["group"])
        if group_index is None:
            continue
        payload = str(raw.get("summary") or "")
        handoffs.append(
            handoff_row(
                event,
                group_index,
                [identity] if identity else list(dispatch.get("identity") or []) if dispatch else [],
                payload,
                "task_notification",
            )
        )

    explicit_seqs = {row.get("seq") for row in handoffs}
    for event in events:
        if event.get("seq") in explicit_seqs or is_child_event(event):
            continue
        if event.get("event_type") not in {"model_input", "assistant_message", "raw_stdout"}:
            continue
        raw = event.get("raw_event")
        if isinstance(raw, dict) and raw.get("subtype") in {"task_started", "background_tasks_changed"}:
            continue
        text = event_text(event)
        lowered = text.casefold()
        ids = component_ids(text)
        group_index = group_number(ids)
        if (
            group_index
            and '"verdict"' in lowered
            and '"evidence"' in lowered
            and re.search(r"\b(?:completed|completion|child|subagent)\b", lowered)
        ):
            handoffs.append(
                handoff_row(
                    event,
                    group_index,
                    child_identifiers(text),
                    text,
                    "native parent-visible completion event",
                )
            )

    deduplicated: dict[tuple[int, str], dict[str, Any]] = {}
    for row in handoffs:
        identity = ",".join(row.get("identity") or []) or f"seq-{row.get('seq')}"
        key = (int(row["group"]), identity)
        current = deduplicated.get(key)
        if current is None or (
            not current.get("payload_complete_shape") and row.get("payload_complete_shape")
        ):
            deduplicated[key] = row
    handoffs = sorted(deduplicated.values(), key=lambda row: (row.get("seq") or 0, row["group"]))
    substantive_handoffs = [row for row in handoffs if row.get("payload_substantive")]
    complete_handoffs = [row for row in substantive_handoffs if row.get("payload_complete_shape")]
    pure_handoffs = [row for row in substantive_handoffs if row.get("payload_pure_json_shape")]

    output_writes = [
        observation(event, tool_arguments(event))
        for event in tools
        if output_write_event(event)
    ]
    first_write = min(
        (row["seq"] for row in output_writes if isinstance(row.get("seq"), int)),
        default=None,
    )
    all_before_write = (
        first_write is not None
        and len(complete_handoffs) == 5
        and all(
            isinstance(row.get("seq"), int) and row["seq"] < first_write
            for row in complete_handoffs
        )
    )
    merged_handoffs: dict[str, Any] = {}
    for row in complete_handoffs:
        payload = row.get("extracted_payload")
        if isinstance(payload, dict):
            for component, value in payload.items():
                if component in merged_handoffs:
                    merged_handoffs[component] = object()
                else:
                    merged_handoffs[component] = value
    final_preserved = (
        all_before_write
        and set(merged_handoffs) == set().union(*GROUPS)
        and isinstance(agent.get("delivery"), dict)
        and agent.get("delivery") == merged_handoffs
    )
    handoff_groups = [row.get("group") for row in substantive_handoffs]
    complete_groups = [row.get("group") for row in complete_handoffs]
    pure_groups = [row.get("group") for row in pure_handoffs]
    delivered_ok = len(substantive_handoffs) == 5 and sorted(handoff_groups) == [1, 2, 3, 4, 5]
    intact_ok = len(complete_handoffs) == 5 and sorted(complete_groups) == [1, 2, 3, 4, 5]
    pure_ok = len(pure_handoffs) == 5 and sorted(pure_groups) == [1, 2, 3, 4, 5]
    prompts_ok = (
        required_issued == 5
        and all(child_prompt_is_scoped(row) for row in required_rows.values())
    )

    lifecycle_reason = (
        f"one root run completed with {len(events)} normalized events"
        if lifecycle_ok
        else compact(
            f"run_starts={len(run_starts)}, run_ids={len(run_ids)}, run_finishes={len(run_finishes)}, "
            f"run_failed={sum(e.get('event_type') == 'run_failed' for e in events)}",
            700,
        )
    )
    if not events:
        lifecycle_reason = "[DEVICE FAILURE] canonical normalized trace is unavailable"

    harness_rules = [
        rule_result(
            "hr_single_turn_trace_complete",
            lifecycle_ok,
            lifecycle_reason,
        ),
        rule_result(
            "hr_native_dispatch_calls_accepted",
            required_issued == 5 and required_accepted == 5,
            f"required dispatches accepted={required_accepted}/5; required dispatches issued={required_issued}/5"
            if required_issued == 5
            else f"[AGENT BYPASS] required dispatches issued={required_issued}/5; accepted={required_accepted}/5",
        ),
        rule_result(
            "hr_distinct_child_identities_observable",
            identities_ok,
            f"accepted required dispatches={required_accepted}/5; distinct observable identities={len(set(required_identities))}/5",
        ),
        rule_result(
            "hr_native_child_terminal_states_observable",
            identities_ok and terminal_groups == {1, 2, 3, 4, 5},
            f"accepted required dispatches={required_accepted}/5; native child terminal evidence={len(terminal_groups)}/5",
        ),
        rule_result(
            "hr_initial_completions_delivered",
            delivered_ok,
            f"dispatches accepted={required_accepted}/5; native child terminal evidence={len(terminal_groups)}/5; "
            f"typed parent-visible initial completions={len(substantive_handoffs)}/5",
        ),
        rule_result(
            "hr_initial_completion_payloads_intact",
            intact_ok,
            f"parent-visible substantive completions={len(substantive_handoffs)}/5; intact complete payloads={len(complete_handoffs)}/5",
        ),
    ]

    agent_rules = [
        rule_result(
            "ar_exact_native_dispatch_plan",
            exact_assignments,
            "exactly five successful native sessions match groups 1 through 5"
            if exact_assignments
            else compact(
                f"successful_session_count={len(successful_sessions)}; groups={actual_groups}; emulated={emulated}",
                760,
            ),
        ),
        rule_result(
            "ar_child_prompt_scope_exact",
            prompts_ok,
            "all five first required child prompts are least-privilege and schema-complete"
            if prompts_ok
            else compact(
                {
                    group: {
                        "scoped": child_prompt_is_scoped(row),
                        "ids": row.get("component_ids"),
                        "snippet": row.get("snippet"),
                    }
                    for group, row in required_rows.items()
                },
                900,
            ),
        ),
        rule_result(
            "ar_parent_delegation_boundary_respected",
            isolated,
            "no parent checklist read, transcript recovery, emulation, or child write observed"
            if isolated
            else compact(
                f"parent_reads={parent_component_reads}; recovery={history_recovery}; emulated={emulated}; child_writes={child_writes}",
                900,
            ),
        ),
        rule_result(
            "ar_initial_handoffs_well_formed",
            pure_ok and (first_write is None or all_before_write),
            f"pure complete handoffs={len(pure_handoffs)}/5; first_output_write_seq={first_write}; all_complete_before_write={all_before_write}",
        ),
        rule_result(
            "ar_final_output_derived_from_handoffs",
            final_preserved,
            "all 23 final entries exactly preserve five complete pre-write native handoffs"
            if final_preserved
            else compact(
                f"delivery_available={agent.get('delivery_available')}; merged_components={len(merged_handoffs)}; "
                f"complete_handoffs={len(complete_handoffs)}; first_write={first_write}; all_before_write={all_before_write}",
                760,
            ),
        ),
    ]
    return harness_rules, agent_rules


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    agent_rules, agent_observations = agent_assessment(args.workspace)
    run_dir = Path(os.environ.get("HARNESSBENCH_RUN_DIR", ""))
    events = trace_events(run_dir) if str(run_dir) not in {"", "."} else []
    harness_rules, trace_agent_rules = harness_assessment(
        args.workspace, events, agent_observations
    )
    print(
        json.dumps(
            {
                "schema_version": "harnessbench_rule_results_v1",
                "rule_results": {
                    "agent": [*trace_agent_rules, *agent_rules],
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
