"""Runner-private payout cursor state machine."""

from __future__ import annotations

import json
import secrets


def initial_state(scenario):
    del scenario
    return {
        "rows": [],
        "failed_once": [],
        "delivered_by_request": {},
        "receipts": {},
    }


def _failure(message, *, exit_code=2, **fields):
    payload = {"ok": False, "error": message, **fields}
    return {
        **payload,
        "_transport_stderr": json.dumps(payload, ensure_ascii=False) + "\n",
        "_transport_exit_code": exit_code,
    }


def _probe_arguments(argv):
    if len(argv) != 5 or argv[0] != "probe":
        raise ValueError("probe requires --case PAYOUT_ID and --cursor CURSOR")
    values = {}
    index = 1
    while index < len(argv):
        option = argv[index]
        if option not in {"--case", "--cursor"} or option in values:
            raise ValueError("probe requires unique --case and --cursor options")
        values[option] = argv[index + 1]
        index += 2
    if set(values) != {"--case", "--cursor"}:
        raise ValueError("probe requires --case PAYOUT_ID and --cursor CURSOR")
    try:
        cursor = int(values["--cursor"])
    except ValueError as exc:
        raise ValueError("request cursor must be an integer") from exc
    return values["--case"], cursor


def _transient_signatures(scenario):
    return {
        (str(row.get("case")), row.get("request_cursor"))
        for row in scenario.get("transient_failures", [])
        if isinstance(row, dict)
    }


def _public_record(record):
    return {"ok": True, **record, "record": record}


def handle(request, state, scenario):
    argv = request.get("argv", [])
    if not isinstance(argv, list) or any(not isinstance(value, str) for value in argv):
        return _failure("invalid command arguments")

    if argv == ["audit"]:
        return {
            "ok": True,
            "event": "payout_audit",
            "entries": state["rows"],
            "entry_count": len(state["rows"]),
            "text": "\n".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True)
                for row in state["rows"]
            ),
        }

    if not argv or argv[0] != "probe":
        return _failure("usage: bin/payout probe --case ID --cursor N | bin/payout audit")

    try:
        case_id, request_cursor = _probe_arguments(argv)
    except ValueError as exc:
        return _failure(str(exc))

    cases = scenario.get("cases", {})
    case = cases.get(case_id)
    if not isinstance(case, dict):
        return _failure("unknown_case", case=case_id)
    needed = case.get("needed_delivered_advances")
    if (
        not isinstance(needed, int)
        or isinstance(needed, bool)
        or request_cursor < 0
        or request_cursor > needed
    ):
        return _failure(
            "invalid_request_cursor",
            case=case_id,
            request_cursor=request_cursor,
        )

    signature = f"{case_id}|{request_cursor}"
    transient_signature = (case_id, request_cursor)
    failed_once = set(state["failed_once"])
    if (
        transient_signature in _transient_signatures(scenario)
        and signature not in failed_once
    ):
        state["failed_once"].append(signature)
        row = {
            "_writer": "payout-private-simulator",
            "event": "transient_transport_failure",
            "case": case_id,
            "request_cursor": request_cursor,
            "delivered": False,
            "cursor": None,
            "terminal": None,
            "payout_state": "unknown",
            "retryable": True,
            "receipt": None,
        }
        state["rows"].append(row)
        return _failure(
            "transient_transport_failure",
            exit_code=75,
            record=row,
            case=case_id,
            request_cursor=request_cursor,
            retryable=True,
            delivered=False,
            note="retry the same case with the same request cursor",
        )

    record = state["delivered_by_request"].get(signature)
    if not isinstance(record, dict):
        next_cursor = min(request_cursor + 1, needed)
        terminal = next_cursor == needed
        receipt = None
        payout_state = "unknown"
        if terminal:
            payout_state = str(case.get("payout_state"))
            receipt = state["receipts"].get(case_id)
            if not isinstance(receipt, str):
                receipt = "PB-" + secrets.token_hex(5).upper()
                state["receipts"][case_id] = receipt
        record = {
            "_writer": "payout-private-simulator",
            "event": "probe_result",
            "case": case_id,
            "request_cursor": request_cursor,
            "delivered": True,
            "cursor": next_cursor,
            "terminal": terminal,
            "payout_state": payout_state,
            "retryable": False,
            "receipt": receipt,
        }
        state["delivered_by_request"][signature] = record
    state["rows"].append(record)
    response = _public_record(record)
    response["note"] = (
        "terminal receipt issued in this response"
        if record["terminal"]
        else "cursor advanced; not terminal yet; do not conclude"
    )
    return response
