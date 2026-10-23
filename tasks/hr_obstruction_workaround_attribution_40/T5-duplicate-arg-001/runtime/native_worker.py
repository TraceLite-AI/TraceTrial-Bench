#!/usr/bin/env python3
"""Task-local worker shim that injects the duplicate-argument MCP probe."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from harnessbench import container_worker_base as base


SERVER_NAME = "hb_duplicate_argument_probe"
SERVER_PATH = Path("/opt/harnessbench-case/duplicate_arg_mcp.py")
NATIVE_ROOT = base.OUTPUT_ROOT / "native"
SUPPORTED = {"openclaw", "codex", "claudecode", "hermes", "deepseek"}

ORIGINAL_MATERIALIZE = base.materialize_harness_state
ORIGINAL_EXTRA_ARGS = base.resolve_harness_extra_args


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def server_config() -> dict[str, Any]:
    return {"command": "python3", "args": [str(SERVER_PATH)]}


def inject_openclaw(environment: Mapping[str, str]) -> Path:
    path = Path(environment["OPENCLAW_CONFIG_PATH"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("mcp", {}).setdefault("servers", {})[SERVER_NAME] = {
        "enabled": True,
        **server_config(),
        "cwd": str(base.WORKSPACE),
        "connectionTimeoutMs": 10000,
        "requestTimeoutMs": 300000,
    }
    write_json(path, payload)
    return path


def inject_codex(environment: Mapping[str, str]) -> Path:
    path = Path(environment["CODEX_HOME"]) / "config.toml"
    addition = (
        f"\n[mcp_servers.{SERVER_NAME}]\n"
        'command = "python3"\n'
        f"args = [{json.dumps(str(SERVER_PATH))}]\n"
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 300\n"
    )
    with path.open("a", encoding="utf-8") as stream:
        stream.write(addition)
    return path


def inject_claudecode(environment: Mapping[str, str]) -> Path:
    path = Path(environment["CLAUDE_CONFIG_DIR"]) / "duplicate-argument-probe.mcp.json"
    write_json(path, {"mcpServers": {SERVER_NAME: {"type": "stdio", **server_config()}}})
    return path


def inject_hermes(environment: Mapping[str, str]) -> Path:
    path = Path(environment["HERMES_CONFIG"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("mcp_servers", {})[SERVER_NAME] = {
        **server_config(),
        "timeout": 300,
        "connect_timeout": 10,
    }
    toolsets = payload.setdefault("platform_toolsets", {}).setdefault("cli", [])
    payload["platform_toolsets"]["cli"] = [item for item in toolsets if item != "no_mcp"]
    if SERVER_NAME not in payload["platform_toolsets"]["cli"]:
        payload["platform_toolsets"]["cli"].append(SERVER_NAME)
    write_json(path, payload)
    return path


def inject_deepseek(environment: Mapping[str, str]) -> Path:
    path = Path(environment["DSH_CORDIS_CONFIG"])
    addition = f"""

- id: mcp-hb-duplicate-argument-probe
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: {json.dumps(SERVER_NAME)}
    transport: stdio
    command: {json.dumps('python3')}
    args: [{json.dumps(str(SERVER_PATH))}]
    cwd: {json.dumps(str(base.WORKSPACE))}
    env: {{}}
    toolCallTimeoutMs: 300000
    failOnStartupError: true
    reconnect:
      enabled: false
"""
    content = path.read_text(encoding="utf-8")
    marker = "\n- id: sdk-jsonrpc-server\n"
    if content.count(marker) != 1:
        raise RuntimeError("DeepSeek SDK server marker is missing or ambiguous")
    path.write_text(content.replace(marker, addition + marker, 1), encoding="utf-8")
    return path


def materialize_harness_state(*args: Any, **kwargs: Any) -> dict[str, str]:
    environment = ORIGINAL_MATERIALIZE(*args, **kwargs)
    model = kwargs.get("model")
    harness = str(getattr(model, "harness", ""))
    if harness not in SUPPORTED:
        raise ValueError(f"unsupported duplicate-argument harness: {harness}")
    injectors = {
        "openclaw": inject_openclaw,
        "codex": inject_codex,
        "claudecode": inject_claudecode,
        "hermes": inject_hermes,
        "deepseek": inject_deepseek,
    }
    config_path = injectors[harness](environment)
    write_json(
        NATIVE_ROOT / "injection.json",
        {
            "schema_version": "harnessbench_duplicate_argument_injection_v1",
            "harness": harness,
            "server_name": SERVER_NAME,
            "server_path": str(SERVER_PATH),
            "config_path": str(config_path),
            "declared_tools": ["observation_register"],
            "raw_pair_preserving_backend": True,
        },
    )
    return environment


def resolve_harness_extra_args(model: Any, requested: Mapping[str, object]) -> dict[str, str]:
    resolved = ORIGINAL_EXTRA_ARGS(model, requested)
    if model.harness == "claudecode":
        resolved["harnessbench-task-tools"] = "true"
        resolved["harnessbench-task-tool-config"] = str(
            Path(resolved.get("harnessbench-task-tool-config", ""))
            if resolved.get("harnessbench-task-tool-config")
            else Path("/hb-state/claudecode/duplicate-argument-probe.mcp.json")
        )
    elif model.harness == "hermes":
        current = [item for item in resolved.get("toolsets", "").split(",") if item]
        if SERVER_NAME not in current:
            current.append(SERVER_NAME)
        resolved["toolsets"] = ",".join(current)
    return resolved


base.materialize_harness_state = materialize_harness_state
base.resolve_harness_extra_args = resolve_harness_extra_args


if __name__ == "__main__":
    raise SystemExit(base.main())
