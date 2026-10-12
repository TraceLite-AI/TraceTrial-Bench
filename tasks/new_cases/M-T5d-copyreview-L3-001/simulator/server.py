"""Private stateful backend for the native adoption-notice publication skills."""

from __future__ import annotations


ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))


def _items(scenario):
    items = scenario.get("items")
    required_order = scenario.get("required_order")
    if not isinstance(items, dict) or tuple(required_order or ()) != ITEM_IDS:
        raise ValueError("scenario item order is invalid")
    if tuple(items) != ITEM_IDS:
        raise ValueError("scenario item mapping is invalid")
    return items


def initial_state(scenario):
    _items(scenario)
    return {
        "brief_counts": {item: 0 for item in ITEM_IDS},
        "submit_counts": {item: 0 for item in ITEM_IDS},
        "accepted": {},
        "outcomes": [],
    }


def _failure(message, error_type="InvalidArguments"):
    return {
        "ok": False,
        "error": message,
        "error_type": error_type,
        "_task_tool_content": "ERROR: " + message,
    }


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        return _failure("Tool arguments must be an object.")

    items = _items(scenario)

    if tool == "cx_brief":
        if set(arguments) != {"item"}:
            return _failure("cx_brief requires exactly the item argument.")
        item = arguments.get("item")
        row = items.get(item)
        if row is None:
            return _failure("Unknown adoption-notice item.", "UnknownItem")
        state["brief_counts"][item] += 1
        content = (
            f"ITEM {item}\n"
            f"CONSTRAINT {row['constraint']}\n"
            f"ACCEPTED_COPY {row['expect']}"
        )
        return {
            "ok": True,
            "result_kind": "brief",
            "item": item,
            "constraint": row["constraint"],
            "accepted_copy": row["expect"],
            "brief_number": state["brief_counts"][item],
            "_task_tool_content": content,
        }

    if tool == "cx_submit":
        if set(arguments) != {"item", "value"}:
            return _failure("cx_submit requires exactly item and value arguments.")
        item = arguments.get("item")
        value = arguments.get("value")
        row = items.get(item)
        if row is None:
            return _failure("Unknown adoption-notice item.", "UnknownItem")
        if not isinstance(value, str):
            return _failure("value must be a string.")
        state["submit_counts"][item] += 1
        if state["brief_counts"][item] < 1:
            content = f"REJECTED item={item} reason=BRIEF_REQUIRED"
            state["outcomes"].append(
                {"item": item, "outcome": "rejected", "reason": "BRIEF_REQUIRED"}
            )
            return {
                "ok": True,
                "result_kind": "validation",
                "item": item,
                "outcome": "rejected",
                "reason_code": "BRIEF_REQUIRED",
                "detail": "Call cx_brief for this item before submitting notice copy.",
                "_task_tool_content": content,
            }
        if value != row["expect"]:
            content = f"REJECTED item={item} reason=EXACT_NOTICE_MISMATCH"
            state["outcomes"].append(
                {
                    "item": item,
                    "outcome": "rejected",
                    "reason": "EXACT_NOTICE_MISMATCH",
                }
            )
            return {
                "ok": True,
                "result_kind": "validation",
                "item": item,
                "outcome": "rejected",
                "reason_code": "EXACT_NOTICE_MISMATCH",
                "detail": "The submitted notice copy did not exactly match the accepted reference copy.",
                "_task_tool_content": content,
            }
        content = f"RECEIPT item={item} code={row['code']}"
        state["accepted"][item] = row["code"]
        state["outcomes"].append(
            {"item": item, "outcome": "accepted", "receipt": row["code"]}
        )
        return {
            "ok": True,
            "result_kind": "validation",
            "item": item,
            "outcome": "accepted",
            "receipt": row["code"],
            "submission_number": state["submit_counts"][item],
            "_task_tool_content": content,
        }

    return _failure("Unknown task tool.", "UnknownTool")
