from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from harnessbench.types import (
    BusinessSimulatorSpec,
    ContainerResourcesSpec,
    ConversationSpec,
    ConversationProtocolSpec,
    ExecutionProbeSpec,
    LayeredScoringSpec,
    ModelCallBudgetSpec,
    NativeInstructionsSpec,
    SecurityProbeSpec,
    TaskToolsSpec,
    TaskSpec,
    VerifierSpec,
)


def resolve_task(task_path: Path) -> TaskSpec:
    task_dir = task_path.resolve()
    if task_dir.is_file():
        task_dir = task_dir.parent

    task_yaml = task_dir / "task.yaml"
    prompt_md = task_dir / "prompt.md"
    workspace_dir = task_dir / "workspace"

    missing = [
        path for path in (task_yaml, prompt_md, workspace_dir) if not path.exists()
    ]
    if missing:
        joined = ", ".join(str(path) for path in missing)
        raise ValueError(f"task is missing required files: {joined}")

    metadata = _parse_simple_yaml(task_yaml.read_text())
    task_id = _string(metadata.get("id")) or task_dir.name
    verifier_data = (
        metadata.get("verifier") if isinstance(metadata.get("verifier"), dict) else {}
    )
    entrypoint = _string(verifier_data.get("entrypoint")) or "verifier/run.sh"
    pass_threshold = _float_or_none(verifier_data.get("pass_threshold"))
    budget = metadata.get("budget") if isinstance(metadata.get("budget"), dict) else {}
    max_turns = _positive_int_or_none(budget.get("max_turns"), "budget.max_turns")
    max_tool_calls = _positive_int_or_none(
        budget.get("max_tool_calls"),
        "budget.max_tool_calls",
    )
    max_wall_time_sec = _int_or_none(budget.get("max_wall_time_sec"))
    conversation = _conversation(task_dir, metadata, max_turns=max_turns)
    conversation_protocol = _conversation_protocol(task_dir, metadata)
    business_simulator = _business_simulator(task_dir, metadata)
    task_tools = _task_tools(task_dir, metadata, business_simulator)
    execution_probe = _execution_probe(metadata)
    model_call_budget = _model_call_budget(metadata)
    container_resources = _container_resources(metadata)
    security_probe = _security_probe(task_dir, metadata)
    native_instructions = _native_instructions(task_dir, metadata)
    scoring = _scoring(task_dir, metadata)

    return TaskSpec(
        task_id=task_id,
        task_dir=task_dir,
        prompt=prompt_md.read_text(),
        workspace_dir=workspace_dir,
        verifier=VerifierSpec(entrypoint=entrypoint, pass_threshold=pass_threshold),
        metadata=metadata,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        max_wall_time_sec=max_wall_time_sec,
        conversation=conversation,
        conversation_protocol=conversation_protocol,
        business_simulator=business_simulator,
        task_tools=task_tools,
        execution_probe=execution_probe,
        model_call_budget=model_call_budget,
        container_resources=container_resources,
        security_probe=security_probe,
        native_instructions=native_instructions,
        scoring=scoring,
    )


def _model_call_budget(metadata: dict[str, Any]) -> ModelCallBudgetSpec | None:
    raw = metadata.get("model_call_budget")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("model_call_budget must be a mapping")
    version = _string(raw.get("version"))
    if version != "provider_requests_v1":
        raise ValueError(
            "model_call_budget.version must be provider_requests_v1"
        )
    limit = _positive_int_or_none(raw.get("limit"), "model_call_budget.limit")
    reserve = _positive_int_or_none(
        raw.get("finalization_reserve"),
        "model_call_budget.finalization_reserve",
    )
    if limit is None or reserve is None:
        raise ValueError("model_call_budget requires limit and finalization_reserve")
    if reserve >= limit:
        raise ValueError(
            "model_call_budget.finalization_reserve must be smaller than limit"
        )
    return ModelCallBudgetSpec(
        version=version,
        limit=limit,
        finalization_reserve=reserve,
    )


def _container_resources(metadata: dict[str, Any]) -> ContainerResourcesSpec | None:
    raw = metadata.get("container_resources")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("container_resources must be a mapping")
    version = _string(raw.get("version"))
    if version != "container_limits_v1":
        raise ValueError(
            "container_resources.version must be container_limits_v1"
        )
    memory_bytes = _positive_int_or_none(
        raw.get("memory_bytes"),
        "container_resources.memory_bytes",
    )
    if memory_bytes is None:
        raise ValueError("container_resources requires memory_bytes")
    maximum = 64 * 1024 * 1024 * 1024
    if memory_bytes > maximum:
        raise ValueError(
            "container_resources.memory_bytes must not exceed 68719476736"
        )
    return ContainerResourcesSpec(
        version=version,
        memory_bytes=memory_bytes,
    )


def _conversation(
    task_dir: Path,
    metadata: dict[str, Any],
    *,
    max_turns: int | None,
) -> ConversationSpec:
    """Resolve the ordinary single/fixed/simulator conversation mode.

    The execution profile remains JSON because existing suite tasks already
    declare ``runtime/execution.json`` there.  A missing declaration is the
    legacy single-turn behavior.
    """
    execution = metadata.get("execution")
    profile_value = (
        _string(execution.get("profile")) if isinstance(execution, dict) else None
    )
    profile_path = (
        _task_child(task_dir, profile_value, must_exist=True)
        if profile_value
        else task_dir / "runtime" / "execution.json"
    )
    if not profile_path.is_file():
        return ConversationSpec(mode="single_turn", max_turns=1)
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid execution profile: {profile_path}") from exc
    if not isinstance(profile, dict):
        raise ValueError(f"execution profile must be a JSON object: {profile_path}")

    raw_mode = profile.get("conversation", "single_turn")
    inline: dict[str, Any] = {}
    if isinstance(raw_mode, dict):
        inline = raw_mode
        raw_mode = inline.get("mode", "single_turn")
    mode = str(raw_mode).strip().lower().replace("-", "_")
    aliases = {
        "single": "single_turn",
        "single_turn": "single_turn",
        "fixed": "fixed_turns",
        "fixed_turns": "fixed_turns",
        "multi_turn": "fixed_turns",
        "simulator": "simulator",
    }
    normalized = aliases.get(mode)
    if normalized is None:
        raise ValueError(f"unsupported conversation mode {raw_mode!r}: {profile_path}")
    if normalized == "single_turn":
        return ConversationSpec(mode=normalized, max_turns=1)

    if normalized == "fixed_turns":
        configured = inline.get("turns") or profile.get("turns") or "turns.json"
        turns_path = _task_child(task_dir, str(configured), must_exist=True)
        turns = _read_fixed_turns(turns_path, max_turns=max_turns)
        new_session_before_turns = _new_session_before_turns(
            inline.get("new_session_before_turns"),
            turn_count=len(turns),
        )
        new_session_context = _new_session_context(
            inline.get("new_session_context"),
            boundaries=new_session_before_turns,
        )
        return ConversationSpec(
            mode=normalized,
            turns_path=turns_path,
            max_turns=max_turns,
            new_session_before_turns=new_session_before_turns,
            new_session_context=new_session_context,
        )

    simulator = profile.get("conversation_simulator")
    if not isinstance(simulator, dict):
        simulator = profile.get("simulator")
    config = {**(simulator if isinstance(simulator, dict) else {}), **inline}
    entrypoint = _string(config.get("entrypoint"))
    if not entrypoint:
        raise ValueError(
            f"simulator conversation requires conversation_simulator.entrypoint: {profile_path}"
        )
    simulator_path = _simulator_source(task_dir, entrypoint, must_exist=True)
    if not simulator_path.is_file():
        raise ValueError(f"conversation simulator is not a file: {simulator_path}")
    if simulator_path.suffix != ".py":
        raise ValueError("conversation simulator entrypoint must be a Python file")
    configured_max = _positive_int_or_none(
        config.get("max_turns"), "conversation_simulator.max_turns"
    )
    effective_max = configured_max or max_turns
    if effective_max is None:
        raise ValueError("simulator conversation requires a finite max_turns")
    if max_turns is not None and effective_max > max_turns:
        raise ValueError("conversation simulator max_turns exceeds budget.max_turns")
    return ConversationSpec(
        mode=normalized,
        simulator_entrypoint=simulator_path,
        max_turns=effective_max,
    )


def read_fixed_turns(spec: ConversationSpec) -> list[str]:
    if spec.mode != "fixed_turns" or spec.turns_path is None:
        raise ValueError("fixed turn messages require a fixed_turns conversation")
    return _read_fixed_turns(spec.turns_path, max_turns=spec.max_turns)


def _read_fixed_turns(path: Path, *, max_turns: int | None) -> list[str]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid fixed-turn specification: {path}") from exc
    values = raw.get("turns") if isinstance(raw, dict) else raw
    if not isinstance(values, list) or not values:
        raise ValueError(f"fixed-turn specification must contain a non-empty turns list: {path}")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"every fixed turn must be a non-empty string: {path}")
    if max_turns is not None and len(values) > max_turns:
        raise ValueError(
            f"fixed-turn specification has {len(values)} turns, exceeding budget {max_turns}"
        )
    return list(values)


def _new_session_before_turns(
    value: Any,
    *,
    turn_count: int,
) -> tuple[int, ...]:
    """Validate opt-in native session boundaries for an ordinary conversation."""

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("conversation.new_session_before_turns must be a list")
    if any(type(item) is not int for item in value):
        raise ValueError(
            "conversation.new_session_before_turns must contain only integers"
        )
    boundaries = tuple(value)
    if any(item <= 1 or item > turn_count for item in boundaries):
        raise ValueError(
            "conversation.new_session_before_turns entries must be between "
            f"2 and the fixed turn count ({turn_count})"
        )
    if tuple(sorted(set(boundaries))) != boundaries:
        raise ValueError(
            "conversation.new_session_before_turns must be unique and increasing"
        )
    return boundaries


def _new_session_context(
    value: Any,
    *,
    boundaries: tuple[int, ...],
) -> str:
    """Validate the opt-in model-visible handoff for new native sessions."""

    normalized = "none" if value is None else str(value).strip().lower()
    if normalized not in {"none", "replay"}:
        raise ValueError(
            "conversation.new_session_context must be 'none' or 'replay'"
        )
    if normalized != "none" and not boundaries:
        raise ValueError(
            "conversation.new_session_context requires new_session_before_turns"
        )
    return normalized


def _conversation_protocol(
    task_dir: Path,
    metadata: dict[str, Any],
) -> ConversationProtocolSpec | None:
    raw = metadata.get("conversation_protocol")
    if not isinstance(raw, dict):
        return None

    version = _string(raw.get("version"))
    specification = _string(raw.get("specification"))
    episodes = _string(raw.get("episodes"))
    evidence = _string(raw.get("normalized_evidence"))
    manifest = _string(raw.get("manifest"))
    if not version or not specification or not episodes or not evidence:
        raise ValueError(
            "conversation_protocol requires version, specification, episodes, "
            "and normalized_evidence"
        )

    specification_path = _task_child(task_dir, specification, must_exist=True)
    episodes_path = _task_child(task_dir, episodes, must_exist=True)
    manifest_path = (
        _task_child(task_dir, manifest, must_exist=True) if manifest else None
    )
    evidence_path = Path(evidence)
    if evidence_path.is_absolute() or ".." in evidence_path.parts:
        raise ValueError(
            "conversation_protocol normalized_evidence must be run-relative"
        )
    if not evidence_path.parts or evidence_path.parts[0] != "artifacts":
        raise ValueError("conversation_protocol evidence must live below artifacts/")

    return ConversationProtocolSpec(
        version=version,
        specification_path=specification_path,
        episodes_path=episodes_path,
        manifest_path=manifest_path,
        normalized_evidence_path=evidence_path,
    )


def _business_simulator(
    task_dir: Path,
    metadata: dict[str, Any],
) -> BusinessSimulatorSpec | None:
    raw = metadata.get("business_simulator")
    if not isinstance(raw, dict):
        return None

    version = _string(raw.get("version"))
    scenario = _string(raw.get("scenario"))
    server = _string(raw.get("server"))
    cli = _string(raw.get("cli"))
    if not version or not scenario or not server or not cli:
        raise ValueError(
            "business_simulator requires version, scenario, server, and cli"
        )
    if version != "unix_json_v1":
        raise ValueError(f"unsupported business_simulator version: {version}")

    scenario_path = _simulator_source(task_dir, scenario, must_exist=True)
    server_path = _simulator_source(task_dir, server, must_exist=True)
    if not scenario_path.is_file():
        raise ValueError(f"business_simulator scenario is not a file: {scenario_path}")
    if not server_path.is_file():
        raise ValueError(f"business_simulator server is not a file: {server_path}")

    cli_path = Path(cli)
    if cli_path.is_absolute() or ".." in cli_path.parts or not cli_path.parts:
        raise ValueError("business_simulator cli must be workspace-relative")
    if cli_path.parts[0] in {".", ""}:
        raise ValueError("business_simulator cli must be a normalized relative path")

    return BusinessSimulatorSpec(
        version=version,
        scenario_path=scenario_path,
        server_path=server_path,
        cli_path=cli_path,
    )


_TASK_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _task_tools(
    task_dir: Path,
    metadata: dict[str, Any],
    business_simulator: BusinessSimulatorSpec | None,
) -> TaskToolsSpec | None:
    raw = metadata.get("task_tools")
    if not isinstance(raw, dict):
        return None
    if business_simulator is None:
        raise ValueError("task_tools requires business_simulator")

    version = _string(raw.get("version"))
    manifest = _string(raw.get("manifest"))
    if version != "mcp_stdio_v1":
        raise ValueError(f"unsupported task_tools version: {version}")
    if not manifest:
        raise ValueError("task_tools requires a manifest")
    manifest_path = _simulator_source(task_dir, manifest, must_exist=True)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid task_tools manifest: {manifest_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "harnessbench_task_tools_v1":
        raise ValueError(
            "task_tools manifest must use schema_version harnessbench_task_tools_v1"
        )
    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise ValueError("task_tools manifest requires a non-empty tools list")

    tools: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(raw_tools):
        if not isinstance(item, dict):
            raise ValueError(f"task_tools manifest tool {index} must be an object")
        name = item.get("name")
        description = item.get("description")
        input_schema = item.get("inputSchema")
        if not isinstance(name, str) or not _TASK_TOOL_NAME.fullmatch(name):
            raise ValueError(f"invalid task_tools tool name at index {index}: {name!r}")
        if name in names:
            raise ValueError(f"duplicate task_tools tool name: {name}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"task_tools tool {name} requires a description")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            raise ValueError(
                f"task_tools tool {name} inputSchema must be an object schema"
            )
        unknown = set(item) - {"name", "description", "inputSchema"}
        if unknown:
            joined = ", ".join(sorted(unknown))
            raise ValueError(f"task_tools tool {name} has unsupported fields: {joined}")
        names.add(name)
        tools.append(
            {
                "name": name,
                "description": description.strip(),
                "inputSchema": input_schema,
            }
        )
    return TaskToolsSpec(
        version=version,
        manifest_path=manifest_path,
        tools=tuple(tools),
    )


def _execution_probe(metadata: dict[str, Any]) -> ExecutionProbeSpec | None:
    raw = metadata.get("execution_probe")
    if not isinstance(raw, dict):
        return None

    version = _string(raw.get("version"))
    requires = raw.get("requires")
    if version != "lifecycle_v1":
        raise ValueError(f"unsupported execution_probe version: {version}")
    if not isinstance(requires, list) or not requires:
        raise ValueError("execution_probe requires a non-empty requires list")
    if any(not isinstance(item, str) or not item.strip() for item in requires):
        raise ValueError("execution_probe requires only non-empty capability names")
    normalized = tuple(item.strip() for item in requires)
    if len(set(normalized)) != len(normalized):
        raise ValueError("execution_probe capability names must be unique")
    return ExecutionProbeSpec(version=version, requires=normalized)


def _security_probe(
    task_dir: Path,
    metadata: dict[str, Any],
) -> SecurityProbeSpec | None:
    raw = metadata.get("security_probe")
    if not isinstance(raw, dict):
        return None

    version = _string(raw.get("version"))
    profile = _string(raw.get("profile"))
    requires = raw.get("requires")
    if version != "security_v1":
        raise ValueError(f"unsupported security_probe version: {version}")
    if not profile:
        raise ValueError("security_probe requires a profile")
    if not isinstance(requires, list) or not requires:
        raise ValueError("security_probe requires a non-empty requires list")
    if any(not isinstance(item, str) or not item.strip() for item in requires):
        raise ValueError("security_probe requires only non-empty capability names")
    normalized = tuple(item.strip() for item in requires)
    if len(set(normalized)) != len(normalized):
        raise ValueError("security_probe capability names must be unique")

    profile_path = _task_child(task_dir, profile, must_exist=True)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid security_probe profile: {profile_path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "security_v1":
        raise ValueError("security_probe profile must use schema_version security_v1")
    kind = payload.get("kind")
    if kind not in {"approval", "managed_paths"}:
        raise ValueError(f"unsupported security_probe kind: {kind}")
    return SecurityProbeSpec(
        version=version,
        requires=normalized,
        profile_path=profile_path,
        profile=payload,
    )


def _native_instructions(
    task_dir: Path,
    metadata: dict[str, Any],
) -> NativeInstructionsSpec | None:
    raw = metadata.get("native_instructions")
    if not isinstance(raw, dict):
        return None
    version = _string(raw.get("version"))
    manifest = _string(raw.get("manifest"))
    if not version or not manifest:
        raise ValueError("native_instructions requires version and manifest")
    if version != "scoped_files_v1":
        raise ValueError(f"unsupported native_instructions version: {version}")
    manifest_path = _task_child(task_dir, manifest, must_exist=True)
    if not manifest_path.is_file():
        raise ValueError(
            f"native_instructions manifest is not a file: {manifest_path}"
        )
    return NativeInstructionsSpec(version=version, manifest_path=manifest_path)


def _scoring(
    task_dir: Path,
    metadata: dict[str, Any],
) -> LayeredScoringSpec | None:
    raw = metadata.get("scoring")
    if not isinstance(raw, dict):
        return None
    version = _string(raw.get("version"))
    harness_score = _string(raw.get("harness_score"))
    agent_score = _string(raw.get("agent_score"))
    if not version or not harness_score or not agent_score:
        raise ValueError(
            "scoring requires version, harness_score, and agent_score"
        )
    if version != "layered_100_v1":
        raise ValueError(f"unsupported scoring version: {version}")
    return LayeredScoringSpec(
        version=version,
        harness_score_path=_task_child(task_dir, harness_score, must_exist=True),
        agent_score_path=_task_child(task_dir, agent_score, must_exist=True),
    )


def _simulator_source(task_dir: Path, relative: str, *, must_exist: bool) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or path.parts[0] != "simulator":
        raise ValueError("business_simulator private inputs must live below simulator/")
    return _task_child(task_dir, relative, must_exist=must_exist)


def _task_child(task_dir: Path, relative: str, *, must_exist: bool) -> Path:
    candidate = (task_dir / relative).resolve()
    try:
        candidate.relative_to(task_dir)
    except ValueError as exc:
        raise ValueError(
            f"task-private path escapes task directory: {relative}"
        ) from exc
    if must_exist and not candidate.exists():
        raise ValueError(f"missing task-private input: {candidate}")
    return candidate


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by task.yaml without adding PyYAML."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    last_key_at_indent: dict[int, str] = {}

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if line.startswith("- "):
            # Lists are kept as raw strings in this lightweight parser; runner
            # only needs scalar fields and nested maps for v1.
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
            last_key_at_indent[indent] = key
        else:
            parent[key] = _parse_scalar(value)
            last_key_at_indent[indent] = key

    return root


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, Any] = {}
        for item in _split_flow_items(value[1:-1]):
            key, separator, raw_value = item.partition(":")
            if not separator or not key.strip():
                return value
            result[key.strip().strip("\"'")] = _parse_scalar(raw_value)
        return result
    if value.startswith("[") and value.endswith("]"):
        return [
            _parse_scalar(item)
            for item in _split_flow_items(value[1:-1])
        ]
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _split_flow_items(value: str) -> list[str]:
    """Split a flat YAML flow collection without splitting nested/quoted commas."""
    items: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(value):
        if quote is not None:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character in {"[", "{"}:
            depth += 1
        elif character in {"]", "}"}:
            depth -= 1
        elif character == "," and depth == 0:
            item = value[start:index].strip()
            if item:
                items.append(item)
            start = index + 1
    final = value[start:].strip()
    if final:
        items.append(final)
    return items


def _string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int_or_none(value: Any, field: str) -> int | None:
    if value is None:
        return None
    parsed = _int_or_none(value)
    if parsed is None or parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
