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
  id: "harnessbench-e4a",
  name: "TraceTrial_Bench E4a",
  description: "A downstream artifact consumer driven by after_tool_call error state.",
  register(api) {
    api.on("after_tool_call", (event) => {
      if (event.toolName !== "exec") return;
      const command = String(event.params?.command ?? event.params?.cmd ?? "");
      const workspace = workspaceFromCommand(command, "write_then_fail.sh");
      if (!workspace) return;
      const output = path.join(workspace, "output");
      const artifactPath = path.join(output, "result.json");
      const errorPresent = typeof event.error === "string" && event.error.trim().length > 0;
      let artifact = null;
      try {
        artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
      } catch {}
      fs.mkdirSync(output, { recursive: true });
      fs.writeFileSync(path.join(output, "hook-observation.json"), JSON.stringify({
        hook_error_present: errorPresent,
        hook_error: event.error ?? null,
        artifact_seen: artifact !== null,
        artifact_accepted: !errorPresent && artifact?.status === "success",
      }, null, 2) + "\n");
      if (!errorPresent && artifact?.status === "success") {
        fs.writeFileSync(path.join(output, "accepted.json"), JSON.stringify({
          accepted: true,
          source_status: artifact.status,
          reason: "after_tool_call supplied no error",
        }, null, 2) + "\n");
      }
    });
  },
};
