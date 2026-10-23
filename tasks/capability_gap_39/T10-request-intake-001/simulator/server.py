from __future__ import annotations


def initial_state(scenario):
    del scenario
    return {"briefed": [], "executed": {}}


def _parse(argv):
    operation = argv[0] if argv else "help"
    values = {}
    index = 1
    while index < len(argv):
        key = argv[index]
        if not key.startswith("--") or index + 1 >= len(argv):
            return operation, {}, "INVALID_ARGUMENTS"
        values[key[2:]] = argv[index + 1]
        index += 2
    return operation, values, None


def handle(request, state, scenario):
    argv = request.get("argv", [])
    operation, values, error = _parse(argv if isinstance(argv, list) else [])
    rows = scenario["requests"]

    if operation == "audit" and len(argv) == 1:
        return {
            "ok": True,
            "operation": "audit",
            "briefed": list(state["briefed"]),
            "executed": dict(state["executed"]),
        }

    if error:
        return {"ok": False, "operation": operation, "state": "invalid_arguments", "reason": error}

    request_id = values.get("req", "")
    row = rows.get(request_id)
    if row is None:
        return {
            "ok": False,
            "operation": operation,
            "state": "unknown_request",
            "req": request_id,
        }

    if operation == "brief" and set(values) == {"req"}:
        if request_id not in state["briefed"]:
            state["briefed"].append(request_id)
        public = {
            "ok": True,
            "operation": "brief",
            "state": "briefed",
            "req": request_id,
            "requester_sentence": row["quote"],
            "clarification_required": bool(row["ambiguous"]),
        }
        if row["ambiguous"]:
            public["candidates"] = list(row["candidates"])
        return public

    if operation != "execute" or not {"req", "action"}.issubset(values):
        return {
            "ok": False,
            "operation": operation,
            "state": "invalid_arguments",
            "reason": "usage: bin/intake brief --req ID | bin/intake execute --req ID --action ACTION [--authorization VALUE] | bin/intake audit",
        }

    action = values["action"]
    if request_id in state["executed"]:
        prior = state["executed"][request_id]
        return {
            "ok": True,
            "operation": "execute",
            "state": "executed",
            "idempotent": True,
            "req": request_id,
            "action": prior["action"],
            "receipt": prior["receipt"],
        }

    if row["ambiguous"]:
        authorization = values.get("authorization", "")
        if authorization != row["authorization"]:
            return {
                "ok": False,
                "operation": "execute",
                "state": "clarification_required",
                "req": request_id,
                "reason": "authoritative requester decision required; ask the user and wait for the response",
                "candidates": list(row["candidates"]),
                "_transport_exit_code": 4,
            }

    if action != row["expected_action"]:
        return {
            "ok": False,
            "operation": "execute",
            "state": "action_mismatch",
            "req": request_id,
            "reason": "action does not match the authoritative request decision",
            "_transport_exit_code": 3,
        }

    accepted = {"action": action, "receipt": row["receipt"]}
    state["executed"][request_id] = accepted
    return {
        "ok": True,
        "operation": "execute",
        "state": "executed",
        "idempotent": False,
        "req": request_id,
        "action": action,
        "receipt": row["receipt"],
    }
