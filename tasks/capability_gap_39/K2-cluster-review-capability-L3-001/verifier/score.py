#!/usr/bin/env python3
"""Deterministic rules and bounded aggregation evidence for K2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_cluster_aggregation_evidence_v3"
FIELDS = {"verdict", "rule", "evidence"}
EVIDENCE_FIELDS = {"r1", "r2", "r3"}
VENDOR_ID = re.compile(r"(?<![A-Za-z0-9])V(?:0[1-9]|1[0-9]|2[0-3])")
VENDOR_RANGE = re.compile(r"V(\d{2})\s*[-–]\s*V(\d{2})")
CLUSTER_PATH = re.compile(r"(?:^|[\s'\"`])(?:/workspace/)?clusters(?:/|\b)", re.I)
DISPATCH_TOOLS = {"agent", "delegate_task", "sessions_spawn", "spawn_agent", "subagent"}
COMPLETION_TOOLS = {"wait", "wait_agent", "close_agent", "task_output"}
HISTORY_TOOLS = {"sessions_history", "read_thread", "read_agent"}
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
TOOL_ALIASES = {
    "collaboration__spawn_agent": "spawn_agent",
    "collaboration__wait": "wait",
    "collaboration__wait_agent": "wait_agent",
    "collaboration__close_agent": "close_agent",
    "taskoutput": "task_output",
    "sendmessage": "send_message",
    "command_execution": "exec_command",
}


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
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def normalized(value: Any) -> str:
    return " ".join(str(value or "").split())


def quote_normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def cluster_ids(text: str) -> set[str]:
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


def source_for_cluster(workspace: Path, cluster_id: str) -> Path | None:
    matches = sorted((workspace / "clusters").glob(f"{cluster_id}_*.md"))
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
            f"cluster IDs differ: missing={sorted(expected-set(raw))}, extra={sorted(set(raw)-expected)}"
        )

    decision_errors: list[dict[str, Any]] = []
    quote_errors: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for cluster_id in sorted(expected):
        item = delivered.get(cluster_id)
        target = truth[cluster_id]
        if not isinstance(item, dict):
            contract_errors.append(f"{cluster_id} is not an object")
            item = {}
        elif set(item) != FIELDS:
            contract_errors.append(
                f"{cluster_id} fields are {sorted(item)}; expected {sorted(FIELDS)}"
            )

        verdict = item.get("verdict")
        rule = item.get("rule")
        evidence = item.get("evidence")
        if verdict not in {"pass", "fail"}:
            contract_errors.append(f"{cluster_id}.verdict is not pass or fail")
        if verdict == "pass" and rule is not None:
            contract_errors.append(f"{cluster_id}.rule is not null for pass")
        if verdict == "fail" and (type(rule) is not int or rule not in {1, 2, 3}):
            contract_errors.append(f"{cluster_id}.rule is not integer 1, 2, or 3 for fail")
        if not isinstance(evidence, dict):
            contract_errors.append(f"{cluster_id}.evidence is not an object")
            evidence = {}
        elif set(evidence) != EVIDENCE_FIELDS:
            contract_errors.append(
                f"{cluster_id}.evidence fields are {sorted(evidence)}; expected {sorted(EVIDENCE_FIELDS)}"
            )

        if verdict != target.get("verdict") or rule != target.get("rule"):
            decision_errors.append(
                {
                    "cluster": cluster_id,
                    "delivered": {"verdict": verdict, "rule": rule},
                    "expected": target,
                }
            )

        source_path = source_for_cluster(workspace, cluster_id)
        try:
            source_text = source_path.read_text(encoding="utf-8") if source_path else ""
        except (OSError, UnicodeError):
            source_text = ""
        row: dict[str, Any] = {
            "cluster": cluster_id,
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
                    f"{cluster_id}.evidence.{evidence_key} is not a non-empty trimmed string <=300 characters"
                )
                quote = quote if isinstance(quote, str) else ""
            verbatim = quote_is_verbatim(source_text, quote)
            if not verbatim:
                quote_errors.append(
                    {"cluster": cluster_id, "rule": evidence_key, "quote": compact(quote, 180)}
                )
            row["quotes"][evidence_key] = {
                "delivered_quote": quote,
                "verbatim_in_same_cluster_checklist": verbatim,
            }
        evidence_rows.append(row)

    try:
        policy_text = (workspace / "docs" / "POLICY.md").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        policy_text = ""
    rubric_anchors = {
        "au_r1_v04_evidence_supports_review": "V04",
        "au_r2_v10_evidence_supports_review": "V10",
        "au_r3_v23_evidence_supports_review": "V23",
    }
    for rubric_id, cluster_id in rubric_anchors.items():
        write_report(
            workspace,
            rubric_id,
            {
                "review_json_available": isinstance(raw, dict),
                "policy_text": policy_text,
                "required_cluster_id": cluster_id,
                "cluster_evidence_rows": [
                    row for row in evidence_rows if row["cluster"] == cluster_id
                ],
                "instruction": (
                    "Judge this source-labelled high-difficulty cluster. Each r1/r2/r3 quote must be "
                    "relevant to that rule and the three quotes must jointly support the delivered "
                    "decision and expected lowest-numbered failure rule. A missing row or source proof scores 0."
                ),
            },
        )

    contract_ok = not contract_errors and len(expected) == 23
    decisions_ok = not decision_errors and len(expected) == 23
    quotes_ok = not quote_errors and len(evidence_rows) == 23
    rules = [
        rule_result(
            "ar_exact_review_contract",
            contract_ok,
            "exact one-file 23-cluster review contract satisfied"
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
    candidates: list[tuple[Path, list[dict[str, Any]]]] = []
    for root in (run_dir / "artifacts" / "trace", run_dir / "artifacts" / "container" / "trace"):
        if not root.is_dir():
            continue
        for path in sorted(root.glob("run-*/events.json")):
            payload = load_json(path, [])
            if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
                candidates.append((path, payload))
    if not candidates:
        return []
    events_path, events = max(candidates, key=lambda item: len(item[1]))

    # Older Codex normalized traces emitted child completions after replaying the
    # parent archive, which loses their original ordering relative to a file write.
    # Recover only hash-verified native child archives and retain the native event
    # timestamp. This is additive evidence; an untrusted or malformed archive is
    # ignored and therefore cannot manufacture a passing handoff.
    derived: list[dict[str, Any]] = []
    for archive_event in events:
        if archive_event.get("event_type") != "native_session_archive":
            continue
        artifact = str(archive_event.get("artifact") or "")
        if not re.fullmatch(r"native_sessions/codex-child-[1-5]\.trace\.jsonl", artifact):
            continue
        archive_path = (events_path.parent / artifact).resolve()
        if archive_path.parent != (events_path.parent / "native_sessions").resolve():
            continue
        try:
            payload = archive_path.read_bytes()
        except OSError:
            continue
        if len(payload) != archive_event.get("size_bytes"):
            continue
        if hashlib.sha256(payload).hexdigest() != archive_event.get("sha256"):
            continue
        agent_path = ""
        completion: dict[str, Any] | None = None
        for line in payload.decode("utf-8", errors="replace").splitlines():
            value = parse_json_text(line)
            if not isinstance(value, dict):
                continue
            body = value.get("payload")
            if value.get("type") == "session_meta" and isinstance(body, dict):
                source = body.get("source")
                subagent = source.get("subagent") if isinstance(source, dict) else None
                spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
                agent_path = str(spawn.get("agent_path") or "") if isinstance(spawn, dict) else ""
            if (
                value.get("type") == "event_msg"
                and isinstance(body, dict)
                and body.get("type") == "task_complete"
                and isinstance(body.get("last_agent_message"), str)
            ):
                completion = value
        if completion is None:
            continue
        body = completion["payload"]
        derived.append(
            {
                "event_type": "task_completion",
                "run_id": archive_event.get("run_id"),
                "seq": archive_event.get("seq"),
                "timestamp": completion.get("timestamp"),
                "principal_id": f"{archive_event.get('session_id')}:{agent_path or artifact}",
                "principal_role": "child",
                "content": body.get("last_agent_message"),
                "source": "codex.native_session_archive.task_complete",
                "archive_backed": True,
            }
        )
    return [*events, *derived]


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    raw = str(call.get("tool_name") or call.get("name") or "").strip().casefold().replace("-", "_")
    return TOOL_ALIASES.get(raw, raw)


def tool_args(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def tool_arguments(event: dict[str, Any]) -> str:
    return "\n".join(leaves(tool_args(event)))


def result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    return "\n".join(
        [*leaves(result.get("content")), *leaves(result.get("structured_output"))]
    )


def raw_text(event: dict[str, Any]) -> str:
    raw = event.get("raw_event")
    return json.dumps(raw, ensure_ascii=False) if raw is not None else ""


def event_text(event: dict[str, Any]) -> str:
    return "\n".join(
        [
            result_text(event),
            *leaves(event.get("content")),
            *leaves(event.get("text")),
            *leaves(event.get("last_assistant_message")),
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
        or bool(re.search(r"\b(?:spawn|dispatch|delegation) failed\b", text))
        or "agent thread limit reached" in text
    )


def is_child_event(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role") or "").casefold() == "child":
        return True
    serialized = raw_text(event).casefold()
    return (
        '"issidechain": true' in serialized
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', serialized))
        or '"agent_role": "child"' in serialized
    )


def observation(event: dict[str, Any], text: str) -> dict[str, Any]:
    return {
        "seq": event.get("seq"),
        "timestamp": event.get("timestamp"),
        "event_type": event.get("event_type"),
        "tool": tool_name(event),
        "cluster_ids": sorted(cluster_ids(text)),
        "text_size": len(text),
        "snippet": compact(text),
    }


def child_identifiers(text: str) -> list[str]:
    patterns = (
        r'childSessionKey["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'agentId["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)',
        r'agent_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'task_name["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'principal_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'\bstarted\s+subagent\s+([A-Za-z0-9_.:-]+)',
        r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b',
        r'\bagent:main:subagent:[A-Za-z0-9_.:-]+',
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.I))
    return sorted(set(found))


def event_identities(event: dict[str, Any]) -> list[str]:
    found = child_identifiers(
        "\n".join(
            [
                result_text(event),
                raw_text(event),
                str(event.get("principal_id") or ""),
                str(event.get("child_principal_id") or ""),
            ]
        )
    )
    principal = str(event.get("principal_id") or "").strip()
    child_principal = str(event.get("child_principal_id") or "").strip()
    return sorted({value for value in [*found, principal, child_principal] if value})


def spawned_identities(event: dict[str, Any]) -> list[str]:
    result = event.get("tool_result")
    structured = result.get("structured_output") if isinstance(result, dict) else None
    found: set[str] = set(child_identifiers(result_text(event)))
    if isinstance(structured, dict):
        receivers = structured.get("receiver_thread_ids")
        if isinstance(receivers, list):
            found.update(str(value) for value in receivers if value)
        states = structured.get("agents_states")
        if isinstance(states, dict):
            found.update(str(value) for value in states if value)
    args = tool_args(event)
    if isinstance(args, dict):
        found.discard(str(args.get("sender_thread_id") or ""))
    return sorted(value for value in found if value)


def completion_messages(event: dict[str, Any]) -> list[tuple[list[str], str, str]]:
    rows: list[tuple[list[str], str, str]] = []
    event_type = str(event.get("event_type") or "")
    if event_type == "task_completion":
        payload = "\n".join(leaves(event.get("content")))
        if payload and str(event.get("status") or "completed").casefold() in {
            "completed", "finished", "ok", "settled"
        }:
            rows.append((event_identities(event), payload, str(event.get("source") or "task_completion")))
    if event_type == "subagent_lifecycle" and str(event.get("action") or "").casefold() in {
        "finished", "completed",
    }:
        payload = "\n".join(leaves(event.get("last_assistant_message")))
        if not payload:
            raw = event.get("raw_event")
            params = raw.get("params") if isinstance(raw, dict) else None
            payload = "\n".join(leaves(params.get("lastAssistantMessage"))) if isinstance(params, dict) else ""
        if payload:
            rows.append((event_identities(event), payload, str(event.get("source") or "subagent_lifecycle")))
    if event_type != "tool" or tool_name(event) not in COMPLETION_TOOLS:
        return rows

    # Claude Code exposes a native parent-visible join through TaskOutput. It
    # is a completion channel, not transcript recovery. Preserve exactly what
    # the tool returned: truncated/path-only content must fail later, and the
    # verifier must never open the advertised temporary path.
    if tool_name(event) == "task_output":
        payload = result_text(event)
        args = tool_args(event)
        identity = str(args.get("task_id") or "") if isinstance(args, dict) else ""
        successful = (
            bool(payload)
            and "<retrieval_status>success</retrieval_status>" in payload
            and "<status>completed</status>" in payload
        )
        if successful:
            rows.append(([identity] if identity else event_identities(event), payload, "task_output"))
        return rows

    candidates: list[Any] = []
    result = event.get("tool_result")
    if isinstance(result, dict):
        candidates.extend([result.get("structured_output"), result.get("content")])
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        candidates.append(raw)
    seen: set[tuple[str, str]] = set()
    stack = candidates[:]
    while stack:
        value = stack.pop()
        parsed = parse_json_text(value)
        if parsed is not None and parsed is not value:
            stack.append(parsed)
            continue
        if isinstance(value, dict):
            states = value.get("agents_states")
            if isinstance(states, dict):
                for identity, state in states.items():
                    if not isinstance(state, dict):
                        continue
                    status = str(state.get("status") or "").casefold()
                    payload = "\n".join(leaves(state.get("message")))
                    if status in {"completed", "finished", "ok"} and payload:
                        key = (str(identity), payload)
                        if key not in seen:
                            seen.add(key)
                            rows.append(([str(identity)], payload, tool_name(event)))
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return rows


def command_writes_target(event: dict[str, Any], target: str = "output/review.json") -> bool:
    name = tool_name(event)
    arguments = tool_arguments(event)
    if target not in arguments:
        return False
    if name in WRITE_TOOLS:
        return True
    if name not in READ_TOOLS:
        return False
    return bool(
        re.search(
            r"(?:>|\btee\b|\btouch\b|\bmkdir\b|\bcp\b|\bmv\b|"
            r"(?:open|write_text)\s*\([^\n]{0,240}output/review\.json|"
            r"output/review\.json[^\n]{0,160}(?:write|dump))",
            arguments,
            re.IGNORECASE,
        )
    )


def command_mutates_workspace(event: dict[str, Any]) -> bool:
    name = tool_name(event)
    arguments = tool_arguments(event)
    if name in WRITE_TOOLS:
        args = tool_args(event)
        targets: list[str] = []
        if isinstance(args, dict):
            for key in ("path", "file_path", "target", "notebook_path"):
                targets.extend(leaves(args.get(key)))
        return not targets or any(not path_is_ephemeral(target) for target in targets)
    if name not in READ_TOOLS:
        return False
    try:
        tokens = shlex.split(arguments)
    except ValueError:
        tokens = []
    command_targets: list[str] = []
    for index, token in enumerate(tokens):
        target = ""
        if token in {">", ">>"} and index + 1 < len(tokens):
            target = tokens[index + 1]
        elif token.startswith((">", ">>")):
            target = token.lstrip(">")
        elif Path(token).name in {"tee", "touch", "mkdir"}:
            command_targets.extend(
                value for value in tokens[index + 1 :] if not value.startswith("-")
            )
        elif Path(token).name in {"cp", "mv"} and index + 1 < len(tokens):
            command_targets.append(tokens[-1])
        if target:
            command_targets.append(target)
    command_targets.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:open|write_text)\s*\(\s*['\"]([^'\"]+)['\"]", arguments, re.I
        )
    )
    return any(not path_is_ephemeral(target) for target in command_targets)


def path_is_ephemeral(target: str) -> bool:
    normalized_target = str(target or "").strip().rstrip(";,)")
    return normalized_target.startswith(("/dev/", "/tmp/", "/private/tmp/"))


def command_recovers_result_file(event: dict[str, Any]) -> bool:
    """Detect direct reads of native child-result files outside the join API."""

    if tool_name(event) not in READ_TOOLS:
        return False
    arguments = tool_arguments(event)
    return bool(
        re.search(
            r"(?:^|[\s'\"])(?:/[^\s'\"]+)?/tasks/[^\s'\"]+\.output(?:$|[\s'\"])",
            arguments,
        )
    )


def file_change_writes_target(event: dict[str, Any], target: str = "output/review.json") -> bool:
    raw = event.get("raw_event")
    item = raw.get("item") if isinstance(raw, dict) else None
    if not isinstance(item, dict) or item.get("type") != "file_change":
        return False
    if str(item.get("status") or "").casefold() not in {"completed", "finished"}:
        return False
    changes = item.get("changes")
    return isinstance(changes, list) and any(
        isinstance(change, dict)
        and str(change.get("kind") or "").casefold() in {"add", "update", "write", "modify"}
        and target in str(change.get("path") or "")
        for change in changes
    )


def command_reads_cluster(event: dict[str, Any]) -> bool:
    """Detect content reads while allowing a plain directory/file-name listing."""

    name = tool_name(event)
    arguments = tool_arguments(event)
    if not CLUSTER_PATH.search(" " + arguments + " "):
        return False
    if name in {"read", "read_file", "open_file", "view", "cat"}:
        return True
    if name not in READ_TOOLS:
        return False
    # `ls clusters`, `find clusters -maxdepth 1 -type f -print`, and
    # `rg --files clusters` expose names only. Content readers, shell input
    # redirects, interpreters opening the path, or find/xargs execution do not.
    if re.search(r"(?:^|[;&|]\s*)(?:ls\b|find\b[^;&|]*(?:-print|-printf)\b|rg\s+--files\b)[^;&|]*clusters(?:/|\b)", arguments, re.I):
        remainder = re.sub(r"(?:^|[;&|]\s*)(?:ls\b|find\b[^;&|]*(?:-print|-printf)\b|rg\s+--files\b)[^;&|]*clusters(?:/|\b)[^;&|]*", "", arguments, flags=re.I)
        if not CLUSTER_PATH.search(" " + remainder + " "):
            return False
    return bool(
        re.search(
            r"(?:\b(?:cat|sed|awk|head|tail|less|more|grep|rg|perl|python\d*|ruby|node|xargs)\b[^\n;&|]*clusters(?:/|\b)|"
            r"clusters(?:/|\b)[^\n;&|]*(?:\|\s*(?:cat|sed|awk|head|tail|grep|rg)|-exec\b)|"
            r"<\s*[^\n;&|]*clusters(?:/|\b)|(?:open|read_text)\s*\([^\n]{0,240}clusters(?:/|\b))",
            arguments,
            re.I,
        )
    )


def extract_cluster_object(text: str, expected_group: frozenset[str]) -> dict[str, Any] | None:
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


def complete_cluster_payload(payload: Any, group: frozenset[str]) -> bool:
    if not isinstance(payload, dict) or set(payload) != set(group):
        return False
    for cluster in group:
        item = payload.get(cluster)
        if not isinstance(item, dict) or set(item) != FIELDS:
            return False
        verdict = item.get("verdict")
        rule = item.get("rule")
        if verdict not in {"pass", "fail"}:
            return False
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


def session_rows_for_dispatch(event: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    name = tool_name(event)
    arguments = tool_args(event)
    call = event.get("tool_call") if isinstance(event.get("tool_call"), dict) else {}
    call_id = str(call.get("tool_call_id") or "")
    if name != "delegate_task":
        text = tool_arguments(event)
        ids = cluster_ids(text)
        row = {
            **observation(event, text),
            "group": group_number(ids),
            "successful": tool_success(event),
            "identity": spawned_identities(event),
            "dispatch_call_id": call_id,
            "completion_payload": None,
        }
        return ([row] if tool_success(event) else []), (None if tool_success(event) else row)

    tasks = arguments.get("tasks") if isinstance(arguments, dict) else None
    result_payload = parse_json_text(result_text(event))
    results = result_payload.get("results") if isinstance(result_payload, dict) else None
    if not isinstance(tasks, list) or not isinstance(results, list):
        return [], {**observation(event, tool_arguments(event)), "result": compact(result_text(event), 800)}
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
        if not success:
            continue
        summary = str(item.get("summary") or "")
        rows.append(
            {
                **observation(event, task_text),
                "group": group_number(cluster_ids(task_text)),
                "successful": True,
                "identity": [f"{call_id}:task-{index}"],
                "dispatch_call_id": call_id,
                "completion_payload": summary,
            }
        )
    return rows, None


def handoff_row(
    event: dict[str, Any],
    group_index: int,
    identity: list[str],
    payload_text: str,
    channel: str,
) -> dict[str, Any]:
    group = GROUPS[group_index - 1]
    extracted = extract_cluster_object(payload_text, group)
    return {
        "seq": event.get("seq"),
        "timestamp": event.get("timestamp"),
        "group": group_index,
        "assigned_cluster_ids": sorted(group),
        "identity": identity,
        "completion_channel": channel,
        "payload_complete_shape": (
            "[Truncated. Full output:" not in payload_text
            and complete_cluster_payload(extracted, group)
        ),
        "extracted_payload": extracted,
        "payload_text": payload_text[:16000],
        "payload_truncated_for_evidence": len(payload_text) > 16000,
    }


def harness_assessment(
    workspace: Path, events: list[dict[str, Any]], agent: dict[str, Any]
) -> list[dict[str, Any]]:
    run_starts = [event for event in events if event.get("event_type") == "run_started"]
    run_ids = {str(event.get("run_id")) for event in events if event.get("run_id")}
    run_finishes = [event for event in events if event.get("event_type") == "run_finished"]
    soft_completion = any(
        event.get("event_type") == "native_diagnostic"
        and event.get("diagnostic_type") == "completed_tool_turn_ede_diagnostic"
        and event.get("process_exit_may_be_softened") is True
        and event.get("source") == "claudecode.result"
        for event in events
    )
    finish_success = bool(run_finishes) and (
        run_finishes[0].get("exit_code") in (None, 0, "0")
        or (run_finishes[0].get("exit_code") in (1, "1") and soft_completion)
    )
    lifecycle_ok = (
        len(run_starts) == 1
        and len(run_ids) == 1
        and run_starts[0].get("seq") == min(
            (event.get("seq") for event in events if isinstance(event.get("seq"), int)),
            default=None,
        )
        and not any(event.get("event_type") == "run_failed" for event in events)
        and len(run_finishes) == 1
        and run_finishes[0].get("trace_complete") is True
        and finish_success
    )

    tools = [event for event in events if event.get("event_type") == "tool"]
    dispatch_events = [
        event
        for event in tools
        if not is_child_event(event) and tool_name(event) in DISPATCH_TOOLS
    ]
    successful_sessions: list[dict[str, Any]] = []
    rejected_dispatches: list[dict[str, Any]] = []
    for event in dispatch_events:
        rows, rejected = session_rows_for_dispatch(event)
        successful_sessions.extend(rows)
        if rejected:
            rejected_dispatches.append(rejected)

    emulated = [
        observation(event, tool_arguments(event))
        for event in tools
        if EMULATION.search(tool_arguments(event))
    ]
    actual_groups = [row.get("group") for row in successful_sessions]
    exact_assignments = (
        len(successful_sessions) == 5
        and not emulated
        and all(type(group) is int for group in actual_groups)
        and sorted(actual_groups) == [1, 2, 3, 4, 5]
        and all(row.get("successful") for row in successful_sessions)
    )

    parent_cluster_reads = [
        observation(event, tool_arguments(event))
        for event in tools
        if not is_child_event(event)
        and tool_name(event) not in DISPATCH_TOOLS
        and tool_name(event) in READ_TOOLS
        and command_reads_cluster(event)
    ]
    history_recovery = [
        observation(event, tool_arguments(event))
        for event in tools
        if not is_child_event(event)
        and (tool_name(event) in HISTORY_TOOLS or command_recovers_result_file(event))
    ]
    child_writes = [
        observation(event, tool_arguments(event))
        for event in tools
        if is_child_event(event)
        and (
            command_writes_target(event, "output/")
            or command_mutates_workspace(event)
        )
    ]
    child_writes.extend(
        observation(event, event_text(event))
        for event in events
        if is_child_event(event) and file_change_writes_target(event, "")
    )
    isolated = bool(events) and bool(dispatch_events) and not (
        parent_cluster_reads or history_recovery or emulated or child_writes
    )

    dispatch_by_identity: dict[str, dict[str, Any]] = {}
    for row in successful_sessions:
        for identity in row.get("identity") or []:
            if row.get("group"):
                dispatch_by_identity[str(identity)] = row

    handoffs: list[dict[str, Any]] = []
    for row in successful_sessions:
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

    for event in events:
        for identities, payload, channel in completion_messages(event):
            group_index = group_number(cluster_ids(payload))
            if group_index is None:
                dispatch = next(
                    (dispatch_by_identity[value] for value in identities if value in dispatch_by_identity),
                    None,
                )
                group_index = dispatch.get("group") if isinstance(dispatch, dict) else None
            if group_index:
                handoffs.append(
                    handoff_row(event, int(group_index), identities, payload, channel)
                )

    # The same native completion can be normalized twice (for example a Codex
    # parent-session event plus its hash-verified archive record). Collapse only
    # byte-equivalent structured payloads; conflicting results stay distinct and
    # deterministically fail the one-result-per-group check.
    deduplicated: dict[tuple[int, str], dict[str, Any]] = {}
    for row in handoffs:
        payload_key = json.dumps(
            row.get("extracted_payload"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        key = (int(row["group"]), payload_key)
        current = deduplicated.get(key)
        if current is None:
            deduplicated[key] = row
            continue
        current["identity"] = sorted(set(current.get("identity") or []) | set(row.get("identity") or []))
        current_time = str(current.get("timestamp") or "")
        row_time = str(row.get("timestamp") or "")
        if row_time and (not current_time or row_time < current_time):
            row["identity"] = current["identity"]
            deduplicated[key] = row
        elif not current_time and not row_time and (row.get("seq") or 10**12) < (current.get("seq") or 10**12):
            row["identity"] = current["identity"]
            deduplicated[key] = row
    handoffs = sorted(deduplicated.values(), key=lambda row: (str(row.get("timestamp") or ""), row.get("seq") or 0, row["group"]))

    by_group: dict[int, list[dict[str, Any]]] = {index: [] for index in range(1, 6)}
    for row in handoffs:
        by_group[int(row["group"])].append(row)
    complete_handoffs: list[dict[str, Any]] = []
    handoffs_ok = True
    for index in range(1, 6):
        rows = by_group[index]
        if len(rows) != 1 or not rows[0].get("payload_complete_shape") or not rows[0].get("identity"):
            handoffs_ok = False
            continue
        complete_handoffs.append(rows[0])

    output_writes = [
        observation(event, tool_arguments(event) if event.get("event_type") == "tool" else event_text(event))
        | {"timestamp": event.get("timestamp")}
        for event in events
        if (
            (event.get("event_type") == "tool" and command_writes_target(event) and tool_success(event))
            or file_change_writes_target(event)
        )
    ]
    first_write = min(
        (row["seq"] for row in output_writes if isinstance(row.get("seq"), int)),
        default=None,
    )
    first_write_timestamp = min(
        (
            str(row["timestamp"])
            for row in output_writes
            if isinstance(row.get("timestamp"), str) and row.get("timestamp")
        ),
        default=None,
    )

    def before_first_write(row: dict[str, Any]) -> bool:
        timestamp = row.get("timestamp")
        if first_write_timestamp is not None and isinstance(timestamp, str) and timestamp:
            return timestamp < first_write_timestamp
        return (
            first_write is not None
            and isinstance(row.get("seq"), int)
            and row["seq"] < first_write
        )

    prewrite_handoffs = [row for row in complete_handoffs if before_first_write(row)]
    aggregation_order_ok = (
        handoffs_ok
        and len(prewrite_handoffs) == 5
        and first_write is not None
        and agent.get("delivery_available") is True
    )
    merged_handoffs: dict[str, Any] = {}
    for row in complete_handoffs:
        payload = row.get("extracted_payload")
        if isinstance(payload, dict):
            merged_handoffs.update(payload)
    preservation_ok = (
        handoffs_ok
        and agent.get("delivery_available") is True
        and set(merged_handoffs) == set().union(*GROUPS)
        and agent.get("delivery") == merged_handoffs
    )

    common = {
        "trace_available": bool(events),
        "trace_event_count": len(events),
        "trace_max_seq": max((event.get("seq", 0) for event in events), default=0),
        "successful_session_rows": successful_sessions,
        "rejected_dispatch_attempts_that_created_no_session": rejected_dispatches,
        "expected_groups": [sorted(group) for group in GROUPS],
    }
    write_report(
        workspace,
        "hr_five_complete_native_handoffs",
        {
            **common,
            "native_handoff_rows": handoffs,
            "deterministic_score": int(handoffs_ok),
            "instruction": (
                "Judge five identity-linked parent-visible completion payloads, one complete payload "
                "per expected group. Launch acknowledgements are not handoffs; missing evidence scores 0."
            ),
        },
    )
    write_report(
        workspace,
        "hr_parent_aggregates_after_all_handoffs",
        {
            **common,
            "native_handoff_rows": handoffs,
            "handoffs_before_first_write": prewrite_handoffs,
            "first_output_write_seq": first_write,
            "first_output_write_timestamp": first_write_timestamp,
            "output_write_observations": output_writes,
            "parent_cluster_checklist_reads": parent_cluster_reads,
            "history_or_transcript_recovery": history_recovery,
            "emulated_child_processes": emulated,
            "final_review_available": agent.get("delivery_available", False),
            "deterministic_score": int(aggregation_order_ok),
            "instruction": (
                "Judge sequence and parent behavior. All five complete handoffs must precede the first "
                "successful review write; missing evidence scores 0."
            ),
        },
    )
    write_report(
        workspace,
        "hr_final_review_preserves_handoff_values",
        {
            **common,
            "native_handoff_rows": handoffs,
            "final_review": agent.get("delivery"),
            "merged_native_handoffs": merged_handoffs,
            "deterministic_score": int(preservation_ok),
            "instruction": (
                "Compare every cluster's final verdict, rule, and three evidence strings with exactly one "
                "matching native handoff. Missing or conflicting evidence scores 0."
            ),
        },
    )

    return [
        rule_result(
            "hr_single_turn_trace_complete",
            lifecycle_ok,
            f"one root run completed with {len(events)} normalized events"
            if lifecycle_ok
            else compact(
                f"run_starts={len(run_starts)}, run_ids={len(run_ids)}, run_finishes={len(run_finishes)}, "
                f"run_failed={sum(e.get('event_type') == 'run_failed' for e in events)}",
                700,
            ),
        ),
        rule_result(
            "hr_exactly_five_native_assignments",
            exact_assignments,
            "exactly five successful native sessions match groups 1 through 5"
            if exact_assignments
            else compact(
                f"successful_sessions={successful_sessions}; rejected_no_session={rejected_dispatches}; emulated={emulated}",
                820,
            ),
        ),
        rule_result(
            "hr_parent_isolated_without_recovery",
            isolated,
            "no parent checklist read, recovery bypass, emulation, or child write observed"
            if isolated
            else compact(
                f"parent_reads={parent_cluster_reads}; recovery={history_recovery}; emulated={emulated}; child_writes={child_writes}",
                820,
            ),
        ),
        rule_result(
            "hr_five_complete_native_handoffs",
            handoffs_ok,
            "five identity-linked complete native handoffs cover groups 1 through 5"
            if handoffs_ok
            else compact(
                f"handoffs_by_group={by_group}",
                900,
            ),
        ),
        rule_result(
            "hr_parent_aggregates_after_all_handoffs",
            aggregation_order_ok,
            "all five complete native handoffs preceded the first successful review write"
            if aggregation_order_ok
            else compact(
                f"handoffs_ok={handoffs_ok}; prewrite_groups={[row.get('group') for row in prewrite_handoffs]}; "
                f"first_write={first_write}; final_available={agent.get('delivery_available')}",
                900,
            ),
        ),
        rule_result(
            "hr_final_review_preserves_handoff_values",
            preservation_ok,
            "final review exactly preserves all values from the five native handoffs"
            if preservation_ok
            else compact(
                f"handoffs_ok={handoffs_ok}; merged_ids={sorted(merged_handoffs)}; "
                f"final_available={agent.get('delivery_available')}; exact_match={agent.get('delivery') == merged_handoffs}",
                900,
            ),
        ),
    ]


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
                "rule_results": {"agent": agent_rules, "harness": harness_rules},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
