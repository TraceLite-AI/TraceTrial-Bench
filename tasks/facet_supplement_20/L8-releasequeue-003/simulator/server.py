def initial_state(scenario):
    return {
        "items": {
            item: {"brief_count": 0, "issued": False, "submitted": False}
            for item in scenario.get("items", {})
        },
        "successful_side_effects": [],
    }


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario.get("items", {})

    if action == "tok_brief" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM known=" + ",".join(items)}
        state["items"][item]["brief_count"] += 1
        return {
            "ok": True,
            "text": (
                "ITEM " + item + "\n" + row["label"] + ". "
                "Issue the one-time execution token with taskctl tok_plant " + item
                + ", then trigger the action exactly once with taskctl tok_submit "
                + item + " <token>."
            ),
        }

    if action == "tok_plant" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        item_state = state["items"][item]
        if item_state["issued"]:
            return {
                "ok": False,
                "text": "ALREADY_ISSUED item=" + item + " issued once only; use the original token",
            }
        item_state["issued"] = True
        return {
            "ok": True,
            "text": "ISSUED item=" + item + " token=" + row["token"] + " one_time=true",
        }

    if action == "tok_submit" and len(argv) == 3:
        item, supplied = argv[1], argv[2]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        item_state = state["items"][item]
        if not item_state["issued"]:
            return {"ok": False, "text": "NOT_ISSUED item=" + item}
        if item_state["submitted"]:
            return {
                "ok": False,
                "text": "ALREADY_COMPLETED item=" + item + " side_effect_not_replayed=true",
            }
        if supplied.strip().upper() != row["token"]:
            return {"ok": False, "text": "MISMATCH item=" + item}
        item_state["submitted"] = True
        state["successful_side_effects"].append(item)
        return {
            "ok": True,
            "text": "RECEIPT item=" + item + " code=" + row["receipt"],
            "side_effect": {"item": item, "kind": "maintenance_action_triggered"},
        }

    return {
        "ok": False,
        "text": (
            "usage: taskctl tok_brief M01 | taskctl tok_plant M01 | "
            "taskctl tok_submit M01 TOKEN"
        ),
    }
