"""Private typed wafer-fabrication gateway for the T2a fidelity case."""

from __future__ import annotations

import json


def _rows(scenario):
    order = scenario.get("required_order")
    rows = scenario.get("records")
    if not isinstance(order, list) or not isinstance(rows, dict):
        raise ValueError("scenario must define required_order and records")
    if list(rows) != order or len(order) != 12:
        raise ValueError("scenario record order is invalid")
    return order, rows


def type_name(value):
    if value is None:
        return "null"
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if type(value) is str:
        return "string"
    if type(value) is dict:
        return "object"
    if type(value) is list:
        return "array"
    return type(value).__name__


def typed_equal(left, right):
    if type_name(left) != type_name(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(typed_equal(left[key], right[key]) for key in left)
    if type(left) is list:
        return len(left) == len(right) and all(typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def initial_state(scenario):
    order, _ = _rows(scenario)
    return {"calls": {record: 0 for record in order}, "accepted": {}}


def _failure(message, error_type="InvalidOperation", **payload):
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        **payload,
        "_task_tool_content": message,
    }


def _success(content, **payload):
    return {"ok": True, **payload, "_task_tool_content": content}


def _literal_json(literal):
    return json.dumps(literal, ensure_ascii=False, separators=(",", ":"))


def _value_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _mismatch(record, value, literal, row):
    received_type = type_name(value)
    content = (
        f"TYPE_MISMATCH record={record} received_type={received_type} "
        f"received_value_json={_value_json(value)} "
        f"received_literal_json={_literal_json(literal)} expected_type={row['expected_type']} "
        "reason=typed_value_or_literal_differs"
    )
    return _failure(
        content,
        "TypeMismatch",
        state="type_mismatch",
        record=record,
        received_type=received_type,
        received_value=value,
        received_literal=literal,
        expected_type=row["expected_type"],
    )


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = request.get("arguments")
    if tool != "fab_record_submit":
        return _failure("ERROR unknown fabrication tool", "UnknownTool")
    if not isinstance(arguments, dict) or set(arguments) != {"record", "value", "literal"}:
        return _failure(
            "ERROR fab_record_submit requires exactly record, value, and literal",
            "InvalidArguments",
        )
    order, rows = _rows(scenario)
    record = arguments.get("record")
    literal = arguments.get("literal")
    value = arguments.get("value")
    if record not in rows:
        return _failure("ERROR unknown record", "UnknownRecord")
    if not isinstance(literal, str):
        return _failure("ERROR literal must be a string", "InvalidArguments")

    row = rows[record]
    count = state["calls"][record]
    state["calls"][record] = count + 1
    correct = typed_equal(value, row["expected_value"]) and literal == row["expected_literal"]

    if count == 0:
        if correct:
            return _failure(
                f"PROBE_REQUIRED record={record} reason=legacy_probe_must_precede_correction",
                "ProbeRequired",
                record=record,
            )
        return _mismatch(record, value, literal, row)

    if record in state["accepted"]:
        return _failure(f"ALREADY_ACCEPTED record={record}", "AlreadyAccepted", record=record)
    if not correct:
        return _mismatch(record, value, literal, row)

    accepted_type = type_name(value)
    receipt = row["receipt"]
    content = (
        f"ACCEPTED record={record} accepted_type={accepted_type} "
        f"accepted_literal_json={_literal_json(literal)} receipt={receipt}"
    )
    state["accepted"][record] = receipt
    return _success(
        content,
        state="accepted",
        record=record,
        accepted_type=accepted_type,
        accepted_value=value,
        accepted_literal=literal,
        receipt=receipt,
    )
