"""Runner-owned MCP bridge for task-native business tools.

The bridge is intentionally task agnostic. A validated tool manifest and a
runner-owned transport endpoint are provided through the environment. The same
bridge and logical tool schema are used by every supported Harness.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Any, Mapping


CONFIG_ENV = "HARNESSBENCH_TASK_TOOLS"
SOCKET_ENV = "HARNESSBENCH_TASK_TOOL_SOCKET"
ENDPOINT_ENV = "HARNESSBENCH_TASK_TOOL_ENDPOINT"
ENDPOINT_SCHEMA = "harnessbench_task_tool_endpoint_v1"
CONTAINER_HOST_ALIAS = "harnessbench-host"
BRIDGE_RELATIVE_PATH = Path(".harnessbench") / "task_tool_mcp.py"
SERVER_NAME = "harnessbench"
MAX_SIMULATOR_RESPONSE_BYTES = 64 * 1024 * 1024


def install_task_tool_bridge(workspace_dir: Path) -> Path:
    """Install the generic bridge in the synthetic workspace."""

    destination = workspace_dir / BRIDGE_RELATIVE_PATH
    if destination.exists():
        raise ValueError(f"task tool bridge would overwrite workspace file: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__), destination)
    destination.chmod(0o755)
    return destination


def task_tool_environment(
    tools: tuple[dict[str, Any], ...],
    *,
    socket_path: Path | None = None,
    endpoint: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Return the public schema and private transport binding for one run."""

    if (socket_path is None) == (endpoint is None):
        raise ValueError("exactly one task tool transport binding is required")
    environment = {
        CONFIG_ENV: json.dumps(
            {
                "schema_version": "harnessbench_task_tools_v1",
                "tools": list(tools),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }
    if endpoint is not None:
        environment[ENDPOINT_ENV] = json.dumps(
            _validate_endpoint(dict(endpoint)),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        assert socket_path is not None
        environment[SOCKET_ENV] = str(socket_path)
    return environment


def _validate_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    if endpoint.get("schema_version") != ENDPOINT_SCHEMA:
        raise ValueError("task tool endpoint has an unsupported schema")
    transport = endpoint.get("transport")
    if transport == "unix":
        if set(endpoint) != {"schema_version", "transport", "path"}:
            raise ValueError("Unix task tool endpoint has invalid fields")
        if not isinstance(endpoint.get("path"), str) or not endpoint["path"]:
            raise ValueError("Unix task tool endpoint path is invalid")
        return endpoint
    if transport == "tcp":
        required = {
            "schema_version",
            "transport",
            "host",
            "port",
            "auth_token",
            "task_id",
            "run_id",
        }
        if set(endpoint) != required:
            raise ValueError("TCP task tool endpoint has invalid fields")
        if not isinstance(endpoint.get("host"), str) or not endpoint["host"]:
            raise ValueError("TCP task tool endpoint host is invalid")
        if (
            type(endpoint.get("port")) is not int
            or not 1 <= endpoint["port"] <= 65535
        ):
            raise ValueError("TCP task tool endpoint port is invalid")
        for field in ("auth_token", "task_id", "run_id"):
            if not isinstance(endpoint.get(field), str) or not endpoint[field]:
                raise ValueError(f"TCP task tool endpoint {field} is invalid")
        return endpoint
    raise ValueError("task tool endpoint transport is unsupported")


def _load_endpoint() -> dict[str, Any]:
    raw_endpoint = os.environ.get(ENDPOINT_ENV, "")
    if raw_endpoint:
        try:
            endpoint = json.loads(raw_endpoint)
        except json.JSONDecodeError as exc:
            raise RuntimeError("task tool endpoint is invalid") from exc
        if not isinstance(endpoint, dict):
            raise RuntimeError("task tool endpoint is invalid")
        try:
            return _validate_endpoint(endpoint)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    socket_value = os.environ.get(SOCKET_ENV, "")
    if not socket_value:
        raise RuntimeError("task tool transport is unavailable")
    return {
        "schema_version": ENDPOINT_SCHEMA,
        "transport": "unix",
        "path": socket_value,
    }


def _authorized_request(
    endpoint: Mapping[str, Any], request: dict[str, Any]
) -> dict[str, Any]:
    outbound = dict(request)
    if endpoint.get("transport") == "tcp":
        outbound["_harnessbench_transport"] = {
            "auth_token": endpoint["auth_token"],
            "task_id": endpoint["task_id"],
            "run_id": endpoint["run_id"],
        }
    return outbound


def _exchange(endpoint: Mapping[str, Any], request: dict[str, Any]) -> Any:
    encoded = json.dumps(
        _authorized_request(endpoint, request),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    if len(encoded) > 1024 * 1024:
        raise RuntimeError("task tool request exceeds the maximum size")
    if endpoint.get("transport") == "tcp":
        connection = socket.create_connection(
            (str(endpoint["host"]), int(endpoint["port"])),
            timeout=10,
        )
        restore_cwd = None
    else:
        socket_path = Path(str(endpoint["path"]))
        previous_fd = os.open(".", os.O_RDONLY)
        restore_cwd = previous_fd
        try:
            if socket_path.is_absolute():
                os.chdir(socket_path.parent)
                connect_path = socket_path.name
            else:
                connect_path = str(socket_path)
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(10)
            connection.connect(connect_path)
        except Exception:
            os.fchdir(previous_fd)
            os.close(previous_fd)
            raise
    try:
        connection.settimeout(300)
        connection.sendall(encoded)
        with connection.makefile("rb") as stream:
            response = stream.readline(MAX_SIMULATOR_RESPONSE_BYTES + 1)
    finally:
        connection.close()
        if restore_cwd is not None:
            os.fchdir(restore_cwd)
            os.close(restore_cwd)
    if not response:
        raise RuntimeError("task tool transport returned no response")
    if len(response) > MAX_SIMULATOR_RESPONSE_BYTES:
        raise RuntimeError("task tool response exceeds the maximum size")
    try:
        return json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("task tool transport returned an invalid response") from exc


def probe_task_tool_endpoint(endpoint: Mapping[str, Any]) -> None:
    """Fail before model execution when the runner-owned backend is unreachable."""

    response = _exchange(
        _validate_endpoint(dict(endpoint)),
        {"_harnessbench_health": True},
    )
    if not isinstance(response, dict) or response.get("ok") is not True:
        raise RuntimeError("task tool transport health check failed")
    if endpoint.get("transport") == "tcp" and response.get("task_id") != endpoint.get(
        "task_id"
    ):
        raise RuntimeError("task tool transport health check binding failed")


def _load_configuration() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    raw = os.environ.get(CONFIG_ENV, "")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("task tool configuration is invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "harnessbench_task_tools_v1":
        raise RuntimeError("task tool configuration has an unsupported schema")
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        raise RuntimeError("task tool configuration contains no tools")
    normalized: list[dict[str, Any]] = []
    indexed: dict[str, dict[str, Any]] = {}
    for item in tools:
        if not isinstance(item, dict):
            raise RuntimeError("task tool definition is invalid")
        name = item.get("name")
        description = item.get("description")
        input_schema = item.get("inputSchema")
        if (
            not isinstance(name, str)
            or not isinstance(description, str)
            or not isinstance(input_schema, dict)
            or name in indexed
        ):
            raise RuntimeError("task tool definition is invalid")
        tool = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        normalized.append(tool)
        indexed[name] = tool
    return normalized, indexed


def _call_simulator(name: str, arguments: dict[str, Any]) -> Any:
    return _exchange(
        _load_endpoint(),
        {
            # ``argv`` preserves compatibility with the existing generic
            # business-simulator transport validator. Task-native backends
            # consume the structured tool name and arguments instead.
            "argv": [],
            "tool": name,
            "arguments": arguments,
        },
    )


def _content_blocks(response: Any) -> tuple[list[dict[str, Any]], bool]:
    is_error = isinstance(response, dict) and response.get("ok") is False
    if isinstance(response, dict) and "_task_tool_content" in response:
        supplied = response.get("_task_tool_content")
        if isinstance(supplied, str):
            return [{"type": "text", "text": supplied}], is_error
        if isinstance(supplied, list) and all(
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
            for item in supplied
        ):
            return supplied, is_error
        raise RuntimeError("task tool returned invalid MCP content")
    if isinstance(response, str):
        text = response
    elif isinstance(response, dict) and isinstance(response.get("text"), str):
        text = response["text"]
    else:
        text = json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True)
    return [{"type": "text", "text": text}], is_error


def _result(identifier: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {"code": code, "message": message},
    }


def _dispatch(
    request: dict[str, Any],
    tools: list[dict[str, Any]],
    indexed: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    identifier = request.get("id")
    method = request.get("method")
    if method == "initialize":
        params = request.get("params")
        requested_version = (
            params.get("protocolVersion") if isinstance(params, dict) else None
        )
        protocol_version = (
            requested_version
            if isinstance(requested_version, str) and requested_version
            else "2024-11-05"
        )
        return _result(
            identifier,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "TraceTrial_Bench Task Tools", "version": "1.0"},
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/list":
        return _result(identifier, {"tools": tools})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return _error(identifier, -32602, "Invalid tool call parameters")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or name not in indexed:
            return _error(identifier, -32602, "Unknown task tool")
        if not isinstance(arguments, dict):
            return _error(identifier, -32602, "Tool arguments must be an object")
        try:
            response = _call_simulator(name, arguments)
            content, is_error = _content_blocks(response)
            return _result(identifier, {"content": content, "isError": is_error})
        except Exception as exc:
            error_payload = json.dumps(
                {
                    "harnessbench_task_tool_error": True,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return _result(
                identifier,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": error_payload,
                        }
                    ],
                    "isError": True,
                },
            )
    if identifier is None:
        return None
    return _error(identifier, -32601, "Method not found")


def main() -> int:
    try:
        tools, indexed = _load_configuration()
    except Exception as exc:
        print(f"TraceTrial_Bench task tool startup failed: {exc}", file=sys.stderr)
        return 2
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = _dispatch(request, tools, indexed)
        except Exception as exc:
            response = _error(None, -32700, f"Invalid JSON-RPC request: {exc}")
        if response is not None:
            encoded = json.dumps(
                response,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8") + b"\n"
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
