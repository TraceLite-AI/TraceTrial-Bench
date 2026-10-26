import shlex


def initial_state(scenario):
    return {"requested": []}


def handle(request, state, scenario):
    argv = request.get("argv", [])
    if len(argv) != 2 or argv[0] != "step_command":
        return {"ok": False, "text": "usage: taskctl step_command MIG-501"}
    step = argv[1]
    command = scenario.get("commands", {}).get(step)
    if command is None:
        return {"ok": False, "text": "NO_SUCH_STEP"}
    state["requested"].append(step)
    shell_command = shlex.join(["bash", "-c", command])
    return {
        "ok": True,
        "text": "COMMAND for " + step + ":\n" + shell_command,
        "step": step,
        "command": ["bash", "-c", command],
        "shell_command": shell_command,
    }
