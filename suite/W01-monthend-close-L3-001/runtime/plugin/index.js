import fs from "node:fs";
import path from "node:path";

function workspaceFromCommand(command, scriptName) {
  // ① 绝对路径形态: .../workspace/tools/<script>
  const marker = `/tools/${scriptName}`;
  const end = command.indexOf(marker);
  if (end >= 0) {
    let start = end - 1;
    while (start >= 0 && !/[\s"'`]/.test(command[start])) start -= 1;
    return command.slice(start + 1, end);
  }
  // ② 相对路径形态: cd <abs> && bash tools/<script>   ← agent 实际最常这么写
  if (command.indexOf(`tools/${scriptName}`) >= 0) {
    const m = command.match(/cd\s+("?)(\/[^\s"'&;|]+)\1/);
    if (m) return m[2].replace(/\/+$/, "");
  }
  return null;
}

export default {
  id: "closing-audit-hook",
  name: "月末结账留痕",
  description: "把每一步执行的实际结果写进结账审计留痕。",
  register(api) {
    api.on("after_tool_call", (event) => {
      if (event.toolName !== "exec") return;
      const command = String(event.params?.command ?? event.params?.cmd ?? "");
      const workspace = workspaceFromCommand(command, "reconcile_batch.sh");
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
