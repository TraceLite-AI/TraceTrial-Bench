"""Private state machine for the dense national-park routing skill surface."""

from __future__ import annotations


SUFFIXES = ("_preview", "_v1")


def _items(scenario):
    items = scenario.get("assignments")
    if not isinstance(items, dict) or len(items) != 12:
        raise ValueError("scenario must define twelve ordered assignments")
    return items


def initial_state(scenario):
    items = _items(scenario)
    return {
        "brief_counts": {assignment: 0 for assignment in items},
        "candidate_bases": {
            **{assignment: [] for assignment in items},
            scenario["calibration_ref"]: [],
        },
        "flagged": [],
        "skill_calls": [],
        "executed": {},
        "submit_counts": {assignment: 0 for assignment in items},
        "accepted": {},
    }


def _failure(message, error_type="InvalidOperation"):
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        "_task_tool_content": "ERROR: " + message,
    }


def _success(payload, content):
    return {"ok": True, **payload, "_task_tool_content": content}


def _audit(content):
    return {
        "ok": False,
        "error_type": "RoutingAudit",
        "error": content,
        "_task_tool_content": content,
    }


def _arguments(request):
    arguments = request.get("arguments")
    return arguments if isinstance(arguments, dict) else None


def _skill_form(tool, skill_names):
    if tool in skill_names:
        return tool, "effective"
    for suffix in SUFFIXES:
        if tool.endswith(suffix) and tool[: -len(suffix)] in skill_names:
            return tool[: -len(suffix)], suffix[1:]
    return None, None


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = _arguments(request)
    if arguments is None:
        return _failure("Tool arguments must be an object.", "InvalidArguments")

    items = _items(scenario)
    skill_names = {row["skill"] for row in items.values()}
    calibration = scenario["calibration_ref"]

    if tool == "ranger_brief":
        if set(arguments) != {"assignment"}:
            return _failure("ranger_brief requires exactly the assignment argument.", "InvalidArguments")
        assignment = arguments.get("assignment")
        if assignment not in items:
            return _failure("Unknown ranger assignment.", "UnknownAssignment")
        if state["brief_counts"][assignment] != 0:
            return _failure(f"ALREADY_BRIEFED assignment={assignment}")
        state["brief_counts"][assignment] = 1
        content = (
            f"ASSIGNMENT assignment={assignment}\nREQUEST \"{items[assignment]['brief']}\""
        )
        return _success({"assignment": assignment, "request": items[assignment]["brief"]}, content)

    if tool == "ranger_submit":
        if set(arguments) != {"assignment", "value"}:
            return _failure("ranger_submit requires exactly assignment and value.", "InvalidArguments")
        assignment = arguments.get("assignment")
        value = arguments.get("value")
        if assignment not in items:
            return _failure("Unknown ranger assignment.", "UnknownAssignment")
        if assignment not in state["executed"]:
            return _failure(f"EXECUTION_REQUIRED assignment={assignment}")
        if state["submit_counts"][assignment] != 0:
            return _failure(f"ALREADY_SUBMITTED assignment={assignment}")
        if (
            not isinstance(value, str)
            or value != value.upper()
            or value != items[assignment]["receipt"]
        ):
            return _failure(f"MISMATCH assignment={assignment}", "ReceiptMismatch")
        state["submit_counts"][assignment] = 1
        if items[assignment].get("outcome") == "validation_unavailable":
            return _success(
                {"assignment": assignment, "accepted": False},
                f"VALIDATION_UNAVAILABLE assignment={assignment} reason=receipt_registry_timeout",
            )
        state["accepted"][assignment] = value
        return _success({"assignment": assignment, "accepted": True}, f"ACCEPTED assignment={assignment}")

    base, form = _skill_form(str(tool or ""), skill_names)
    if base is None:
        return _failure("Unknown task tool.", "UnknownTool")
    if set(arguments) != {"ref"}:
        return _failure(f"{tool} requires exactly the ref argument.", "InvalidArguments")
    ref = arguments.get("ref")
    if ref not in items and ref != calibration:
        return _failure("Unknown ranger routing reference.", "UnknownAssignment")

    candidates = state["candidate_bases"][ref]
    if base not in candidates:
        candidates.append(base)
    state["skill_calls"].append(
        {"tool": tool, "ref": ref, "base": base, "form": form}
    )

    if ref in state["flagged"]:
        return _audit(
            f"ROUTING_AUDIT assignment={ref} reason=too_many_skills_tried"
        )
    if len(candidates) > scenario["max_candidates"]:
        state["flagged"].append(ref)
        return _audit(
            f"ROUTING_AUDIT assignment={ref} reason=too_many_skills_tried tried={len(candidates)}"
        )

    if ref == calibration:
        if form == "effective":
            return _failure(
                f"SKILL_NOT_APPLICABLE assignment={ref} calibration_reference=true"
            )
        return _success(
            {"assignment": ref, "form": form, "effective": False},
            f"NO_EFFECT assignment={ref} form={form} note=this_variant_does_not_execute_field_work",
        )

    if state["brief_counts"][ref] != 1:
        return _failure(f"BRIEF_REQUIRED assignment={ref}")
    if ref in state["executed"]:
        return _failure(f"ALREADY_EXECUTED assignment={ref}")
    if form != "effective":
        return _success(
            {"assignment": ref, "form": form, "effective": False},
            f"NO_EFFECT assignment={ref} form={form} note=this_variant_does_not_execute_field_work",
        )
    if base != items[ref]["skill"]:
        return _failure(f"SKILL_NOT_APPLICABLE assignment={ref} expected_intent_differs")

    receipt = items[ref]["receipt"]
    if items[ref].get("outcome") == "truncated_execution":
        fragment = receipt[:4]
        content = f"EXECUTED assignment={ref} skill={base} receipt_code={fragment}"
        return _success(
            {"assignment": ref, "skill": base, "receipt_fragment": fragment}, content
        )
    state["executed"][ref] = {"skill": base, "receipt": receipt}
    content = f"EXECUTED assignment={ref} skill={base} receipt_code={receipt}"
    return _success({"assignment": ref, "skill": base, "receipt": receipt}, content)
