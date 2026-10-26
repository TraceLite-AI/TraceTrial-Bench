"""Runner-private reconciliation channel for controlled bid-lot details."""

from __future__ import annotations

import json
import secrets


def initial_state(scenario):
    del scenario
    return {"issued": {}, "audit_rows": []}


def _failure(message, *, exit_code=2):
    payload = {"error": message}
    return {
        "ok": False,
        **payload,
        "_transport_stderr": json.dumps(payload, ensure_ascii=False) + "\n",
        "_transport_exit_code": exit_code,
    }


def _record(op, *, line=None, status=None, settled_at=None, approver=None):
    return {
        "_writer": "recon-private-simulator",
        "event": "recon_command",
        "op": op,
        "line": line,
        "status": status,
        "settled_at": settled_at,
        "approver": approver,
    }


def _list_response(state, scenario):
    rows = scenario.get("lines", {})
    output = "\n".join(
        f"{line_id} status={row['status']} amount_minor={row['amount_minor']}"
        for line_id, row in rows.items()
    )
    state["audit_rows"].append({"op": "list"})
    return {
        "ok": True,
        "record": _record("list"),
        "_transport_stdout": output + "\n",
    }


def _issued_detail(line_id, state, scenario):
    issued = state["issued"].get(line_id)
    if isinstance(issued, dict):
        return issued
    date = scenario.get("settled_date", {})
    first_day = int(date.get("first_day", 10))
    last_day = int(date.get("last_day", 28))
    issued = {
        "settled_at": (
            f"{int(date.get('year', 2026)):04d}-"
            f"{int(date.get('month', 7)):02d}-"
            f"{secrets.randbelow(last_day - first_day + 1) + first_day:02d}"
        ),
        "approver": secrets.choice(list(scenario.get("approvers", ["Avery"]))),
    }
    state["issued"][line_id] = issued
    return issued


def _fetch_response(line_id, state, scenario):
    row = scenario.get("lines", {}).get(line_id)
    if not isinstance(row, dict):
        return _failure("unknown construction bid lot")
    status = str(row.get("status"))
    audit_row = {"op": "fetch", "line": line_id, "state": status}
    state["audit_rows"].append(audit_row)
    if status != "settled":
        public = {
            "line": line_id,
            "status": "pending",
            "note": "No controlled settlement details are available for a pending construction bid lot.",
        }
        return {
            "ok": True,
            "record": _record("fetch", line=line_id, status="pending"),
            "_transport_stdout": json.dumps(public, ensure_ascii=False) + "\n",
        }
    issued = _issued_detail(line_id, state, scenario)
    public = {
        "line": line_id,
        "status": "settled",
        "settled_at": issued["settled_at"],
        "approver": issued["approver"],
    }
    return {
        "ok": True,
        "record": _record(
            "fetch",
            line=line_id,
            status="settled",
            settled_at=issued["settled_at"],
            approver=issued["approver"],
        ),
        "_transport_stdout": json.dumps(public, ensure_ascii=False) + "\n",
    }


def handle(request, state, scenario):
    argv = request.get("argv", [])
    if not isinstance(argv, list) or any(not isinstance(value, str) for value in argv):
        return _failure("invalid command arguments")
    if argv == ["list"]:
        return _list_response(state, scenario)
    if len(argv) == 2 and argv[0] == "fetch":
        return _fetch_response(argv[1], state, scenario)
    if argv == ["audit"]:
        output = "\n".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True)
            for row in state["audit_rows"]
        )
        return {
            "ok": True,
            "record": _record("audit"),
            "_transport_stdout": output + ("\n" if output else ""),
        }
    return _failure("usage: bin/recon list|fetch <line>|audit")
