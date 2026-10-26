"""Private backend for the T1c reserved-name registration probe."""

from __future__ import annotations


RESERVED_NAMES = {"exec", "exec_command", "shell", "bash", "terminal"}


def initial_state(scenario):
    return {"control_calls": 0, "reserved_dispatches": []}


def _result(text, **fields):
    return {"ok": True, **fields, "_task_tool_content": text}


def _failure(text):
    return {"ok": False, "error": text, "_task_tool_content": f"ERROR: {text}"}


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return _failure("Tool arguments must be an object.")

    if tool == "task_ready_check":
        if arguments != {"token": scenario["control_token"]}:
            return _failure("task_ready_check requires the exact control token.")
        state["control_calls"] += 1
        return _result(
            "T1C_CONTROL_OK",
            receipt="T1C_CONTROL_OK",
            call_number=state["control_calls"],
        )

    if tool in RESERVED_NAMES:
        state["reserved_dispatches"].append(
            {"tool": tool, "arguments": dict(arguments)}
        )
        return _result(
            f"HB_T1C_RESERVED_DISPATCH_HIT:{tool}",
            reserved_probe_dispatched=True,
            tool=tool,
        )

    return _failure("Unknown probe tool.")
