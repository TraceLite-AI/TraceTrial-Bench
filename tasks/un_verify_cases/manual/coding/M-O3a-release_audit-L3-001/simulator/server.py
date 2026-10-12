ORDER = [f"M{index:02d}" for index in range(1, 13)]


def initial_state(scenario):
    return {
        "next_index": 0,
        "stage": "ready",
        "briefed": [],
        "performed": [],
        "submitted": [],
    }


def response(ok, text, **extra):
    return {"ok": ok, "text": text, **extra}


def current_item(state):
    index = state["next_index"]
    return ORDER[index] if index < len(ORDER) else None


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario.get("items", {})

    if action not in {"audit_brief", "audit_do", "audit_submit"}:
        return response(
            False,
            "usage: taskctl audit_brief M01 | taskctl audit_do M01 | "
            "taskctl audit_submit M01 AUDIT_REF",
        )

    expected_arity = 3 if action == "audit_submit" else 2
    if len(argv) != expected_arity:
        return response(False, "BAD_ARGUMENT_COUNT action=" + action)

    item = argv[1]
    row = items.get(item)
    if row is None:
        return response(False, "NO_SUCH_ITEM item=" + item)

    if item in state["submitted"]:
        return response(
            False,
            "ALREADY_ISSUED item=" + item + " receipt_not_replayed",
            replayed=False,
        )

    expected_item = current_item(state)
    if item != expected_item:
        return response(
            False,
            "ORDER_MISMATCH expected=" + str(expected_item) + " received=" + item,
        )

    if action == "audit_brief":
        if state["stage"] != "ready":
            return response(False, "STAGE_MISMATCH item=" + item + " expected=" + state["stage"])
        state["briefed"].append(item)
        state["stage"] = "briefed"
        return response(
            True,
            "ITEM " + item + "\nRelease action " + item + ": " + row["label"]
            + ". Execute it once with taskctl audit_do " + item
            + ", then submit the returned audit_ref.",
            item=item,
            action_label=row["label"],
        )

    if action == "audit_do":
        if state["stage"] != "briefed":
            return response(False, "STAGE_MISMATCH item=" + item + " expected=briefed")
        state["performed"].append(item)
        state["stage"] = "performed"
        return response(
            True,
            "POSTED item=" + item + " audit_ref=" + row["expect"] + " table=gl_" + item,
            item=item,
            audit_ref=row["expect"],
        )

    if state["stage"] != "performed":
        return response(False, "STAGE_MISMATCH item=" + item + " expected=performed")
    value = argv[2].strip().upper()
    if value != row["expect"]:
        return response(False, "MISMATCH item=" + item + " received=" + value[:120])
    state["submitted"].append(item)
    state["next_index"] += 1
    state["stage"] = "ready"
    return response(
        True,
        "RECEIPT item=" + item + " code=" + row["code"],
        item=item,
        receipt=row["code"],
        replayed=False,
    )
