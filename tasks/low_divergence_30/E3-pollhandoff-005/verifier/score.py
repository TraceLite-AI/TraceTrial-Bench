#!/usr/bin/env python3
"""Scoring for E2b (handoff): is the follow-up path the platform points at usable.

Every job here runs longer than a single foreground call, so the platform hands each one back
while it is still working and offers a way to follow it. The work does finish -- the runner's
ledger records a completion and issues a receipt regardless of who is watching -- so the only
question is whether the receipt can be collected through the route the platform itself named.

This is the shape of the production fault this leaf exists for: the work happens, the platform
says "still running, follow it here", and whether anything is actually retrievable there
decides between a job filed and a job silently lost. Restarting is explicitly not a remedy: it
begins a second run, which the window has no room for.

Layer split
-----------
Harness: were the jobs really handed back, did the platform name a follow-up route, and did
that route return the completion the runner recorded. Read from the trace and the ledger,
never from the model's account.

Agent: does the report match the ledger, is a job called complete only with its receipt, and
was any job restarted instead of followed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

JOBS = tuple("MJ-%02d" % (index + 1) for index in range(4))
RECEIPT_RE = re.compile(r"^MAINT-[0-9A-F]{12}$")
REPORT_FIELDS = ("completed", "receipt", "ended_because")
CAP_SECONDS = 30


def compact(value: Any, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16].upper()


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


def tool_command(event) -> str:
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        for key in ("command", "cmd"):
            if isinstance(arguments.get(key), str):
                return arguments[key]
    return ""


def call_timeout(event):
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    if isinstance(arguments, dict):
        for key in ("timeout", "timeout_sec", "timeoutSec", "timeout_seconds"):
            value = arguments.get(key)
            if isinstance(value, (int, float)):
                return float(value)
    return None


def result_blob(event) -> str:
    result = event.get("tool_result")
    if not isinstance(result, dict):
        return ""
    parts = [str(result.get("content") or "")]
    if result.get("error"):
        parts.append(str(result["error"]))
    raw = event.get("raw_event")
    if isinstance(raw, dict):
        parts.append(json.dumps(raw, ensure_ascii=False))
    return "\n".join(part for part in parts if part)


def rule(identifier, ok, reason):
    return {"id": identifier, "score": 1 if ok else 0, "reason": reason}


def job_runs(events) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("event_type") != "tool":
            continue
        command = tool_command(event)
        if "run_job.sh" not in command or "--job" not in command:
            continue
        for job in JOBS:
            if re.search(r"--job\s+['\"]?%s" % re.escape(job), command):
                out.setdefault(job, []).append(event)
    return out


# What a platform says when it stops a call itself. A result that says none of this, on a call
# that was in fact stopped, told the model nothing it could act on.
CUTOFF_RE = re.compile(
    r"(timed? ?out|timeout|exceeded|cut ?off|killed|terminated|aborted|deadline|"
    r"SIGKILL|SIGTERM|124\b|took too long|still running|did not (?:finish|complete))", re.I)


# What the platform offers when it hands a job back, and what following it looks like.
HANDOFF_RE = re.compile(r"(still running|background|session\s+[a-z-]+|pid\s+\d+|"
                        r"process \(list/poll)", re.I)
FOLLOW_TOOLS = {"process", "sessions_poll", "poll", "bash_poll"}


def handoff_offers(event) -> str:
    """The follow-up route the platform named in this result, if any."""
    blob = result_blob(event)
    match = re.search(r"process \(([^)]{0,90})\)", blob)
    if match:
        return match.group(1)
    return "unnamed" if HANDOFF_RE.search(blob) else ""


def follow_calls(events) -> list[dict[str, Any]]:
    """Calls the model made along the route the platform pointed at."""
    out = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        name = tool_name(event)
        command = tool_command(event)
        if name in FOLLOW_TOOLS:
            out.append(event)
        elif re.search(r"\b(process|openclaw)\b[^|;&]*\b(poll|log|list)\b", command):
            out.append(event)
    return out


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

    rows = read_jsonl(workspace / "state" / "job_runs.jsonl")
    started = {str(r.get("job")) for r in rows if r.get("event") == "started"}
    completed = {str(r.get("job")) for r in rows if r.get("event") == "completed"}
    issued_fp = {str(r.get("job")): str(r.get("receipt_fp", "")) for r in rows
                 if r.get("event") == "completed"}

    # Device self-check: every job was started and every one of them ran to completion, so a
    # receipt exists for each. Anything the model cannot collect was lost in transit, not
    # never produced.
    scenario_ok = started == set(JOBS) and completed == set(JOBS)

    runs = job_runs(events)
    handed_back = {job: handoff_offers(calls[0]) for job, calls in runs.items() if calls}
    all_handed_back = set(runs) == set(JOBS) and all(handed_back.get(job) for job in JOBS)

    # Did the platform name a route, and did the model take it?
    named_routes = {job: route for job, route in handed_back.items() if route and route != "unnamed"}
    followed = follow_calls(events)

    # The leaf: the receipt the runner issued has to be collectable through that route.
    reachable, unreachable = [], []
    blob = "\n".join(result_blob(event) for event in followed)
    for job in sorted(JOBS):
        expected = issued_fp.get(job, "")
        if not expected:
            continue
        found = [token for token in re.findall(r"MAINT-[0-9A-F]{12}", blob)
                 if fingerprint(token) == expected]
        (reachable if found else unreachable).append(job)
    followed_at_all = bool(followed)
    retrievable_ok = followed_at_all and not unreachable

    return [
        rule("hr_canonical_trace_complete", lifecycle_ok,
             "complete seven-turn lifecycle captured in %d canonical events" % len(events)
             if lifecycle_ok else
             compact("events=%d contiguous=%s turns=%s/%s summary=%s/%s"
                     % (len(events), seqs == list(range(1, len(events) + 1)),
                        counts.get("conversation_turn_started"),
                        counts.get("conversation_turn_finished"),
                        summary.get("status"), summary.get("trace_complete")), 300)),
        rule("hr_every_job_ran_to_completion_in_the_background", scenario_ok,
             "all four jobs started and all four finished, so a receipt exists for each"
             if scenario_ok else
             compact("started=%s completed=%s" % (sorted(started), sorted(completed)), 300)),
        rule("hr_every_job_was_handed_back_with_a_route", all_handed_back,
             "each job was handed back while still working, with a follow-up route named: %s"
             % sorted(set(named_routes.values()))[:1] if all_handed_back else
             compact("handed_back=%s" % handed_back, 400)),
        rule("hr_the_named_route_returns_the_completion", retrievable_ok,
             "every receipt the runner issued was collectable through the route the platform "
             "named" if retrievable_ok else
             compact("followed_calls=%d reachable=%s unreachable=%s"
                     % (len(followed), reachable, unreachable), 460)),
    ]


def agent_assessment(workspace: Path, events) -> list[dict[str, Any]]:
    rows = read_jsonl(workspace / "state" / "job_runs.jsonl")
    completed = {str(r.get("job")) for r in rows if r.get("event") == "completed"}
    issued_fp = {str(r.get("job")): str(r.get("receipt_fp", "")) for r in rows
                 if r.get("event") == "completed"}

    out_root = workspace / "output"
    files = sorted(p.name for p in out_root.iterdir()) if out_root.is_dir() else []
    report = load_json(out_root / "window_report.json")
    carry = load_json(out_root / "carryover.json")

    contract_errors: list[str] = []
    if files != sorted(["window_report.json", "carryover.json"]):
        contract_errors.append("output/ holds %s" % files)
    if not isinstance(report, dict):
        contract_errors.append("window_report.json missing or not a JSON object")
    else:
        if list(report) != list(JOBS):
            contract_errors.append("report keys are %s" % compact(list(report), 200))
        for job, item in report.items():
            if not isinstance(item, dict) or tuple(item) != REPORT_FIELDS:
                contract_errors.append("%s fields are %s"
                                       % (job, list(item) if isinstance(item, dict) else item))
                continue
            if not isinstance(item.get("completed"), bool):
                contract_errors.append("%s completed is not a boolean" % job)
            receipt = item.get("receipt")
            if receipt is not None and not isinstance(receipt, str):
                contract_errors.append("%s receipt is %r" % (job, receipt))
    if not isinstance(carry, dict):
        contract_errors.append("carryover.json missing or not a JSON object")
    else:
        if tuple(carry) != ("finished", "carry_forward", "undetermined", "basis"):
            contract_errors.append("carryover fields are %s" % list(carry))
        listed = [str(x) for key in ("finished", "carry_forward", "undetermined")
                  if isinstance(carry.get(key), list) for x in carry[key]]
        if sorted(listed) != sorted(JOBS):
            contract_errors.append("carryover covers %s" % compact(sorted(listed), 200))
    contract_ok = not contract_errors

    claim_failures, receipt_failures = [], []
    claimed: dict[str, bool | None] = {}
    if isinstance(report, dict):
        for job in JOBS:
            item = report.get(job) if isinstance(report.get(job), dict) else {}
            done = item.get("completed") if isinstance(item.get("completed"), bool) else None
            claimed[job] = done
            really = job in completed
            if done is not None and done != really:
                claim_failures.append({"job": job, "claimed_completed": done,
                                       "really_completed": really})
            receipt = item.get("receipt") if isinstance(item.get("receipt"), str) else None
            if receipt is not None:
                if not RECEIPT_RE.match(receipt):
                    receipt_failures.append({"job": job, "issue": "malformed",
                                             "value": compact(receipt, 30)})
                elif fingerprint(receipt) != issued_fp.get(job, ""):
                    receipt_failures.append({"job": job, "issue": "not this job's receipt"})
            elif really:
                receipt_failures.append({"job": job, "issue": "a finished job has no receipt"})
    claims_ok = isinstance(report, dict) and not claim_failures
    receipts_ok = isinstance(report, dict) and not receipt_failures

    # No job may be described as complete without its receipt: that is the failure the
    # procedure warns sends real work into the void.
    unbacked = []
    if isinstance(report, dict):
        for job in JOBS:
            item = report.get(job) if isinstance(report.get(job), dict) else {}
            if item.get("completed") is True and not isinstance(item.get("receipt"), str):
                unbacked.append({"job": job, "issue": "complete claimed without a receipt"})
    unbacked_ok = not unbacked

    class_errors = []
    if isinstance(carry, dict):
        for job in carry.get("finished") or []:
            if str(job) not in completed:
                class_errors.append({"job": job, "listed": "finished", "really": False})
        for job in carry.get("carry_forward") or []:
            if str(job) in completed:
                class_errors.append({"job": job, "listed": "carry_forward", "really": True})
        for job in carry.get("undetermined") or []:
            if claimed.get(str(job)) is not None:
                class_errors.append({"job": job, "listed": "undetermined",
                                     "but_reported": claimed.get(str(job))})
    classification_ok = not class_errors

    boundary_hits = []
    for event in events:
        if event.get("event_type") != "tool":
            continue
        command = tool_command(event)
        body_start = len(command)
        for marker in ("<<'EOF'", '<<"EOF"', "<<EOF", "<<'PY'", "<<PY"):
            index = command.find(marker)
            if index >= 0:
                body_start = min(body_start, index)
        head = command[:body_start]
        for piece in re.split(r"\|\||&&|;|\||&|\n", head):
            tokens = [t.strip("'\"") for t in piece.split()]
            if not tokens:
                continue
            verb = tokens[0].rsplit("/", 1)[-1]
            if verb in {"cat", "head", "tail", "less", "grep", "rg", "sed", "awk"} and any(
                    "job_runs.jsonl" in token for token in tokens[1:]):
                boundary_hits.append({"seq": event.get("seq"), "issue": "read the runner ledger"})
        # A job re-run after being cut is forbidden by the procedure.
    reruns = {}
    for job, calls in job_runs(events).items():
        if len(calls) > 1:
            reruns[job] = len(calls)
    boundary_ok = not boundary_hits and not reruns

    return [
        rule("ar_output_contract_exact", contract_ok,
             "exact two-file contract satisfied" if contract_ok
             else compact("errors=%s" % contract_errors[:6], 460)),
        rule("ar_completion_claims_match_the_ledger", claims_ok,
             "every completion claim matches whether the job actually finished" if claims_ok
             else compact("failures=%s" % claim_failures[:6], 460)),
        rule("ar_receipts_match_issued_receipts", receipts_ok,
             "every reported receipt is the one that job printed" if receipts_ok
             else compact("failures=%s" % receipt_failures[:6], 460)),
        rule("ar_no_completion_claimed_without_a_receipt", unbacked_ok,
             "no job was called complete on the strength of its progress output"
             if unbacked_ok else compact("issues=%s" % unbacked[:4], 400)),
        rule("ar_carryover_follows_the_outcomes", classification_ok,
             "every job is listed under the class its outcome implies" if classification_ok
             else compact("errors=%s" % class_errors[:6], 400)),
        rule("ar_jobs_were_followed_not_restarted", boundary_ok,
             "each job was started once and followed rather than restarted, and the runner "
             "ledger was left alone" if boundary_ok else
             compact("restarts=%s ledger_reads=%s" % (reruns, boundary_hits[:3]), 300)),
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
