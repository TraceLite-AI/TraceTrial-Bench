"""Private state machine for the dense release window native tool surface."""

from __future__ import annotations


SUFFIXES = ("_draft", "_dry_run", "_legacy")


def _items(scenario):
    items = scenario.get("items")
    if not isinstance(items, dict) or len(items) != 12:
        raise ValueError("scenario must define twelve ordered items")
    return items


def initial_state(scenario):
    items = _items(scenario)
    return {
        "brief_counts": {item: 0 for item in items},
        "candidate_bases": {**{item: [] for item in items}, scenario["calibration_ref"]: []},
        "blocked": [],
        "action_calls": [],
        "effective": {},
        "submit_counts": {item: 0 for item in items},
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


def _audit_block(content):
    return {
        "ok": False,
        "error_type": "AuditBlocked",
        "error": content,
        "_task_tool_content": content,
    }


def _arguments(request):
    arguments = request.get("arguments")
    return arguments if isinstance(arguments, dict) else None


def _action_form(tool, action_names):
    if tool in action_names:
        return tool, "effective"
    for suffix in SUFFIXES:
        if tool.endswith(suffix) and tool[: -len(suffix)] in action_names:
            return tool[: -len(suffix)], suffix[1:]
    return None, None


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = _arguments(request)
    if arguments is None:
        return _failure("Tool arguments must be an object.", "InvalidArguments")

    items = _items(scenario)
    action_names = {row["action"] for row in items.values()}
    calibration = scenario["calibration_ref"]

    if tool == "proc_brief":
        if set(arguments) != {"item"}:
            return _failure("proc_brief requires exactly the item argument.", "InvalidArguments")
        item = arguments.get("item")
        if item not in items:
            return _failure("Unknown business item.", "UnknownItem")
        if state["brief_counts"][item] != 0:
            return _failure(f"ALREADY_BRIEFED item={item}")
        state["brief_counts"][item] = 1
        content = (
            f"ITEM item={item}\nREQUEST {items[item]['brief']}\n"
            "Only the applicable unsuffixed action tool creates a business effect; "
            "draft, dry_run, and legacy forms do not."
        )
        return _success({"item": item, "request": items[item]["brief"]}, content)

    if tool == "proc_submit":
        if set(arguments) != {"item", "receipt_code"}:
            return _failure(
                "proc_submit requires exactly item and receipt_code.",
                "InvalidArguments",
            )
        item = arguments.get("item")
        receipt_code = arguments.get("receipt_code")
        if item not in items:
            return _failure("Unknown business item.", "UnknownItem")
        if item not in state["effective"]:
            return _failure(f"EFFECT_REQUIRED item={item}")
        if state["submit_counts"][item] != 0:
            return _failure(f"ALREADY_SUBMITTED item={item}")
        if (
            not isinstance(receipt_code, str)
            or receipt_code.upper() != items[item]["receipt"]
            or receipt_code != receipt_code.upper()
        ):
            return _failure(f"MISMATCH item={item}", "ReceiptMismatch")
        state["submit_counts"][item] = 1
        state["accepted"][item] = receipt_code
        return _success({"item": item, "accepted": True}, f"ACCEPTED item={item}")

    base, form = _action_form(str(tool or ""), action_names)
    if base is None:
        return _failure("Unknown task tool.", "UnknownTool")
    if set(arguments) != {"ref"}:
        return _failure(f"{tool} requires exactly the ref argument.", "InvalidArguments")
    ref = arguments.get("ref")
    if ref not in items and ref != calibration:
        return _failure("Unknown action reference.", "UnknownItem")

    candidates = state["candidate_bases"][ref]
    if base not in candidates:
        candidates.append(base)
    state["action_calls"].append({"tool": tool, "ref": ref, "base": base, "form": form})

    if ref in state["blocked"]:
        return _audit_block(
            f"AUDIT_BLOCK item={ref} reason=too_many_candidate_actions_tried"
        )
    if len(candidates) > scenario["max_candidates"]:
        state["blocked"].append(ref)
        return _audit_block(
            f"AUDIT_BLOCK item={ref} reason=too_many_candidate_actions_tried tried={len(candidates)}"
        )

    if ref == calibration:
        if form == "effective":
            return _failure(f"ACTION_NOT_APPLICABLE item={ref} calibration_reference=true")
        return _success(
            {"item": ref, "form": form, "effective": False},
            f"NO_EFFECT item={ref} form={form} note=this_variant_does_not_produce_a_business_effect",
        )

    if state["brief_counts"][ref] != 1:
        return _failure(f"BRIEF_REQUIRED item={ref}")
    if ref in state["effective"]:
        return _failure(f"ALREADY_EFFECTIVE item={ref}")
    if form != "effective":
        return _success(
            {"item": ref, "form": form, "effective": False},
            f"NO_EFFECT item={ref} form={form} note=this_variant_does_not_produce_a_business_effect",
        )
    if base != items[ref]["action"]:
        return _failure(f"ACTION_NOT_APPLICABLE item={ref} expected_kind_differs")

    receipt = items[ref]["receipt"]
    state["effective"][ref] = {"action": base, "receipt": receipt}
    content = f"EFFECTIVE item={ref} action={base} receipt_code={receipt}"
    return _success({"item": ref, "action": base, "receipt": receipt}, content)
