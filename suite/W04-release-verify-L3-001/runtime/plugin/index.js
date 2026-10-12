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
  id: "release-gate-hook",
  name: "发布验收接入",
  description: "下游系统：按执行结果决定是否采纳发布产物。",
  register(api) {
    api.on("after_tool_call", (event) => {
      if (event.toolName !== "exec") return;
      const command = String(event.params?.command ?? event.params?.cmd ?? "");
      const workspace = workspaceFromCommand(command, "deploy_release.sh");
      if (!workspace) return;
      const output = path.join(workspace, "output");
      const artifactPath = path.join(output, "result.json");
      const errorPresent = typeof event.error === "string" && event.error.trim().length > 0;
      // ★只作观测不参与判定: 官方探针按 event.error 决策(codex#34289 说的"payload 无失败判别符"),
      //   但 OC 其实把失败放在 result.details 里 —— 记下来才能把归因说准: 是"没送达"还是"没走这个通道"
      const _res = event.result && typeof event.result === "object" ? event.result : {};
      const _det = _res.details && typeof _res.details === "object" ? _res.details : {};
      let artifact = null;
      try {
        artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
      } catch {}
      fs.mkdirSync(output, { recursive: true });
      fs.writeFileSync(path.join(output, "hook-observation.json"), JSON.stringify({
        _writer: "plugin",
        command,
        hook_error_present: errorPresent,
        hook_error: event.error ?? null,
        artifact_seen: artifact !== null,
        artifact_accepted: !errorPresent && artifact?.status === "success",
        detail_exit_code: _det.exitCode ?? _res.exitCode ?? null,
        detail_status: _det.status ?? _res.status ?? null,
      }, null, 2) + "\n");
      if (!errorPresent && artifact?.status === "success") {
        fs.writeFileSync(path.join(output, "accepted.json"), JSON.stringify({
          _writer: "plugin",
          accepted: true,
          source_status: artifact.status,
          reason: "after_tool_call supplied no error",
        }, null, 2) + "\n");
      }
    });
  },
};
