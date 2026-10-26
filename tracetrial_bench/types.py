from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VerifierSpec:
    entrypoint: str
    pass_threshold: float | None = None


@dataclass(frozen=True)
class ConversationProtocolSpec:
    version: str
    specification_path: Path
    episodes_path: Path
    manifest_path: Path | None
    normalized_evidence_path: Path


@dataclass(frozen=True)
class ConversationSpec:
    """Runner-owned user-input policy for ordinary harness executions."""

    mode: str
    turns_path: Path | None = None
    simulator_entrypoint: Path | None = None
    max_turns: int | None = None
    # Explicit, opt-in native session boundaries for fixed-turn tasks.  This is
    # deliberately separate from harness restart and context compaction.
    new_session_before_turns: tuple[int, ...] = ()
    # Optional runner-owned transcript handoff into each configured new
    # session.  ``none`` preserves the existing isolated-session behavior;
    # ``replay`` supplies only prior user/assistant-visible messages.
    new_session_context: str = "none"


@dataclass(frozen=True)
class BusinessSimulatorSpec:
    """Runner-private stateful service exposed through an agent-side CLI."""

    version: str
    scenario_path: Path
    server_path: Path
    cli_path: Path


@dataclass(frozen=True)
class TaskToolsSpec:
    """Runner-owned native tools backed by the private business simulator."""

    version: str
    manifest_path: Path
    tools: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class ExecutionProbeSpec:
    """Opt-in runner capabilities required to produce valid task evidence."""

    version: str
    requires: tuple[str, ...]


@dataclass(frozen=True)
class ModelCallBudgetSpec:
    """Opt-in provider-request budget for one Docker task run."""

    version: str
    limit: int
    finalization_reserve: int


@dataclass(frozen=True)
class ContainerResourcesSpec:
    """Optional per-task Docker resource overrides.

    Omitted fields continue to use the runner's global defaults.  The versioned
    shape leaves room for future CPU or PID overrides without changing existing
    task manifests.
    """

    version: str
    memory_bytes: int | None = None


@dataclass(frozen=True)
class SecurityProbeSpec:
    """Opt-in approval/path-policy contract and its private configuration."""

    version: str
    requires: tuple[str, ...]
    profile_path: Path
    profile: dict[str, Any]


@dataclass(frozen=True)
class NativeInstructionsSpec:
    """Runner-installed, harness-native scoped instruction files."""

    version: str
    manifest_path: Path


@dataclass(frozen=True)
class LayeredScoringSpec:
    """Runner-owned 100-point scoring configuration for one task."""

    version: str
    harness_score_path: Path
    agent_score_path: Path


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_dir: Path
    prompt: str
    workspace_dir: Path
    verifier: VerifierSpec
    metadata: dict[str, Any] = field(default_factory=dict)
    max_turns: int | None = None
    max_tool_calls: int | None = None
    max_wall_time_sec: int | None = None
    conversation: ConversationSpec = field(
        default_factory=lambda: ConversationSpec(mode="single_turn")
    )
    conversation_protocol: ConversationProtocolSpec | None = None
    business_simulator: BusinessSimulatorSpec | None = None
    task_tools: TaskToolsSpec | None = None
    execution_probe: ExecutionProbeSpec | None = None
    model_call_budget: ModelCallBudgetSpec | None = None
    container_resources: ContainerResourcesSpec | None = None
    security_probe: SecurityProbeSpec | None = None
    native_instructions: NativeInstructionsSpec | None = None
    scoring: LayeredScoringSpec | None = None


@dataclass(frozen=True)
class ModelBinding:
    requested: str
    provider: str | None
    canonical: str
    harness_arg: str
    binding_status: str
    binding_mode: str
    flag: str | None
    validation_status: str
    validation_message: str | None = None
    observed: str | None = None


@dataclass(frozen=True)
class HarnessRunSpec:
    harness: str
    model: str
    mode: str
    harness_path: str | None = None
    timeout: int | None = None
    docker_image: str | None = None
    extra_args: dict[str, str] = field(default_factory=dict)
    provider_source: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    provider_artifacts: dict[str, str] = field(default_factory=dict)
    harness_isolation: str = "none"


@dataclass(frozen=True)
class EvaluationResult:
    status: str
    score: float | None
    exit_code: int | None
    stdout_path: Path
    stderr_path: Path
    score_path: Path | None
    timed_out: bool = False
    error_message: str | None = None
