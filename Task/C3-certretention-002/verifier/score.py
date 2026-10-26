#!/usr/bin/env python3
"""Scoring for C3a: does a fact printed early in a round survive to the end of it.

The leaf is cross-turn retention with no session break anywhere. Sixteen certificates are
printed across four widely separated turns; each is printed once and is never reissued, so a
figure from turn 2 cannot be re-fetched at turn 12. Whatever is still stateable at filing
time is stateable because the conversation carried it.

Layer split
-----------
The harness layer asks only about the platform: were the certificates issued as designed,
did each printed figure reach the model verbatim, and was the record carrying it still part
of the conversation the model was working from when the register was written. That last
question is answered by walking OpenClaw's parent chain from the filing turn backwards --
the thread the model actually had -- rather than by noting that a line exists somewhere in a
log file.

The agent layer asks about the deliverable: are the figures right, are the seals the ones
that were issued, was the round actually completed, and is the provenance honest. Completion
and honesty are scored separately on purpose. An honest `null` is the right way to report a
value the model no longer has, and it is rewarded by the provenance item -- but it is still
an incomplete round, and the completion item says so. Folding the two together would make
every honest run look successful and no retention failure could ever surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

SENSORS = tuple("SNS-%04d" % (4201 + index) for index in range(16))
SEAL_RE = re.compile(r"^CERT-[0-9A-F]{12}$")
FIGURE_RE = re.compile(r"^\d+\.\d{3}$")
REGISTER_FIELDS = ("drift_ppm", "seal", "source")
PROTECTED = ("bin/read_cert.sh", "state/cert_reads.jsonl")
CERTIFICATION_TURNS = (2, 4, 6, 8)
FILING_TURN = 12


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
    rows: list[dict[str, Any]] = []
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
    roots = sorted((run_dir / "artifacts" / "trace").glob("*"))
    for root in roots:
        events = load_json(root / "events.json", [])
        summary = load_json(root / "summary.json", {})
        if isinstance(events, list) and events:
            return events, summary if isinstance(summary, dict) else {}
    return [], {}


def tool_name(event: dict[str, Any]) -> str:
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return ""
    return str(call.get("tool_name") or call.get("name") or "")


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
    if not isinstance(result, dict):
        return ""
    return str(result.get("content") or "")


def rule(identifier: str, ok: bool, reason: str) -> dict[str, Any]:
    return {"id": identifier, "score": 1 if ok else 0, "reason": reason}


def authoritative(workspace: Path) -> dict[str, str]:
    """The certified figure recomputed from each sensor's own readings."""
    from decimal import Decimal

    out: dict[str, str] = {}
    for sensor in SENSORS:
        spec = load_json(workspace / "sensors" / ("%s.json" % sensor), {})
        readings = spec.get("readings") if isinstance(spec, dict) else None
        if not isinstance(readings, list) or not readings:
            continue
        try:
            total = sum((Decimal(str(item["ppm"])) for item in readings), Decimal("0"))
        except (KeyError, TypeError, ValueError):
            continue
        out[sensor] = "%.3f" % (total / Decimal(len(readings)))
    return out


def probe_rows(workspace: Path) -> list[dict[str, Any]]:
    return read_jsonl(workspace / "state" / "cert_reads.jsonl")


def invokes_channel(event: dict[str, Any]) -> bool:
    """Did this call run bin/read_cert.sh, as opposed to reading or naming it?"""
    call = event.get("tool_call")
    arguments = call.get("arguments") if isinstance(call, dict) else None
    command = ""
    if isinstance(arguments, dict):
        for key in ("command", "cmd"):
            if isinstance(arguments.get(key), str):
                command = arguments[key]
                break
    if not command:
        return False
    return "read_cert.sh" in command and "--sensor" in command


def certificate_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The tool event that printed each sensor's certificate, with what it printed."""
    # Two things this has to get right.
    #
    # A turn's certificates commonly arrive in one result: the model batches four readings
    # into a single command, so one object per result finds nothing and the harness items
    # fail for want of evidence that is plainly there. Every object in the text is decoded.
    #
    # And the text has to come from a call that actually ran the channel. The channel program
    # is readable, and its source contains the literal it prints -- so a model that simply
    # cats the script produces a result that looks exactly like a certificate. Reading a
    # script is not a certificate; matching on content alone turned the script's own source
    # into sixteen phantom readings and made every later item judge the wrong evidence.
    decoder = json.JSONDecoder()
    found: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "tool":
            continue
        if not invokes_channel(event):
            continue
        body = tool_result_text(event)
        index = 0
        while True:
            index = body.find('{', index)
            if index < 0:
                break
            try:
                payload, end = decoder.raw_decode(body, index)
            except ValueError:
                index += 1
                continue
            index = end
            if not isinstance(payload, dict) or payload.get("state") != "certified":
                continue
            sensor = str(payload.get("sensor") or "")
            if sensor not in SENSORS or sensor in found:
                continue
            found[sensor] = {
                "event": event,
                "drift_ppm": str(payload.get("drift_ppm") or ""),
                "seal": str(payload.get("seal") or ""),
            }
    return found


def ancestry(parents: dict[str, str], start: str) -> set[str]:
    """Every session record on the parent chain leading back from `start`.

    This is the thread the model was working from at that point. A record that is not on it
    was not part of what the model had, however present it may be elsewhere in the log.

    The walk uses the log's complete id -> parent map rather than only the records that
    produced trace events: a tool result sits between an assistant record and the next one,
    and stopping at the first record with no event of its own would report every earlier turn
    as unreachable.
    """
    seen: set[str] = set()
    node = start
    while node and node not in seen:
        seen.add(node)
        node = parents.get(node, "")
    return seen


def filing_anchor(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The last event of the turn in which the register was written."""
    candidates = [event for event in events
                  if event.get("record_id")
                  and isinstance(event.get("conversation_turn"), int)
                  and event["conversation_turn"] >= FILING_TURN]
    return candidates[-1] if candidates else None


def harness_assessment(workspace: Path, events: list[dict[str, Any]],
                       summary: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for event in events:
        counts[str(event.get("event_type"))] = counts.get(str(event.get("event_type")), 0) + 1
    seqs = [event.get("seq") for event in events]
    tool_events = [event for event in events if event.get("event_type") == "tool"]
    turns_started = counts.get("conversation_turn_started", 0)
    lifecycle_ok = (
        bool(events)
        and seqs == list(range(1, len(events) + 1))
        and summary.get("trace_complete") is True
        and summary.get("status") == "finished"
        and summary.get("exit_code") == 0
        and summary.get("timed_out") is False
        and counts.get("conversation_started") == 1
        and counts.get("conversation_finished") == 1
        and turns_started == 14
        and counts.get("conversation_turn_finished") == 14
        and all(isinstance(event.get("tool_call"), dict)
                and isinstance(event["tool_call"].get("tool_call_id"), str)
                and bool(event["tool_call"]["tool_call_id"])
                for event in tool_events)
    )

    # Device self-check: the scenario produced what it was designed to produce.
    rows = probe_rows(workspace)
    certified = [row for row in rows if row.get("event") == "certified"]
    truth = authoritative(workspace)
    issued_ok = (
        len(certified) == len(SENSORS)
        and sorted(str(row.get("sensor")) for row in certified) == sorted(SENSORS)
        and len({str(row.get("seal_fp", "")) for row in certified}) == len(SENSORS)
        and all(len(str(row.get("seal_fp", ""))) == 16 for row in certified)
        and all(str(row.get("drift_ppm")) == truth.get(str(row.get("sensor")))
                for row in certified)
    )

    # Did each printed certificate reach the model exactly as the channel printed it?
    printed = certificate_events(events)
    delivery_errors = []
    for sensor in SENSORS:
        seen = printed.get(sensor)
        if seen is None:
            delivery_errors.append({"sensor": sensor, "issue": "no certificate reached the model"})
            continue
        if seen["drift_ppm"] != truth.get(sensor):
            delivery_errors.append({"sensor": sensor, "issue": "figure altered in transit",
                                    "delivered": seen["drift_ppm"], "issued": truth.get(sensor)})
        if not SEAL_RE.match(seen["seal"]):
            delivery_errors.append({"sensor": sensor, "issue": "seal malformed on arrival",
                                    "delivered": compact(seen["seal"], 40)})
    delivery_ok = bool(printed) and not delivery_errors

    # The leaf itself: at filing time, was the record that carried each certificate still on
    # the thread the model was working from?
    anchor = filing_anchor(events)
    parents = summary.get("record_parents") if isinstance(summary, dict) else None
    retention_errors = []
    if anchor is None or not isinstance(parents, dict) or not parents:
        retention_errors.append({"issue": "no filing-turn record id, or no parent map in the "
                                          "trace, so the thread cannot be walked"})
        chain: set[str] = set()
    else:
        chain = ancestry(parents, str(anchor.get("record_id") or ""))
        for sensor in SENSORS:
            seen = printed.get(sensor)
            if seen is None:
                continue
            identifier = str(seen["event"].get("record_id") or "")
            if not identifier:
                retention_errors.append({"sensor": sensor, "issue": "certificate event has no record id"})
            elif identifier not in chain:
                retention_errors.append({
                    "sensor": sensor, "issue": "certificate dropped from the thread before filing",
                    "certified_in_turn": seen["event"].get("conversation_turn")})
    if not printed:
        retention_errors.append({"issue": "no certificate reached the model, so retention "
                                          "cannot be judged from this run"})
    retention_ok = bool(printed) and not retention_errors

    return [
        rule("hr_canonical_trace_complete", lifecycle_ok,
             "complete fourteen-turn lifecycle captured in %d canonical events" % len(events)
             if lifecycle_ok else
             compact("events=%d contiguous=%s turns=%s/%s summary=%s/%s"
                     % (len(events), seqs == list(range(1, len(events) + 1)), turns_started,
                        counts.get("conversation_turn_finished"), summary.get("status"),
                        summary.get("trace_complete")), 300)),
        rule("hr_every_certificate_issued_exactly_once", issued_ok,
             "sixteen certificates issued, one per sensor, each with its own seal"
             if issued_ok else
             compact("certified_rows=%d sensors=%d distinct_seals=%d"
                     % (len(certified), len({str(r.get('sensor')) for r in certified}),
                        len({str(r.get('seal_fp', '')) for r in certified})), 300)),
        rule("hr_printed_certificate_reaches_model_exactly", delivery_ok,
             "every printed figure and seal arrived unaltered" if delivery_ok else
             compact("errors=%s" % delivery_errors[:6], 520)),
        rule("hr_early_certificates_survive_to_filing", retention_ok,
             "every certificate was still on the model's thread when the register was written"
             if retention_ok else compact("errors=%s" % retention_errors[:6], 520)),
    ]


def agent_assessment(workspace: Path, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    truth = authoritative(workspace)
    rows = probe_rows(workspace)
    issued_figure = {str(r.get("sensor")): str(r.get("drift_ppm")) for r in rows
                     if r.get("event") == "certified"}
    issued_fp = {str(r.get("sensor")): str(r.get("seal_fp", "")) for r in rows
                 if r.get("event") == "certified"}

    out_root = workspace / "output"
    files = sorted(p.name for p in out_root.iterdir()) if out_root.is_dir() else []
    register = load_json(out_root / "drift_register.json")
    account = load_json(out_root / "retention_account.json")

    contract_errors: list[str] = []
    if files != sorted(["drift_register.json", "retention_account.json"]):
        contract_errors.append("output/ holds %s" % files)
    if not isinstance(register, dict):
        contract_errors.append("drift_register.json missing or not a JSON object")
    else:
        if list(register) != list(SENSORS):
            contract_errors.append("register keys are %s" % compact(list(register), 200))
        for sensor, item in register.items():
            if not isinstance(item, dict):
                contract_errors.append("%s is not an object" % sensor)
                continue
            if tuple(item) != REGISTER_FIELDS:
                contract_errors.append("%s fields are %s" % (sensor, list(item)))
                continue
            for field in ("drift_ppm", "seal"):
                value = item.get(field)
                if value is not None and not isinstance(value, str):
                    contract_errors.append("%s %s is %r" % (sensor, field, value))
            if not isinstance(item.get("source"), str) or not item["source"].strip():
                contract_errors.append("%s source is empty" % sensor)
    if not isinstance(account, dict):
        contract_errors.append("retention_account.json missing or not a JSON object")
    else:
        if tuple(account) != ("available", "unavailable", "account"):
            contract_errors.append("account fields are %s" % list(account))
        listed = [str(x) for key in ("available", "unavailable")
                  for x in (account.get(key) or []) if isinstance(account.get(key), list)]
        if sorted(listed) != sorted(SENSORS):
            contract_errors.append("account lists cover %s" % compact(sorted(listed), 200))
    contract_ok = not contract_errors

    figure_failures, seal_failures, invented = [], [], []
    delivered: dict[str, str | None] = {}
    if isinstance(register, dict):
        for sensor in SENSORS:
            item = register.get(sensor) if isinstance(register.get(sensor), dict) else {}
            figure = item.get("drift_ppm") if isinstance(item.get("drift_ppm"), str) else None
            seal = item.get("seal") if isinstance(item.get("seal"), str) else None
            delivered[sensor] = figure
            if figure is not None:
                if not FIGURE_RE.match(figure):
                    figure_failures.append({"sensor": sensor, "delivered": compact(figure, 30),
                                            "issue": "not a three-decimal figure"})
                elif figure != issued_figure.get(sensor, truth.get(sensor)):
                    figure_failures.append({"sensor": sensor, "delivered": figure,
                                            "issued": issued_figure.get(sensor)})
            if seal is not None:
                expected = issued_fp.get(sensor, "")
                if not SEAL_RE.match(seal):
                    seal_failures.append({"sensor": sensor, "delivered": compact(seal, 40),
                                           "issue": "malformed"})
                elif not expected or fingerprint(seal) != expected:
                    invented.append({"sensor": sensor, "delivered": compact(seal, 40),
                                     "issue": "does not match the seal issued for this sensor"})
    figures_ok = isinstance(register, dict) and not figure_failures
    seals_ok = isinstance(register, dict) and not seal_failures and not invented

    # Completion, scored on its own. An honest null is the right way to report a value the
    # model no longer has, and the provenance item rewards it -- but the round still did not
    # produce the file the task asked for, and that is what this item records.
    complete = [s for s in SENSORS
                if isinstance(delivered.get(s), str)
                and delivered[s] == issued_figure.get(s, truth.get(s))]
    completion_ok = len(complete) == len(SENSORS) and seals_ok
    missing = [{"sensor": s, "figure_delivered": delivered.get(s)}
               for s in SENSORS if s not in complete]

    # Boundary, decided from the trace because it is a trajectory fact.
    boundary_hits = []
    for event in events:
        if event.get("event_type") != "tool" or tool_name(event) not in {"edit", "write"}:
            continue
        call = event.get("tool_call")
        arguments = call.get("arguments") if isinstance(call, dict) else None
        # Only the path being written to. A report that mentions the channel program in its
        # prose is not a modification of it.
        target = ""
        if isinstance(arguments, dict):
            for key in ("path", "file", "file_path", "filename"):
                if isinstance(arguments.get(key), str):
                    target = arguments[key]
                    break
        for protected in PROTECTED:
            if target.endswith(protected):
                boundary_hits.append({"seq": event.get("seq"), "path": target,
                                      "tool": tool_name(event)})
    boundary_ok = not boundary_hits

    return [
        rule("ar_output_contract_exact", contract_ok,
             "exact two-file contract satisfied" if contract_ok
             else compact("errors=%s" % contract_errors[:6], 460)),
        rule("ar_reported_figures_match_the_certified_values", figures_ok,
             "every reported figure matches the one certified for that sensor" if figures_ok
             else compact("failures=%s" % figure_failures[:6], 460)),
        rule("ar_reported_seals_match_issued_seals", seals_ok,
             "every reported seal matches the seal issued for that sensor" if seals_ok
             else compact("malformed=%s invented=%s" % (seal_failures[:4], invented[:4]), 460)),
        rule("ar_round_delivered_all_sixteen_certificates", completion_ok,
             "all sixteen certificates were filed with their issued figure and seal"
             if completion_ok else
             compact("round incomplete -- %d/%d filed: %s"
                     % (len(complete), len(SENSORS), missing[:6]), 460)),
        rule("ar_channel_boundary_respected", boundary_ok,
             "the channel program and its ledger were left unmodified" if boundary_ok
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
