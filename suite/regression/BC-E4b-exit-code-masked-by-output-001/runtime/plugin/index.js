import fs from "node:fs";
import path from "node:path";

function workspaceFromCommand(command, scriptName) {
  const marker = `/tools/${scriptName}`;
  const end = command.indexOf(marker);
  if (end < 0) return null;
  let start = end - 1;
  while (start >= 0 && !/[\s"'`]/.test(command[start])) start -= 1;
  return command.slice(start + 1, end);
}

export default {
  id: "harnessbench-e4b",
  name: "HarnessBench E4b",
  description: "Records nonzero-exit propagation to after_tool_call.",
  register(api) {
    api.on("after_tool_call", (event) => {
      if (event.toolName !== "exec") return;
      const command = String(event.params?.command ?? event.params?.cmd ?? "");
      const workspace = workspaceFromCommand(command, "ok_then_fail.sh");
      if (!workspace) return;
      const result = event.result && typeof event.result === "object" ? event.result : {};
      const details = result.details && typeof result.details === "object" ? result.details : {};
      const output = path.join(workspace, "output");
      fs.mkdirSync(output, { recursive: true });
      fs.writeFileSync(path.join(output, "hook-observation.json"), JSON.stringify({
        hook_error_present: typeof event.error === "string" && event.error.trim().length > 0,
        hook_error: event.error ?? null,
        result_status: details.status ?? result.status ?? null,
        result_timed_out: details.timedOut ?? result.timedOut ?? null,
        result_exit_code: details.exitCode ?? result.exitCode ?? null,
      }, null, 2) + "\n");
    });
  },
};
