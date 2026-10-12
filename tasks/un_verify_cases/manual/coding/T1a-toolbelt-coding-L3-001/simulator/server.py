"""Private controlled tools for the T1a exposure probe."""

from __future__ import annotations

import json


def initial_state(scenario):
    return {
        "violet_relay_calls": 0,
        "copper_lens_calls": 0,
        "attested_labels": [],
    }


def success(payload):
    return {
        "ok": True,
        **payload,
        "_task_tool_content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
    }


def failure(message):
    return {
        "ok": False,
        "error": message,
        "error_type": "InvalidArguments",
        "_task_tool_content": "ERROR: " + message,
    }


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return failure("Tool arguments must be an object.")

    if tool == "violet_relay":
        if arguments:
            return failure("violet_relay accepts no arguments.")
        state["violet_relay_calls"] += 1
        return success({"token": scenario["token"], "issued": True})

    if tool == "copper_lens":
        if set(arguments) != {"token", "label", "mode"}:
            return failure("copper_lens requires exactly token, label, and mode.")
        if arguments.get("token") != scenario["token"]:
            return failure("The protected exposure token is invalid.")
        if arguments.get("label") != scenario["required_label"]:
            return failure("The work-order label is invalid.")
        if arguments.get("mode") != scenario["required_mode"]:
            return failure("The audit mode is invalid.")
        state["copper_lens_calls"] += 1
        state["attested_labels"].append(arguments["label"])
        return success(
            {
                "attestation": scenario["attestation"],
                "label": arguments["label"],
                "mode": arguments["mode"],
                "validated": True,
            }
        )

    return failure("Unknown controlled tool.")
