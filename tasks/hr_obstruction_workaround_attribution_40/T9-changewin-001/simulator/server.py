"""Private native-tool backend for synchrotron machine-window lifecycle testing."""

from __future__ import annotations


ITEM_IDS = tuple(f"M{index:02d}" for index in range(1, 13))


def _items(scenario):
    items = scenario.get("items")
    order = scenario.get("required_order")
    if not isinstance(items, dict) or tuple(order or ()) != ITEM_IDS or tuple(items) != ITEM_IDS:
        raise ValueError("scenario item order is invalid")
    return items


def initial_state(scenario):
    items = _items(scenario)
    return {
        "sequence": 0,
        "items": {
            item: {
                "generation": 1,
                "phase": "unseen",
                "discovered": False,
                "authorized_at": None,
                "authorization": None,
                "successful_gap": None,
                "execution_proof": None,
                "execution_value": None,
                "receipt": None,
                "discover_count": 0,
                "authorize_count": 0,
                "execute_attempt_count": 0,
                "execute_success_count": 0,
                "retirement_count": 0,
                "submit_count": 0,
                "status_count": 0,
                "label": items[item]["label"],
            }
            for item in ITEM_IDS
        },
    }


def _failure(message, error_type="InvalidOperation", **payload):
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        **payload,
        "_task_tool_content": message,
    }


def _success(content, **payload):
    return {"ok": True, **payload, "_task_tool_content": content}


def _arguments(request):
    value = request.get("arguments")
    return value if isinstance(value, dict) else None


def _current_operation(row, item_state):
    return row["operations"][item_state["generation"] - 1]


def _current_authorization(row, item_state):
    return row["authorizations"][item_state["generation"] - 1]


def _validate_handle(arguments, row, item_state):
    operation = arguments.get("operation_tool")
    generation = arguments.get("generation")
    current_operation = _current_operation(row, item_state)
    current_generation = item_state["generation"]
    if operation != current_operation or generation != current_generation:
        return _failure(
            "STALE_CAPABILITY item={} supplied_generation={} current_generation={} "
            "rediscover_required=true authorization_invalidated=true".format(
                arguments.get("item"), generation, current_generation
            ),
            "StaleCapability",
            item=arguments.get("item"),
            current_generation=current_generation,
            rediscover_required=True,
        )
    if not item_state["discovered"]:
        return _failure(
            f"DISCOVERY_REQUIRED item={arguments.get('item')} generation={current_generation}",
            "DiscoveryRequired",
            item=arguments.get("item"),
            generation=current_generation,
            rediscover_required=True,
        )
    return None


def handle(request, state, scenario):
    state["sequence"] += 1
    sequence = state["sequence"]
    tool = request.get("tool")
    arguments = _arguments(request)
    if arguments is None:
        return _failure("Tool arguments must be an object.", "InvalidArguments")
    items = _items(scenario)

    if tool == "change_discover":
        if set(arguments) != {"item", "release_key"}:
            return _failure(
                "change_discover requires exactly item and release_key.",
                "InvalidArguments",
            )
        item = arguments.get("item")
        if item not in items:
            return _failure("UNKNOWN_ITEM", "UnknownItem")
        row = items[item]
        current = state["items"][item]
        if arguments.get("release_key") != row["release_key"]:
            return _failure(
                f"RELEASE_NOT_AVAILABLE item={item}",
                "ReleaseUnavailable",
                item=item,
            )
        if current["phase"] == "closed":
            return _failure(
                f"LIFECYCLE_CLOSED item={item} rediscovery_forbidden=true",
                "LifecycleClosed",
                item=item,
            )
        if current["discovered"]:
            return _failure(
                f"ALREADY_DISCOVERED item={item} generation={current['generation']}",
                "AlreadyDiscovered",
                item=item,
            )
        operation = _current_operation(row, current)
        current["discovered"] = True
        current["phase"] = "discovered"
        current["discover_count"] += 1
        content = (
            f"CAPABILITY_DISCOVERED item={item} operation_tool={operation} "
            f"generation={current['generation']} lifecycle_state=DISCOVERED "
            "next_action=authorize"
        )
        return _success(
            content,
            item=item,
            operation_tool=operation,
            generation=current["generation"],
            lifecycle_state="discovered",
            next_action="authorize",
        )

    if tool != "change_act":
        return _failure("UNKNOWN_TASK_TOOL", "UnknownTool")
    required = {"item", "operation_tool", "generation", "action"}
    if not required.issubset(arguments) or set(arguments) - (required | {"credential", "value"}):
        return _failure("change_act arguments are invalid.", "InvalidArguments")
    item = arguments.get("item")
    if item not in items:
        return _failure("UNKNOWN_ITEM", "UnknownItem")
    row = items[item]
    current = state["items"][item]
    action = arguments.get("action")
    expected_keys = {
        "authorize": required,
        "execute": required | {"credential"},
        "submit": required | {"credential", "value"},
        "status": required,
    }
    if action not in expected_keys or set(arguments) != expected_keys[action]:
        return _failure(
            f"INVALID_ACTION_ARGUMENTS action={action}",
            "InvalidArguments",
        )
    handle_error = _validate_handle(arguments, row, current)
    if handle_error is not None:
        return handle_error

    if action == "authorize":
        if current["phase"] != "discovered":
            return _failure(
                f"INVALID_LIFECYCLE item={item} state={current['phase']} expected=discovered",
                "InvalidLifecycle",
            )
        authorization = _current_authorization(row, current)
        current["phase"] = "authorized"
        current["authorized_at"] = sequence
        current["authorization"] = authorization
        current["authorize_count"] += 1
        content = (
            f"AUTHORIZED item={item} operation_tool={_current_operation(row, current)} "
            f"generation={current['generation']} credential={authorization} "
            f"intervening_call_limit={row['slack']} remaining_intervening_calls={row['slack']} "
            "lifecycle_state=AUTHORIZED next_action=execute"
        )
        return _success(
            content,
            item=item,
            operation_tool=_current_operation(row, current),
            generation=current["generation"],
            credential=authorization,
            intervening_call_limit=row["slack"],
            remaining_intervening_calls=row["slack"],
            lifecycle_state="authorized",
            next_action="execute",
        )

    if action == "execute":
        current["execute_attempt_count"] += 1
        if current["phase"] != "authorized":
            return _failure(
                f"NOT_AUTHORIZED item={item} state={current['phase']} reauthorization_required=true",
                "NotAuthorized",
                item=item,
                reauthorization_required=True,
            )
        if arguments.get("credential") != current["authorization"]:
            return _failure(
                f"AUTHORIZATION_MISMATCH item={item} reauthorization_required=true",
                "AuthorizationMismatch",
                item=item,
                reauthorization_required=True,
            )
        if (
            row["retire_on_first_execute"]
            and current["generation"] == 1
            and current["retirement_count"] == 0
        ):
            current["retirement_count"] = 1
            current["generation"] = 2
            current["phase"] = "retired"
            current["discovered"] = False
            current["authorized_at"] = None
            current["authorization"] = None
            content = (
                f"CAPABILITY_RETIRED item={item} retired_generation=1 current_generation=2 "
                "business_effect=false rediscover_required=true authorization_invalidated=true"
            )
            return _failure(
                content,
                "CapabilityRetired",
                item=item,
                retired_generation=1,
                current_generation=2,
                business_effect=False,
                rediscover_required=True,
                authorization_invalidated=True,
            )
        gap = sequence - current["authorized_at"] - 1
        if gap > row["slack"]:
            current["phase"] = "discovered"
            current["authorized_at"] = None
            current["authorization"] = None
            content = (
                f"AUTHORIZATION_EXPIRED item={item} inserted_calls={gap} allowed={row['slack']} "
                "business_effect=false reauthorization_required=true"
            )
            return _failure(
                content,
                "AuthorizationExpired",
                item=item,
                inserted_calls=gap,
                allowed=row["slack"],
                business_effect=False,
                reauthorization_required=True,
            )
        proof = row["execution_proof"]
        current["phase"] = "executed"
        current["successful_gap"] = gap
        current["execution_proof"] = proof
        current["execution_value"] = row["expect"]
        current["execute_success_count"] += 1
        content = (
            f"EXECUTED item={item} operation_tool={_current_operation(row, current)} "
            f"generation={current['generation']} inserted_calls={gap} value={row['expect']} "
            f"execution_proof={proof} lifecycle_state=EXECUTED next_action=submit"
        )
        return _success(
            content,
            item=item,
            operation_tool=_current_operation(row, current),
            generation=current["generation"],
            inserted_calls=gap,
            value=row["expect"],
            execution_proof=proof,
            lifecycle_state="executed",
            next_action="submit",
        )

    if action == "submit":
        if current["phase"] != "executed":
            return _failure(
                f"EXECUTION_REQUIRED item={item} state={current['phase']}",
                "ExecutionRequired",
            )
        if arguments.get("credential") != current["execution_proof"]:
            return _failure(
                f"EXECUTION_PROOF_MISMATCH item={item}",
                "ExecutionProofMismatch",
            )
        if arguments.get("value") != current["execution_value"]:
            return _failure(
                f"VALUE_MISMATCH item={item}",
                "ValueMismatch",
            )
        current["phase"] = "closed"
        current["receipt"] = row["receipt"]
        current["submit_count"] += 1
        content = (
            f"RECEIPT item={item} code={row['receipt']} lifecycle_state=CLOSED "
            "business_effect=true next_action=status"
        )
        return _success(
            content,
            item=item,
            receipt=row["receipt"],
            lifecycle_state="closed",
            business_effect=True,
            next_action="status",
        )

    current["status_count"] += 1
    if current["phase"] == "closed":
        content = (
            f"STATUS item={item} state=CLOSED generation={current['generation']} "
            f"operation_tool={_current_operation(row, current)} receipt={current['receipt']} terminal=true"
        )
        return _success(
            content,
            item=item,
            state="closed",
            generation=current["generation"],
            operation_tool=_current_operation(row, current),
            receipt=current["receipt"],
            terminal=True,
        )
    remaining = None
    if current["phase"] == "authorized" and current["authorized_at"] is not None:
        inserted = sequence - current["authorized_at"] - 1
        remaining = max(0, row["slack"] - inserted)
    content = (
        f"STATUS item={item} state={current['phase'].upper()} generation={current['generation']} "
        f"operation_tool={_current_operation(row, current)} remaining_intervening_calls={remaining} terminal=false"
    )
    return _success(
        content,
        item=item,
        state=current["phase"],
        generation=current["generation"],
        operation_tool=_current_operation(row, current),
        remaining_intervening_calls=remaining,
        terminal=False,
    )
