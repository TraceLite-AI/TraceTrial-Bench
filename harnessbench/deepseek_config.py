from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping


_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REASONING_EFFORTS = {
    "off": "off",
    "low": "off",
    "medium": "high",
    "high": "high",
    "max": "max",
}


def render_deepseek_cordis_config(
    *,
    reasoning_effort: str = "max",
    continuable_subagents: bool = False,
    task_tool_bridge: Path | None = None,
    task_tool_environment: Mapping[str, str] | None = None,
) -> str:
    """Render the Docker lane's explicit official JSON-RPC composition."""

    normalized_effort = reasoning_effort.strip().lower()
    if normalized_effort not in _REASONING_EFFORTS:
        raise ValueError("DeepSeek reasoning effort is invalid")
    native_effort = _REASONING_EFFORTS[normalized_effort]

    subagent_background_config = (
        "    enableRunInBackground: true\n"
        "    backgroundMode: continuable"
        if continuable_subagents
        else "    enableRunInBackground: false"
    )

    config = f"""# HarnessBench DeepSeek Harness SDK composition.
# stdout belongs exclusively to the SDK JSON-RPC transport.

- id: llm-deepseek
  name: '@deepseek-ai/dsh-llm-deepseek'
  config:
    thinking: enabled
    reasoningEffort: {native_effort}

- id: subprocess
  name: '@deepseek-ai/dsh-subprocess-local'

- id: pty
  name: '@deepseek-ai/dsh-terminal'

- id: terminal-bash
  name: '@deepseek-ai/dsh-terminal-bash'
  config:
    timeoutMs: 300000

- id: sandbox
  name: '@deepseek-ai/dsh-sandbox-local'

- id: sandbox-policy
  name: '@deepseek-ai/dsh-sandbox-policy'
  config:
    mode: !!js process.env.DSH_PERMISSION_MODE ?? 'danger-full-access'
    workspaceRoot: !!js process.env.DSH_CWD ?? process.cwd()

- id: bash
  name: '@deepseek-ai/dsh-bash-sandbox'
  config:
    cwd: !!js process.env.DSH_CWD ?? process.cwd()
    timeoutMs: 60000

- id: approval
  name: '@deepseek-ai/dsh-user-approval'
  config:
    policy: !!js "(process.env.DSH_PERMISSION_MODE ?? 'danger-full-access') === 'danger-full-access' ? 'never' : 'ask'"

- id: agent-spine
  name: '@deepseek-ai/dsh-agent-spine-demo'
  config:
    persona: !!js process.env.DSH_SYSTEM_PROMPT ?? 'You are a coding agent.'
    workspaceContext:
      maxBytes: 65536
    skills:
      enabled: false
    toolBash:
      enableRunInBackground: true

- id: tool-terminal
  name: '@deepseek-ai/dsh-tool-terminal'
  config:
    enableRunInBackground: true
    maxResultBytes: 262144

- id: sessions
  name: '@deepseek-ai/dsh-session-persistence-jsonl'
  config:
    root: !!js process.env.DSH_SESSION_ROOT ?? './.sessions'
    compression: !!js "process.env.DSH_SNAPSHOT === undefined ? 'zstd' : 'none'"

- id: session-checkpoints
  name: '@deepseek-ai/dsh-session-checkpoint-policy'

- id: subagent
  name: '@deepseek-ai/dsh-subagent'

- id: subagent-spawn-in-process
  name: '@deepseek-ai/dsh-subagent-spawn-in-process'
  config:
    providerName: spawn

- id: tool-subagent
  name: '@deepseek-ai/dsh-tool-subagent'
  config:
    provider: spawn
    toolName: subagent
{subagent_background_config}

- id: tool-todo
  name: '@deepseek-ai/dsh-tool-todo'
  config:
    allowParallelInProgress: true

- id: fs
  name: '@deepseek-ai/dsh-fs-sandbox'
  config:
    cwd: !!js process.env.DSH_CWD ?? process.cwd()

- id: fs-observation-policy
  name: '@deepseek-ai/dsh-fs-observation-policy'

- id: tool-fs
  name: '@deepseek-ai/dsh-tool-fs'

- id: token-meter
  name: '@deepseek-ai/dsh-token-meter'

- id: compaction-basic
  name: '@deepseek-ai/dsh-compaction-basic'
  config:
    thresholdRatio: 0.8
    retainRatio: 0.16
    maxTokens: 8192
    compactionRetries: 1

# Keep the serving transport last so it does not widen the async activation
# window for later plugins. The SDK bridge also starts the runtime before the
# first prompt and applies a bounded settlement window for local MCP discovery.
- id: sdk-jsonrpc-server
  name: '@deepseek-ai/dsh-sdk-jsonrpc-server'
  config:
    maxTokensAsSuccess: true
"""
    if task_tool_bridge is None:
        return config

    names = sorted((task_tool_environment or {}).keys())
    if any(not _ENV_NAME.fullmatch(name) for name in names):
        raise ValueError("DeepSeek task-tool environment contains an unsafe name")
    env_lines = "\n".join(
        f"      {name}: !!js process.env.{name}" for name in names
    )
    env_block = f"    env:\n{env_lines}" if env_lines else "    env: {{}}"
    mcp_entry = f"""

- id: mcp-harnessbench
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: harnessbench
    transport: stdio
    command: {json.dumps('/usr/bin/python3')}
    args: [{json.dumps(str(task_tool_bridge))}]
    cwd: {json.dumps(str(task_tool_bridge.parent.parent))}
{env_block}
    toolCallTimeoutMs: 300000
    failOnStartupError: true
    reconnect:
      enabled: false
"""
    marker = "\n- id: sdk-jsonrpc-server\n"
    if config.count(marker) != 1:
        raise RuntimeError("DeepSeek SDK server marker is missing or ambiguous")
    return config.replace(marker, mcp_entry + marker, 1)
