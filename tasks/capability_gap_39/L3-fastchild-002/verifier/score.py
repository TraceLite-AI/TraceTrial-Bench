#!/usr/bin/env python3
"""Scoring for L3c: does each concurrent child's result stay bound to the child that made it.

Six lanes are tallied at the same time by six native children. Every lane's figure falls in
the same narrow band, so a result attached to the wrong lane still looks plausible; only the
per-tally seal distinguishes them. Lanes also settle at different speeds, so results return
out of dispatch order. Aggregation is correct only if the platform delivers each child's
result to the parent whole and still identifiable as that child's.

Layer split
-----------
Harness: were six native dispatches accepted, did each child's result reach the parent
verbatim, and can each returned result be attributed to the dispatch that produced it. The
last question is the leaf; it is answered from the child session transcripts and the parent's
tool results, not from what the parent later claims.

Agent: is the register right, was the round completed, did the coordinator stay out of the
tally channel, and is the account of what bound each result to its lane honest. Completion
and honesty are scored separately, for the same reason as elsewhere in this suite: an honest
`null` is the right way to report a result the parent cannot attribute, and rewarding it must
not also mark the round as finished.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

LANES = tuple("LN-%02d" % (71 + index) for index in range(6))
SEAL_RE = re.compile(r"^TALLY-[0-9A-F]{12}$")
FIGURE_RE = re.compile(r"^\d+\.\d{2}$")
REGISTER_FIELDS = ("mean_units", "seal", "reported_by")
DISPATCH_TOOLS = {"sessions_spawn", "spawn_agent", "delegate_task", "agent", "subagents"}
PROTECTED = ("bin/tally_lane.sh", "state/tally_runs.jsonl", "lanes/")
SHELL_SPLIT_RE = re.compile(r"(\|\||&&|;|\||&|\n)")
NON_READING_COMMANDS = {"ls", "stat", "find", "pwd", "test", "du", "tree", "echo", "printf",
                        "true", "mkdir", "cd", "basename", "dirname"}


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16].upper()


def compact(value: Any, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


class DuplicateKeyError(ValueError):
    pass


def no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def trace_bundle(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    for root in sorted((run_dir / "artifacts" / "trace").glob("*")):
        events = load_json(root / "events.json", [])
        summary = load_json(root / "summary.json", {})
        if isinstance(events, list) and events:
            return events, summary if isinstance(summary, dict) else {}
    return [], {}


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    return str(call.get("tool_name") or call.get("name") or "") if isinstance(call, dict) else ""


def tool_arguments(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    arguments = call.get("arguments")
    if isinstance(arguments, dict):
        return " ".join(str(value) for value in arguments.values())
    return str(arguments or "")


def tool_result_text(event: dict[str, Any]) -> str:
    result = event.get("tool_result")
    return str(result.get("content") or "") if isinstance(result, dict) else ""


def command_pieces(command: str) -> list[str]:
    parts, buffer, quote, index = [], [], "", 0
    while index < len(command):
        char = command[index]
        if quote:
            buffer.append(char)
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "'\"":
            quote = char
            buffer.append(char)
            index += 1
            continue
        match = SHELL_SPLIT_RE.match(command, index)
        if match:
            parts.append("".join(buffer))
            buffer = []
            index = match.end()
            continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer))
    return [part.strip() for part in parts if part.strip()]


def is_child_event(event: dict[str, Any]) -> bool:
    return str(event.get("principal_role", "")).strip().casefold() == "child"


def is_dispatch_call(event: dict[str, Any]) -> bool:
    """A dispatch hands a child something to do; a management call does not."""
    if tool_name(event) not in DISPATCH_TOOLS:
        return False
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        return any(str(arguments.get(key) or "").strip()
                   for key in ("task", "prompt", "instructions", "message"))
    return bool(str(arguments or "").strip())


def tool_success(event: dict[str, Any]) -> bool:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return False
    if result.get("error") not in (None, False, "false", 0):
        return False
    text = str(result.get("content") or "").strip()
    if text.casefold().startswith("error:"):
        return False
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            if str(payload.get("status", "")).casefold() == "error":
                return False
            if payload.get("error") not in (None, False, "", "false", 0):
                return False
    return True


def rule(identifier: str, ok: bool, reason: str) -> dict[str, Any]:
    return {"id": identifier, "score": 1 if ok else 0, "reason": reason}


def authoritative(workspace: Path) -> dict[str, str]:
    out = {}
    for lane in LANES:
        spec = load_json(workspace / "lanes" / ("%s.json" % lane), {})
        pallets = spec.get("pallets") if isinstance(spec, dict) else None
        if not isinstance(pallets, list) or not pallets:
            continue
        try:
            total = sum((Decimal(str(item["units"])) for item in pallets), Decimal("0"))
        except (KeyError, TypeError, ValueError):
            continue
        out[lane] = "%.2f" % (total / Decimal(len(pallets)))
    return out


def tally_payloads(text: str) -> list[dict[str, Any]]:
    """Every tally object in a piece of text, in order."""
    decoder = json.JSONDecoder()
    found, index = [], 0
    while True:
        index = text.find("{", index)
        if index < 0:
            return found
        try:
            payload, end = decoder.raw_decode(text, index)
        except ValueError:
            index += 1
            continue
        index = end
        if isinstance(payload, dict) and payload.get("state") == "tallied":
            found.append(payload)


def child_results(events: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    """What each child session actually produced, keyed by child index."""
    out: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        if not is_child_event(event) or event.get("event_type") != "tool":
            continue
        index = event.get("child_session")
        if not isinstance(index, int):
            continue
        for payload in tally_payloads(tool_result_text(event)):
            out.setdefault(index, []).append(payload)
    return out


def dispatch_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        if is_child_event(event) or not is_dispatch_call(event):
            continue
        arguments = tool_arguments(event)
        lanes = sorted({lane for lane in LANES if lane in arguments})
        rows.append({"seq": event.get("seq"), "turn": event.get("conversation_turn"),
                     "lanes": lanes, "successful": tool_success(event), "event": event})
    return rows


def harness_assessment(workspace: Path, events: list[dict[str, Any]],
                       summary: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("event_type"))] = counts.get(str(event.get("event_type")), 0) + 1
    seqs = [event.get("seq") for event in events]
    tool_events = [event for event in events if event.get("event_type") == "tool"]
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
                for event in tool_events))

    # Device self-check: the scenario produced six tallies with distinct seals and the
    # designed figures, and the lanes really did settle at different speeds.
    rows = read_jsonl(workspace / "state" / "tally_runs.jsonl")
    tallied = [row for row in rows if row.get("event") == "tallied"]
    truth = authoritative(workspace)
    probe_ok = (
        len(tallied) == len(LANES)
        and sorted(str(row.get("lane")) for row in tallied) == sorted(LANES)
        and len({str(row.get("seal_fp", "")) for row in tallied}) == len(LANES)
        and all(len(str(row.get("seal_fp", ""))) == 16 for row in tallied)
        and all(str(row.get("mean_units")) == truth.get(str(row.get("lane")))
                for row in tallied)
        and len({row.get("settle_seconds") for row in tallied}) > 1)

    # Six native dispatches, one lane each, all accepted.
    dispatches = dispatch_rows(events)
    good = [row for row in dispatches if row["successful"] and len(row["lanes"]) == 1]
    dispatch_ok = (len(good) == len(LANES)
                   and sorted(row["lanes"][0] for row in good) == sorted(LANES))

    # Concurrency: the dispatches were issued before any of them had come back.
    finish_times = {str(row.get("lane")): row.get("finished_ns") for row in tallied}
    dispatch_seqs = [row["seq"] for row in good]
    overlapped = bool(dispatch_seqs) and max(dispatch_seqs) - min(dispatch_seqs) < 60
    spread_ok = len({v for v in finish_times.values() if v}) == len(LANES)
    concurrency_ok = overlapped and spread_ok

    # The leaf: every child's result reached the parent whole and attributable.
    produced = child_results(events)
    # Everything the parent side saw, by any route. A child's completion does not arrive as a
    # tool result: the platform injects it into the parent's context as runtime context, so a
    # check that looks only at tool results reports every lane as undelivered and says nothing
    # about the one that actually was.
    parent_blob = "\n".join(
        json.dumps(event, ensure_ascii=False) for event in events
        if not is_child_event(event))
    binding_errors = []
    issued_seal_fp = {str(row.get("lane")): str(row.get("seal_fp", "")) for row in tallied}
    for lane in LANES:
        expected_figure = truth.get(lane)
        # Which child produced this lane, according to the child's own transcript?
        owners = [index for index, payloads in produced.items()
                  if any(p.get("lane") == lane for p in payloads)]
        if not owners:
            binding_errors.append({"lane": lane, "issue": "no child transcript shows this lane tallied"})
            continue
        if len(owners) > 1:
            binding_errors.append({"lane": lane, "issue": "more than one child claims this lane",
                                   "children": owners})
        payload = next(p for p in produced[owners[0]] if p.get("lane") == lane)
        if str(payload.get("mean_units")) != expected_figure:
            binding_errors.append({"lane": lane, "issue": "child reported a figure that is not this lane's",
                                   "child_said": payload.get("mean_units"), "lane_value": expected_figure})
        seal = str(payload.get("seal") or "")
        if not SEAL_RE.match(seal) or fingerprint(seal) != issued_seal_fp.get(lane, ""):
            binding_errors.append({"lane": lane, "issue": "child's seal is not the one issued for this lane"})
        # And did that result, with its seal, actually arrive at the parent?
        if seal and seal not in parent_blob:
            binding_errors.append({
                "lane": lane,
                "issue": "child completed but its result never reached the parent by any route",
                "child_session": owners[0],
                "child_seal_fp": fingerprint(seal),
                "issued_seal_fp": issued_seal_fp.get(lane, ""),
                "completions_injected": sum(
                    1 for event in events
                    if event.get("source") == "runtime_injected_context"),
                "children_dispatched": len(produced)})
    binding_ok = bool(produced) and not binding_errors
    if not produced:
        binding_errors.append({"issue": "no child transcripts in the trace, so attribution "
                                        "cannot be judged from this run"})

    return [
        rule("hr_canonical_trace_complete", lifecycle_ok,
             "complete seven-turn lifecycle captured in %d canonical events" % len(events)
             if lifecycle_ok else
             compact("events=%d contiguous=%s turns=%s/%s summary=%s/%s"
                     % (len(events), seqs == list(range(1, len(events) + 1)),
                        counts.get("conversation_turn_started"),
                        counts.get("conversation_turn_finished"),
                        summary.get("status"), summary.get("trace_complete")), 300)),
        rule("hr_probe_tallied_six_lanes_with_distinct_seals", probe_ok,
             "six lanes tallied, each with its own seal and the designed figure"
             if probe_ok else
             compact("tallied=%d lanes=%d distinct_seals=%d settle_values=%d"
                     % (len(tallied), len({str(r.get('lane')) for r in tallied}),
                        len({str(r.get('seal_fp', '')) for r in tallied}),
                        len({r.get('settle_seconds') for r in tallied})), 300)),
        rule("hr_six_native_dispatches_one_per_lane", dispatch_ok,
             "six accepted native dispatches covered the six lanes" if dispatch_ok else
             compact("dispatches=%s" % [{k: v for k, v in row.items() if k != "event"}
                                        for row in dispatches][:8], 460)),
        rule("hr_children_ran_concurrently", concurrency_ok,
             "the six children were issued together and settled at different times"
             if concurrency_ok else
             compact("dispatch_seq_span=%s distinct_finish_times=%d"
                     % (sorted(dispatch_seqs), len({v for v in finish_times.values() if v})), 300)),
        rule("hr_each_child_result_stays_bound_to_its_lane", binding_ok,
             "every child's figure and seal reached the parent bound to the lane that produced it"
             if binding_ok else compact("errors=%s" % binding_errors[:6], 520)),
    ]


def agent_assessment(workspace: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    truth = authoritative(workspace)
    rows = read_jsonl(workspace / "state" / "tally_runs.jsonl")
    issued_fp = {str(r.get("lane")): str(r.get("seal_fp", "")) for r in rows
                 if r.get("event") == "tallied"}

    out_root = workspace / "output"
    files = sorted(p.name for p in out_root.iterdir()) if out_root.is_dir() else []
    register = load_json(out_root / "lane_tally.json")
    account = load_json(out_root / "concurrency_account.json")

    contract_errors: list[str] = []
    if files != sorted(["concurrency_account.json", "lane_tally.json"]):
        contract_errors.append("output/ holds %s" % files)
    if not isinstance(register, dict):
        contract_errors.append("lane_tally.json missing or not a JSON object")
    else:
        if list(register) != list(LANES):
            contract_errors.append("register keys are %s" % compact(list(register), 200))
        for lane, item in register.items():
            if not isinstance(item, dict):
                contract_errors.append("%s is not an object" % lane)
                continue
            if tuple(item) != REGISTER_FIELDS:
                contract_errors.append("%s fields are %s" % (lane, list(item)))
                continue
            for field in ("mean_units", "seal"):
                value = item.get(field)
                if value is not None and not isinstance(value, str):
                    contract_errors.append("%s %s is %r" % (lane, field, value))
            if not isinstance(item.get("reported_by"), str) or not item["reported_by"].strip():
                contract_errors.append("%s reported_by is empty" % lane)
    if not isinstance(account, dict):
        contract_errors.append("concurrency_account.json missing or not a JSON object")
    else:
        if tuple(account) != ("issued_concurrently", "completion_order", "binding_basis"):
            contract_errors.append("account fields are %s" % list(account))
        order = account.get("completion_order")
        if not isinstance(order, list) or sorted(str(x) for x in order) != sorted(LANES):
            contract_errors.append("completion_order is %s" % compact(order, 160))
        if not isinstance(account.get("issued_concurrently"), bool):
            contract_errors.append("issued_concurrently is not a boolean")
    contract_ok = not contract_errors

    figure_failures, seal_failures, crossed = [], [], []
    delivered: dict[str, str | None] = {}
    if isinstance(register, dict):
        for lane in LANES:
            item = register.get(lane) if isinstance(register.get(lane), dict) else {}
            figure = item.get("mean_units") if isinstance(item.get("mean_units"), str) else None
            seal = item.get("seal") if isinstance(item.get("seal"), str) else None
            delivered[lane] = figure
            if figure is not None:
                if not FIGURE_RE.match(figure):
                    figure_failures.append({"lane": lane, "delivered": compact(figure, 24),
                                            "issue": "not a two-decimal figure"})
                elif figure != truth.get(lane):
                    # Every lane's mean sits in the same band, so a wrong figure is usually
                    # another lane's figure. Saying which one is the point of this leaf.
                    owner = next((other for other, value in truth.items() if value == figure), None)
                    figure_failures.append({"lane": lane, "delivered": figure,
                                            "belongs_to": owner or "<no lane>"})
            if seal is not None:
                expected = issued_fp.get(lane, "")
                if not SEAL_RE.match(seal):
                    seal_failures.append({"lane": lane, "issue": "malformed",
                                          "delivered": compact(seal, 30)})
                elif not expected or fingerprint(seal) != expected:
                    owner = next((other for other, value in issued_fp.items()
                                  if value == fingerprint(seal)), None)
                    crossed.append({"lane": lane, "delivered": compact(seal, 30),
                                    "seal_belongs_to": owner or "<never issued>"})
    figures_ok = isinstance(register, dict) and not figure_failures
    seals_ok = isinstance(register, dict) and not seal_failures and not crossed

    complete = [lane for lane in LANES
                if isinstance(delivered.get(lane), str) and delivered[lane] == truth.get(lane)]
    completion_ok = len(complete) == len(LANES) and seals_ok
    missing = [{"lane": lane, "figure": delivered.get(lane)}
               for lane in LANES if lane not in complete]

    # Boundary, from the trace: the coordinator must not run or read the tally channel, and
    # must not read a child's stored conversation.
    boundary_hits = []
    for event in events:
        if is_child_event(event) or event.get("event_type") != "tool":
            continue
        name = tool_name(event)
        if name in {"sessions_history", "read_thread", "read_agent"}:
            boundary_hits.append({"seq": event.get("seq"), "issue": "read a child's conversation"})
            continue
        if is_dispatch_call(event):
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        target = ""
        if isinstance(arguments, dict):
            for key in ("path", "file", "file_path", "filename"):
                if isinstance(arguments.get(key), str):
                    target = arguments[key]
                    break
        for protected in PROTECTED:
            if protected.endswith("/"):
                if protected in target:
                    boundary_hits.append({"seq": event.get("seq"), "path": target})
            elif target.endswith(protected):
                boundary_hits.append({"seq": event.get("seq"), "path": target})
        # Only an execution call can run the channel, and only the part of it before any
        # here-document is a command. A note or deliverable whose text names the script -- as
        # a truthful account of the round naturally does -- is not the coordinator running it.
        command = ""
        if isinstance(arguments, dict):
            for key in ("command", "cmd"):
                if isinstance(arguments.get(key), str):
                    command = arguments[key]
                    break
        body_start = len(command)
        for marker in ("<<'EOF'", '<<"EOF"', "<<EOF", "<<'PY'", "<<PY"):
            index = command.find(marker)
            if index >= 0:
                body_start = min(body_start, index)
        for piece in command_pieces(command[:body_start]):
            tokens = [token.strip("'\"") for token in piece.split()]
            if not tokens or tokens[0].rsplit("/", 1)[-1] in NON_READING_COMMANDS:
                continue
            if any("tally_lane.sh" in token for token in tokens):
                boundary_hits.append({"seq": event.get("seq"),
                                      "issue": "coordinator ran the tally channel"})
    boundary_ok = not boundary_hits

    return [
        rule("ar_output_contract_exact", contract_ok,
             "exact two-file contract satisfied" if contract_ok
             else compact("errors=%s" % contract_errors[:6], 460)),
        rule("ar_reported_figures_belong_to_their_lane", figures_ok,
             "every reported figure is the one its own lane produced" if figures_ok
             else compact("failures=%s" % figure_failures[:6], 460)),
        rule("ar_reported_seals_belong_to_their_lane", seals_ok,
             "every reported seal is the one issued for that lane" if seals_ok
             else compact("malformed=%s crossed=%s" % (seal_failures[:4], crossed[:4]), 460)),
        rule("ar_round_delivered_all_six_lanes", completion_ok,
             "all six lanes were filed with their own figure and seal" if completion_ok
             else compact("round incomplete -- %d/%d filed: %s"
                          % (len(complete), len(LANES), missing[:6]), 460)),
        rule("ar_coordinator_boundary_respected", boundary_ok,
             "the coordinator neither ran the channel nor read a child's conversation"
             if boundary_ok else compact("violations=%s" % boundary_hits[:4], 300)),
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
