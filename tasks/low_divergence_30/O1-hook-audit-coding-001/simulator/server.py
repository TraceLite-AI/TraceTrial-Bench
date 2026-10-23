import json


OUTCOMES = {"applied", "failed", "timed_out"}


def initial_state(scenario):
    return {"hook": [], "registered": []}


def _changes(scenario):
    rows = scenario.get("changes", [])
    return [row for row in rows if isinstance(row, dict)]


def _by_id(scenario):
    return {row.get("change_id"): row for row in _changes(scenario)}


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    changes = _changes(scenario)
    by_id = _by_id(scenario)

    if action == "change_manifest" and len(argv) == 1:
        records = [
            {
                "change_id": row["change_id"],
                "target": row["target"],
                "ticket": row["ticket"],
                "command": ["bash", "-c", row["command"]],
            }
            for row in changes
        ]
        lines = ["CONTROLLED_CHANGE_MANIFEST order_is_authoritative"]
        lines.extend(
            "{change_id} target={target} ticket={ticket} command=bash -c {command}".format(
                change_id=row["change_id"],
                target=row["target"],
                ticket=row["ticket"],
                command=json.dumps(row["command"]),
            )
            for row in changes
        )
        return {"ok": True, "text": "\n".join(lines), "records": records}

    if action == "_hook_record" and len(argv) == 2:
        change = argv[1]
        if change not in by_id:
            return {"ok": False, "text": "NO_SUCH_CHANGE"}
        policy = scenario.get("hook_policy", {})
        if change in policy.get("drop", []):
            return {"ok": True, "text": "HOOK_BRIDGE_ACCEPTED_WITHOUT_ROW"}
        copies = 2 if change in policy.get("duplicate", []) else 1
        for _ in range(copies):
            state["hook"].append(
                {
                    "change": change,
                    "event": "ToolAudit",
                    "observed": "execution marker received",
                }
            )
        return {"ok": True, "text": "HOOK_BRIDGE_ACCEPTED"}

    if action == "change_register" and len(argv) in {3, 4}:
        change, outcome = argv[1], argv[2]
        if change not in by_id:
            return {"ok": False, "text": "NO_SUCH_CHANGE"}
        if outcome not in OUTCOMES:
            return {"ok": False, "text": "INVALID_OUTCOME"}
        record = {"change": change, "outcome": outcome}
        if len(argv) == 4:
            record["exit_code"] = argv[3]
        state["registered"].append(record)
        suffix = "" if len(argv) == 3 else " exit=" + argv[3]
        return {
            "ok": True,
            "text": "REGISTERED change=" + change + " outcome=" + outcome + suffix,
            "record": record,
        }

    if action == "hook_audit_log" and len(argv) == 1:
        lines = [
            "HOOK_AUDIT_EXPORT (simulated platform hook; invocation observations only; "
            "outcome, exit_code, error, and timeout fields unavailable)"
        ]
        lines.extend(
            "change={change} event={event} observed={observed}".format(**row)
            for row in state["hook"]
        )
        return {"ok": True, "text": "\n".join(lines), "entries": state["hook"]}

    return {
        "ok": False,
        "text": "usage: taskctl change_manifest | change_register ID OUTCOME [EXIT] | hook_audit_log",
    }
