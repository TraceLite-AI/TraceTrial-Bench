#!/usr/bin/env python3
"""Task-local worker shim that injects one duplicate-name MCP catalog.

The repository worker is copied to ``container_worker_base.py`` by the task
Dockerfile.  This shim changes only per-Harness MCP configuration and records
probe evidence; execution, gateways, tracing, cleanup, and secret handling stay
owned by the repository worker.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from harnessbench import container_worker_base as base


SERVER_NAME = "hb_duplicate_probe"
SERVER_PATH = Path("/opt/harnessbench-case/duplicate_mcp.py")
NATIVE_ROOT = base.OUTPUT_ROOT / "native"
SUPPORTED = {"openclaw", "codex", "claudecode", "hermes"}

ORIGINAL_MATERIALIZE = base.materialize_harness_state
ORIGINAL_EXTRA_ARGS = base.resolve_harness_extra_args
ORIGINAL_RUN_REQUEST = base.run_request


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def server_config() -> dict[str, Any]:
    return {
        "command": "python3",
        "args": [str(SERVER_PATH)],
    }


def inject_openclaw(environment: Mapping[str, str]) -> Path:
    path = Path(environment["OPENCLAW_CONFIG_PATH"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    mcp = payload.setdefault("mcp", {})
    servers = mcp.setdefault("servers", {})
    servers[SERVER_NAME] = {
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
    path = Path(environment["CLAUDE_CONFIG_DIR"]) / "duplicate-probe.mcp.json"
    write_json(
        path,
        {
            "mcpServers": {
                SERVER_NAME: {
                    "type": "stdio",
                    **server_config(),
                }
            }
        },
    )
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
    payload["platform_toolsets"]["cli"] = [
        item for item in toolsets if item != "no_mcp"
    ]
    if SERVER_NAME not in payload["platform_toolsets"]["cli"]:
        payload["platform_toolsets"]["cli"].append(SERVER_NAME)
    write_json(path, payload)
    return path


def materialize_harness_state(*args: Any, **kwargs: Any) -> dict[str, str]:
    environment = ORIGINAL_MATERIALIZE(*args, **kwargs)
    model = kwargs.get("model")
    harness = str(getattr(model, "harness", ""))
    if harness not in SUPPORTED:
        raise ValueError(f"unsupported duplicate-probe harness: {harness}")
    injectors = {
        "openclaw": inject_openclaw,
        "codex": inject_codex,
        "claudecode": inject_claudecode,
        "hermes": inject_hermes,
    }
    config_path = injectors[harness](environment)
    write_json(
        NATIVE_ROOT / "injection.json",
        {
            "schema_version": "harnessbench_duplicate_tool_injection_v1",
            "harness": harness,
            "server_name": SERVER_NAME,
            "server_path": str(SERVER_PATH),
            "config_path": str(config_path),
            "declared_names": [
                "hb_host_record",
                "hb_host_record",
                "hb_host_pair_control",
            ],
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
            else Path("/hb-state/claudecode/duplicate-probe.mcp.json")
        )
    elif model.harness == "hermes":
        current = [item for item in resolved.get("toolsets", "").split(",") if item]
        if SERVER_NAME not in current:
            current.append(SERVER_NAME)
        resolved["toolsets"] = ",".join(current)
    return resolved


DIAGNOSTIC = re.compile(
    r"duplicate\s+(?:tool\s+)?name|tool\s+name[^\n]*(?:already|duplicate|collision|conflict)|"
    r"(?:already\s+registered|name\s+collision|conflicting\s+tool|unique\s+tool\s+names)|"
    r"(?:error|warn|reject)[^\n]{0,160}hb_host_record|"
    r"hb_host_record[^\n]{0,200}registered\s+as[^\n]{0,200}"
    r"hb_host_record-2",
    re.IGNORECASE,
)


def summarize_registration(control: Any, error: Exception | None) -> None:
    excerpts: list[dict[str, str]] = []
    for path in sorted(base.OUTPUT_ROOT.rglob("*")):
        if not path.is_file() or path.name in {
            "duplicate_probe.jsonl",
            "harness.stdout.log",
            "injection.json",
            "registration_observation.json",
        }:
            continue
        if "trace" in path.parts or path.suffix not in {".log", ".txt", ".json", ".jsonl"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[-2_000_000:]
        except OSError:
            continue
        for match in DIAGNOSTIC.finditer(text):
            start = max(0, match.start() - 160)
            end = min(len(text), match.end() + 240)
            excerpts.append(
                {
                    "artifact": str(path.relative_to(base.OUTPUT_ROOT)),
                    "excerpt": text[start:end].replace("\x00", "")[:800],
                }
            )
            if len(excerpts) >= 20:
                break
        if len(excerpts) >= 20:
            break
    write_json(
        NATIVE_ROOT / "registration_observation.json",
        {
            "schema_version": "harnessbench_duplicate_registration_observation_v1",
            "worker_status": control.get("status") if isinstance(control, dict) else None,
            "worker_error": f"{type(error).__name__}: {error}" if error else None,
            "diagnostic_detected": bool(excerpts),
            "diagnostic_excerpts": excerpts,
        },
    )


def run_request(*args: Any, **kwargs: Any) -> dict[str, object]:
    control: dict[str, object] | None = None
    caught: Exception | None = None
    try:
        control = ORIGINAL_RUN_REQUEST(*args, **kwargs)
        return control
    except Exception as exc:
        caught = exc
        raise
    finally:
        summarize_registration(control, caught)


base.materialize_harness_state = materialize_harness_state
base.resolve_harness_extra_args = resolve_harness_extra_args
base.run_request = run_request


if __name__ == "__main__":
    raise SystemExit(base.main())
