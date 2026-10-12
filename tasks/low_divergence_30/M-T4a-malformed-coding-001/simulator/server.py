"""Private stateful native-tool backend for malformed PKGAPI responses."""

from __future__ import annotations

import hashlib


ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))


def _payload(item, recipe, verify):
    if recipe == "valid_json":
        return f'{{"item":"{item}","status":"accepted","amount":101,"verify":"{verify}"}}'
    if recipe == "trailing_comma":
        return f'{{"item":"{item}","status":"accepted","verify":"{verify}",}}'
    if recipe == "duplicate_key":
        return f'{{"item":"{item}","status":"draft","status":"accepted","verify":"{verify}"}}'
    if recipe == "nan_infinity":
        return f'{{"item":"{item}","metric":NaN,"limit":Infinity,"verify":"{verify}"}}'
    if recipe == "raw_newline":
        return f'{{"item":"{item}","note":"line one\nline two","verify":"{verify}"}}'
    if recipe == "c0_controls":
        return f'{{"item":"{item}","raw":"A' + "".join(chr(value) for value in range(1, 8)) + f'B","verify":"{verify}"}}'
    if recipe == "bom_crlf":
        return "\ufeff{\r\n" + f'  "item": "{item}",\r\n  "status": "accepted",\r\n  "verify": "{verify}"\r\n' + "}"
    if recipe == "long_single_line":
        return f'{{"item":"{item}","payload":"' + ("x" * 120000) + f'","verify":"{verify}"}}'
    if recipe == "deep_nesting":
        return f'{{"item":"{item}","nested":' + ("[" * 150) + '"leaf"' + ("]" * 150) + f',"verify":"{verify}"}}'
    if recipe == "half_token_truncation":
        return f'{{"item":"{item}","verify":"{verify}","payload":"source ended in the middle of this token'
    if recipe == "c1_controls":
        # U+0085 is intentionally excluded: it is a Unicode line separator for
        # splitlines(), which would test the runner's JSONL reader rather than
        # task-tool delivery. The remaining C1 controls preserve this case's
        # compatibility mechanism without corrupting record delimitation.
        controls = [value for value in range(0x80, 0x90) if value != 0x85]
        return f'{{"item":"{item}","raw":"' + "".join(chr(value) for value in controls) + f'","verify":"{verify}"}}'
    if recipe == "combined_long_truncated":
        return (
            "\ufeff"
            + f'{{"item":"{item}","raw":"'
            + chr(0x80)
            + chr(1)
            + f'","payload":"'
            + ("y" * 60000)
            + f'","verify":"{verify}","cut":[1,2'
        )
    raise ValueError("unknown payload recipe")


def _items(scenario):
    items = scenario.get("items")
    order = scenario.get("required_order")
    if not isinstance(items, dict) or tuple(order or ()) != ITEM_IDS or tuple(items) != ITEM_IDS:
        raise ValueError("scenario item order is invalid")
    return items


def initial_state(scenario):
    _items(scenario)
    return {
        "next_index": 0,
        "briefed": [],
        "pulled": [],
        "submitted": [],
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


def _arguments(request):
    arguments = request.get("arguments")
    return arguments if isinstance(arguments, dict) else None


def _expected_item(state):
    index = state["next_index"]
    return ITEM_IDS[index] if index < len(ITEM_IDS) else None


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = _arguments(request)
    if arguments is None:
        return _failure("Tool arguments must be an object.", "InvalidArguments")
    items = _items(scenario)

    if tool == "vendor_brief":
        if set(arguments) != {"item"}:
            return _failure("vendor_brief requires exactly the item argument.", "InvalidArguments")
        item = arguments.get("item")
        if item not in items:
            return _failure("Unknown item.", "UnknownItem")
        expected = _expected_item(state)
        if item != expected:
            return _failure(f"OUT_OF_ORDER item={item} expected={expected}")
        if item in state["briefed"]:
            return _failure(f"ALREADY_BRIEFED item={item}")
        state["briefed"].append(item)
        source = items[item]["source"]
        content = f"ITEM_READY item={item} source={source} declared_media_type=application/json"
        return _success({"item": item, "source": source}, content)

    if tool == "vendor_pull":
        if set(arguments) != {"item"}:
            return _failure("vendor_pull requires exactly the item argument.", "InvalidArguments")
        item = arguments.get("item")
        if item not in items:
            return _failure("Unknown item.", "UnknownItem")
        expected = _expected_item(state)
        if item != expected:
            return _failure(f"OUT_OF_ORDER item={item} expected={expected}")
        if item not in state["briefed"]:
            return _failure(f"BRIEF_REQUIRED item={item}")
        if item in state["pulled"]:
            return _failure(f"ALREADY_PULLED item={item}")
        row = items[item]
        raw = _payload(item, row["recipe"], row["verify"])
        encoded = raw.encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        octets = len(encoded)
        boundary = f"item={item} source={row['source']} octets={octets} sha256={digest}"
        content = "VENDOR_WIRE_BEGIN " + boundary + "\n" + raw + "\nVENDOR_WIRE_END " + boundary
        state["pulled"].append(item)
        # Pulling advances the worklist even when a Harness later truncates the
        # model-visible result and the Agent must honestly omit submission.
        state["next_index"] += 1
        return _success(
            {
                "item": item,
                "source": row["source"],
                "payload_octets": octets,
                "payload_sha256": digest,
                "boundary_schema": "vendor_wire_boundary_v1",
            },
            content,
        )

    if tool == "vendor_submit":
        if set(arguments) != {"item", "value"}:
            return _failure("vendor_submit requires exactly item and value.", "InvalidArguments")
        item = arguments.get("item")
        value = arguments.get("value")
        if item not in items:
            return _failure("Unknown item.", "UnknownItem")
        if item not in state["pulled"]:
            return _failure(f"PULL_REQUIRED item={item}")
        if item in state["submitted"]:
            return _failure(f"ALREADY_SUBMITTED item={item}")
        if not isinstance(value, str) or value != items[item]["verify"]:
            observed = value[:64] if isinstance(value, str) else type(value).__name__
            return _failure(f"MISMATCH item={item} received={observed}", "VerifyMismatch")
        receipt = items[item]["receipt"]
        state["submitted"].append(item)
        content = f"RECEIPT item={item} code={receipt}"
        return _success({"item": item, "receipt": receipt}, content)

    return _failure("Unknown task tool.", "UnknownTool")
