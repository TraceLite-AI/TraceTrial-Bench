#!/usr/bin/env python3
"""Deterministic verifier for isolated native research subagents.

Every score item is binary.  Mechanism rules consume only normalized native
trajectory events (plus the final deliverable for merge provenance); ordinary
parent prose, launch metadata, continuation requests, transcripts, and child
files never qualify as completion handoffs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable


HERE = Path(__file__).resolve().parent
SCHEMA = "harnessbench_subagent_evidence_v3"
FIELDS = {"angle", "value", "quote", "source_url"}
ANGLES = tuple(f"A{i}" for i in range(1, 6))
FACT_ID = re.compile(r"\bA([1-5])-[1-5]\b")
URL = re.compile(r"https?://[^\s\"'<>`]+")
HANDOFF_MAX_BYTES = 60_000
ASSIGNMENT_MAX_BYTES = 12_000
DISPATCH_TOOLS = {"agent", "delegate_task", "sessions_spawn", "spawn_agent", "subagent"}
CONTINUATION_TOOLS = {
    "followup_task",
    "send_input",
    "send_message",
    "sendmessage",
    "sessions_send",
}
WAIT_TOOLS = {
    "taskoutput",
    "wait",
    "wait_agent",
    "wait_threads",
    "sessions_yield",
    "yield",
    "yield_control",
}
HISTORY_TOOLS = {
    "read_agent",
    "read_thread",
    "sessions_history",
    "sessions_list",
    "subagents",
}
NETWORK_TOOLS = {
    "browser",
    "curl",
    "fetch",
    "search_query",
    "web_fetch",
    "web_search",
    "websearch",
    "wget",
}
EMULATION = re.compile(
    r"(?:^|[;&|\s])(?:codex\s+exec|claude(?:\s+-p|\s+--print)|hermes\s+run|dsh-jsonrpc-agent)",
    re.IGNORECASE,
)


class DuplicateKeyError(ValueError):
    pass


class VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


@dataclass
class Dispatch:
    seq: int
    turn: int
    angle: str
    prompt: str
    ids: set[str] = field(default_factory=set)
    success: bool = False


@dataclass
class Completion:
    seq: int
    turn: int
    ids: set[str]
    payload: dict[str, Any]
    source: str
    text_size: int
    exact_json_envelope: bool

    @property
    def fact_ids(self) -> set[str]:
        return {key for key in self.payload if FACT_ID.fullmatch(str(key))}


def strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in strings(child)]
    return [] if value is None else [str(value)]


def named_values(value: Any, names: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).casefold() in names:
                found.append(child)
            found.extend(named_values(child, names))
    elif isinstance(value, list):
        for child in value:
            found.extend(named_values(child, names))
    return found


def compact(value: Any, limit: int = 500) -> str:
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
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_object
        ), None
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


def rst_rendered_variants(raw: str) -> set[str]:
    """Approximate visible RST while preserving contiguous paragraph order.

    Section adornment lines are not rendered text.  Removing them fixes the
    prior false negative where a quote joined a heading to its first paragraph.
    Two variants preserve and strip inline RST markup respectively.
    """

    kept = [
        line
        for line in raw.splitlines()
        if not re.fullmatch(r"\s*[=~`^\"'_*+#<>:-]{3,}\s*", line)
        and not re.fullmatch(r"\s*\.\.\s+[A-Za-z0-9_-]+::.*", line)
    ]
    rendered = "\n".join(kept)
    plain = re.sub(r"``([^`]*)``", r"\1", rendered)
    plain = re.sub(r"(?<!`)`([^`]*)`_?", r"\1", plain)
    return {normalized(rendered), normalized(plain)}


def source_variants(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    variants = {normalized(raw)}
    variants.update(rst_rendered_variants(raw))
    variants.add(normalized(re.sub(r"[`*_#]", "", raw)))
    if "<html" in raw.casefold() or "<!doctype html" in raw.casefold():
        parser = VisibleText()
        try:
            parser.feed(raw)
        except Exception:
            pass
        variants.add(normalized(" ".join(parser.parts)))
    return [item for item in variants if item]


def fact_subject(fid: str, question: str) -> str:
    match = re.search(r"`([^`]+)`", question)
    return match.group(1) if match else fid


def equivalent_value(delivered: str, expected: str) -> bool:
    left = normalized(delivered)
    right = normalized(expected)
    if left == right:
        return True
    if left.startswith(right + " ("):
        return True
    if right.startswith('"') and right.endswith('"') and left == right.strip('"'):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected):
        for pattern in ("%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(delivered.strip(), pattern).date().isoformat() == expected
            except ValueError:
                continue
    return False


def quote_contains_value(quote: str, value: str) -> bool:
    q = normalized(value)
    body = normalized(quote)
    if q and q in body:
        return True
    if q.startswith('"') and q.endswith('"'):
        return q.strip('"') in body
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


def rule_result(item_id: str, score: bool, reason: str) -> dict[str, Any]:
    return {"id": item_id, "score": int(bool(score)), "reason": reason}


def agent_assessment(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, urls, sources = truth_rows()
    expected_ids = {str(row.get("fid")) for row in rows}
    output_root = workspace / "output"
    files: list[str] = []
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
    delivered = delivery if isinstance(delivery, dict) else {}
    if not isinstance(delivery, dict):
        contract_errors.append("top-level JSON value is not an object")
    elif set(delivered) != expected_ids:
        contract_errors.append(
            f"fact IDs differ: missing={sorted(expected_ids-set(delivered))}, "
            f"extra={sorted(set(delivered)-expected_ids)}"
        )
    for fid in sorted(expected_ids):
        item = delivered.get(fid)
        if not isinstance(item, dict):
            contract_errors.append(f"{fid} is not an object")
            continue
        if set(item) != FIELDS:
            contract_errors.append(f"{fid} has fields {sorted(item)}, expected {sorted(FIELDS)}")
        if any(not isinstance(item.get(field), str) for field in FIELDS):
            contract_errors.append(f"{fid} does not have four string fields")
        if item.get("angle") != fid.split("-", 1)[0]:
            contract_errors.append(f"{fid} has incorrect angle")

    docs = {
        angle: source_variants(HERE / "docs" / f"{source}.txt")
        for angle, source in sources.items()
    }
    values_ok = {angle: True for angle in ANGLES}
    citations_ok = {angle: True for angle in ANGLES}
    observations: list[dict[str, Any]] = []
    for row in rows:
        fid = str(row.get("fid"))
        angle = str(row.get("angle"))
        item = delivered.get(fid) if isinstance(delivered.get(fid), dict) else {}
        value = item.get("value") if isinstance(item.get("value"), str) else ""
        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        source_url = item.get("source_url") if isinstance(item.get("source_url"), str) else ""
        subject = fact_subject(fid, str(row.get("q", "")))
        value_ok = equivalent_value(value, str(row.get("answer", "")))
        quote_norm = normalized(quote)
        verbatim = bool(quote_norm) and any(quote_norm in source for source in docs.get(angle, []))
        url_ok = source_url == urls.get(angle, "")
        subject_ok = normalized(subject) in quote_norm
        reported_ok = quote_contains_value(quote, value)
        values_ok[angle] &= value_ok
        citations_ok[angle] &= verbatim and url_ok and subject_ok and reported_ok
        observations.append(
            {
                "fid": fid,
                "value": compact(value, 160),
                "value_correct": value_ok,
                "quote": compact(quote, 360),
                "verbatim": verbatim,
                "canonical_url": url_ok,
                "mentions_subject": subject_ok,
                "contains_reported_value": reported_ok,
            }
        )

    write_report(
        workspace,
        "agent_fact_evidence",
        {"contract_errors": contract_errors, "facts": observations},
    )
    rules = [
        rule_result(
            "ar_exact_single_json_contract",
            not contract_errors,
            "exact single-file 25-key/four-field JSON contract satisfied"
            if not contract_errors
            else compact("; ".join(contract_errors), 700),
        )
    ]
    for angle in ANGLES:
        failed = [row["fid"] for row in observations if row["fid"].startswith(angle + "-") and not row["value_correct"]]
        rules.append(
            rule_result(
                f"ar_{angle}_values_correct",
                values_ok[angle] and len([row for row in rows if row.get("angle") == angle]) == 5,
                f"all five {angle} values match pinned answers" if values_ok[angle] else f"incorrect {angle} values: {failed}",
            )
        )
    for angle in ANGLES:
        failed = [
            row["fid"]
            for row in observations
            if row["fid"].startswith(angle + "-")
            and not (
                row["verbatim"]
                and row["canonical_url"]
                and row["mentions_subject"]
                and row["contains_reported_value"]
            )
        ]
        rules.append(
            rule_result(
                f"ar_{angle}_citations_supported",
                citations_ok[angle] and len([row for row in rows if row.get("angle") == angle]) == 5,
                f"all five {angle} citations are canonical, verbatim, and directly supportive"
                if citations_ok[angle]
                else f"unsupported {angle} citations: {failed}",
            )
        )
    return rules, {
        "delivery": delivered,
        "delivery_available": isinstance(delivery, dict),
        "contract_ok": not contract_errors,
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
    name = name.strip().casefold().replace("-", "_")
    return name.rsplit("__", 1)[-1]


def event_turn(event: dict[str, Any]) -> int:
    value = event.get("conversation_turn", event.get("turn_index", 0))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def event_seq(event: dict[str, Any]) -> int:
    value = event.get("seq", 0)
    return int(value) if isinstance(value, int) else 0


def call_arguments(event: dict[str, Any]) -> Any:
    call = event.get("tool_call")
    return call.get("arguments") if isinstance(call, dict) else None


def event_prompt(event: dict[str, Any]) -> str:
    arguments = call_arguments(event)
    parts: list[str] = []
    if isinstance(arguments, dict):
        for key in ("task", "prompt", "message", "url", "command", "cmd"):
            if key in arguments:
                parts.extend(strings(arguments[key]))
        if not parts:
            parts.extend(strings(arguments))
    else:
        parts.extend(strings(arguments))
    raw = event.get("raw_event")
    if not parts:
        for value in named_values(raw, {"prompt", "task", "message", "content"}):
            parts.extend(strings(value))
    return "\n".join(dict.fromkeys(part for part in parts if part))


def result_text(event: dict[str, Any]) -> str:
    parts: list[str] = []
    result = event.get("tool_result")
    if isinstance(result, dict):
        parts.extend(strings(result.get("content")))
    raw = event.get("raw_event")
    for value in named_values(raw, {"message", "summary", "lastassistantmessage"}):
        parts.extend(strings(value))
    return "\n".join(dict.fromkeys(part for part in parts if part))


def tool_success(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    if result.get("exit_code") not in (None, 0, "0"):
        return False
    text = normalized(result.get("content"))
    return not (
        text.startswith("error:")
        or text.startswith('{"error"')
        or '"status":"error"' in text.replace(" ", "")
    )


def assigned_angles(text: str) -> list[str]:
    return sorted({f"A{value}" for value in re.findall(r"\bA([1-5])(?:-[1-5])?\b", text)})


def fact_ids(text: str) -> set[str]:
    return {match.group(0) for match in FACT_ID.finditer(text)}


def identity_values(event: dict[str, Any]) -> set[str]:
    names = {
        "agent_id",
        "agentid",
        "child_id",
        "child_principal_id",
        "childsessionid",
        "childsessionkey",
        "receiver_thread_ids",
        "resumedagentid",
        "canonical_task_name",
        "task_name",
    }
    found: set[str] = set()
    for value in named_values(event, names):
        for text in strings(value):
            if text and len(text) <= 300:
                found.add(text)
    for text in strings(event):
        if not text.lstrip().startswith(("{", "[")):
            continue
        try:
            embedded = json.loads(text)
        except json.JSONDecodeError:
            continue
        for value in named_values(embedded, names):
            for identity in strings(value):
                if identity and len(identity) <= 300:
                    found.add(identity)
    serialized = json.dumps(event, ensure_ascii=False)
    for pattern in (
        r'["\']?childSessionKey["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'["\']?childSessionId["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'["\']?agentId["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r'["\']?agent_id["\']?\s*[:=]\s*["\']([^"\'\s,}]+)',
        r"\bstarted\s+subagent\s+([^\s,;}]+)",
    ):
        found.update(re.findall(pattern, serialized, flags=re.IGNORECASE))
    if (
        str(event.get("principal_role", "")).casefold() == "child"
        or event.get("event_type") in {"task_completion", "subagent_lifecycle"}
    ):
        principal = event.get("principal_id")
        if isinstance(principal, str) and principal:
            found.add(principal)
    call = event.get("tool_call")
    if isinstance(call, dict) and tool_name(event) == "agent":
        call_id = str(call.get("tool_call_id") or "")
        if call_id:
            found.add(f"claudecode-child:{call_id}")
    return found


def is_child_event(event: dict[str, Any]) -> bool:
    if str(event.get("principal_role", "")).casefold() == "child":
        return True
    raw = json.dumps(event.get("raw_event"), ensure_ascii=False).casefold()
    return (
        '"issidechain": true' in raw
        or bool(re.search(r'"parent_tool_use_id"\s*:\s*"[^"\s]+', raw))
        or '"agent_role": "child"' in raw
    )


def json_objects(text: str) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder(object_pairs_hook=no_duplicate_object)
    objects: list[dict[str, Any]] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except (json.JSONDecodeError, DuplicateKeyError):
            continue
        if isinstance(value, dict) and any(FACT_ID.fullmatch(str(key)) for key in value):
            objects.append(value)
    unique: dict[str, dict[str, Any]] = {}
    for value in objects:
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        unique[key] = value
    return list(unique.values())


def best_fact_payload(text: str) -> dict[str, Any]:
    objects = json_objects(text)
    return max(
        objects,
        key=lambda value: sum(bool(FACT_ID.fullmatch(str(key))) for key in value),
        default={},
    )


def exact_fact_json(text: str, payload: dict[str, Any]) -> bool:
    """Accept a bare JSON object or one JSON code fence, with no commentary."""

    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*\n([\s\S]*?)\n```", candidate, re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1).strip()
    try:
        parsed = json.loads(candidate, object_pairs_hook=no_duplicate_object)
    except (json.JSONDecodeError, DuplicateKeyError):
        return False
    return isinstance(parsed, dict) and parsed == payload


def valid_handoff(payload: dict[str, Any], expected: set[str]) -> bool:
    if set(payload) != expected:
        return False
    for fid, item in payload.items():
        if not isinstance(item, dict) or set(item) != FIELDS:
            return False
        if any(not isinstance(item.get(field), str) for field in FIELDS):
            return False
        if item.get("angle") != fid.split("-", 1)[0]:
            return False
    return True


def dispatches(events: list[dict[str, Any]]) -> list[Dispatch]:
    rows: list[Dispatch] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        if tool_name(event) not in DISPATCH_TOOLS:
            continue
        prompt = event_prompt(event)
        angles = assigned_angles(prompt)
        rows.append(
            Dispatch(
                seq=event_seq(event),
                turn=event_turn(event),
                angle=angles[0] if len(angles) == 1 else "",
                prompt=prompt,
                ids=identity_values(event),
                success=tool_success(event),
            )
        )
    for event in events:
        if event.get("event_type") != "subagent_lifecycle":
            continue
        if normalized(event.get("action")) not in {"spawned", "started"}:
            continue
        event_ids = identity_values(event)
        direct = [row for row in rows if row.ids & event_ids]
        if len(direct) == 1:
            direct[0].ids.update(event_ids)
            continue
        text = "\n".join(strings(event.get("content")) + strings(event.get("raw_event")))
        angles = assigned_angles(text)
        matches = [row for row in rows if len(angles) == 1 and row.angle == angles[0]]
        if len(matches) == 1:
            matches[0].ids.update(event_ids)
            continue
        unbound = [row for row in rows if row.seq <= event_seq(event) and not row.ids]
        if event_ids and unbound:
            max(unbound, key=lambda row: row.seq).ids.update(event_ids)
    return rows


def completion_records(events: list[dict[str, Any]]) -> list[Completion]:
    rows: list[Completion] = []
    for event in events:
        kind = str(event.get("event_type", ""))
        texts: list[tuple[str, set[str]]] = []
        if (
            kind == "task_completion"
            and str(event.get("status", "completed")).casefold() in {"completed", "ok", "success"}
            and event.get("parent_reactivated") is True
        ):
            texts.append(("\n".join(strings(event.get("content"))), identity_values(event)))
        elif kind == "tool" and tool_name(event) in {"wait", "wait_agent"}:
            raw = event.get("raw_event")
            for states in named_values(raw, {"agents_states"}):
                if not isinstance(states, dict):
                    continue
                for child_id, state in states.items():
                    if not isinstance(state, dict) or normalized(state.get("status")) not in {"completed", "done", "ok"}:
                        continue
                    message = state.get("message")
                    if isinstance(message, str) and message.strip():
                        texts.append((message, {str(child_id)}))
        for text, ids in texts:
            payload = best_fact_payload(text)
            if text.strip() and ids:
                rows.append(
                    Completion(
                        seq=event_seq(event),
                        turn=event_turn(event),
                        ids=ids,
                        payload=payload,
                        source=kind if kind != "tool" else f"tool:{tool_name(event)}",
                        text_size=len(text.encode("utf-8")),
                        exact_json_envelope=exact_fact_json(text, payload),
                    )
                )
    unique: dict[tuple[str, tuple[str, ...]], Completion] = {}
    for row in rows:
        payload_key = json.dumps(row.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        key = f"{row.source}:{row.seq}:{payload_key}", tuple(sorted(row.ids))
        if key not in unique or row.seq < unique[key].seq:
            unique[key] = row
    return sorted(unique.values(), key=lambda row: row.seq)


def matching_dispatch(completion: Completion, rows: list[Dispatch]) -> list[Dispatch]:
    return [row for row in rows if row.ids & completion.ids]


def first_turn_start(events: list[dict[str, Any]], turn: int, default: int) -> int:
    seqs = [event_seq(event) for event in events if event.get("event_type") == "conversation_turn_started" and event_turn(event) == turn]
    return min(seqs) if seqs else default


def output_write_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        name = tool_name(event)
        text = event_prompt(event)
        mentions_output = bool(
            re.search(r"(?:^|[/\s'\"])(?:output/|/workspace/output/)research\.json\b", text)
        )
        direct_write = name in {"apply_patch", "create_file", "write", "write_file"}
        shell_write = name in {"bash", "exec", "exec_command", "shell"} and bool(
            re.search(
                r"(?:>|>>|\btee\b|\binstall\b|\bcp\b|\bmv\b)[^\n;|]*research\.json\b",
                text,
                re.IGNORECASE,
            )
        )
        if mentions_output and (direct_write or shell_write):
            found.append(event)
    return found


def trajectory_assessment(
    workspace: Path,
    events: list[dict[str, Any]],
    agent: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    event_types = [event.get("event_type") for event in events]
    starts = sorted(event_turn(event) for event in events if event.get("event_type") == "conversation_turn_started")
    finishes = sorted(event_turn(event) for event in events if event.get("event_type") == "conversation_turn_finished")
    lifecycle_ok = (
        event_types.count("conversation_started") == 1
        and starts == [1, 2, 3]
        and finishes == [1, 2, 3]
        and event_types.count("conversation_finished") == 1
        and not any(event.get("event_type") == "run_failed" for event in events)
        and any(event.get("event_type") == "run_finished" and event.get("trace_complete") is True for event in events)
    )

    dispatch_rows = dispatches(events)
    dispatch_plan_ok = (
        len(dispatch_rows) == 5
        and all(row.turn == 1 and row.angle for row in dispatch_rows)
        and sorted(row.angle for row in dispatch_rows) == list(ANGLES)
        and not any(EMULATION.search(event_prompt(event)) for event in events if event.get("event_type") == "tool")
    )
    dispatch_accepted = dispatch_plan_ok and all(row.success for row in dispatch_rows)
    dispatch_identity_observable = (
        dispatch_accepted
        and all(row.ids for row in dispatch_rows)
        and all(
            not (left.ids & right.ids)
            for index, left in enumerate(dispatch_rows)
            for right in dispatch_rows[index + 1 :]
        )
    )
    _, canonical_urls, _ = truth_rows()
    assignment_errors: list[str] = []
    for row in dispatch_rows:
        expected = {f"{row.angle}-{index}" for index in range(1, 6)} if row.angle else set()
        urls = {match.group(0).rstrip(".,);]") for match in URL.finditer(row.prompt)}
        schema_ok = all(f'"{field}"' in row.prompt for field in sorted(FIELDS))
        budget_text = normalized(row.prompt).replace(",", "")
        budget_ok = (
            bool(re.search(r"(?:at most|maximum|max)\s+one\s+(?:canonical\s+)?(?:url\s+)?retrieval", budget_text))
            and bool(re.search(r"(?:at most|maximum|max)\s+12\s+tool\s+calls?", budget_text))
            and "60000 utf-8 bytes" in budget_text
            and bool(re.search(r"(?:no|without)\s+(?:retrieval\s+)?retr(?:y|ies)", budget_text))
        )
        if fact_ids(row.prompt) != expected:
            assignment_errors.append(f"{row.angle or '?'} fact IDs")
        if row.angle and urls != {canonical_urls.get(row.angle, "")}:
            assignment_errors.append(f"{row.angle} URL scope")
        if not schema_ok:
            assignment_errors.append(f"{row.angle or '?'} schema")
        if not budget_ok:
            assignment_errors.append(f"{row.angle or '?'} local budget")
        if len(row.prompt.encode("utf-8")) > ASSIGNMENT_MAX_BYTES:
            assignment_errors.append(f"{row.angle or '?'} prompt exceeds {ASSIGNMENT_MAX_BYTES} bytes")
    assignments_ok = dispatch_plan_ok and not assignment_errors

    turn2_seq = first_turn_start(events, 2, 10**18)
    turn3_seq = first_turn_start(events, 3, 10**18)
    completions = completion_records(events)
    native_initial_delivery: dict[str, Completion] = {}
    if dispatch_identity_observable:
        for row in dispatch_rows:
            candidates = [
                completion
                for completion in completions
                if completion.seq < turn2_seq
                and matching_dispatch(completion, dispatch_rows) == [row]
            ]
            if candidates:
                native_initial_delivery[row.angle] = min(candidates, key=lambda item: item.seq)
    initial_completions_delivered = len(native_initial_delivery) == 5
    initial_terminal_angles = set(native_initial_delivery)
    for event in events:
        if (
            event.get("event_type") != "subagent_lifecycle"
            or normalized(event.get("action")) not in {"completed", "finished"}
            or event_seq(event) >= turn2_seq
        ):
            continue
        event_ids = identity_values(event)
        matches = [row for row in dispatch_rows if row.ids & event_ids]
        if len(matches) == 1:
            initial_terminal_angles.add(matches[0].angle)
    initial_groups: dict[str, list[Completion]] = {angle: [] for angle in ANGLES}
    for completion in completions:
        if completion.seq >= turn2_seq:
            continue
        for angle in ANGLES:
            expected = {f"{angle}-{index}" for index in range(1, 6)}
            if valid_handoff(completion.payload, expected):
                initial_groups[angle].append(completion)
    selected_initial: dict[str, Completion] = {}
    conflicting_initial: list[str] = []
    for angle, candidates in initial_groups.items():
        payloads = {
            json.dumps(row.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in candidates
        }
        if len(payloads) == 1 and candidates:
            selected_initial[angle] = min(candidates, key=lambda row: row.seq)
        elif len(payloads) > 1:
            conflicting_initial.append(angle)
    initial_complete = len(selected_initial) == 5 and not conflicting_initial
    initial_compact = initial_complete and all(
        row.text_size <= HANDOFF_MAX_BYTES and row.exact_json_envelope
        for row in selected_initial.values()
    )

    continuation_events = [
        event
        for event in events
        if event.get("event_type") == "tool"
        and not is_child_event(event)
        and tool_name(event) in CONTINUATION_TOOLS
    ]
    continuation_event: dict[str, Any] | None = None
    continuation_dispatch: Dispatch | None = None
    continuation_fact = ""
    if len(continuation_events) == 1:
        candidate = continuation_events[0]
        prompt = event_prompt(candidate)
        facts = sorted(fact_ids(prompt))
        serialized = json.dumps(candidate, ensure_ascii=False)
        targets = [row for row in dispatch_rows if row.ids and any(identifier in serialized for identifier in row.ids)]
        if (
            event_turn(candidate) == 2
            and len(facts) == 1
            and len(targets) == 1
            and facts[0].startswith(targets[0].angle + "-")
        ):
            continuation_event = candidate
            continuation_dispatch = targets[0]
            continuation_fact = facts[0]
    continuation_plan_ok = continuation_event is not None and dispatch_plan_ok
    continuation_accepted = continuation_plan_ok and tool_success(continuation_event)

    native_update_delivery: Completion | None = None
    if continuation_event is not None and continuation_dispatch is not None:
        candidates = [
            completion
            for completion in completions
            if event_seq(continuation_event) < completion.seq < turn3_seq
            and matching_dispatch(completion, dispatch_rows) == [continuation_dispatch]
        ]
        if candidates:
            native_update_delivery = min(candidates, key=lambda item: item.seq)
    update_completion_delivered = continuation_accepted and native_update_delivery is not None
    update_terminal_observed = update_completion_delivered
    if continuation_event is not None and continuation_dispatch is not None:
        for event in events:
            if (
                event.get("event_type") == "subagent_lifecycle"
                and normalized(event.get("action")) in {"completed", "finished"}
                and event_seq(continuation_event) < event_seq(event) < turn3_seq
                and bool(continuation_dispatch.ids & identity_values(event))
            ):
                update_terminal_observed = True

    update_candidates: list[Completion] = []
    if continuation_event is not None and continuation_dispatch is not None:
        for completion in completions:
            if not (event_seq(continuation_event) < completion.seq < turn3_seq):
                continue
            if valid_handoff(completion.payload, {continuation_fact}):
                matches = matching_dispatch(completion, dispatch_rows)
                if len(matches) == 1 and matches[0] is continuation_dispatch:
                    update_candidates.append(completion)
    update_payloads = {
        json.dumps(row.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in update_candidates
    }
    update_completion = min(update_candidates, key=lambda row: row.seq) if len(update_payloads) == 1 and update_candidates else None
    update_ok = update_completion is not None
    handoffs_compact = (
        initial_compact
        and update_completion is not None
        and update_completion.text_size <= HANDOFF_MAX_BYTES
        and update_completion.exact_json_envelope
    )

    parent_source_violations: list[dict[str, Any]] = []
    recovery_violations: list[dict[str, Any]] = []
    emulation_violations: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "tool" or is_child_event(event):
            continue
        name = tool_name(event)
        prompt = event_prompt(event)
        lower = normalized(prompt)
        if name not in DISPATCH_TOOLS | CONTINUATION_TOOLS:
            urls = URL.findall(prompt)
            if name in NETWORK_TOOLS or (
                name in {"bash", "exec", "exec_command", "shell"}
                and re.search(
                    r"\b(?:curl|wget)\b|requests\.(?:get|post)\s*\(|urllib\.request|httpx\.(?:get|post)\s*\(",
                    lower,
                )
            ):
                parent_source_violations.append({"seq": event_seq(event), "tool": name, "urls": urls[:3]})
        history = name in {"read_agent", "read_thread", "sessions_history"}
        file_recovery = bool(
            re.search(r"(?:/tasks/|tasks/)[^\s]*\.output\b|subagent[^\s]*(?:\.json|\.output)|_(?:data|verify)\.json", lower)
            and (name in {"bash", "exec", "exec_command", "read", "shell"} or re.search(r"\b(?:cat|head|less|open|read|tail)\b", lower))
        )
        if history or file_recovery:
            recovery_violations.append({"seq": event_seq(event), "tool": name})
        if EMULATION.search(prompt):
            emulation_violations.append({"seq": event_seq(event), "tool": name})
    parent_isolated = bool(events) and not parent_source_violations
    no_recovery = bool(events) and not recovery_violations and not emulation_violations

    writes = output_write_events(events)
    first_write = min(writes, key=event_seq) if writes else None
    final_waits = [
        event
        for event in events
        if event.get("event_type") == "tool"
        and not is_child_event(event)
        and event_turn(event) == 3
        and tool_name(event) in WAIT_TOOLS
    ]
    finalization_ok = (
        initial_complete
        and update_ok
        and first_write is not None
        and event_turn(first_write) == 3
        and update_completion is not None
        and update_completion.seq < event_seq(first_write)
        and not any(event_turn(event) in {1, 2} for event in writes)
        and not final_waits
    )

    final_delivery = agent.get("delivery") if isinstance(agent.get("delivery"), dict) else {}
    expected_from_handoffs: dict[str, Any] = {}
    if initial_complete:
        for completion in selected_initial.values():
            expected_from_handoffs.update(completion.payload)
    if update_completion is not None:
        expected_from_handoffs.update(update_completion.payload)
    derived_ok = (
        finalization_ok
        and agent.get("contract_ok") is True
        and final_delivery == expected_from_handoffs
        and len(expected_from_handoffs) == 25
    )

    write_report(
        workspace,
        "mechanism_evidence",
        {
            "trace_event_count": len(events),
            "dispatches": [
                {"seq": row.seq, "turn": row.turn, "angle": row.angle, "ids": sorted(row.ids), "success": row.success}
                for row in dispatch_rows
            ],
            "assignment_errors": assignment_errors,
            "initial_handoffs": {
                angle: {
                    "seq": row.seq,
                    "source": row.source,
                    "ids": sorted(row.ids),
                    "text_size": row.text_size,
                    "exact_json_envelope": row.exact_json_envelope,
                }
                for angle, row in selected_initial.items()
            },
            "conflicting_initial_handoffs": conflicting_initial,
            "dispatch_plan_ok": dispatch_plan_ok,
            "dispatch_accepted": dispatch_accepted,
            "dispatch_identity_observable": dispatch_identity_observable,
            "native_initial_completion_angles": sorted(native_initial_delivery),
            "native_initial_terminal_angles": sorted(initial_terminal_angles),
            "continuation": {
                "seq": event_seq(continuation_event) if continuation_event else None,
                "fact": continuation_fact or None,
                "angle": continuation_dispatch.angle if continuation_dispatch else None,
                "plan_ok": continuation_plan_ok,
                "accepted": continuation_accepted,
            },
            "native_updated_completion_delivered": update_completion_delivered,
            "native_updated_terminal_observed": update_terminal_observed,
            "updated_handoff": {
                "seq": update_completion.seq if update_completion else None,
                "source": update_completion.source if update_completion else None,
                "ids": sorted(update_completion.ids) if update_completion else [],
                "text_size": update_completion.text_size if update_completion else None,
                "exact_json_envelope": update_completion.exact_json_envelope if update_completion else None,
            },
            "handoffs_compact": handoffs_compact,
            "parent_source_violations": parent_source_violations,
            "recovery_violations": recovery_violations,
            "emulation_violations": emulation_violations,
            "output_writes": [{"seq": event_seq(event), "turn": event_turn(event), "tool": tool_name(event)} for event in writes],
            "turn3_waits": [{"seq": event_seq(event), "tool": tool_name(event)} for event in final_waits],
            "final_matches_handoffs": derived_ok,
        },
    )

    if dispatch_plan_ok:
        dispatch_reason = (
            "all five required native dispatch calls were accepted"
            if dispatch_accepted
            else f"required dispatches accepted={sum(row.success for row in dispatch_rows)}/5"
        )
    else:
        dispatch_reason = "[AGENT BYPASS] Agent did not issue the exact five-angle native dispatch plan."
    if not dispatch_plan_ok:
        lineage_reason = "[AGENT BYPASS] Exact native dispatch plan was not exercised."
    elif not dispatch_accepted:
        lineage_reason = "dispatch rejection prevented complete child identity observation"
    else:
        lineage_reason = (
            "five distinct child identities are observable"
            if dispatch_identity_observable
            else "one or more accepted dispatches lack a distinct observable child identity"
        )
    if not dispatch_plan_ok:
        initial_delivery_reason = "[AGENT BYPASS] Exact native dispatch plan was not exercised."
    elif not dispatch_accepted:
        initial_delivery_reason = "one or more required dispatch calls were rejected before completion delivery"
    else:
        initial_delivery_reason = (
            "dispatches accepted=5/5; native child terminal evidence=5/5; typed parent-visible initial completions=5/5"
            if initial_completions_delivered
            else f"dispatches accepted=5/5; native child terminal evidence={len(initial_terminal_angles)}/5; typed parent-visible initial completions={len(native_initial_delivery)}/5"
        )
    if not continuation_plan_ok:
        continuation_reason = "[AGENT BYPASS] Agent did not issue one valid same-child continuation request."
    else:
        continuation_reason = (
            "the valid same-child continuation call was accepted"
            if continuation_accepted
            else "the valid same-child continuation call was rejected"
        )
    if not continuation_plan_ok:
        update_delivery_reason = "[AGENT BYPASS] Same-child continuation was not exercised."
    elif not continuation_accepted:
        update_delivery_reason = "the valid continuation call was rejected before updated completion delivery"
    else:
        update_delivery_reason = (
            "continuation accepted; native updated terminal evidence=True; typed same-child updated completion delivered"
            if update_completion_delivered
            else f"continuation accepted; native updated terminal evidence={update_terminal_observed}; typed same-child updated completion missing"
        )

    harness_rules = [
        rule_result("hr_three_turn_trace_complete", lifecycle_ok, "complete normalized three-turn lifecycle" if lifecycle_ok else f"incomplete lifecycle: starts={starts}, finishes={finishes}, events={len(events)}"),
        rule_result("hr_native_dispatch_calls_accepted", dispatch_accepted, dispatch_reason),
        rule_result("hr_distinct_child_identities_observable", dispatch_identity_observable, lineage_reason),
        rule_result("hr_initial_completions_delivered", initial_completions_delivered, initial_delivery_reason),
        rule_result("hr_same_child_continuation_accepted", continuation_accepted, continuation_reason),
        rule_result("hr_updated_completion_delivered", update_completion_delivered, update_delivery_reason),
    ]
    protocol_rules = [
        rule_result("ar_exact_native_dispatch_set", dispatch_plan_ok, "Agent issued exactly one native dispatch for each of A1-A5" if dispatch_plan_ok else (compact([{"turn": row.turn, "angle": row.angle} for row in dispatch_rows]) or "no recognized native dispatch rows in trace")),
        rule_result("ar_child_assignments_isolated", assignments_ok, "all child prompts contain only their five facts, canonical URL, compact budget, and four-field schema" if assignments_ok else f"assignment errors: {assignment_errors}"),
        rule_result("ar_initial_handoffs_well_formed", initial_complete, "five exact five-fact handoffs arrived before turn 2" if initial_complete else f"available={sorted(selected_initial)}, conflicts={conflicting_initial}"),
        rule_result("ar_native_handoffs_compact", handoffs_compact, f"all six native handoffs are clean JSON envelopes at most {HANDOFF_MAX_BYTES} UTF-8 bytes" if handoffs_compact else "one or more required native handoffs are missing, contain text outside the JSON envelope, or exceed the compactness limit"),
        rule_result("ar_same_child_reverification_requested", continuation_plan_ok, f"Agent targeted original {continuation_dispatch.angle} child for {continuation_fact}" if continuation_plan_ok and continuation_dispatch else f"continuation attempts={len(continuation_events)}"),
        rule_result("ar_updated_handoff_well_formed", update_ok, f"same child returned exact updated JSON for {continuation_fact}" if update_ok else f"valid updated handoffs={len(update_payloads)}"),
        rule_result("ar_parent_did_not_research_sources", parent_isolated, "no parent-side source retrieval or research" if parent_isolated else f"parent source violations: {parent_source_violations}"),
        rule_result("ar_no_history_or_file_recovery", no_recovery, "no transcript/history/file recovery or subagent emulation" if no_recovery else f"recovery={recovery_violations}, emulation={emulation_violations}"),
        rule_result("ar_turn_protocol_and_finalization_order", finalization_ok, "turn-3 write followed all required handoffs and used no final wait" if finalization_ok else f"writes={[(event_seq(e), event_turn(e)) for e in writes]}, turn3_waits={len(final_waits)}, update={update_ok}"),
        rule_result("ar_final_output_derived_from_handoffs", derived_ok, "final JSON exactly equals native handoffs with the update applied last" if derived_ok else "final JSON does not exactly match the complete native handoff set"),
    ]
    return harness_rules, protocol_rules


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    agent_rules, agent_observations = agent_assessment(args.workspace)
    run_dir = Path(os.environ.get("HARNESSBENCH_RUN_DIR", ""))
    events = trace_events(run_dir) if str(run_dir) not in {"", "."} else []
    harness_rules, protocol_rules = trajectory_assessment(args.workspace, events, agent_observations)
    agent_rules = [*protocol_rules, *agent_rules]
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
