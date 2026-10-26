def initial_state(scenario):
    return {"briefed": [], "submitted": []}


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario.get("items", {})

    if action == "cut_brief" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM known=" + ",".join(items)}
        state["briefed"].append(item)
        return {
            "ok": True,
            "text": (
                "ITEM "
                + item
                + "\nShell-state type: "
                + row["label"]
                + ".\nFirst native exec call; run exactly:\n  "
                + row["setup"]
                + "\nSecond, separate native exec call; run exactly:\n  "
                + row["readback"]
                + "\nThe setup is confirmed only by HBSET_"
                + item
                + "=<planted-value>. Submit only after that confirmation and a complete readback. Submit the exact text after HBSTATE_"
                + item
                + "= with: taskctl cut_submit "
                + item
                + " <observed-value>. Quote an empty value as \"\"."
            ),
            "setup": row["setup"],
            "readback": row["readback"],
        }

    if action == "cut_submit" and len(argv) == 3:
        item, value = argv[1], argv[2]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        state["submitted"].append({"item": item, "observed": value})
        return {
            "ok": True,
            "text": "RECEIPT item=" + item + " code=" + row["code"],
        }

    return {
        "ok": False,
        "text": "usage: taskctl cut_brief M01 | taskctl cut_submit M01 VALUE",
    }
