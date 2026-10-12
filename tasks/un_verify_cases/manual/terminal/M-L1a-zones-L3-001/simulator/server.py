from __future__ import annotations

import hashlib


def initial_state(scenario):
    return {
        "started": False,
        "nonce_revision": 0,
        "current_nonce": None,
        "active": None,
        "completed": [],
        "finalized": False,
    }


def _nonce(state, scenario):
    material = f"{scenario['nonce_seed']}|{state['nonce_revision']}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12].upper()


def _rotate_nonce(state, scenario):
    state["nonce_revision"] += 1
    state["current_nonce"] = _nonce(state, scenario)


def _authorized(argv, state):
    return bool(argv) and state["started"] and argv[-1] == state["current_nonce"]


def _response(lines, state, *, ok, **fields):
    if state["current_nonce"]:
        lines.append("NEXT_NONCE " + state["current_nonce"])
    return {
        "ok": ok,
        "text": "\n".join(lines),
        "next_nonce": state["current_nonce"],
        **fields,
    }


def _reject(text, state):
    return _response([text], state, ok=False)


def _success(lines, state, scenario, **fields):
    _rotate_nonce(state, scenario)
    return _response(lines, state, ok=True, **fields)


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario["items"]

    if action == "session_start" and len(argv) == 1:
        if state["started"]:
            return _reject("SESSION_ALREADY_STARTED", state)
        state["started"] = True
        return _success(
            ["SESSION_STARTED controller=zoneal_queue"],
            state,
            scenario,
        )

    if action == "zone_start" and len(argv) == 3:
        item = argv[1]
        if not _authorized(argv, state):
            return _reject("NONCE_MISMATCH action=zone_start", state)
        if state["finalized"]:
            return _reject("CONTROLLER_FINALIZED", state)
        if state["active"] is not None:
            return _reject(
                "ACTIVE_REGION_EXISTS item=" + state["active"]["item"], state
            )
        if item not in items:
            return _reject("NO_SUCH_ITEM item=" + item, state)
        if item in state["completed"]:
            return _reject("ALREADY_COMPLETED item=" + item, state)
        state["active"] = {"item": item, "ready": False}
        challenge = items[item]["challenge"]
        return _success(
            [
                f"REGION_STARTED item={item}",
                f"WORK item={item} challenge={challenge}",
            ],
            state,
            scenario,
            item=item,
            challenge=challenge,
        )

    if action == "zone_work" and len(argv) == 4:
        item, answer = argv[1], argv[2]
        if not _authorized(argv, state):
            return _reject("NONCE_MISMATCH action=zone_work", state)
        active = state["active"]
        if not isinstance(active, dict) or active.get("item") != item:
            return _reject("NO_MATCHING_ACTIVE_REGION item=" + item, state)
        if active.get("ready"):
            return _reject("REGION_ALREADY_READY item=" + item, state)
        if answer.strip() != str(items[item]["answer"]):
            return _reject("WORK_MISMATCH item=" + item, state)
        active["ready"] = True
        result = str(items[item]["result"])
        return _success(
            [f"REGION_READY item={item} result={result}"],
            state,
            scenario,
            item=item,
            result=result,
        )

    if action == "zone_submit" and len(argv) == 4:
        item, result = argv[1], argv[2]
        if not _authorized(argv, state):
            return _reject("NONCE_MISMATCH action=zone_submit", state)
        active = state["active"]
        if not isinstance(active, dict) or active.get("item") != item:
            return _reject("NO_MATCHING_ACTIVE_REGION item=" + item, state)
        if not active.get("ready"):
            return _reject("REGION_NOT_READY item=" + item, state)
        if result.strip() != str(items[item]["result"]):
            return _reject("RESULT_MISMATCH item=" + item, state)
        state["completed"].append(item)
        state["active"] = None
        receipt = items[item]["receipt"]
        return _success(
            [f"RECEIPT item={item} code={receipt}"],
            state,
            scenario,
            item=item,
            receipt=receipt,
        )

    if action == "finalize" and len(argv) == 2:
        if not _authorized(argv, state):
            return _reject("NONCE_MISMATCH action=finalize", state)
        if state["finalized"]:
            return _reject("ALREADY_FINALIZED", state)
        if state["active"] is not None:
            return _reject(
                "ACTIVE_REGION_NOT_LANDED item=" + state["active"]["item"], state
            )
        state["finalized"] = True
        unfinished = [item for item in items if item not in state["completed"]]
        return _success(
            [
                "FINALIZED completed=" + (",".join(state["completed"]) or "none"),
                "UNFINISHED " + (",".join(unfinished) or "none"),
            ],
            state,
            scenario,
            completed=list(state["completed"]),
            unfinished=unfinished,
            finalized=True,
        )

    return _reject(
        "usage: taskctl session_start | taskctl zone_start ITEM NONCE | "
        "taskctl zone_work ITEM ANSWER NONCE | "
        "taskctl zone_submit ITEM RESULT NONCE | taskctl finalize NONCE",
        state,
    )
