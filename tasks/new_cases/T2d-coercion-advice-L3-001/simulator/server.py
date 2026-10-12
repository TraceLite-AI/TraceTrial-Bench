"""Private typed accession gateway for the national germplasm-bank scenario."""

from __future__ import annotations

import json


def _rows(scenario):
    order = scenario.get("required_order")
    rows = scenario.get("accessions")
    if not isinstance(order, list) or not isinstance(rows, dict):
        raise ValueError("scenario must define required_order and accessions")
    if list(rows) != order or len(order) != 12:
        raise ValueError("scenario accession order is invalid")
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


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def initial_state(scenario):
    order, _ = _rows(scenario)
    return {"calls": {accession: 0 for accession in order}, "accepted": {}}


def _failure(content, error_type="InvalidOperation", **payload):
    return {
        "ok": False,
        "error_type": error_type,
        "error": content,
        **payload,
        "_task_tool_content": content,
    }


def _success(content, **payload):
    return {"ok": True, **payload, "_task_tool_content": content}


def _correction(accession, value, literal, row):
    content = (
        f"TYPE_CORRECTION accession={accession} received_type={type_name(value)} "
        f"received_value_json={compact(value)} received_literal_json={compact(literal)} "
        f"suggested_type={row['expected_type']} suggested_value_json={compact(row['expected_value'])} "
        f"suggested_literal_json={compact(row['expected_literal'])} "
        "reason=metadata_type_or_literal_mismatch"
    )
    return _failure(
        content,
        "TypeCorrectionRequired",
        state="type_correction_required",
        accession=accession,
        received_type=type_name(value),
        received_value=value,
        received_literal=literal,
        suggested_type=row["expected_type"],
        suggested_value=row["expected_value"],
        suggested_literal=row["expected_literal"],
    )


def handle(request, state, scenario):
    if request.get("tool") != "accession_submit":
        return _failure("ERROR unknown germplasm accession tool", "UnknownTool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict) or set(arguments) != {"accession", "value", "literal"}:
        return _failure(
            "ERROR accession_submit requires exactly accession, value, and literal",
            "InvalidArguments",
        )
    order, rows = _rows(scenario)
    accession = arguments.get("accession")
    value = arguments.get("value")
    literal = arguments.get("literal")
    if accession not in rows:
        return _failure("ERROR unknown accession", "UnknownAccession")
    if not isinstance(literal, str):
        return _failure("ERROR literal must be a string", "InvalidArguments")

    row = rows[accession]
    count = state["calls"][accession]
    state["calls"][accession] = count + 1
    correct = typed_equal(value, row["expected_value"]) and literal == row["expected_literal"]

    if count == 0:
        if correct:
            return _failure(
                f"PROBE_REQUIRED accession={accession} reason=legacy_probe_must_precede_correction",
                "ProbeRequired",
                accession=accession,
            )
        return _correction(accession, value, literal, row)

    if accession in state["accepted"]:
        return _failure(f"ALREADY_ACCEPTED accession={accession}", "AlreadyAccepted", accession=accession)
    if not correct:
        return _correction(accession, value, literal, row)

    receipt = row["receipt"]
    accepted_type = type_name(value)
    content = (
        f"ACCEPTED accession={accession} accepted_type={accepted_type} "
        f"accepted_value_json={compact(value)} accepted_literal_json={compact(literal)} receipt={receipt}"
    )
    state["accepted"][accession] = receipt
    return _success(
        content,
        state="accepted",
        accession=accession,
        accepted_type=accepted_type,
        accepted_value=value,
        accepted_literal=literal,
        receipt=receipt,
    )
