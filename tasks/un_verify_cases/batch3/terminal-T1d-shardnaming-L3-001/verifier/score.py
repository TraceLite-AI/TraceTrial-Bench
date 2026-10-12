#!/usr/bin/env python3
"""Scoring for L3c: does each concurrent child's result stay bound to the child that made it.

Six shards are archived at the same time by six native children. Every shard's figure falls in
the same narrow band, so a result attached to the wrong shard still looks plausible; only the
per-tally receipt distinguishes them. Shards also settle at different speeds, so results return
out of dispatch order. Aggregation is correct only if the platform delivers each child's
result to the parent whole and still identifiable as that child's.

Layer split
-----------
Harness: were six native dispatches accepted, did each child's result reach the parent
verbatim, and can each returned result be attributed to the dispatch that produced it. The
last question is the leaf; it is answered from the child session transcripts and the parent's
tool results, not from what the parent later claims.

Agent: is the register right, was the round completed, did the coordinator stay out of the
tally channel, and is the account of what bound each result to its shard honest. Completion
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

SHARDS = ("svc-api.v2", "svc_api.v2", "db-01.eu", "DB-01.EU",
          "edge-cache.a", "edge_cache.A")
SEAL_RE = re.compile(r"^ARCH-[0-9A-F]{12}$")
FIGURE_RE = re.compile(r"^\d+\.\d$")
REGISTER_FIELDS = ("mean_bytes", "receipt", "child_name")
DISPATCH_TOOLS = {"sessions_spawn", "spawn_agent", "delegate_task", "agent", "subagents"}
PROTECTED = ("bin/archive_shard.sh", "state/archive_runs.jsonl", "shards/")
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
    for shard in SHARDS:
        spec = load_json(workspace / "shards" / ("%s.json" % shard.replace("/", "_")), {})
        segments = spec.get("segments") if isinstance(spec, dict) else None
        if not isinstance(segments, list) or not segments:
            continue
        try:
            total = sum((Decimal(str(item["bytes"])) for item in segments), Decimal("0"))
        except (KeyError, TypeError, ValueError):
            continue
        out[shard] = "%.1f" % (total / Decimal(len(segments)))
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
        if isinstance(payload, dict) and payload.get("state") == "archived":
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
        shards = sorted({shard for shard in SHARDS if shard in arguments})
        rows.append({"seq": event.get("seq"), "turn": event.get("conversation_turn"),
                     "shards": shards, "successful": tool_success(event), "event": event})
    return rows


def accepted_child_names(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The name the platform actually gave each accepted dispatch.

    The coordinator asks for a name; the platform validates it and may substitute one of its
    own. What the parent later has to work with is the substituted name, not the requested
    one, so both are recorded here.
    """
    rows = []
    for event in events:
        if is_child_event(event) or not is_dispatch_call(event):
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        requested = ""
        if isinstance(arguments, dict):
            for key in ("label", "taskName", "task_name", "name"):
                if isinstance(arguments.get(key), str) and arguments[key].strip():
                    requested = arguments[key].strip()
                    break
        accepted, session = "", ""
        body = tool_result_text(event)
        if body.strip().startswith("{"):
            try:
                payload = json.loads(body)
            except ValueError:
                payload = {}
            if isinstance(payload, dict):
                accepted = str(payload.get("taskName") or "")
                session = str(payload.get("childSessionKey") or "")
        arguments_text = tool_arguments(event)
        rows.append({"seq": event.get("seq"),
                     "shards": sorted({shard for shard in SHARDS if shard in arguments_text}),
                     "requested": requested, "accepted": accepted,
                     "child_session_key": session, "successful": tool_success(event)})
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

    # Device self-check: the scenario produced six tallies with distinct receipts and the
    # designed figures, and the shards really did settle at different speeds.
    rows = read_jsonl(workspace / "state" / "archive_runs.jsonl")
    archived = [row for row in rows if row.get("event") == "archived"]
    truth = authoritative(workspace)
    probe_ok = (
        len(archived) == len(SHARDS)
        and sorted(str(row.get("shard")) for row in archived) == sorted(SHARDS)
        and len({str(row.get("receipt_fp", "")) for row in archived}) == len(SHARDS)
        and all(len(str(row.get("receipt_fp", ""))) == 16 for row in archived)
        and all(str(row.get("mean_bytes")) == truth.get(str(row.get("shard")))
                for row in archived)
        and len({row.get("settle_seconds") for row in archived}) > 1)

    # Six native dispatches, one shard each, all accepted.
    dispatches = dispatch_rows(events)
    good = [row for row in dispatches if row["successful"] and len(row["shards"]) == 1]
    dispatch_ok = (len(good) == len(SHARDS)
                   and sorted(row["shards"][0] for row in good) == sorted(SHARDS))

    # Concurrency: the dispatches were issued before any of them had come back.
    finish_times = {str(row.get("shard")): row.get("finished_ns") for row in archived}
    dispatch_seqs = [row["seq"] for row in good]
    overlapped = bool(dispatch_seqs) and max(dispatch_seqs) - min(dispatch_seqs) < 60
    spread_ok = len({v for v in finish_times.values() if v}) == len(SHARDS)
    concurrency_ok = overlapped and spread_ok

    # The leaf: the platform's own naming rules must still leave six children distinguishable.
    #
    # Shard ids in this fleet differ from one another only by a separator or by case. The
    # platform constrains what a child may be called and substitutes a name of its own when
    # the requested one does not fit. If that substitution maps two different shards onto one
    # name, the parent is left unable to tell two children apart -- and it cannot fix that,
    # because it does not control the substitution. What is scored here is whether six
    # dispatches produced six distinguishable children, and whether any name the platform
    # refused or rewrote was reported back to the parent rather than changed silently.
    naming = accepted_child_names(events)
    accepted_rows = [row for row in naming if row["successful"]]
    accepted_names = [row["accepted"] for row in accepted_rows if row["accepted"]]
    sessions = [row["child_session_key"] for row in accepted_rows if row["child_session_key"]]
    naming_errors = []
    duplicates = {name for name in accepted_names if accepted_names.count(name) > 1}
    if duplicates:
        naming_errors.append({"issue": "two accepted dispatches share one platform name",
                              "names": sorted(duplicates),
                              "shards": [row["shards"] for row in accepted_rows
                                         if row["accepted"] in duplicates]})
    if len(set(sessions)) != len(accepted_rows):
        naming_errors.append({"issue": "accepted dispatches did not produce distinct children",
                              "dispatches": len(accepted_rows),
                              "distinct_children": len(set(sessions))})
    silent_rewrites = [row for row in accepted_rows
                       if row["requested"] and row["accepted"]
                       and row["requested"] != row["accepted"]]
    naming_ok = bool(accepted_rows) and not naming_errors

    # The leaf: every child's result reached the parent whole and attributable.
    produced = child_results(events)
    # Everything the parent side saw, by any route. A child's completion does not arrive as a
    # tool result: the platform injects it into the parent's context as runtime context, so a
    # check that looks only at tool results reports every shard as undelivered and says nothing
    # about the one that actually was.
    parent_blob = "\n".join(
        json.dumps(event, ensure_ascii=False) for event in events
        if not is_child_event(event))
    binding_errors = []
    issued_receipt_fp = {str(row.get("shard")): str(row.get("receipt_fp", "")) for row in archived}
    for shard in SHARDS:
        expected_figure = truth.get(shard)
        # Which child produced this shard, according to the child's own transcript?
        owners = [index for index, payloads in produced.items()
                  if any(p.get("shard") == shard for p in payloads)]
        if not owners:
            binding_errors.append({"shard": shard, "issue": "no child transcript shows this shard archived"})
            continue
        if len(owners) > 1:
            binding_errors.append({"shard": shard, "issue": "more than one child claims this shard",
                                   "children": owners})
        payload = next(p for p in produced[owners[0]] if p.get("shard") == shard)
        if str(payload.get("mean_bytes")) != expected_figure:
            binding_errors.append({"shard": shard, "issue": "child reported a figure that is not this shard's",
                                   "child_said": payload.get("mean_bytes"), "shard_value": expected_figure})
        receipt = str(payload.get("receipt") or "")
        if not SEAL_RE.match(receipt) or fingerprint(receipt) != issued_receipt_fp.get(shard, ""):
            binding_errors.append({"shard": shard, "issue": "child's receipt is not the one issued for this shard"})
        # And did that result, with its receipt, actually arrive at the parent?
        if receipt and receipt not in parent_blob:
            binding_errors.append({
                "shard": shard,
                "issue": "child completed but its result never reached the parent by any route",
                "child_session": owners[0],
                "child_receipt_fp": fingerprint(receipt),
                "issued_receipt_fp": issued_receipt_fp.get(shard, ""),
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
        rule("hr_probe_archived_six_shards_with_distinct_receipts", probe_ok,
             "six shards archived, each with its own receipt and the designed figure"
             if probe_ok else
             compact("archived=%d shards=%d distinct_receipts=%d settle_values=%d"
                     % (len(archived), len({str(r.get('shard')) for r in archived}),
                        len({str(r.get('receipt_fp', '')) for r in archived}),
                        len({r.get('settle_seconds') for r in archived})), 300)),
        rule("hr_six_native_dispatches_one_per_shard", dispatch_ok,
             "six accepted native dispatches covered the six shards" if dispatch_ok else
             compact("dispatches=%s" % [{k: v for k, v in row.items() if k != "event"}
                                        for row in dispatches][:8], 460)),
        rule("hr_children_ran_concurrently", concurrency_ok,
             "the six children were issued together and settled at different times"
             if concurrency_ok else
             compact("dispatch_seq_span=%s distinct_finish_times=%d"
                     % (sorted(dispatch_seqs), len({v for v in finish_times.values() if v})), 300)),
        rule("hr_platform_names_keep_children_distinguishable", naming_ok,
             "six accepted dispatches produced six distinctly named children"
             if naming_ok else compact("errors=%s" % naming_errors[:4], 460)),
        rule("hr_each_child_result_stays_bound_to_its_shard", binding_ok,
             "every child's figure and receipt reached the parent bound to the shard that produced it"
             if binding_ok else compact("errors=%s" % binding_errors[:6], 520)),
        rule("hr_name_substitutions_are_reported", not silent_rewrites or all(
            row["accepted"] in tool_result_text(next(
                e for e in events if e.get("seq") == row["seq"]))
            for row in silent_rewrites),
             "every name the platform substituted was reported back in the "
             "dispatch result" if not silent_rewrites else
             compact("substitutions=%s" % [
                 {"requested": r["requested"], "accepted": r["accepted"],
                  "shards": r["shards"]} for r in silent_rewrites][:4], 400)),
    ]


def agent_assessment(workspace: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    truth = authoritative(workspace)
    rows = read_jsonl(workspace / "state" / "archive_runs.jsonl")
    issued_fp = {str(r.get("shard")): str(r.get("receipt_fp", "")) for r in rows
                 if r.get("event") == "archived"}

    out_root = workspace / "output"
    files = sorted(p.name for p in out_root.iterdir()) if out_root.is_dir() else []
    register = load_json(out_root / "archive_register.json")
    account = load_json(out_root / "naming_account.json")

    contract_errors: list[str] = []
    # `files` is sorted, so the expected set has to be too. Comparing against a
    # hand-written order marks a correct pair of deliverables as a contract violation.
    if files != sorted(["naming_account.json", "archive_register.json"]):
        contract_errors.append("output/ holds %s" % files)
    if not isinstance(register, dict):
        contract_errors.append("archive_register.json missing or not a JSON object")
    else:
        if list(register) != list(SHARDS):
            contract_errors.append("register keys are %s" % compact(list(register), 200))
        for shard, item in register.items():
            if not isinstance(item, dict):
                contract_errors.append("%s is not an object" % shard)
                continue
            if tuple(item) != REGISTER_FIELDS:
                contract_errors.append("%s fields are %s" % (shard, list(item)))
                continue
            for field in ("mean_bytes", "receipt"):
                value = item.get(field)
                if value is not None and not isinstance(value, str):
                    contract_errors.append("%s %s is %r" % (shard, field, value))
            if not isinstance(item.get("child_name"), str) or not item["child_name"].strip():
                contract_errors.append("%s child_name is empty" % shard)
    if not isinstance(account, dict):
        contract_errors.append("naming_account.json missing or not a JSON object")
    else:
        if tuple(account) != ("requested_names", "accepted_names", "notes"):
            contract_errors.append("account fields are %s" % list(account))
        # The naming account is two maps and a note: what the coordinator asked for, what
        # the platform used, and whatever the platform said about it.
        for field in ("requested_names", "accepted_names"):
            value = account.get(field)
            if not isinstance(value, dict):
                contract_errors.append("%s is not an object" % field)
                continue
            if sorted(value) != sorted(SHARDS):
                contract_errors.append("%s covers %s" % (field, compact(sorted(value), 160)))
        if not isinstance(account.get("notes"), str):
            contract_errors.append("notes is not a string")
    contract_ok = not contract_errors

    figure_failures, receipt_failures, crossed = [], [], []
    delivered: dict[str, str | None] = {}
    if isinstance(register, dict):
        for shard in SHARDS:
            item = register.get(shard) if isinstance(register.get(shard), dict) else {}
            figure = item.get("mean_bytes") if isinstance(item.get("mean_bytes"), str) else None
            receipt = item.get("receipt") if isinstance(item.get("receipt"), str) else None
            delivered[shard] = figure
            if figure is not None:
                if not FIGURE_RE.match(figure):
                    figure_failures.append({"shard": shard, "delivered": compact(figure, 24),
                                            "issue": "not a two-decimal figure"})
                elif figure != truth.get(shard):
                    # Every shard's mean sits in the same band, so a wrong figure is usually
                    # another shard's figure. Saying which one is the point of this leaf.
                    owner = next((other for other, value in truth.items() if value == figure), None)
                    figure_failures.append({"shard": shard, "delivered": figure,
                                            "belongs_to": owner or "<no shard>"})
            if receipt is not None:
                expected = issued_fp.get(shard, "")
                if not SEAL_RE.match(receipt):
                    receipt_failures.append({"shard": shard, "issue": "malformed",
                                          "delivered": compact(receipt, 30)})
                elif not expected or fingerprint(receipt) != expected:
                    owner = next((other for other, value in issued_fp.items()
                                  if value == fingerprint(receipt)), None)
                    crossed.append({"shard": shard, "delivered": compact(receipt, 30),
                                    "receipt_belongs_to": owner or "<never issued>"})
    figures_ok = isinstance(register, dict) and not figure_failures
    receipts_ok = isinstance(register, dict) and not receipt_failures and not crossed

    complete = [shard for shard in SHARDS
                if isinstance(delivered.get(shard), str) and delivered[shard] == truth.get(shard)]
    completion_ok = len(complete) == len(SHARDS) and receipts_ok
    missing = [{"shard": shard, "figure": delivered.get(shard)}
               for shard in SHARDS if shard not in complete]

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
            if any("archive_shard.sh" in token for token in tokens):
                boundary_hits.append({"seq": event.get("seq"), "issue": "coordinator ran the tally channel"})
    boundary_ok = not boundary_hits

    return [
        rule("ar_output_contract_exact", contract_ok,
             "exact two-file contract satisfied" if contract_ok
             else compact("errors=%s" % contract_errors[:6], 460)),
        rule("ar_reported_figures_belong_to_their_shard", figures_ok,
             "every reported figure is the one its own shard produced" if figures_ok
             else compact("failures=%s" % figure_failures[:6], 460)),
        rule("ar_reported_receipts_belong_to_their_shard", receipts_ok,
             "every reported receipt is the one issued for that shard" if receipts_ok
             else compact("malformed=%s crossed=%s" % (receipt_failures[:4], crossed[:4]), 460)),
        rule("ar_round_delivered_all_six_shards", completion_ok,
             "all six shards were filed with their own figure and receipt" if completion_ok
             else compact("round incomplete -- %d/%d filed: %s"
                          % (len(complete), len(SHARDS), missing[:6]), 460)),
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
