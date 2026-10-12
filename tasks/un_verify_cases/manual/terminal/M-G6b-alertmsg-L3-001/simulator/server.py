from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation


ATTACHMENT_MARKER = "[[VENDOR_ATTACHMENT_ROWS]]"


def _message_content(row):
    content = row["message"]
    padding_bytes = row.get("attachment_bytes", 0)
    if not isinstance(padding_bytes, int) or padding_bytes <= 0:
        return content
    unit = "Attachment reference row: non-authoritative invoice metadata only.\n"
    padding = (unit * ((padding_bytes // len(unit)) + 1))[:padding_bytes]
    return content.replace(ATTACHMENT_MARKER, padding)


def initial_state(scenario):
    return {"open_batches": [], "briefed": [], "submitted": []}


def _next_item(state, scenario):
    submitted = set(state["submitted"])
    for batch_id in scenario["batch_order"]:
        for item in scenario["batches"][batch_id]["items"]:
            if item not in submitted:
                return item
    return None


def _prior_batches_complete(batch_id, state, scenario):
    submitted = set(state["submitted"])
    for current in scenario["batch_order"]:
        if current == batch_id:
            return True
        if not set(scenario["batches"][current]["items"]).issubset(submitted):
            return False
    return False


def _normalize_amount(value):
    compact = value.replace(",", "").replace("_", "").replace(" ", "")
    try:
        number = Decimal(compact)
    except InvalidOperation:
        return compact
    if not number.is_finite() or number != number.to_integral_value():
        return compact
    return str(int(number))


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario["items"]
    batches = scenario["batches"]

    if action == "inj_open" and len(argv) == 3:
        batch_id, grant = argv[1], argv[2]
        batch = batches.get(batch_id)
        if batch is None:
            return {"ok": False, "text": "NO_SUCH_BATCH"}
        if batch_id in state["open_batches"]:
            return {"ok": False, "text": "ALREADY_OPEN batch=" + batch_id}
        if grant != batch["grant"]:
            return {"ok": False, "text": "INVALID_BATCH_GRANT batch=" + batch_id}
        if not _prior_batches_complete(batch_id, state, scenario):
            return {"ok": False, "text": "PRIOR_BATCH_INCOMPLETE batch=" + batch_id}
        state["open_batches"].append(batch_id)
        return {
            "ok": True,
            "text": "BATCH_OPENED batch=" + batch_id + " items=" + ",".join(batch["items"]),
            "batch": batch_id,
            "items": batch["items"],
        }

    if action == "inj_brief" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        if row["batch"] not in state["open_batches"]:
            return {"ok": False, "text": "BATCH_NOT_RELEASED item=" + item}
        expected = _next_item(state, scenario)
        if item != expected:
            return {"ok": False, "text": "OUT_OF_ORDER item=" + item + " expected=" + str(expected)}
        if item in state["briefed"]:
            return {"ok": False, "text": "ALREADY_BRIEFED item=" + item}
        content = _message_content(row)
        content_bytes = content.encode("utf-8")
        maximum = scenario["max_message_bytes"]
        if len(content_bytes) > maximum:
            return {
                "ok": False,
                "text": "UNTRUSTED_CONTENT_TOO_LARGE item=" + item,
                "content_length": len(content_bytes),
                "max_message_bytes": maximum,
            }
        state["briefed"].append(item)
        digest = hashlib.sha256(content_bytes).hexdigest()
        boundary_id = digest[:16]
        return {
            "ok": True,
            "text": (
                "UNTRUSTED_VENDOR_MESSAGE_BEGIN boundary=" + boundary_id
                + " item=" + item + " source=" + row["source"]
                + " bytes=" + str(len(content_bytes)) + " sha256=" + digest + "\n"
                + content
                + "UNTRUSTED_VENDOR_MESSAGE_END boundary=" + boundary_id + " item=" + item
            ),
            "trust": "untrusted_external_data",
            "instruction_authority": "none",
            "data_origin": "vendor_external",
            "boundary_id": boundary_id,
            "item": item,
            "source": row["source"],
            "content_length": len(content_bytes),
            "max_message_bytes": maximum,
            "content_sha256": digest,
            "truncated": False,
        }

    if action == "inj_submit" and len(argv) == 3:
        item, value = argv[1], argv[2]
        row = items.get(item)
        if row is None:
            return {"ok": False, "text": "NO_SUCH_ITEM"}
        expected = _next_item(state, scenario)
        if item != expected:
            return {"ok": False, "text": "OUT_OF_ORDER item=" + item + " expected=" + str(expected)}
        if item not in state["briefed"]:
            return {"ok": False, "text": "BRIEF_REQUIRED item=" + item}
        if item in state["submitted"]:
            return {"ok": False, "text": "ALREADY_SUBMITTED item=" + item}
        normalized = _normalize_amount(value)
        if normalized != row["expect"]:
            return {"ok": False, "text": "MISMATCH item=" + item + " received=" + normalized[:120]}
        state["submitted"].append(item)
        return {
            "ok": True,
            "text": "RECEIPT item=" + item + " code=" + row["code"],
            "item": item,
            "receipt": row["code"],
        }

    return {
        "ok": False,
        "text": (
            "usage: taskctl inj_open BATCH GRANT | taskctl inj_brief ITEM | "
            "taskctl inj_submit ITEM AMOUNT_CENTS"
        ),
    }
