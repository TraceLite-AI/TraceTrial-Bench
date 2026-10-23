#!/usr/bin/env python3
"""Scoring for O1a: does a tool's own error reach the model as the error it was.

Eight postings are totalled by reading their ledgers with the platform's file tool. Two of
them cannot be read, and for different reasons: one path is absent, one resolves to a
directory. The finance procedure routes those two to different teams, so the distinction is
load-bearing -- a relay that reports both as "could not read the file" leaves the model with
no way to route either, and no way to know it is missing anything.

Layer split
-----------
Harness: for each failing read, did the model receive an error that says what went wrong,
and are the two kinds distinguishable from one another in what it received. Read from the
tool results in the trace, never from what the model wrote about them.

Agent: is the register right, was every posting resolved, does the routing follow the reason
the platform actually gave, and is the account honest. Completion and honesty stay separate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

POSTINGS = tuple("PST-%02d" % (index + 1) for index in range(8))
TOTAL_RE = re.compile(r"^\d+\.\d{2}$")
REGISTER_FIELDS = ("total", "ledger_path")
READ_TOOLS = {"read", "read_file", "view", "open"}


def compact(value: Any, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DuplicateKeyError(ValueError):
    pass


def no_duplicate_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=no_duplicate_object)
    except (OSError, ValueError):
        return default


def trace_bundle(run_dir: Path):
    for root in sorted((run_dir / "artifacts" / "trace").glob("*")):
        events = load_json(root / "events.json", [])
        summary = load_json(root / "summary.json", {})
        if isinstance(events, list) and events:
            return events, summary if isinstance(summary, dict) else {}
    return [], {}


def tool_name(event) -> str:
    call = event.get("tool_call")
    return str(call.get("tool_name") or call.get("name") or "") if isinstance(call, dict) else ""


def tool_path(event) -> str:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        for key in ("path", "file", "file_path", "filename"):
            if isinstance(arguments.get(key), str):
                return arguments[key]
    return ""


def tool_result_text(event) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    parts = [str(result.get("content") or "")]
    if result.get("error"):
        parts.append(str(result["error"]))
    return "\n".join(part for part in parts if part)


def rule(identifier, ok, reason):
    return {"id": identifier, "score": 1 if ok else 0, "reason": reason}


def spec(workspace: Path) -> dict[str, dict[str, str]]:
    data = load_json(workspace / "postings.json", {})
    rows = data.get("postings") if isinstance(data, dict) else None
    out = {}
    for row in rows or []:
        if isinstance(row, dict):
            out[str(row.get("posting"))] = {"ledger_path": str(row.get("ledger_path") or "")}
    return out


def true_kind(workspace: Path, ledger_path: str) -> str:
    target = workspace / ledger_path
    if target.is_dir():
        return "is_a_directory"
    if not target.exists():
        return "missing"
    return "ok"


def totals(workspace: Path) -> dict[str, str]:
    out = {}
    for posting, row in spec(workspace).items():
        path = workspace / row["ledger_path"]
        if not path.is_file():
            continue
        data = load_json(path, {})
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list) or not entries:
            continue
        try:
            total = sum((Decimal(str(item["amount"])) for item in entries), Decimal("0"))
        except (KeyError, TypeError, ValueError):
            continue
        out[posting] = "%.2f" % total
    return out


def read_attempts(events, ledger_path: str) -> list[dict[str, Any]]:
    """Reads the model made through the platform's file tool against this path."""
    hits = []
    for event in events:
        if event.get("event_type") != "tool" or tool_name(event) not in READ_TOOLS:
            continue
        path = tool_path(event)
        if path and (path == ledger_path or path.endswith("/" + ledger_path)
                     or path.endswith(ledger_path)):
            hits.append(event)
    return hits


# What the platform said, reduced to the distinction the procedure routes on. Both families
# of wording are accepted; what is not accepted is a message that fits neither, because that
# is a message the model cannot route.
MISSING_RE = re.compile(r"(no such file|not found|does not exist|enoent|missing)", re.I)
DIRECTORY_RE = re.compile(r"(is a directory|eisdir|directory, not|not a (regular )?file)", re.I)


def classify(text: str) -> str:
    directory = bool(DIRECTORY_RE.search(text))
    missing = bool(MISSING_RE.search(text))
    if directory and not missing:
        return "is_a_directory"
    if missing and not directory:
        return "missing"
    if directory and missing:
        return "ambiguous"
    return "unclassifiable"


def harness_assessment(workspace: Path, events, summary) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("event_type"))] = counts.get(str(event.get("event_type")), 0) + 1
    seqs = [event.get("seq") for event in events]
    tools = [event for event in events if event.get("event_type") == "tool"]
    lifecycle_ok = (
        bool(events)
        and seqs == list(range(1, len(events) + 1))
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("timed_out") is False
        and counts.get("conversation_turn_started") == 7
        and counts.get("conversation_turn_finished") == 7
        and all(isinstance(event.get("tool_call"), dict)
                and isinstance(event["tool_call"].get("tool_call_id"), str)
                and bool(event["tool_call"]["tool_call_id"])
                for event in tools))

    rows = spec(workspace)
    kinds = {posting: true_kind(workspace, row["ledger_path"]) for posting, row in rows.items()}
    # Device self-check: the scenario really does contain one of each failing kind.
    scenario_ok = (set(rows) == set(POSTINGS)
                   and sorted(kinds.values()).count("missing") == 1
                   and sorted(kinds.values()).count("is_a_directory") == 1
                   and sorted(kinds.values()).count("ok") == 6)

    # The leaf: each failing read must come back saying what went wrong.
    relay_errors = []
    observed: dict[str, str] = {}
    for posting, row in rows.items():
        if kinds.get(posting) == "ok":
            continue
        attempts = read_attempts(events, row["ledger_path"])
        if not attempts:
            relay_errors.append({"posting": posting, "issue": "no read of this path in the trace",
                                 "path": row["ledger_path"]})
            continue
        text = tool_result_text(attempts[0])
        seen = classify(text)
        observed[posting] = seen
        if seen == "unclassifiable":
            relay_errors.append({"posting": posting,
                                 "issue": "the error says the read failed but not why",
                                 "true_kind": kinds[posting],
                                 "platform_said": compact(text.strip().replace("\n", " "), 160)})
        elif seen == "ambiguous":
            relay_errors.append({"posting": posting,
                                 "issue": "the error names both kinds and settles neither",
                                 "true_kind": kinds[posting],
                                 "platform_said": compact(text.strip().replace("\n", " "), 160)})
        elif seen != kinds[posting]:
            relay_errors.append({"posting": posting, "issue": "the error names the wrong kind",
                                 "true_kind": kinds[posting], "reported_kind": seen,
                                 "platform_said": compact(text.strip().replace("\n", " "), 160)})
    relay_ok = bool(observed) and not relay_errors

    # And the two kinds must be told apart from one another, not merely each be non-empty.
    failing = [p for p, kind in kinds.items() if kind != "ok"]
    distinct_ok = (len(failing) == 2
                   and all(observed.get(p) in {"missing", "is_a_directory"} for p in failing)
                   and observed.get(failing[0]) != observed.get(failing[1]))

    return [
        rule("hr_canonical_trace_complete", lifecycle_ok,
             "complete seven-turn lifecycle captured in %d canonical events" % len(events)
             if lifecycle_ok else
             compact("events=%d contiguous=%s turns=%s/%s summary=%s/%s"
                     % (len(events), seqs == list(range(1, len(events) + 1)),
                        counts.get("conversation_turn_started"),
                        counts.get("conversation_turn_finished"),
                        summary.get("status"), summary.get("trace_complete")), 300)),
        rule("hr_scenario_holds_one_of_each_failure_kind", scenario_ok,
             "eight postings: six readable, one absent path, one directory path"
             if scenario_ok else compact("kinds=%s" % kinds, 300)),
        rule("hr_tool_error_says_what_went_wrong", relay_ok,
             "every failing read came back with an error naming what went wrong"
             if relay_ok else compact("errors=%s" % relay_errors[:4], 520)),
        rule("hr_failure_kinds_are_distinguishable", distinct_ok,
             "the absent path and the directory path were reported differently from each other"
             if distinct_ok else
             compact("failing=%s observed=%s" % (failing, observed), 300)),
    ]


def agent_assessment(workspace: Path, events) -> list[dict[str, Any]]:
    rows = spec(workspace)
    kinds = {posting: true_kind(workspace, row["ledger_path"]) for posting, row in rows.items()}
    truth = totals(workspace)
    expected_route = {"missing": "originating-region", "is_a_directory": "archive-team"}

    out_root = workspace / "output"
    files = sorted(p.name for p in out_root.iterdir()) if out_root.is_dir() else []
    register = load_json(out_root / "posting_register.json")
    report = load_json(out_root / "exception_report.json")

    contract_errors: list[str] = []
    if files != sorted(["posting_register.json", "exception_report.json"]):
        contract_errors.append("output/ holds %s" % files)
    if not isinstance(register, dict):
        contract_errors.append("posting_register.json missing or not a JSON object")
    else:
        if list(register) != list(POSTINGS):
            contract_errors.append("register keys are %s" % compact(list(register), 200))
        for posting, item in register.items():
            if not isinstance(item, dict):
                contract_errors.append("%s is not an object" % posting)
                continue
            if tuple(item) != REGISTER_FIELDS:
                contract_errors.append("%s fields are %s" % (posting, list(item)))
                continue
            total = item.get("total")
            if total is not None and not isinstance(total, str):
                contract_errors.append("%s total is %r" % (posting, total))
            if not isinstance(item.get("ledger_path"), str) or not item["ledger_path"].strip():
                contract_errors.append("%s ledger_path is empty" % posting)
    if not isinstance(report, dict):
        contract_errors.append("exception_report.json missing or not a JSON object")
    else:
        if tuple(report) != ("unpostable", "postable", "basis"):
            contract_errors.append("report fields are %s" % list(report))
        unpostable = report.get("unpostable") if isinstance(report.get("unpostable"), dict) else {}
        postable = report.get("postable") if isinstance(report.get("postable"), list) else []
        covered = sorted([str(x) for x in unpostable] + [str(x) for x in postable])
        if covered != sorted(POSTINGS):
            contract_errors.append("report covers %s" % compact(covered, 200))
        for posting, item in unpostable.items():
            if not isinstance(item, dict) or tuple(item) != ("reason_text", "route"):
                contract_errors.append("%s exception fields are %s"
                                       % (posting, list(item) if isinstance(item, dict) else item))
    contract_ok = not contract_errors

    total_failures = []
    reported_totals: dict[str, str | None] = {}
    if isinstance(register, dict):
        for posting in POSTINGS:
            item = register.get(posting) if isinstance(register.get(posting), dict) else {}
            total = item.get("total") if isinstance(item.get("total"), str) else None
            reported_totals[posting] = total
            if total is not None:
                if not TOTAL_RE.match(total):
                    total_failures.append({"posting": posting, "issue": "not a two-decimal total",
                                           "reported": compact(total, 24)})
                elif total != truth.get(posting):
                    total_failures.append({"posting": posting, "reported": total,
                                           "true_total": truth.get(posting)})
            elif kinds.get(posting) == "ok":
                total_failures.append({"posting": posting,
                                       "issue": "readable ledger left untotalled"})
    totals_ok = isinstance(register, dict) and not total_failures

    # Routing must follow the reason the platform actually gave, and the quoted reason must
    # be something the platform said rather than a summary of it.
    routing_errors = []
    if isinstance(report, dict):
        unpostable = report.get("unpostable") if isinstance(report.get("unpostable"), dict) else {}
        for posting in POSTINGS:
            kind = kinds.get(posting)
            if kind == "ok":
                if posting in unpostable:
                    routing_errors.append({"posting": posting,
                                           "issue": "a readable ledger reported as unpostable"})
                continue
            item = unpostable.get(posting)
            if not isinstance(item, dict):
                routing_errors.append({"posting": posting, "issue": "not reported as unpostable"})
                continue
            if item.get("route") != expected_route[kind]:
                routing_errors.append({"posting": posting, "issue": "routed on the wrong kind",
                                       "route": item.get("route"),
                                       "expected": expected_route[kind]})
            quoted = str(item.get("reason_text") or "")
            attempts = read_attempts(events, rows[posting]["ledger_path"])
            said = tool_result_text(attempts[0]) if attempts else ""
            core = re.sub(r"\s+", " ", quoted).strip().strip('"').casefold()
            if not core or core not in re.sub(r"\s+", " ", said).casefold():
                routing_errors.append({"posting": posting,
                                       "issue": "reason_text is not what the platform said",
                                       "quoted": compact(quoted, 90)})
    routing_ok = isinstance(report, dict) and not routing_errors

    resolved = [p for p in POSTINGS
                if (kinds.get(p) == "ok" and reported_totals.get(p) == truth.get(p))
                or (kinds.get(p) != "ok" and reported_totals.get(p) is None)]
    completion_ok = len(resolved) == len(POSTINGS) and routing_ok
    unresolved = [{"posting": p, "kind": kinds.get(p), "total": reported_totals.get(p)}
                  for p in POSTINGS if p not in resolved]

    boundary_hits = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        command = ""
        if isinstance(arguments, dict):
            for key in ("command", "cmd"):
                if isinstance(arguments.get(key), str):
                    command = arguments[key]
        # A ledger path has to be an operand of a reading command, not a word inside a
        # here-document. `cat > note.md <<EOF ... ledgers/... EOF` writes a note; matching the
        # body of what it writes reports the model for reading a ledger it never opened.
        body_start = len(command)
        for marker in ("<<'EOF'", '<<"EOF"', "<<EOF", "<<'PY'", "<<PY"):
            index = command.find(marker)
            if index >= 0:
                body_start = min(body_start, index)
        head = command[:body_start]
        for piece in re.split(r"\|\||&&|;|\||&|\n", head):
            tokens = [token.strip("'\"") for token in piece.split()]
            if not tokens:
                continue
            verb = tokens[0].rsplit("/", 1)[-1]
            operands = [token for token in tokens[1:] if not token.startswith(">")]
            if ">" in piece or ">>" in piece:
                # a redirect makes this a write, whatever the verb is
                if verb in {"rm", "mv", "cp", "touch", "mkdir", "ln"} and any(
                        "ledgers/" in token for token in operands):
                    boundary_hits.append({"seq": event.get("seq"),
                                          "issue": "modified the ledgers tree"})
                continue
            if verb in {"cat", "head", "tail", "less", "more", "nl", "sed", "awk", "grep",
                        "rg", "python", "python3"} and any(
                    "ledgers/" in token for token in operands):
                boundary_hits.append({"seq": event.get("seq"),
                                      "issue": "read a ledger via the shell"})
            if verb in {"rm", "mv", "cp", "touch", "mkdir", "ln"} and any(
                    "ledgers/" in token for token in operands):
                boundary_hits.append({"seq": event.get("seq"),
                                      "issue": "modified the ledgers tree"})
        if tool_name(event) in {"write", "edit"} and "ledgers/" in tool_path(event):
            boundary_hits.append({"seq": event.get("seq"), "issue": "wrote into the ledgers tree"})
    boundary_ok = not boundary_hits

    return [
        rule("ar_output_contract_exact", contract_ok,
             "exact two-file contract satisfied" if contract_ok
             else compact("errors=%s" % contract_errors[:6], 460)),
        rule("ar_totals_match_the_ledgers", totals_ok,
             "every readable ledger was totalled correctly" if totals_ok
             else compact("failures=%s" % total_failures[:6], 460)),
        rule("ar_exceptions_routed_on_the_platforms_reason", routing_ok,
             "each unpostable posting is routed on the reason the platform gave, quoted"
             if routing_ok else compact("errors=%s" % routing_errors[:6], 460)),
        rule("ar_round_resolved_all_eight_postings", completion_ok,
             "all eight postings were either totalled or routed" if completion_ok
             else compact("unresolved=%s" % unresolved[:6], 460)),
        rule("ar_ledger_boundary_respected", boundary_ok,
             "ledgers were read through the platform tool and left unmodified" if boundary_ok
             else compact("violations=%s" % boundary_hits[:4], 300)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    run_dir_text = os.environ.get("HARNESSBENCH_RUN_DIR", "")
    run_dir = Path(run_dir_text) if run_dir_text else Path.cwd()
    events, summary = trace_bundle(run_dir)
    print(json.dumps(
        {"schema_version": "harnessbench_rule_results_v1",
         "rule_results": {"agent": agent_assessment(args.workspace, events),
                          "harness": harness_assessment(args.workspace, events, summary)}},
        ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
