from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from harness_tracer.process import ProcessRunner
from harness_tracer.runner import RunConfig as TraceRunConfig
from harness_tracer.runner import TraceRunner

from tracetrial_bench.conversation_runner import (
    ConversationRunConfig,
    ConversationSimulator,
    ConversationTraceRunner,
)
from tracetrial_bench.business_simulator import (
    BUSINESS_ENDPOINT_ENV,
    SOCKET_FILENAME,
    probe_business_endpoint,
    validate_business_endpoint,
)
from tracetrial_bench.container_environment import (
    add_loopback_no_proxy,
    resolve_transport_environment,
)
from tracetrial_bench.deepseek_config import render_deepseek_cordis_config
from tracetrial_bench.deepseek_runner import (
    DeepSeekTraceRunner,
    patch_root_summary_containment,
)
from tracetrial_bench.litellm_gateway import (
    GATEWAY_HOST,
    GATEWAY_PORT,
    GATEWAY_TOKEN_ENV,
    GatewayInfo,
    LiteLLMGateway,
    OutputSecurityError,
    assert_output_has_no_secrets,
    protect_process_credentials,
    redact_output_secrets,
)
from tracetrial_bench.model_registry import (
    INTERNAL_API_KEY_ENV,
    SUPPORTED_CONTAINER_HARNESSES,
    ModelRuntimeSpec,
)
from tracetrial_bench.model_call_budget import (
    MODEL_CALL_BUDGET_PORT,
    ModelCallBudgetInfo,
    ModelCallBudgetProxy,
    validate_model_call_budget,
)
from tracetrial_bench.openclaw_gateway import (
    OPENCLAW_GATEWAY_PORT,
    OPENCLAW_GATEWAY_TOKEN_ENV,
    OpenClawGateway,
)
from tracetrial_bench.provider_request_recorder import (
    PROVIDER_REQUEST_RECORDER_PORT,
    ProviderRequestRecorderInfo,
    ProviderRequestRecorderProxy,
)
from tracetrial_bench.task_tool_mcp import (
    BRIDGE_RELATIVE_PATH,
    CONFIG_ENV as TASK_TOOL_CONFIG_ENV,
    ENDPOINT_ENV as TASK_TOOL_ENDPOINT_ENV,
    SERVER_NAME as TASK_TOOL_SERVER_NAME,
    SOCKET_ENV as TASK_TOOL_SOCKET_ENV,
    _validate_endpoint,
    probe_task_tool_endpoint,
    task_tool_environment,
)


WORKSPACE = Path("/workspace")
STATE_ROOT = Path("/hb-state")
OUTPUT_ROOT = Path("/hb-output")
_HERMES_BENCHMARK_TOOLSETS = ("terminal", "file", "delegation")


def materialize_harness_state(
    *,
    model: ModelRuntimeSpec,
    state_root: Path,
    workspace: Path,
    auth_mode: str = "model_registry",
    subscription_auth: Mapping[str, object] | None = None,
    transport_environment: Mapping[str, str] | None = None,
    gateway_base_url: str | None = None,
    gateway_token: str | None = None,
    security_settings: Mapping[str, str] | None = None,
    harness_settings: Mapping[str, str] | None = None,
    business_simulator_endpoint: Mapping[str, object] | None = None,
    task_tools: Mapping[str, object] | None = None,
    task_tool_endpoint: Mapping[str, object] | None = None,
    model_call_budget: Mapping[str, object] | None = None,
    provider_request_capture: bool = False,
) -> dict[str, str]:
    workspace = workspace.resolve(strict=True)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("container workspace must be a real directory")
    if state_root.is_symlink():
        raise ValueError("container state root may not be a symlink")
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    base_environment = {
        **dict(transport_environment or {}),
        "PATH": os.environ.get(
            "PATH",
            "/opt/hermes/.venv/bin:/usr/local/bin:/usr/bin:/bin",
        ),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    if business_simulator_endpoint is not None:
        base_environment[BUSINESS_ENDPOINT_ENV] = json.dumps(
            validate_business_endpoint(dict(business_simulator_endpoint)),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    security_settings = dict(security_settings or {})
    harness_settings = dict(harness_settings or {})
    task_tools = dict(task_tools or {})
    budget_enabled = model_call_budget is not None
    task_tools_enabled = bool(task_tools)
    task_tool_bridge = workspace / BRIDGE_RELATIVE_PATH
    if task_tools_enabled:
        if task_tool_bridge.is_symlink() or not task_tool_bridge.is_file():
            raise ValueError("container task tool bridge is missing")
        raw_tools = task_tools.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("container task tools are invalid")
        base_environment.update(
            task_tool_environment(
                tuple(item for item in raw_tools if isinstance(item, dict)),
                endpoint=task_tool_endpoint,
                socket_path=(
                    None if task_tool_endpoint is not None else workspace / SOCKET_FILENAME
                ),
            )
        )
    task_tool_subprocess_env = (
        {
            name: base_environment[name]
            for name in (
                TASK_TOOL_CONFIG_ENV,
                TASK_TOOL_ENDPOINT_ENV,
                TASK_TOOL_SOCKET_ENV,
            )
            if name in base_environment
        }
        if task_tools_enabled
        else {}
    )
    if (workspace / "taskctl").is_file():
        base_environment["WORKSPACE"] = str(workspace)
    if model.harness == "openclaw":
        token = _require_gateway_token(gateway_token)
        provider_base_url = model.base_url
        provider_api_key = model.api_key
        thinking_level = (
            _arg_value(harness_settings, "thinking") or "off"
        ).strip().lower()
        openclaw_model_entry: dict[str, object] = {
            "id": model.model,
            "name": model.model,
        }
        if thinking_level != "off":
            # OpenClaw treats configured custom models as non-reasoning unless
            # their catalog row says otherwise.  It validates an explicit
            # --thinking level before making the first provider request, so
            # advertise reasoning only for runs that explicitly requested it.
            # Leaving the field absent for ordinary/off runs preserves their
            # previous default behavior.
            openclaw_model_entry["reasoning"] = True
        if budget_enabled:
            provider_base_url, provider_api_key = _require_gateway_connection(
                gateway_base_url,
                gateway_token,
                expected_port=MODEL_CALL_BUDGET_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
        elif provider_request_capture:
            provider_base_url = _require_local_gateway_url(
                gateway_base_url,
                expected_port=PROVIDER_REQUEST_RECORDER_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
        base_environment[INTERNAL_API_KEY_ENV] = model.api_key
        harness_state = state_root / "openclaw"
        home = state_root / "openclaw-home"
        harness_state.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        config_path = harness_state / "openclaw.json"
        payload = {
            "models": {
                "providers": {
                    model.provider: {
                        "baseUrl": provider_base_url,
                        "apiKey": provider_api_key,
                        "api": "openai-completions",
                        "models": [openclaw_model_entry],
                    }
                }
            },
            "agents": {
                "defaults": {
                    "workspace": str(workspace),
                    "model": {"primary": model.harness_model},
                    "models": {model.harness_model: {"alias": model.model}},
                }
            },
            "gateway": {
                "mode": "local",
                "bind": "loopback",
                "port": OPENCLAW_GATEWAY_PORT,
                "auth": {"mode": "token"},
            },
        }
        if task_tools_enabled:
            payload["mcp"] = {
                "servers": {
                    TASK_TOOL_SERVER_NAME: {
                        "enabled": True,
                        "command": sys.executable,
                        "args": [str(task_tool_bridge)],
                        "env": task_tool_subprocess_env,
                        "cwd": str(workspace),
                        "connectionTimeoutMs": 10000,
                        "requestTimeoutMs": 300000,
                    }
                }
            }
        if security_settings.get("harnessbench-approval-policy") == "ask_always":
            # OpenClaw 2026.6.9 rejects the normalized `mode` selector when
            # legacy-compatible `security` or `ask` fields are also present.
            payload["tools"] = {
                "exec": {"security": "full", "ask": "always"}
            }
            _write_private_json(
                harness_state / "exec-approvals.json",
                {
                    "version": 1,
                    # OpenClaw derives a process-independent local approval
                    # runtime credential from this runner-owned token.  The
                    # token remains in private state and is already included
                    # in the run's secret-redaction set.
                    "socket": {"token": token},
                    "defaults": {
                        "security": "full",
                        "ask": "always",
                        "askFallback": "deny",
                        "autoAllowSkills": False,
                    },
                    "agents": {
                        "main": {
                            "security": "full",
                            "ask": "always",
                            "askFallback": "deny",
                            "autoAllowSkills": False,
                        }
                    },
                },
            )
            if (
                security_settings.get("harnessbench-approval-fallback")
                == "unavailable"
            ):
                base_environment[
                    "HARNESSBENCH_OPENCLAW_AUTO_DENY_APPROVALS"
                ] = "1"
        _write_private_json(config_path, payload)
        base_environment.update(
            {
                "HOME": str(home),
                "OPENCLAW_STATE_DIR": str(harness_state),
                "OPENCLAW_CONFIG_PATH": str(config_path),
                "OPENCLAW_CONFIG": str(config_path),
                "OPENCLAW_WORKSPACE_DIR": str(workspace),
                OPENCLAW_GATEWAY_TOKEN_ENV: token,
            }
        )
        return add_loopback_no_proxy(base_environment)
    if model.harness == "hermes":
        provider_base_url = model.base_url
        provider_key_env = INTERNAL_API_KEY_ENV
        if budget_enabled:
            provider_base_url, _ = _require_gateway_connection(
                gateway_base_url,
                gateway_token,
                expected_port=MODEL_CALL_BUDGET_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
            provider_key_env = GATEWAY_TOKEN_ENV
            base_environment[GATEWAY_TOKEN_ENV] = _require_gateway_token(
                gateway_token
            )
        elif provider_request_capture:
            provider_base_url = _require_local_gateway_url(
                gateway_base_url,
                expected_port=PROVIDER_REQUEST_RECORDER_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
        base_environment[INTERNAL_API_KEY_ENV] = model.api_key
        harness_state = state_root / "hermes"
        home = state_root / "hermes-home"
        harness_state.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        provider_name = (model.harness_provider or f"custom:{model.provider}").split(
            ":", 1
        )[-1]
        payload = {
            "custom_providers": [
                {
                    "name": provider_name,
                    "base_url": provider_base_url,
                    "key_env": provider_key_env,
                    "api_mode": "chat_completions",
                    "model": model.harness_model,
                    "models": {model.harness_model: {}},
                }
            ],
            "model": {
                "default": model.harness_model,
                "provider": model.harness_provider or f"custom:{model.provider}",
            },
            "plugins": {"enabled": []},
            "skills": {"disabled": ["*"]},
            "platform_toolsets": {
                "cli": [
                    *_HERMES_BENCHMARK_TOOLSETS,
                    *([TASK_TOOL_SERVER_NAME] if task_tools_enabled else ["no_mcp"]),
                ],
            },
            "terminal": {"cwd": str(workspace)},
        }
        if security_settings.get("harnessbench-approval-policy") == "ask_always":
            # Hermes intentionally trusts unattended CLI sessions unless this
            # explicit native ask surface is enabled. With no approval client,
            # commands that require consent return a model-visible pending
            # approval result instead of being silently executed.
            payload["approvals"] = {"mode": "manual"}
            base_environment["HERMES_EXEC_ASK"] = "1"
        if task_tools_enabled:
            payload["mcp_servers"] = {
                TASK_TOOL_SERVER_NAME: {
                    "command": sys.executable,
                    "args": [str(task_tool_bridge)],
                    "env": task_tool_subprocess_env,
                    "timeout": 300,
                    "connect_timeout": 10,
                    "tools": {
                        "include": [
                            str(item["name"])
                            for item in task_tools["tools"]
                            if isinstance(item, dict) and isinstance(item.get("name"), str)
                        ]
                    },
                }
            }
        _write_private_json(harness_state / "config.yaml", payload)
        base_environment.update(
            {
                "HOME": str(home),
                "HERMES_HOME": str(harness_state),
                "HERMES_CONFIG": str(harness_state / "config.yaml"),
                "HERMES_IGNORE_RULES": "1",
            }
        )
        return base_environment
    if model.harness == "deepseek":
        provider_base_url = model.base_url
        provider_api_key = model.api_key
        if budget_enabled:
            provider_base_url, provider_api_key = _require_gateway_connection(
                gateway_base_url,
                gateway_token,
                expected_port=MODEL_CALL_BUDGET_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
        elif provider_request_capture:
            provider_base_url = _require_local_gateway_url(
                gateway_base_url,
                expected_port=PROVIDER_REQUEST_RECORDER_PORT,
            )
            provider_base_url += _provider_base_path(model.base_url)
            # The recorder replaces client authorization with the registered
            # provider credential, so the harness receives a non-secret token.
            provider_api_key = "harnessbench-provider-recorder"
        harness_state = state_root / "deepseek"
        home = state_root / "deepseek-home"
        sessions = harness_state / "sessions"
        harness_state.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        sessions.mkdir(mode=0o700)
        # Cordis resolves bare plugin names from the directory containing the
        # private composition file.  Expose only the image-pinned dependency
        # tree at that resolution boundary; configuration and session data
        # remain in the per-run state mount.
        (harness_state / "node_modules").symlink_to(
            "/opt/deepseek-runtime/node_modules",
            target_is_directory=True,
        )
        deepseek_reasoning_effort = (
            _arg_value(harness_settings, "reasoning-effort")
            or model.deepseek_reasoning_effort
        )
        config_path = harness_state / "cordis.yml"
        _write_private_text(
            config_path,
            render_deepseek_cordis_config(
                reasoning_effort=deepseek_reasoning_effort,
                continuable_subagents=(
                    _arg_value(
                        harness_settings,
                        "harnessbench-deepseek-continuable-subagents",
                    )
                    == "true"
                ),
                task_tool_bridge=(task_tool_bridge if task_tools_enabled else None),
                task_tool_environment=(
                    task_tool_subprocess_env if task_tools_enabled else None
                ),
            ),
        )
        base_environment.update(
            {
                "HOME": str(home),
                "DSH_HOME": str(home),
                "DSH_CWD": str(workspace),
                "DSH_SESSION_ROOT": str(sessions),
                "DSH_CORDIS_CONFIG": str(config_path),
                "DSH_SNAPSHOT": "1",
                "DSH_TELEMETRY_DISABLED": "1",
                "DSH_REASONING_EFFORT": deepseek_reasoning_effort,
                "DSH_PERMISSION_MODE": (
                    "workspace-write"
                    if security_settings.get("harnessbench-approval-policy")
                    == "ask_always"
                    else "danger-full-access"
                ),
                "DSH_SYSTEM_PROMPT": (
                    "You are a coding agent. Work directly in the current "
                    "workspace, use the available native tools, and complete "
                    "the user's task."
                ),
                "DEEPSEEK_BASE_URL": provider_base_url,
                "DEEPSEEK_API_KEY": provider_api_key,
            }
        )
        return add_loopback_no_proxy(base_environment)
    if model.harness == "codex":
        if auth_mode == "subscription":
            return _materialize_subscription_harness_state(
                harness="codex",
                state_root=state_root,
                workspace=workspace,
                base_environment=base_environment,
                subscription_auth=subscription_auth,
                task_tools_enabled=task_tools_enabled,
                task_tool_bridge=task_tool_bridge,
                task_tool_subprocess_env=task_tool_subprocess_env,
            )
        gateway_url, token = _require_gateway_connection(
            gateway_base_url,
            gateway_token,
            expected_port=(MODEL_CALL_BUDGET_PORT if budget_enabled else GATEWAY_PORT),
        )
        harness_state = state_root / "codex"
        home = state_root / "codex-home"
        harness_state.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        provider_name = "harnessbench_litellm"
        config_text = "\n".join(
            [
                f"model = {json.dumps(model.harness_model)}",
                f"model_provider = {json.dumps(provider_name)}",
                "",
                f"[model_providers.{provider_name}]",
                'name = "TraceTrial_Bench LiteLLM"',
                f"base_url = {json.dumps(gateway_url + '/v1')}",
                f"env_key = {json.dumps(GATEWAY_TOKEN_ENV)}",
                'wire_api = "responses"',
                "",
            ]
        )
        if task_tools_enabled:
            config_text += "\n".join(
                [
                    f"[mcp_servers.{TASK_TOOL_SERVER_NAME}]",
                    f"command = {json.dumps(sys.executable)}",
                    f"args = {json.dumps([str(task_tool_bridge)])}",
                    "env = { "
                    + ", ".join(
                        f"{name} = {json.dumps(value)}"
                        for name, value in sorted(task_tool_subprocess_env.items())
                    )
                    + " }",
                    "startup_timeout_sec = 10",
                    "tool_timeout_sec = 300",
                    "",
                ]
            )
        _write_private_text(harness_state / "config.toml", config_text)
        base_environment.update(
            {
                "HOME": str(home),
                "CODEX_HOME": str(harness_state),
                GATEWAY_TOKEN_ENV: token,
            }
        )
        return add_loopback_no_proxy(base_environment)
    if model.harness == "claudecode":
        if auth_mode == "subscription":
            return _materialize_subscription_harness_state(
                harness="claudecode",
                state_root=state_root,
                workspace=workspace,
                base_environment=base_environment,
                subscription_auth=subscription_auth,
                task_tools_enabled=task_tools_enabled,
                task_tool_bridge=task_tool_bridge,
                task_tool_subprocess_env=task_tool_subprocess_env,
            )
        gateway_url, token = _require_gateway_connection(
            gateway_base_url,
            gateway_token,
            expected_port=(MODEL_CALL_BUDGET_PORT if budget_enabled else GATEWAY_PORT),
        )
        harness_state = state_root / "claudecode"
        home = state_root / "claudecode-home"
        harness_state.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        if task_tools_enabled:
            _write_private_json(
                harness_state / "task-tools.mcp.json",
                {
                    "mcpServers": {
                        TASK_TOOL_SERVER_NAME: {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [str(task_tool_bridge)],
                            "env": task_tool_subprocess_env,
                        }
                    }
                },
            )
        base_environment.update(
            {
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(harness_state),
                "ANTHROPIC_BASE_URL": gateway_url,
                "ANTHROPIC_AUTH_TOKEN": token,
                GATEWAY_TOKEN_ENV: token,
            }
        )
        return add_loopback_no_proxy(base_environment)
    raise ValueError(f"unsupported container harness: {model.harness}")


def _materialize_subscription_harness_state(
    *,
    harness: str,
    state_root: Path,
    workspace: Path,
    base_environment: dict[str, str],
    subscription_auth: Mapping[str, object] | None,
    task_tools_enabled: bool,
    task_tool_bridge: Path,
    task_tool_subprocess_env: Mapping[str, str],
) -> dict[str, str]:
    if not isinstance(subscription_auth, Mapping):
        raise ValueError("subscription auth state is missing")
    if subscription_auth.get("schema_version") != "harnessbench_subscription_auth_v1":
        raise ValueError("subscription auth state schema is invalid")
    if subscription_auth.get("harness") != harness:
        raise ValueError("subscription auth state harness does not match")

    harness_state = state_root / harness
    home = state_root / f"{harness}-home"
    harness_state.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    files = subscription_auth.get("files")
    if not isinstance(files, Mapping):
        raise ValueError("subscription auth files are invalid")
    for relative_name, encoded in files.items():
        if not isinstance(relative_name, str) or not isinstance(encoded, str):
            raise ValueError("subscription auth file entry is invalid")
        relative_path = Path(relative_name)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or len(relative_path.parts) != 1
        ):
            raise ValueError("subscription auth file path is unsafe")
        try:
            payload = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeError) as exc:
            raise ValueError("subscription auth file payload is invalid") from exc
        destination = harness_state / relative_path
        destination.write_bytes(payload)
        destination.chmod(0o600)

    environment_values = subscription_auth.get("env") or {}
    if not isinstance(environment_values, Mapping):
        raise ValueError("subscription auth environment is invalid")

    base_environment.update(
        {
            "HOME": str(home),
        }
    )
    for key, value in environment_values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("subscription auth environment entry is invalid")
        if key not in {"CLAUDE_CODE_OAUTH_TOKEN"}:
            raise ValueError(f"unsupported subscription auth environment: {key}")
        base_environment[key] = value

    if harness == "codex":
        config_path = harness_state / "config.toml"
        if task_tools_enabled:
            config_text = ""
            if config_path.exists():
                config_text = config_path.read_text(encoding="utf-8")
                if config_text and not config_text.endswith("\n"):
                    config_text += "\n"
            config_text += "\n".join(
                [
                    f"[mcp_servers.{TASK_TOOL_SERVER_NAME}]",
                    f"command = {json.dumps(sys.executable)}",
                    f"args = {json.dumps([str(task_tool_bridge)])}",
                    "env = { "
                    + ", ".join(
                        f"{name} = {json.dumps(value)}"
                        for name, value in sorted(task_tool_subprocess_env.items())
                    )
                    + " }",
                    "startup_timeout_sec = 10",
                    "tool_timeout_sec = 300",
                    "",
                ]
            )
            _write_private_text(config_path, config_text)
        base_environment["CODEX_HOME"] = str(harness_state)
        return add_loopback_no_proxy(base_environment)

    if harness == "claudecode":
        if task_tools_enabled:
            _write_private_json(
                harness_state / "task-tools.mcp.json",
                {
                    "mcpServers": {
                        TASK_TOOL_SERVER_NAME: {
                            "type": "stdio",
                            "command": sys.executable,
                            "args": [str(task_tool_bridge)],
                            "env": dict(task_tool_subprocess_env),
                        }
                    }
                },
            )
        base_environment["CLAUDE_CONFIG_DIR"] = str(harness_state)
        return add_loopback_no_proxy(base_environment)

    raise ValueError(f"unsupported subscription harness: {harness}")


def redact_text(value: str, secret_values: tuple[str, ...]) -> str:
    redacted = value
    for secret in sorted(
        (item for item in secret_values if isinstance(item, str) and item),
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(secret, "<redacted>")
    return redacted


def resolve_harness_extra_args(
    model: ModelRuntimeSpec, requested: Mapping[str, object]
) -> dict[str, str]:
    resolved = {
        str(key): str(value)
        for key, value in requested.items()
        if key not in {"provider", "toolsets"}
    }
    task_tools_enabled = resolved.get("harnessbench-task-tools") == "true"
    if task_tools_enabled:
        resolved["harnessbench-task-tool-bridge"] = str(
            WORKSPACE / BRIDGE_RELATIVE_PATH
        )
        if model.harness == "codex":
            # materialize_harness_state writes the complete private MCP server
            # table, including its endpoint/auth environment, into CODEX_HOME.
            # Re-declaring the same server through CLI -c overrides can discard
            # that private environment and silently remove the task tools.
            resolved["harnessbench-task-tools-preconfigured"] = "true"
        if model.harness == "claudecode":
            resolved["harnessbench-task-tool-config"] = str(
                STATE_ROOT / "claudecode" / "task-tools.mcp.json"
            )
    if model.harness == "hermes":
        resolved["toolsets"] = ",".join(
            [
                *_HERMES_BENCHMARK_TOOLSETS,
                *([TASK_TOOL_SERVER_NAME] if task_tools_enabled else []),
            ]
        )
        # One-shot containers cannot receive detached delegation results after
        # the parent exits, so the adapter must use Hermes' stateless channel.
        resolved["harnessbench-stateless"] = "true"
        if model.harness_provider:
            resolved["provider"] = model.harness_provider
    if model.harness == "openclaw":
        resolved["harnessbench-gateway"] = "true"
    return resolved


class RecordingProcessRunner:
    def __init__(self, output_root: Path, *, secret_values: tuple[str, ...]) -> None:
        self.delegate = ProcessRunner()
        self.output_root = output_root
        self.secret_values = secret_values
        self.calls = 0

    def run(self, command: list[str], **kwargs):
        completed = self.delegate.run(command, **kwargs)
        self.calls += 1
        if self.calls == 1:
            (self.output_root / "harness.stdout.log").write_text(
                redact_text(completed.stdout, self.secret_values), encoding="utf-8"
            )
            (self.output_root / "harness.stderr.log").write_text(
                redact_text(completed.stderr, self.secret_values), encoding="utf-8"
            )
        return completed


def main() -> int:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        protect_process_credentials()
    except Exception:
        print("container worker process credential isolation failed", file=sys.stderr)
        return 2
    try:
        request = _read_request(sys.stdin.read())
        control = run_request(
            request,
            gateway_token=os.environ.get(GATEWAY_TOKEN_ENV),
        )
    except OutputSecurityError as exc:
        print(f"container worker output security failure: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        secret_values = tuple(
            value
            for name in (INTERNAL_API_KEY_ENV, GATEWAY_TOKEN_ENV)
            if (value := os.environ.get(name))
        )
        message = redact_text(f"{type(exc).__name__}: {exc}", secret_values)
        relative_summary = _write_failure_trace(message, output_root=OUTPUT_ROOT)
        control = {"status": "failed", "trace_summary": str(relative_summary)}
    print(json.dumps(control, ensure_ascii=False))
    return 0


def run_request(
    request: Mapping[str, object],
    *,
    workspace: Path = WORKSPACE,
    state_root: Path = STATE_ROOT,
    output_root: Path = OUTPUT_ROOT,
    transport_environment: Mapping[str, str] | None = None,
    gateway_token: str | None = None,
) -> dict[str, object]:
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth_mode = str(request.get("auth_mode") or "model_registry").replace("-", "_")
    if auth_mode not in {"model_registry", "subscription"}:
        raise ValueError("container request auth mode is invalid")
    model = _model_from_request(request)
    secret_values = [model.api_key] if model.api_key else []
    task_tool_endpoint = (
        request.get("task_tool_endpoint")
        if isinstance(request.get("task_tool_endpoint"), dict)
        else None
    )
    business_simulator_endpoint = (
        request.get("business_simulator_endpoint")
        if isinstance(request.get("business_simulator_endpoint"), dict)
        else None
    )
    if business_simulator_endpoint is not None:
        business_token = business_simulator_endpoint.get("auth_token")
        if isinstance(business_token, str) and business_token:
            secret_values.append(business_token)
    if task_tool_endpoint is not None:
        task_tool_token = task_tool_endpoint.get("auth_token")
        if isinstance(task_tool_token, str) and task_tool_token:
            secret_values.append(task_tool_token)
    gateway: LiteLLMGateway | None = None
    budget_proxy: ModelCallBudgetProxy | None = None
    budget_info: ModelCallBudgetInfo | None = None
    provider_recorder: ProviderRequestRecorderProxy | None = None
    if auth_mode == "model_registry":
        provider_recorder = ProviderRequestRecorderProxy(
            output_root=output_root,
            upstream_base_url=model.base_url,
            upstream_api_key=model.api_key,
        )
    provider_recorder_info: ProviderRequestRecorderInfo | None = None
    openclaw_gateway: OpenClawGateway | None = None
    deepseek_trace_runner: DeepSeekTraceRunner | None = None
    gateway_info: GatewayInfo | None = None
    summary: dict[str, object] | None = None
    errors: list[Exception] = []

    raw_model_call_budget = request.get("model_call_budget")
    model_call_budget = (
        validate_model_call_budget(raw_model_call_budget)
        if isinstance(raw_model_call_budget, dict)
        else None
    )

    if auth_mode == "subscription" and model_call_budget is not None:
        raise ValueError("subscription auth does not support model_call_budget")
    if (
        auth_mode == "model_registry"
        and model.harness in {"openclaw", "codex", "claudecode"}
    ) or model_call_budget:
        gateway_token = _require_gateway_token(gateway_token)
        secret_values.append(gateway_token)
    gateway_metadata_required = (
        auth_mode == "model_registry" and model.harness in {"codex", "claudecode"}
    )
    if auth_mode == "subscription":
        subscription_auth = request.get("subscription_auth")
        subscription_env = (
            subscription_auth.get("env")
            if isinstance(subscription_auth, dict)
            else None
        )
        if isinstance(subscription_env, Mapping):
            secret_values.extend(
                value
                for value in subscription_env.values()
                if isinstance(value, str) and value
            )

    try:
        if business_simulator_endpoint is not None:
            probe_business_endpoint(business_simulator_endpoint)
        if task_tool_endpoint is not None:
            probe_task_tool_endpoint(task_tool_endpoint)
        if provider_recorder is not None:
            provider_recorder_info = provider_recorder.start()
        if auth_mode == "model_registry" and model.harness in {"codex", "claudecode"}:
            assert provider_recorder_info is not None
            gateway = LiteLLMGateway(
                model=replace(
                    model,
                    base_url=(
                        provider_recorder_info.base_url
                        + _provider_base_path(model.base_url)
                    ),
                ),
                state_root=state_root / "runtime",
                output_root=output_root,
                token_factory=lambda: gateway_token,
            )
        if gateway is not None:
            gateway_info = gateway.start()
        if model_call_budget is not None:
            budget_proxy = ModelCallBudgetProxy(
                budget=model_call_budget,
                output_root=output_root,
                task_tools=(
                    request.get("task_tools")
                    if isinstance(request.get("task_tools"), dict)
                    else None
                ),
                upstream_base_url=(
                    gateway_info.base_url
                    if gateway_info is not None
                    else provider_recorder_info.base_url
                ),
                upstream_api_key=(
                    None if gateway_info is not None else model.api_key
                ),
            )
            budget_info = budget_proxy.start()
        environment = materialize_harness_state(
            model=model,
            state_root=state_root,
            workspace=workspace,
            auth_mode=auth_mode,
            subscription_auth=(
                request.get("subscription_auth")
                if isinstance(request.get("subscription_auth"), dict)
                else None
            ),
            transport_environment=(
                resolve_transport_environment()
                if transport_environment is None
                else transport_environment
            ),
            gateway_base_url=(
                budget_info.base_url
                if budget_info is not None
                else (
                    gateway_info.base_url
                    if gateway_info is not None
                    else (
                        provider_recorder_info.base_url
                        if provider_recorder_info is not None
                        else None
                    )
                )
            ),
            gateway_token=gateway_token,
            security_settings={
                str(key): str(value)
                for key, value in (request.get("extra_args") or {}).items()
                if str(key).startswith("harnessbench-approval-")
            },
            harness_settings={
                str(key): str(value)
                for key, value in (request.get("extra_args") or {}).items()
            },
            business_simulator_endpoint=business_simulator_endpoint,
            task_tools=(
                request.get("task_tools")
                if isinstance(request.get("task_tools"), dict)
                else None
            ),
            task_tool_endpoint=task_tool_endpoint,
            model_call_budget=model_call_budget,
            provider_request_capture=(provider_recorder_info is not None),
        )
        if budget_info is not None:
            environment.update(budget_info.environment())
        if model.harness == "openclaw":
            openclaw_gateway = OpenClawGateway(
                state_root=state_root / "runtime",
                output_root=output_root,
                environment=environment,
                cwd=workspace,
                secret_values=tuple(secret_values),
            )
            openclaw_gateway.start()
        if model.harness == "deepseek":
            deepseek_trace_runner = DeepSeekTraceRunner(
                output_root=output_root,
                secret_values=tuple(secret_values),
            )
            trace_runner = deepseek_trace_runner
        else:
            recording_runner = RecordingProcessRunner(
                output_root,
                secret_values=tuple(secret_values),
            )
            trace_runner = TraceRunner(process_runner=recording_runner)
        conversation = request.get("conversation") or {"mode": "single_turn"}
        conversation_mode = str(conversation.get("mode") or "single_turn")
        timeout = (
            int(request["timeout"])
            if isinstance(request.get("timeout"), int)
            else None
        )
        extra_args = resolve_harness_extra_args(
            model,
            request.get("extra_args") or {},
        )
        max_output_bytes_value = extra_args.pop(
            "harnessbench-max-output-bytes", None
        )
        max_output_bytes = None
        if max_output_bytes_value is not None:
            try:
                max_output_bytes = int(max_output_bytes_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "harnessbench-max-output-bytes must be a positive integer"
                ) from exc
            if not 1 <= max_output_bytes <= 64_000_000:
                raise ValueError(
                    "harnessbench-max-output-bytes must be between 1 and 64000000"
                )
        if conversation_mode == "single_turn":
            summary = trace_runner.run(
                TraceRunConfig(
                    harness=model.harness,
                    model=model.harness_model,
                    input_query=str(request["prompt"]),
                    output_dir=output_root / "trace",
                    timeout=timeout,
                    max_output_bytes=max_output_bytes,
                    extra_args=extra_args,
                    cwd=workspace,
                    env=environment,
                    replace_environment=True,
                    isolation={"mode": "docker", "fresh_state": True},
                    redact_paths=(str(state_root),),
                    redact_values=tuple(secret_values),
                    provider_request_events_path=(
                        provider_recorder_info.events_path
                        if provider_recorder_info is not None
                        else None
                    ),
                )
            )
        else:
            simulator = None
            messages = tuple(conversation.get("turns") or ())
            if conversation_mode == "simulator":
                simulator_path = state_root / "conversation-simulator.py"
                _write_private_text(
                    simulator_path,
                    str(conversation.get("simulator_source") or ""),
                )
                simulator = ConversationSimulator(simulator_path)
                messages = (str(request["prompt"]),)
            summary = ConversationTraceRunner(trace_runner=trace_runner).run(
                ConversationRunConfig(
                    task_id=str(request.get("task_id") or ""),
                    harness=model.harness,
                    model=model.harness_model,
                    initial_messages=messages,
                    output_dir=output_root / "trace",
                    timeout=timeout,
                    max_turns=(
                        int(conversation["max_turns"])
                        if isinstance(conversation.get("max_turns"), int)
                        else None
                    ),
                    new_session_before_turns=tuple(
                        conversation.get("new_session_before_turns") or ()
                    ),
                    new_session_context=str(
                        conversation.get("new_session_context") or "none"
                    ),
                    extra_args=extra_args,
                    cwd=workspace,
                    env=environment,
                    replace_environment=True,
                    isolation={"mode": "docker", "fresh_state": True},
                    redact_paths=(str(state_root),),
                    redact_values=tuple(secret_values),
                    provider_request_events_path=(
                        provider_recorder_info.events_path
                        if provider_recorder_info is not None
                        else None
                    ),
                    simulator=simulator,
                )
            )
    except Exception as exc:
        errors.append(exc)
    finally:
        if deepseek_trace_runner is not None:
            try:
                containment = deepseek_trace_runner.close()
                if summary is not None:
                    patch_root_summary_containment(summary, containment)
            except Exception as exc:
                errors.append(exc)
        if openclaw_gateway is not None:
            try:
                openclaw_gateway.stop()
            except Exception as exc:
                errors.append(exc)
        if budget_proxy is not None:
            try:
                budget_proxy.stop()
            except Exception as exc:
                errors.append(exc)
        if gateway is not None:
            try:
                gateway.stop()
            except Exception as exc:
                errors.append(exc)
        if provider_recorder is not None:
            try:
                provider_recorder.stop()
            except Exception as exc:
                errors.append(exc)

    gateway_summary = (
        gateway_info.public_summary()
        if gateway_info
        else (
            {
                "implementation": "litellm",
                "ready": False,
                "listen_host": GATEWAY_HOST,
                "listen_port": GATEWAY_PORT,
                "failure_stage": "startup",
            }
            if gateway is not None or gateway_metadata_required
            else None
        )
    )
    if errors:
        message = redact_text(
            "; ".join(f"{type(error).__name__}: {error}" for error in errors),
            tuple(secret_values),
        )
        relative_summary = _write_failure_trace(message, output_root=output_root)
        control: dict[str, object] = {
            "status": "failed",
            "trace_summary": str(relative_summary),
        }
    else:
        if summary is None:
            raise RuntimeError("container worker completed without a trace summary")
        summary_path = Path(str(summary["summary_path"])).resolve(strict=True)
        relative_summary = summary_path.relative_to(output_root.resolve())
        control = {
            "status": (
                "finished" if summary.get("status") == "finished" else "failed"
            ),
            "trace_summary": str(relative_summary),
        }
    if gateway_summary is not None:
        control["gateway"] = gateway_summary
    if budget_proxy is not None:
        control["model_call_budget"] = budget_proxy.public_summary()
    if provider_recorder is not None and provider_recorder_info is not None:
        control["provider_request_evidence"] = provider_recorder.public_summary()
    redaction = redact_output_secrets(output_root, tuple(secret_values))
    control["output_redaction"] = redaction
    assert_output_has_no_secrets(output_root, tuple(secret_values))
    return control


def _read_request(text: str) -> dict:
    try:
        request = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("container request is not valid JSON") from exc
    if not isinstance(request, dict) or request.get("schema_version") != (
        "harnessbench_container_request_v1"
    ):
        raise ValueError("container request schema is invalid")
    if request.get("harness") not in SUPPORTED_CONTAINER_HARNESSES:
        raise ValueError("container request harness is invalid")
    if not isinstance(request.get("prompt"), str) or not request["prompt"]:
        raise ValueError("container request prompt is empty")
    if not isinstance(request.get("model"), dict):
        raise ValueError("container request model is invalid")
    if not isinstance(
        request["model"].get("litellm_disable_web_search", False), bool
    ):
        raise ValueError("container request model compatibility is invalid")
    auth_mode = str(request.get("auth_mode") or "model_registry").replace("-", "_")
    if auth_mode not in {"model_registry", "subscription"}:
        raise ValueError("container request auth mode is invalid")
    if auth_mode == "subscription":
        if request.get("harness") not in {"codex", "claudecode"}:
            raise ValueError("container subscription auth harness is invalid")
        subscription_auth = request.get("subscription_auth")
        if not isinstance(subscription_auth, dict):
            raise ValueError("container request subscription auth is invalid")
    conversation = request.get("conversation")
    if conversation is not None:
        if not isinstance(conversation, dict) or conversation.get("mode") not in {
            "single_turn",
            "fixed_turns",
            "simulator",
        }:
            raise ValueError("container request conversation is invalid")
        if conversation.get("mode") == "fixed_turns" and (
            not isinstance(conversation.get("turns"), list)
            or not conversation["turns"]
            or any(
                not isinstance(value, str) or not value.strip()
                for value in conversation["turns"]
            )
        ):
            raise ValueError("container request fixed turns are invalid")
        session_boundaries = conversation.get("new_session_before_turns") or []
        if (
            not isinstance(session_boundaries, list)
            or any(type(value) is not int for value in session_boundaries)
            or (
                session_boundaries
                and conversation.get("mode") != "fixed_turns"
            )
            or any(
                value <= 1 or value > len(conversation.get("turns") or [])
                for value in session_boundaries
            )
            or session_boundaries != sorted(set(session_boundaries))
        ):
            raise ValueError("container request session boundaries are invalid")
        new_session_context = conversation.get("new_session_context") or "none"
        if new_session_context not in {"none", "replay"} or (
            new_session_context != "none" and not session_boundaries
        ):
            raise ValueError("container request new-session context is invalid")
        if conversation.get("mode") == "simulator" and (
            not isinstance(conversation.get("simulator_source"), str)
            or not conversation["simulator_source"].strip()
            or not isinstance(conversation.get("max_turns"), int)
            or conversation["max_turns"] < 1
        ):
            raise ValueError("container request simulator is invalid")
    task_tools = request.get("task_tools")
    if task_tools is not None and (
        not isinstance(task_tools, dict)
        or task_tools.get("schema_version") != "harnessbench_task_tools_v1"
        or not isinstance(task_tools.get("tools"), list)
        or not task_tools["tools"]
    ):
        raise ValueError("container request task tools are invalid")
    endpoint = request.get("task_tool_endpoint")
    if (task_tools is None) != (endpoint is None):
        raise ValueError(
            "container request task tools and endpoint must be configured together"
        )
    if endpoint is not None:
        if not isinstance(endpoint, dict):
            raise ValueError("container request task tool endpoint is invalid")
        _validate_endpoint(endpoint)
    business_endpoint = request.get("business_simulator_endpoint")
    if business_endpoint is not None:
        if not isinstance(business_endpoint, dict):
            raise ValueError(
                "container request business simulator endpoint is invalid"
            )
        validate_business_endpoint(business_endpoint)
    model_call_budget = request.get("model_call_budget")
    if model_call_budget is not None:
        if not isinstance(model_call_budget, dict):
            raise ValueError("container request model-call budget is invalid")
        validate_model_call_budget(model_call_budget)
    return request


def _arg_value(arguments: Mapping[str, str], canonical_key: str) -> str | None:
    for key, value in arguments.items():
        if key.strip().lower().lstrip("-").replace("_", "-") == canonical_key:
            return value
    return None


def _model_from_request(request: Mapping[str, object]) -> ModelRuntimeSpec:
    raw = request["model"]
    assert isinstance(raw, dict)
    auth_mode = str(request.get("auth_mode") or "model_registry").replace("-", "_")
    api_key = os.environ.get(INTERNAL_API_KEY_ENV, "")
    if auth_mode == "model_registry" and not api_key:
        raise ValueError(
            f"container model credential is missing: {INTERNAL_API_KEY_ENV}"
        )
    return ModelRuntimeSpec(
        alias=str(raw.get("alias") or ""),
        provider=str(raw.get("provider") or ""),
        model=str(raw.get("model") or ""),
        base_url=str(raw.get("base_url") or ""),
        api_mode=str(raw.get("api_mode") or ""),
        harness=str(request["harness"]),
        harness_model=str(raw.get("harness_model") or ""),
        harness_provider=(
            str(raw["harness_provider"])
            if isinstance(raw.get("harness_provider"), str)
            else None
        ),
        api_key=api_key,
        deepseek_reasoning_effort=str(
            raw.get("deepseek_reasoning_effort") or "max"
        ),
        litellm_disable_web_search=(
            raw.get("litellm_disable_web_search") is True
        ),
    )


def _write_private_json(path: Path, payload: dict) -> None:
    _write_private_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _write_private_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _provider_base_path(provider_base_url: str) -> str:
    path = urlsplit(provider_base_url).path.rstrip("/")
    return "" if path == "/" else path


def _require_gateway_connection(
    gateway_base_url: str | None,
    gateway_token: str | None,
    *,
    expected_port: int = GATEWAY_PORT,
) -> tuple[str, str]:
    expected_base_url = f"http://{GATEWAY_HOST}:{expected_port}"
    normalized_base_url = (gateway_base_url or "").rstrip("/")
    if normalized_base_url != expected_base_url or not gateway_token:
        raise ValueError("gateway connection is missing or invalid")
    return normalized_base_url, gateway_token


def _require_local_gateway_url(
    gateway_base_url: str | None,
    *,
    expected_port: int,
) -> str:
    expected_base_url = f"http://{GATEWAY_HOST}:{expected_port}"
    normalized_base_url = (gateway_base_url or "").rstrip("/")
    if normalized_base_url != expected_base_url:
        raise ValueError("gateway connection is missing or invalid")
    return normalized_base_url


def _require_gateway_token(gateway_token: str | None) -> str:
    if not isinstance(gateway_token, str) or not gateway_token:
        raise ValueError("gateway token is missing")
    return gateway_token


def _write_failure_trace(message: str, *, output_root: Path = OUTPUT_ROOT) -> Path:
    trace_dir = output_root / "trace" / f"run-worker-error-{uuid.uuid4().hex[:8]}"
    trace_dir.mkdir(parents=True, exist_ok=False)
    events_path = trace_dir / "events.json"
    summary_path = trace_dir / "summary.json"
    events_path.write_text(
        json.dumps(
            [
                {
                    "seq": 1,
                    "event_type": "run_failed",
                    "error_type": "container_worker_error",
                    "error_message": message,
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "trace_complete": False,
                "exit_code": None,
                "error_type": "container_worker_error",
                "error_message": message,
                "events_path": str(events_path),
                "summary_path": str(summary_path),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_root / "harness.stdout.log").touch()
    (output_root / "harness.stderr.log").write_text(message + "\n", encoding="utf-8")
    return summary_path.relative_to(output_root)


if __name__ == "__main__":
    raise SystemExit(main())
