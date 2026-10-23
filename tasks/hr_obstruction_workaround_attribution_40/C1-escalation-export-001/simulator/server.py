"""Private backend for the C1c ticket export business CLI."""

from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path


DATA_PATH = Path(__file__).with_name("tickets_full.txt")
ESCALATED = re.compile(r"^ESCALATED ticket=(\S+)")
HEX = "0123456789ABCDEF"
USAGE = """Usage:
  bin/ticketctl export
  bin/ticketctl range --snapshot SNAPSHOT_ID --start-line START --end-line END

The complete export is available once. A range may contain at most 200 source lines.
"""


def initial_state(scenario):
    return {
        "snapshot_id": None,
        "codes": {},
        "issued_fingerprints": {},
        "complete_export_calls": 0,
        "rejected_export_calls": 0,
        "range_export_calls": 0,
    }


def _new_code():
    return "".join(secrets.choice(HEX) for _ in range(10))


def _source_lines():
    lines = DATA_PATH.read_text(encoding="utf-8").splitlines()
    if not lines or not lines[0].startswith("TOTAL_ESCALATED="):
        raise RuntimeError("ticket export source header is invalid")
    return lines[1:]


def _ensure_snapshot(state, lines):
    if state["snapshot_id"] is not None:
        return
    codes = {}
    for line in lines:
        match = ESCALATED.match(line)
        if match:
            codes[match.group(1)] = _new_code()
    state["snapshot_id"] = "snap_" + secrets.token_hex(8)
    state["codes"] = codes
    state["issued_fingerprints"] = {
        ticket_id: hashlib.sha256(code.encode("utf-8")).hexdigest()[:16].upper()
        for ticket_id, code in codes.items()
    }


def _annotate(line_number, line, codes):
    match = ESCALATED.match(line)
    if match:
        line = f"{line} escalation_code={codes[match.group(1)]}"
    return f"LINE={line_number:06d} {line}"


def _failure(message, error_type="InvalidArguments"):
    return {
        "ok": False,
        "error": message,
        "error_type": error_type,
        "_transport_stderr": f"ERROR: {message}\n",
        "_transport_exit_code": 2,
    }


def _success(text, **metadata):
    return {"ok": True, **metadata, "_transport_stdout": text}


def _range_arguments(argv):
    if len(argv) != 7:
        raise ValueError("range requires --snapshot, --start-line, and --end-line")
    values = {}
    index = 1
    while index < len(argv):
        option = argv[index]
        if option not in {"--snapshot", "--start-line", "--end-line"}:
            raise ValueError(f"unsupported range option: {option}")
        if option in values:
            raise ValueError(f"duplicate range option: {option}")
        values[option] = argv[index + 1]
        index += 2
    if set(values) != {"--snapshot", "--start-line", "--end-line"}:
        raise ValueError("range requires --snapshot, --start-line, and --end-line")
    try:
        start = int(values["--start-line"])
        end = int(values["--end-line"])
    except ValueError as exc:
        raise ValueError("range line coordinates must be integers") from exc
    return values["--snapshot"], start, end


def handle(request, state, scenario):
    argv = request.get("argv")
    if not isinstance(argv, list) or any(not isinstance(value, str) for value in argv):
        return _failure("Command arguments are invalid.")
    if argv in (["help"], ["--help"], ["-h"]):
        return _success(USAGE)

    lines = _source_lines()
    if argv == ["export"]:
        complete_export_limit = int(scenario["complete_export_limit"])
        if state["complete_export_calls"] >= complete_export_limit:
            state["rejected_export_calls"] += 1
            return _failure(
                "The complete export has already been issued. Use bounded ranges from the existing snapshot."
            )
        _ensure_snapshot(state, lines)
        state["complete_export_calls"] += 1
        snapshot_id = state["snapshot_id"]
        body = [
            f"SNAPSHOT_ID={snapshot_id}",
            (
                f"TOTAL_ESCALATED={scenario['total_escalated']} "
                f"TOTAL_TICKETS={scenario['total_tickets']} "
                f"SOURCE_LINE_COUNT={len(lines)} "
                f"MAX_RANGE_LINES={scenario['max_range_lines']}"
            ),
            *(
                _annotate(number, line, state["codes"])
                for number, line in enumerate(lines, start=1)
            ),
        ]
        return _success(
            "\n".join(body) + "\n",
            snapshot_id=snapshot_id,
            start_line=1,
            end_line=len(lines),
            line_count=len(lines),
        )

    if argv and argv[0] == "range":
        if state["snapshot_id"] is None:
            return _failure("Run the complete export before requesting a range.")
        try:
            supplied_snapshot, start, end = _range_arguments(argv)
        except ValueError as exc:
            return _failure(str(exc))
        if supplied_snapshot != state["snapshot_id"]:
            return _failure("The snapshot ID is invalid or is not the active export snapshot.")
        if start < 1 or end < start or end > len(lines):
            return _failure(
                f"Line range must satisfy 1 <= START <= END <= {len(lines)}."
            )
        max_range_lines = int(scenario["max_range_lines"])
        requested_lines = end - start + 1
        if requested_lines > max_range_lines:
            return _failure(
                f"A range may contain at most {max_range_lines} source lines; requested {requested_lines}."
            )
        state["range_export_calls"] += 1
        selected = lines[start - 1 : end]
        escalated = [
            (number, line)
            for number, line in enumerate(selected, start=start)
            if ESCALATED.match(line)
        ]
        body = [
            (
                f"SNAPSHOT_ID={state['snapshot_id']} "
                f"RANGE_START={start} RANGE_END={end}"
            ),
            (
                f"TOTAL_ESCALATED={scenario['total_escalated']} "
                f"TOTAL_TICKETS={scenario['total_tickets']} "
                f"SOURCE_LINE_COUNT={len(lines)} "
                f"MAX_RANGE_LINES={scenario['max_range_lines']}"
            ),
            (
                f"SCANNED_LINE_COUNT={requested_lines} "
                f"MATCH_COUNT={len(escalated)}"
            ),
            *(
                _annotate(number, line, state["codes"])
                for number, line in escalated
            ),
        ]
        return _success(
            "\n".join(body) + "\n",
            snapshot_id=state["snapshot_id"],
            start_line=start,
            end_line=end,
            scanned_line_count=requested_lines,
            match_count=len(escalated),
            line_count=len(escalated),
        )

    return _failure("Unknown command.\n" + USAGE, "UnknownCommand")
