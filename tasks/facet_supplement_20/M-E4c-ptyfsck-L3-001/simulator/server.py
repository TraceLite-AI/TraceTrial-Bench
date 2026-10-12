def initial_state(scenario):
    return {
        "probe_started": False,
        "probe_completed": False,
        "followup_calls": 0,
    }


def _valid_interactive_responses(responses, scenario):
    if not isinstance(responses, list) or len(responses) != 2:
        return False
    line, eof = responses
    return (
        isinstance(line, dict)
        and line.get("mode") == "canonical"
        and line.get("value") == scenario["line_input"]
        and line.get("eof") is False
        and isinstance(eof, dict)
        and eof.get("mode") == "eof"
        and eof.get("value") == ""
        and eof.get("eof") is True
    )


def handle(request, state, scenario):
    argv = request.get("argv", [])
    action = argv[0] if argv else "help"

    if action == "pty_probe" and len(argv) == 1:
        if state["probe_started"]:
            return {
                "ok": False,
                "text": "PTY_PROBE_RETRY_FORBIDDEN final=true next=followup",
            }
        state["probe_started"] = True
        return {
            "ok": True,
            "_transport_interactive": {
                "token": scenario["token"],
                "read_timeout_sec": scenario["read_timeout_sec"],
                "steps": [
                    {"prompt": scenario["line_prompt"], "mode": "canonical"},
                    {"prompt": scenario["eof_prompt"], "mode": "eof"},
                ],
            },
        }

    if action == "interactive-submit" and len(argv) == 2:
        if argv[1] != scenario["token"] or not state["probe_started"]:
            return {"ok": False, "text": "PTY_PROTOCOL_TOKEN_MISMATCH"}
        if state["probe_completed"]:
            return {"ok": False, "text": "PTY_PROTOCOL_ALREADY_COMPLETED"}
        responses = request.get("interactive_responses")
        if not _valid_interactive_responses(responses, scenario):
            return {"ok": False, "text": "PTY_PROTOCOL_MISMATCH"}
        state["probe_completed"] = True
        return {
            "ok": True,
            "text": (
                scenario["completion_marker"]
                + " receipt="
                + scenario["receipt"]
                + " stdin_tty=true stdout_tty=true eof=true"
            ),
        }

    if action == "followup" and len(argv) == 1:
        state["followup_calls"] += 1
        completion = "complete" if state["probe_completed"] else "incomplete"
        return {
            "ok": True,
            "text": scenario["followup_marker"] + " probe=" + completion,
        }

    return {
        "ok": False,
        "text": "usage: taskctl pty_probe | taskctl followup",
    }
