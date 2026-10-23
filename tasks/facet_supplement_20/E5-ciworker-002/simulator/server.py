import json
import time


def initial_state(scenario):
    return {"runs": {}, "submissions": {}}


def _payload(item, row):
    return {
        "item": item,
        "complete": row["complete"],
        "token": row.get("token"),
        "failure_code": row.get("failure_code"),
        "observation": row["observation"],
    }


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"
    items = scenario.get("items", {})

    if action == "job_run" and len(argv) == 2:
        item = argv[1]
        row = items.get(item)
        if row is None:
            return {"ok": False, "error": "NO_SUCH_JOB"}
        if item in state["runs"]:
            return {"ok": False, "error": "JOB_ALREADY_RUN"}
        time.sleep(float(row["duration_sec"]))
        state["runs"][item] = {
            "outcome": row["outcome"],
            "exit_code": row["exit_code"],
            "signal": row.get("signal"),
            "observation": row["observation"],
        }
        response = {
            "ok": row["outcome"] == "succeeded",
            "_transport_stdout": json.dumps(_payload(item, row), sort_keys=True) + "\n",
        }
        if row["outcome"] == "signaled":
            response["_transport_signal"] = int(row["signal"])
        elif row["outcome"] != "succeeded":
            response["_transport_exit_code"] = int(row["exit_code"])
        return response

    if action == "job_submit" and len(argv) == 3:
        item, observation = argv[1], argv[2]
        row = items.get(item)
        if row is None:
            return {"ok": False, "error": "NO_SUCH_JOB"}
        run = state["runs"].get(item)
        if run is None:
            return {"ok": False, "error": "JOB_NOT_FINISHED"}
        if item in state["submissions"]:
            return {"ok": False, "error": "ALREADY_SUBMITTED"}
        if observation != row["observation"]:
            return {"ok": False, "error": "OBSERVATION_MISMATCH"}
        state["submissions"][item] = observation
        return {
            "ok": True,
            "_transport_stdout": "RECEIPT item=" + item + " code=" + row["receipt"] + "\n",
        }

    return {
        "ok": False,
        "error": "unsupported private action",
    }
