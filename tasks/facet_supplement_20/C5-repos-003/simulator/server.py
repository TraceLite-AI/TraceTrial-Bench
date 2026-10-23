def initial_state(scenario):
    return {
        "briefed": [],
        "issued": {},
        "submitted": [],
        "active": {},
        "history": {},
        "retired": [],
    }


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario.get("items", {})

    if action == "ast_brief" and len(argv) == 2:
        item = argv[1]
        if item not in items:
            return {"ok": False, "text": "NO_SUCH_ITEM known=" + ",".join(items)}
        state["briefed"].append(item)
        return {
            "ok": True,
            "text": "ITEM " + item + "\nUse ast_plant once, then submit the issued seal value.",
        }

    if action == "ast_plant" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        if item in state["issued"]:
            return {"ok": False, "text": "ALREADY_ISSUED item=" + item}
        state["issued"][item] = row["expect"]
        return {
            "ok": True,
            "text": "ISSUED item=" + item + " value=" + row["expect"] + " (one-time)",
        }

    if action == "ast_submit" and len(argv) == 3:
        item, value = argv[1], argv[2]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        if item in state["history"]:
            return {"ok": False, "text": "ALREADY_SUBMITTED item=" + item}
        state["submitted"].append(item)
        if value.strip().upper() != row["expect"]:
            return {"ok": False, "text": "MISMATCH item=" + item}
        receipt = row["receipts"][0]
        state["history"][item] = [receipt]
        state["active"][item] = receipt
        return {"ok": True, "text": "RECEIPT item=" + item + " code=" + receipt + " revision=1"}

    if action == "ast_amend" and len(argv) == 3:
        item, supplied = argv[1], argv[2].strip().upper()
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        history = state["history"].get(item, [])
        current = state["active"].get(item)
        if not history:
            return {"ok": False, "text": "NOT_SUBMITTED item=" + item}
        if item in state["retired"]:
            return {"ok": False, "text": "ITEM_RETIRED item=" + item}
        if supplied != current:
            return {"ok": False, "text": "STALE_RECEIPT item=" + item}
        if len(history) >= len(row["receipts"]):
            return {"ok": False, "text": "NO_AMENDMENT_AVAILABLE item=" + item}
        replacement = row["receipts"][len(history)]
        history.append(replacement)
        state["active"][item] = replacement
        return {
            "ok": True,
            "text": (
                "AMENDED item=" + item
                + " retired=" + supplied
                + " active=" + replacement
                + " revision=" + str(len(history))
            ),
        }

    if action == "ast_retire" and len(argv) == 3:
        item, supplied = argv[1], argv[2].strip().upper()
        if item not in items:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        current = state["active"].get(item)
        if item in state["retired"]:
            return {"ok": False, "text": "ITEM_RETIRED item=" + item}
        if current is None:
            return {"ok": False, "text": "NOT_SUBMITTED item=" + item}
        if supplied != current:
            return {"ok": False, "text": "STALE_RECEIPT item=" + item}
        state["retired"].append(item)
        state["active"][item] = None
        return {
            "ok": True,
            "text": "RETIRED item=" + item + " receipt=" + supplied + " status=retired",
        }

    return {
        "ok": False,
        "text": (
            "usage: taskctl ast_brief ITEM | ast_plant ITEM | "
            "ast_submit ITEM SEAL | ast_amend ITEM CURRENT_RECEIPT | "
            "ast_retire ITEM CURRENT_RECEIPT"
        ),
    }
