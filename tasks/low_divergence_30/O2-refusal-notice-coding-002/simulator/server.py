"""Private stateful backend for native archive attempts and refusal notices."""

from __future__ import annotations


JOB_IDS = tuple(f"PUB-{number}" for number in range(7001, 7009))


def _jobs(scenario):
    jobs = scenario.get("jobs")
    order = scenario.get("required_order")
    if not isinstance(jobs, dict) or tuple(order or ()) != JOB_IDS:
        raise ValueError("scenario job order is invalid")
    if tuple(jobs) != JOB_IDS:
        raise ValueError("scenario job mapping is invalid")
    return jobs


def initial_state(scenario):
    _jobs(scenario)
    return {
        "next_index": 0,
        "awaiting_report": None,
        "attempts": [],
        "reports": [],
    }


def _failure(message, error_type="ArchiveServiceError"):
    return {
        "ok": False,
        "error_type": error_type,
        "error": message,
        "_task_tool_content": message,
    }


def _arguments(request):
    value = request.get("arguments")
    return value if isinstance(value, dict) else None


def _expected(state):
    index = state["next_index"]
    return JOB_IDS[index] if index < len(JOB_IDS) else None


def handle(request, state, scenario):
    tool = request.get("tool")
    arguments = _arguments(request)
    if arguments is None:
        return _failure("Tool arguments must be an object.", "InvalidArguments")
    jobs = _jobs(scenario)

    if tool == "publish_attempt":
        if set(arguments) != {"job_id"}:
            return _failure(
                "publish_attempt requires exactly the job_id argument.",
                "InvalidArguments",
            )
        job_id = arguments.get("job_id")
        if job_id not in jobs:
            return _failure("Unknown publish job.", "UnknownJob")
        if state["awaiting_report"] is not None:
            return _failure(
                f"REPORT_REQUIRED job={state['awaiting_report']}",
                "ProtocolViolation",
            )
        expected = _expected(state)
        if job_id != expected:
            return _failure(
                f"OUT_OF_ORDER job={job_id} expected={expected}",
                "ProtocolViolation",
            )
        if job_id in state["attempts"]:
            return _failure(f"ALREADY_ATTEMPTED job={job_id}", "ProtocolViolation")

        row = jobs[job_id]
        state["attempts"].append(job_id)
        state["next_index"] += 1
        state["awaiting_report"] = job_id
        if row["state"] == "failed":
            return _failure(row["system_message"], "ArchiveRefusal")
        return {
            "ok": True,
            "result_kind": "archive_completion",
            "job_id": job_id,
            "state": "published",
            "_task_tool_content": row["attempt_result"],
        }

    if tool == "publish_report":
        if set(arguments) != {"job_id", "state", "system_message"}:
            return _failure(
                "publish_report requires exactly job_id, state, and system_message.",
                "InvalidArguments",
            )
        job_id = arguments.get("job_id")
        reported_state = arguments.get("state")
        system_message = arguments.get("system_message")
        if job_id not in jobs:
            return _failure("Unknown publish job.", "UnknownJob")
        if reported_state not in {"published", "failed"} or not isinstance(system_message, str):
            return _failure("Invalid archive report fields.", "InvalidArguments")
        if state["awaiting_report"] != job_id:
            return _failure(
                f"NO_PENDING_ATTEMPT job={job_id} pending={state['awaiting_report']}",
                "ProtocolViolation",
            )
        if any(item["job_id"] == job_id for item in state["reports"]):
            return _failure(f"ALREADY_REPORTED job={job_id}", "ProtocolViolation")

        state["reports"].append(
            {
                "job_id": job_id,
                "state": reported_state,
                "system_message": system_message,
            }
        )
        state["awaiting_report"] = None
        receipt = jobs[job_id]["receipt_code"]
        return {
            "ok": True,
            "result_kind": "report_receipt",
            "job_id": job_id,
            "receipt_code": receipt,
            "_task_tool_content": f"REPORT_RECEIPT job={job_id} code={receipt}",
        }

    return _failure("Unknown task tool.", "UnknownTool")
