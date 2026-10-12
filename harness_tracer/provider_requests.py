from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


PROVIDER_REQUEST_EVIDENCE_SCHEMA = "harnessbench_provider_request_evidence_v1"
PROVIDER_REQUEST_EVIDENCE_VERSION = "model_visible_tools_v1"


class ProviderRequestEvidenceError(RuntimeError):
    """Raised when runner-owned provider-request evidence is malformed."""


def extract_structured_tool_inventory(request_body: bytes | None) -> dict[str, Any]:
    """Extract structured tool declarations without retaining the request body.

    A missing declaration field is deliberately *not* treated as a complete empty
    inventory. Some harnesses encode tool protocols in prompt text, so absence can
    only be inferred from an explicit, well-formed declaration channel.
    """

    try:
        payload = json.loads((request_body or b"").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        payload = None

    valid_body = isinstance(payload, dict)
    channels: list[str] = []
    raw_declarations: list[object] = []
    invalid_channel = False
    if valid_body:
        assert isinstance(payload, dict)
        for field in ("tools", "functions"):
            if field not in payload:
                continue
            channels.append(field)
            value = payload.get(field)
            if isinstance(value, list):
                raw_declarations.extend(value)
            else:
                invalid_channel = True

    tools: list[dict[str, Any]] = []
    invalid_tool_count = 0
    for raw in raw_declarations:
        normalized = _normalize_tool(raw)
        if normalized is None:
            invalid_tool_count += 1
        else:
            tools.append(normalized)

    complete = (
        valid_body
        and bool(channels)
        and not invalid_channel
        and invalid_tool_count == 0
    )
    return {
        "scope": "structured_model_tools",
        "request_body_valid": valid_body,
        "declaration_channels": channels,
        "declaration_field_present": bool(channels),
        "complete": complete,
        "tool_count": len(tools),
        "invalid_tool_count": invalid_tool_count,
        "tools": tools,
    }


def provider_request_cursor(path: Path | None) -> int:
    if path is None:
        return 0
    rows = read_provider_request_events(path)
    return int(rows[-1]["seq"]) if rows else 0


def trace_tool_inventory_payloads(
    path: Path | None,
    *,
    after_seq: int,
) -> list[dict[str, Any]]:
    """Return deduplicated trace payloads for requests after ``after_seq``."""

    if path is None:
        return []
    rows = [
        row
        for row in read_provider_request_events(path)
        if int(row["seq"]) > after_seq
    ]
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        inventory = row["tool_inventory"]
        semantic = {
            "model_visible": row["model_visible"],
            "scope": inventory["scope"],
            "request_body_valid": inventory["request_body_valid"],
            "declaration_channels": inventory["declaration_channels"],
            "declaration_field_present": inventory["declaration_field_present"],
            "complete": inventory["complete"],
            "tool_count": inventory["tool_count"],
            "invalid_tool_count": inventory["invalid_tool_count"],
            "tools": inventory["tools"],
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                semantic,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint not in grouped:
            grouped[fingerprint] = {
                "source": "provider_request",
                "source_schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
                "source_version": PROVIDER_REQUEST_EVIDENCE_VERSION,
                "inventory_fingerprint": fingerprint,
                **semantic,
                "request_count": 0,
                "provider_request_seqs": [],
                "provider_request_paths": [],
                "first_request_timestamp": row["timestamp"],
                "last_request_timestamp": row["timestamp"],
                "upstream_statuses": [],
            }
            order.append(fingerprint)
        group = grouped[fingerprint]
        group["request_count"] += 1
        group["provider_request_seqs"].append(row["seq"])
        if row["path"] not in group["provider_request_paths"]:
            group["provider_request_paths"].append(row["path"])
        group["last_request_timestamp"] = row["timestamp"]
        status = row.get("upstream_status")
        if status not in group["upstream_statuses"]:
            group["upstream_statuses"].append(status)
    return [grouped[fingerprint] for fingerprint in order]


def read_provider_request_events(path: Path) -> list[dict[str, Any]]:
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
    except FileNotFoundError as exc:
        raise ProviderRequestEvidenceError(
            "provider-request evidence file is missing"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderRequestEvidenceError(
            "provider-request evidence is unreadable or invalid JSON"
        ) from exc
    if any(not isinstance(row, dict) for row in rows):
        raise ProviderRequestEvidenceError(
            "provider-request evidence rows must be objects"
        )
    expected_sequences = list(range(1, len(rows) + 1))
    if [row.get("seq") for row in rows] != expected_sequences:
        raise ProviderRequestEvidenceError(
            "provider-request evidence sequence is not contiguous"
        )
    for row in rows:
        _validate_event(row)
    return rows


def _normalize_tool(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    nested = raw.get("function")
    core = nested if isinstance(nested, Mapping) else raw
    name = core.get("name") or core.get("tool_name")
    tool_type = raw.get("type")
    if (
        (not isinstance(name, str) or not name.strip())
        and isinstance(tool_type, str)
        and tool_type
        and tool_type != "function"
    ):
        # Responses-style provider-native tools (for example web_search)
        # identify the capability by type rather than a separate name.
        name = tool_type
    if not isinstance(name, str) or not name.strip():
        return None
    description = core.get("description")
    schema = None
    for key in ("parameters", "input_schema", "inputSchema", "schema"):
        if key in core:
            schema = core.get(key)
            break
    return {
        "name": name,
        "type": tool_type if isinstance(tool_type, str) else "function",
        "description": description if isinstance(description, str) else "",
        "input_schema": schema,
    }


def _validate_event(row: dict[str, Any]) -> None:
    upstream_status = row.get("upstream_status")
    if (
        row.get("schema_version") != PROVIDER_REQUEST_EVIDENCE_SCHEMA
        or row.get("version") != PROVIDER_REQUEST_EVIDENCE_VERSION
        or row.get("method") != "POST"
        or not isinstance(row.get("timestamp"), str)
        or not row["timestamp"]
        or not isinstance(row.get("path"), str)
        or not row["path"].startswith("/")
        or type(row.get("model_visible")) is not bool
        or (
            upstream_status is not None
            and type(upstream_status) is not int
        )
        or row.get("model_visible")
        != (
            isinstance(upstream_status, int)
            and 200 <= upstream_status < 300
        )
    ):
        raise ProviderRequestEvidenceError(
            "provider-request evidence row metadata is invalid"
        )
    inventory = row.get("tool_inventory")
    if not isinstance(inventory, dict):
        raise ProviderRequestEvidenceError(
            "provider-request tool inventory is missing"
        )
    required = {
        "scope",
        "request_body_valid",
        "declaration_channels",
        "declaration_field_present",
        "complete",
        "tool_count",
        "invalid_tool_count",
        "tools",
    }
    if set(inventory) != required:
        raise ProviderRequestEvidenceError(
            "provider-request tool inventory fields are invalid"
        )
    tools = inventory.get("tools")
    channels = inventory.get("declaration_channels")
    if (
        inventory.get("scope") != "structured_model_tools"
        or type(inventory.get("request_body_valid")) is not bool
        or type(inventory.get("declaration_field_present")) is not bool
        or type(inventory.get("complete")) is not bool
        or type(inventory.get("tool_count")) is not int
        or type(inventory.get("invalid_tool_count")) is not int
        or not isinstance(channels, list)
        or any(channel not in {"tools", "functions"} for channel in channels)
        or len(set(channels)) != len(channels)
        or not isinstance(tools, list)
        or any(not isinstance(tool, dict) for tool in tools)
        or inventory["tool_count"] != len(tools)
        or inventory["invalid_tool_count"] < 0
    ):
        raise ProviderRequestEvidenceError(
            "provider-request tool inventory values are invalid"
        )
    if inventory["declaration_field_present"] != bool(channels):
        raise ProviderRequestEvidenceError(
            "provider-request declaration-channel metadata is inconsistent"
        )
    if inventory["complete"] and (
        inventory["request_body_valid"] is not True
        or not channels
        or inventory["invalid_tool_count"] != 0
    ):
        raise ProviderRequestEvidenceError(
            "provider-request completeness metadata is inconsistent"
        )
    for tool in tools:
        if (
            set(tool) != {"name", "type", "description", "input_schema"}
            or not isinstance(tool.get("name"), str)
            or not tool["name"]
            or not isinstance(tool.get("type"), str)
            or not isinstance(tool.get("description"), str)
        ):
            raise ProviderRequestEvidenceError(
                "provider-request normalized tool is invalid"
            )
