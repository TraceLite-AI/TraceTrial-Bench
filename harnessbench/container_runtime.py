from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import tarfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping

from harnessbench.business_simulator import (
    CONTAINER_HOST_ALIAS,
    validate_business_endpoint,
)
from harnessbench.cache_metrics import attach_cache_savings
from harnessbench.container_environment import (
    TRANSPORT_ENV_NAMES,
)
from harnessbench.litellm_gateway import GATEWAY_TOKEN_ENV
from harnessbench.model_call_budget import (
    MODEL_CALL_BUDGET_SCHEMA,
    validate_model_call_budget,
)
from harnessbench.model_registry import (
    INTERNAL_API_KEY_ENV,
    SUPPORTED_CONTAINER_HARNESSES,
    ContainerBuildConfig,
    ModelRuntimeSpec,
)
from harnessbench.provider_request_recorder import (
    validate_provider_request_summary,
)
from harnessbench.provider_usage import attach_provider_token_usage
from harnessbench.task_tool_mcp import _validate_endpoint
from harnessbench.workspace import validate_workspace_for_host_verifier
from harness_tracer.provider_requests import (
    PROVIDER_REQUEST_EVIDENCE_SCHEMA,
    PROVIDER_REQUEST_EVIDENCE_VERSION,
    ProviderRequestEvidenceError,
    read_provider_request_events,
)


DEFAULT_MEMORY_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_PIDS_LIMIT = 512
DEFAULT_CPUS = 2.0
MAX_EXPORTED_FILE_BYTES = 64 * 1024 * 1024
MAX_EXPORTED_TOTAL_BYTES = 256 * 1024 * 1024
MAX_EXPORTED_FILES = 10_000
MAX_ARCHIVE_BYTES = MAX_EXPORTED_TOTAL_BYTES + 32 * 1024 * 1024
PROVIDER_REQUEST_SALVAGE_RELATIVE_PATH = Path(
    "provider_requests_salvage/events.jsonl"
)
FULL_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
FULL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
TRACE_EVENTS_FILE = re.compile(
    r"^trace/run-[A-Za-z0-9_-]+/(?:turns/run-[A-Za-z0-9_-]+/)?events\.json$"
)
SAFE_RUN_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SAFE_PROVIDER_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_RUNTIME_GATEWAY_TOKEN_HARNESSES = frozenset(
    {"openclaw", "codex", "claudecode"}
)
_PROVIDER_REQUEST_EVENT_FIELDS = {
    "schema_version",
    "version",
    "seq",
    "timestamp",
    "method",
    "path",
    "upstream_status",
    "model_visible",
    "error_type",
    "tool_inventory",
}


class ContainerRuntimeError(RuntimeError):
    pass


class ContainerArchiveError(ContainerRuntimeError):
    pass


@dataclass(frozen=True)
class ContainerLimits:
    cpus: float = DEFAULT_CPUS
    memory_bytes: int = DEFAULT_MEMORY_BYTES
    pids_limit: int = DEFAULT_PIDS_LIMIT

    def __post_init__(self) -> None:
        if self.cpus <= 0:
            raise ValueError("container cpus must be positive")
        if self.memory_bytes <= 0:
            raise ValueError("container memory must be positive")
        if self.pids_limit <= 0:
            raise ValueError("container pids limit must be positive")


@dataclass(frozen=True)
class ContainerRunRequest:
    task_id: str
    harness: str
    model: ModelRuntimeSpec
    prompt: str
    workspace: Path
    artifacts_dir: Path
    timeout: int | None
    auth_mode: str = "model_registry"
    subscription_auth: dict[str, object] | None = field(default=None, repr=False)
    conversation_mode: str = "single_turn"
    turns: tuple[str, ...] = ()
    conversation_max_turns: int | None = None
    new_session_before_turns: tuple[int, ...] = ()
    new_session_context: str = "none"
    conversation_simulator_source: str | None = field(default=None, repr=False)
    business_simulator_endpoint: dict[str, object] | None = field(
        default=None, repr=False
    )
    task_tools: dict[str, object] | None = None
    task_tool_endpoint: dict[str, object] | None = field(default=None, repr=False)
    model_call_budget: dict[str, object] | None = None
    container_build: ContainerBuildConfig = field(
        default_factory=ContainerBuildConfig,
        repr=False,
    )
    extra_args: dict[str, str] = field(default_factory=dict)
    image_override: str | None = None
    limits: ContainerLimits = field(default_factory=ContainerLimits)
    transport_environment: Mapping[str, str] = field(
        default_factory=dict, repr=False
    )
    transient_workspace_sockets: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        auth_mode = self.auth_mode.replace("-", "_")
        object.__setattr__(self, "auth_mode", auth_mode)
        if auth_mode not in {"model_registry", "subscription"}:
            raise ValueError("container auth mode is invalid")
        if auth_mode == "subscription":
            if self.harness not in {"codex", "claudecode"}:
                raise ValueError(
                    "container subscription auth supports only codex and claudecode"
                )
            if not isinstance(self.subscription_auth, dict):
                raise ValueError("container subscription auth state is missing")
        elif self.subscription_auth is not None:
            raise ValueError(
                "container subscription auth state requires subscription auth mode"
            )
        if self.conversation_mode not in {"single_turn", "fixed_turns", "simulator"}:
            raise ValueError("container conversation mode is invalid")
        object.__setattr__(self, "turns", tuple(self.turns))
        object.__setattr__(
            self,
            "new_session_before_turns",
            tuple(self.new_session_before_turns),
        )
        if self.conversation_mode == "fixed_turns" and (
            not self.turns or any(not isinstance(value, str) or not value.strip() for value in self.turns)
        ):
            raise ValueError("container fixed-turn conversation is empty or invalid")
        if self.new_session_before_turns and self.conversation_mode != "fixed_turns":
            raise ValueError(
                "container session boundaries require a fixed-turn conversation"
            )
        if any(
            type(item) is not int or item <= 1 or item > len(self.turns)
            for item in self.new_session_before_turns
        ) or tuple(sorted(set(self.new_session_before_turns))) != (
            self.new_session_before_turns
        ):
            raise ValueError("container session boundaries are invalid")
        if self.new_session_context not in {"none", "replay"}:
            raise ValueError("container new-session context mode is invalid")
        if self.new_session_context != "none" and not self.new_session_before_turns:
            raise ValueError(
                "container new-session context requires a session boundary"
            )
        if self.conversation_mode == "simulator" and (
            not isinstance(self.conversation_simulator_source, str)
            or not self.conversation_simulator_source.strip()
            or not isinstance(self.conversation_max_turns, int)
            or self.conversation_max_turns < 1
        ):
            raise ValueError("container simulator conversation is incomplete")
        if self.business_simulator_endpoint is not None:
            normalized_business_endpoint = validate_business_endpoint(
                dict(self.business_simulator_endpoint)
            )
            if (
                normalized_business_endpoint.get("transport") == "tcp"
                and normalized_business_endpoint.get("host") != CONTAINER_HOST_ALIAS
            ):
                raise ValueError(
                    "container business simulator endpoint must use the runner host alias"
                )
            object.__setattr__(
                self,
                "business_simulator_endpoint",
                normalized_business_endpoint,
            )
        if self.task_tools is not None and (
            not isinstance(self.task_tools, dict)
            or self.task_tools.get("schema_version")
            != "harnessbench_task_tools_v1"
            or not isinstance(self.task_tools.get("tools"), list)
            or not self.task_tools["tools"]
        ):
            raise ValueError("container task tools are invalid")
        if (self.task_tools is None) != (self.task_tool_endpoint is None):
            raise ValueError(
                "container task tools and transport endpoint must be configured together"
            )
        if self.task_tool_endpoint is not None:
            normalized_endpoint = _validate_endpoint(dict(self.task_tool_endpoint))
            if (
                normalized_endpoint.get("transport") == "tcp"
                and normalized_endpoint.get("host") != CONTAINER_HOST_ALIAS
            ):
                raise ValueError(
                    "container task tool endpoint must use the runner host alias"
                )
            object.__setattr__(
                self,
                "task_tool_endpoint",
                normalized_endpoint,
            )
        if self.model_call_budget is not None:
            object.__setattr__(
                self,
                "model_call_budget",
                validate_model_call_budget(dict(self.model_call_budget)),
            )
        values = dict(self.transport_environment)
        if any(
            name not in TRANSPORT_ENV_NAMES
            or not isinstance(value, str)
            or not value
            for name, value in values.items()
        ):
            raise ValueError("container transport environment is invalid")
        object.__setattr__(self, "transport_environment", MappingProxyType(values))
        object.__setattr__(
            self,
            "transient_workspace_sockets",
            tuple(Path(path) for path in self.transient_workspace_sockets),
        )


@dataclass(frozen=True)
class HarnessImage:
    reference: str
    image_id: str
    digest: str | None
    built: bool

    def __post_init__(self) -> None:
        if not FULL_IMAGE_ID.fullmatch(self.image_id):
            raise ValueError("harness image ID must be a full sha256 ID")


@dataclass(frozen=True)
class ContainerExecution:
    trace_summary: dict
    image: HarnessImage
    container_id: str
    lifecycle_path: Path
    archive_status: str
    cleanup_status: str
    gateway: dict[str, object] | None = None


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ImageResolver = Callable[[ContainerRunRequest, Path], HarnessImage]


def _resolve_runtime_gateway_token(
    harness: str,
    token_factory: Callable[[], str],
    *,
    model_call_budget: bool = False,
) -> str | None:
    if harness not in _RUNTIME_GATEWAY_TOKEN_HARNESSES and not model_call_budget:
        return None
    token = token_factory()
    if not isinstance(token, str) or not token:
        raise ValueError("gateway token factory returned an empty value")
    return token


class DockerHarnessRuntime:
    def __init__(
        self,
        *,
        docker_bin: str = "docker",
        command_runner: CommandRunner = subprocess.run,
        image_resolver: ImageResolver | None = None,
        run_token_factory: Callable[[], str] | None = None,
        gateway_token_factory: Callable[[], str] | None = None,
    ) -> None:
        self.docker_bin = docker_bin
        self.command_runner = command_runner
        self.image_resolver = image_resolver or self._resolve_default_image
        self.run_token_factory = run_token_factory or (lambda: uuid.uuid4().hex[:16])
        self.gateway_token_factory = gateway_token_factory or (
            lambda: secrets.token_urlsafe(32)
        )

    def run(self, request: ContainerRunRequest) -> ContainerExecution:
        request.artifacts_dir.mkdir(parents=True, exist_ok=True)
        lifecycle_path = request.artifacts_dir / "container_lifecycle.json"
        run_token = self.run_token_factory()
        if not SAFE_RUN_TOKEN.fullmatch(run_token):
            raise ValueError("container run token is unsafe")
        container_name = f"harnessbench-{request.harness}-{run_token}"
        lifecycle: dict[str, object] = {
            "schema_version": "harnessbench_container_lifecycle_v1",
            "run_token": run_token,
            "container_name": container_name,
            "container_id": None,
            "harness": request.harness,
            "task_id": request.task_id,
            "model": request.model.public_summary(),
            "started_at": _utc_now(),
            "archive_status": "not_started",
            "cleanup_status": "not_started",
            "gateway": None,
            "error_type": None,
            "error_message": None,
        }
        container_id: str | None = None
        image: HarnessImage | None = None
        trace_summary: dict | None = None
        gateway_summary: dict[str, object] | None = None
        gateway_token: str | None = None
        workspace_security_complete = False
        primary_error: Exception | None = None
        cleanup_error: Exception | None = None
        create_attempted = False
        try:
            gateway_token = _resolve_runtime_gateway_token(
                (
                    request.harness
                    if request.auth_mode == "model_registry"
                    else "__subscription__"
                ),
                self.gateway_token_factory,
                model_call_budget=request.model_call_budget is not None,
            )
            image = self.image_resolver(request, request.artifacts_dir / "image_build.log")
            lifecycle["image"] = {
                "reference": image.reference,
                "image_id": image.image_id,
                "digest": image.digest,
                "built": image.built,
            }
            if self._list_owned(run_token):
                raise ContainerRuntimeError(
                    "run-unique container label already exists before create"
                )
            create_command = build_container_create_command(
                request=request,
                image_id=image.image_id,
                container_name=container_name,
                run_label=run_token,
                uid=os.getuid(),
                gid=os.getgid(),
            )
            create_attempted = True
            created = self._docker(create_command, timeout=60)
            if created.returncode != 0:
                raise ContainerRuntimeError(
                    "docker create failed: " + _completed_detail(created)
                )
            raw_id = created.stdout.strip()
            if not FULL_CONTAINER_ID.fullmatch(raw_id):
                raise ContainerRuntimeError("docker create returned a non-full container ID")
            container_id = raw_id
            lifecycle["container_id"] = container_id

            started = self._docker(
                ["container", "start", container_id], timeout=60
            )
            if started.returncode != 0:
                raise ContainerRuntimeError(
                    "docker start failed: " + _completed_detail(started)
                )
            metadata = self._inspect(container_id)
            _validate_container_metadata(
                metadata,
                request=request,
                image=image,
                container_id=container_id,
                container_name=container_name,
                run_token=run_token,
                uid=os.getuid(),
                gid=os.getgid(),
            )
            lifecycle["security"] = _security_summary(metadata)

            control = self._execute_worker(
                container_id,
                request,
                gateway_token=gateway_token,
            )
            output_redaction = control.get("output_redaction")
            if not isinstance(output_redaction, dict) or any(
                type(output_redaction.get(name)) is not int
                or output_redaction[name] < 0
                for name in ("files_redacted", "matches_redacted")
            ):
                raise ContainerRuntimeError(
                    "container worker output redaction metadata is invalid"
                )
            lifecycle["output_redaction"] = output_redaction
            gateway_summary = validate_worker_gateway_summary(
                control,
                request.harness,
                auth_mode=request.auth_mode,
                model_call_budget=request.model_call_budget is not None,
            )
            lifecycle["gateway"] = gateway_summary
            model_call_budget_summary = validate_worker_model_call_budget_summary(
                control,
                request.model_call_budget,
            )
            if model_call_budget_summary is not None:
                lifecycle["model_call_budget"] = model_call_budget_summary
            provider_request_summary = validate_worker_provider_request_summary(
                control,
                configured=request.auth_mode == "model_registry",
            )
            if provider_request_summary is not None:
                lifecycle["provider_request_evidence"] = provider_request_summary
            trace_summary = self._archive_output(
                container_id=container_id,
                request=request,
                control=control,
                secret_values=tuple(
                    value
                    for value in (
                        request.model.api_key,
                        gateway_token,
                        (request.business_simulator_endpoint or {}).get("auth_token"),
                        (request.task_tool_endpoint or {}).get("auth_token"),
                    )
                    if value
                ),
            )
            if model_call_budget_summary is not None:
                trace_summary["model_call_budget_evidence"] = (
                    _validate_exported_model_call_budget(
                        request.artifacts_dir / "container",
                        model_call_budget_summary,
                    )
                )
            if provider_request_summary is not None:
                trace_summary["provider_request_evidence"] = (
                    _validate_exported_provider_requests(
                        request.artifacts_dir / "container",
                        provider_request_summary,
                    )
                )
                attach_provider_token_usage(trace_summary)
                attach_cache_savings(trace_summary, request.model.pricing)
            lifecycle["archive_status"] = "complete"
        except Exception as exc:
            safe_message = _redact_request_values(
                str(exc),
                request,
                extra_sensitive_values=(gateway_token,),
            )
            primary_error = ContainerRuntimeError(safe_message)
            lifecycle["error_type"] = type(exc).__name__
            lifecycle["error_message"] = safe_message
            if lifecycle["archive_status"] == "not_started":
                lifecycle["archive_status"] = "failed"
            if container_id is not None and request.auth_mode == "model_registry":
                try:
                    salvage = self._salvage_provider_request_evidence(
                        container_id,
                        request,
                        extra_sensitive_values=(gateway_token,),
                    )
                except Exception as salvage_error:
                    lifecycle["provider_request_salvage"] = {
                        "status": "failed",
                        "error_type": type(salvage_error).__name__,
                    }
                else:
                    lifecycle["provider_request_salvage"] = (
                        {
                            key: value
                            for key, value in salvage.items()
                            if key != "events"
                        }
                        if salvage is not None
                        else {"status": "unavailable"}
                    )
        finally:
            if container_id is not None or create_attempted:
                try:
                    if container_id is not None:
                        self._cleanup(container_id, run_token)
                    else:
                        self._cleanup_owned(run_token)
                    lifecycle["cleanup_status"] = "complete"
                except Exception as exc:
                    safe_message = _redact_request_values(
                        str(exc),
                        request,
                        extra_sensitive_values=(gateway_token,),
                    )
                    cleanup_error = ContainerRuntimeError(safe_message)
                    lifecycle["cleanup_status"] = "failed"
                    lifecycle["cleanup_error"] = {
                        "type": type(exc).__name__,
                        "message": safe_message,
                    }
            else:
                lifecycle["cleanup_status"] = "not_needed"
            workspace_secret_values = tuple(
                value
                for value in (
                    request.model.api_key,
                    gateway_token,
                    (request.business_simulator_endpoint or {}).get("auth_token"),
                    (request.task_tool_endpoint or {}).get("auth_token"),
                )
                if isinstance(value, str) and value
            )
            if workspace_secret_values and not workspace_security_complete:
                try:
                    validate_workspace_for_host_verifier(
                        request.workspace,
                        secret_values=workspace_secret_values,
                        transient_socket_paths=request.transient_workspace_sockets,
                    )
                    workspace_security_complete = True
                except Exception as exc:
                    safe_message = _redact_request_values(
                        str(exc),
                        request,
                        extra_sensitive_values=(gateway_token,),
                    )
                    workspace_error = ContainerRuntimeError(safe_message)
                    if primary_error is None:
                        primary_error = workspace_error
                    elif safe_message not in str(primary_error):
                        primary_error = ContainerRuntimeError(
                            f"{primary_error}; {workspace_error}"
                        )
                    lifecycle["error_type"] = "unsafe_workspace"
                    lifecycle["error_message"] = safe_message
                    if lifecycle["archive_status"] == "not_started":
                        lifecycle["archive_status"] = "failed"
            lifecycle["finished_at"] = _utc_now()
            _write_json(lifecycle_path, lifecycle)

        if cleanup_error is not None:
            detail = f"container cleanup failed: {cleanup_error}"
            if primary_error is not None:
                detail = f"{primary_error}; {detail}"
            raise ContainerRuntimeError(detail) from cleanup_error
        if primary_error is not None:
            if isinstance(primary_error, ContainerRuntimeError):
                raise primary_error
            raise ContainerRuntimeError(str(primary_error)) from primary_error
        if image is None or container_id is None or trace_summary is None:
            raise ContainerRuntimeError("container lifecycle completed without a result")
        return ContainerExecution(
            trace_summary=trace_summary,
            image=image,
            container_id=container_id,
            lifecycle_path=lifecycle_path,
            archive_status="complete",
            cleanup_status="complete",
            gateway=gateway_summary,
        )

    def _docker(
        self,
        args: list[str],
        *,
        timeout: int,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.command_runner(
            [self.docker_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            input=input_text,
            env=dict(env) if env is not None else None,
        )

    def _docker_archive(
        self, args: list[str], *, timeout: int
    ) -> subprocess.CompletedProcess[bytes]:
        return self.command_runner(
            [self.docker_bin, *args],
            capture_output=True,
            text=False,
            check=False,
            timeout=timeout,
        )

    def _salvage_provider_request_evidence(
        self,
        container_id: str,
        request: ContainerRunRequest,
        *,
        extra_sensitive_values: tuple[object, ...] = (),
    ) -> dict[str, object] | None:
        """Snapshot recorder-only metadata before a failed container is removed."""

        completed = self._docker(
            [
                "exec",
                container_id,
                "/bin/cat",
                "/hb-output/provider_requests/events.jsonl",
            ],
            timeout=30,
        )
        if completed.returncode != 0:
            return None
        sensitive_values = tuple(
            value
            for value in (
                request.model.api_key,
                *request.transport_environment.values(),
                (request.business_simulator_endpoint or {}).get("auth_token"),
                (request.task_tool_endpoint or {}).get("auth_token"),
                *extra_sensitive_values,
            )
            if isinstance(value, str) and value
        )
        return persist_provider_request_salvage(
            request.artifacts_dir,
            completed.stdout,
            secret_values=sensitive_values,
        )

    def _list_owned(self, run_token: str) -> set[str]:
        completed = self._docker(
            [
                "container",
                "ls",
                "--all",
                "--quiet",
                "--no-trunc",
                "--filter",
                f"label=harnessbench.run={run_token}",
            ],
            timeout=30,
        )
        if completed.returncode != 0:
            raise ContainerRuntimeError(
                "cannot list run-owned containers: " + _completed_detail(completed)
            )
        ids = {line.strip() for line in completed.stdout.splitlines() if line.strip()}
        if any(not FULL_CONTAINER_ID.fullmatch(value) for value in ids):
            raise ContainerRuntimeError("docker list returned a non-full container ID")
        return ids

    def _inspect(self, container_id: str) -> dict:
        completed = self._docker(
            ["container", "inspect", container_id], timeout=30
        )
        if completed.returncode != 0:
            raise ContainerRuntimeError(
                "cannot inspect harness container: " + _completed_detail(completed)
            )
        try:
            values = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker inspect returned invalid JSON") from exc
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise ContainerRuntimeError("docker inspect returned an invalid shape")
        return values[0]

    def _execute_worker(
        self,
        container_id: str,
        request: ContainerRunRequest,
        *,
        gateway_token: str | None,
    ) -> dict:
        payload = {
            "schema_version": "harnessbench_container_request_v1",
            "task_id": request.task_id,
            "harness": request.harness,
            "model": request.model.public_summary(),
            "auth_mode": request.auth_mode,
            "subscription_auth": request.subscription_auth,
            "prompt": request.prompt,
            "timeout": request.timeout,
            "extra_args": request.extra_args,
            "conversation": {
                "mode": request.conversation_mode,
                "turns": list(request.turns),
                "max_turns": request.conversation_max_turns,
                "new_session_before_turns": list(
                    request.new_session_before_turns
                ),
                "new_session_context": request.new_session_context,
                "simulator_source": request.conversation_simulator_source,
            },
            "business_simulator_endpoint": request.business_simulator_endpoint,
            "task_tools": request.task_tools,
            "task_tool_endpoint": request.task_tool_endpoint,
            "model_call_budget": request.model_call_budget,
        }
        worker_timeout = (request.timeout or 3600) + 90
        worker_env = dict(os.environ)
        worker_env.update(request.transport_environment)
        if request.model.api_key:
            worker_env[INTERNAL_API_KEY_ENV] = request.model.api_key
        if gateway_token is not None:
            worker_env[GATEWAY_TOKEN_ENV] = gateway_token
        exec_environment: list[str] = []
        for name in sorted(request.transport_environment):
            if name == INTERNAL_API_KEY_ENV:
                continue
            exec_environment.extend(["--env", name])
        if request.model.api_key:
            exec_environment.extend(["--env", INTERNAL_API_KEY_ENV])
        if gateway_token is not None:
            exec_environment.extend(["--env", GATEWAY_TOKEN_ENV])
        completed = self._docker(
            [
                "exec",
                "--interactive",
                *exec_environment,
                container_id,
                "python3",
                "-m",
                "harnessbench.container_worker",
            ],
            timeout=worker_timeout,
            input_text=json.dumps(payload, ensure_ascii=False),
            env=worker_env,
        )
        if completed.returncode != 0:
            raise ContainerRuntimeError(
                f"container worker exited with {completed.returncode}: "
                + _completed_detail(completed)
            )
        try:
            control = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("container worker returned invalid control JSON") from exc
        if not isinstance(control, dict) or control.get("status") not in {
            "finished",
            "failed",
        }:
            raise ContainerRuntimeError("container worker control response is invalid")
        return control

    def _archive_output(
        self,
        *,
        container_id: str,
        request: ContainerRunRequest,
        control: dict,
        secret_values: tuple[str, ...],
    ) -> dict:
        with tempfile.TemporaryDirectory(
            prefix=".harnessbench-export-",
            dir=request.artifacts_dir.parent,
        ) as tmp:
            staging = Path(tmp)
            archived = self._docker_archive(
                [
                    "exec",
                    container_id,
                    "/bin/tar",
                    "-C",
                    "/hb-output",
                    "-cf",
                    "-",
                    ".",
                ],
                timeout=60,
            )
            if archived.returncode != 0:
                raise ContainerArchiveError(
                    "container archive stream failed: "
                    + _binary_completed_detail(archived)
                )
            if not isinstance(archived.stdout, bytes):
                raise ContainerArchiveError("container archive stream was not binary")
            extract_container_archive(
                archived.stdout,
                staging,
                secret_values=secret_values,
            )
            relative_summary = _safe_export_path(control.get("trace_summary"))
            summary_source = staging / relative_summary
            if not summary_source.is_file():
                raise ContainerArchiveError(
                    "container export is missing the declared trace summary"
                )
            target = request.artifacts_dir / "container"
            if target.exists():
                raise ContainerArchiveError(f"container artifact target already exists: {target}")
            shutil.copytree(staging, target)

        summary_path = target / relative_summary
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContainerArchiveError("exported trace summary is invalid") from exc
        if not isinstance(summary, dict):
            raise ContainerArchiveError("exported trace summary must be an object")
        _rewrite_trace_paths(summary, target)
        attach_cache_savings(summary, request.model.pricing)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_archive_manifest(target)
        return summary

    def _cleanup(self, container_id: str, run_token: str) -> None:
        self._remove_and_prove_absent(container_id)
        leftovers = self._list_owned(run_token)
        if leftovers:
            raise ContainerRuntimeError(
                "run-owned containers remain after cleanup: " + ",".join(sorted(leftovers))
            )

    def _cleanup_owned(self, run_token: str) -> None:
        for container_id in sorted(self._list_owned(run_token)):
            self._remove_and_prove_absent(container_id)
        leftovers = self._list_owned(run_token)
        if leftovers:
            raise ContainerRuntimeError(
                "run-owned containers remain after cleanup: " + ",".join(sorted(leftovers))
            )

    def _remove_and_prove_absent(self, container_id: str) -> None:
        removed = self._docker(
            ["container", "rm", "--force", container_id], timeout=60
        )
        if removed.returncode != 0:
            raise ContainerRuntimeError(
                "docker rm --force failed: " + _completed_detail(removed)
            )
        inspected = self._docker(
            ["container", "inspect", container_id], timeout=30
        )
        absence_text = f"{inspected.stdout}\n{inspected.stderr}".lower()
        if inspected.returncode == 0 or (
            "no such container" not in absence_text
            and "no such object" not in absence_text
        ):
            raise ContainerRuntimeError("container disappearance was not proven")

    @staticmethod
    def _resolve_default_image(
        request: ContainerRunRequest, build_log: Path
    ) -> HarnessImage:
        from harnessbench.container_images import HarnessImageManager

        return HarnessImageManager().ensure(
            request.harness,
            override=request.image_override,
            build_log=build_log,
            build_config=request.container_build,
        )


def build_container_create_command(
    *,
    request: ContainerRunRequest,
    image_id: str,
    container_name: str,
    run_label: str,
    uid: int,
    gid: int,
) -> list[str]:
    workspace = request.workspace.resolve(strict=True)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError(f"container workspace is not a real directory: {workspace}")
    if "," in str(workspace):
        raise ValueError("container workspace path may not contain a comma")
    if request.harness not in SUPPORTED_CONTAINER_HARNESSES:
        raise ValueError(f"unsupported container harness: {request.harness}")
    if not image_id.startswith("sha256:") or len(image_id) != 71:
        raise ValueError("container image must be an immutable sha256 image ID")
    memory = str(request.limits.memory_bytes)
    identity = f"{uid}:{gid}"
    tmpfs_options = (
        f"rw,nosuid,nodev,noexec,size=1g,uid={uid},gid={gid},mode=0700"
    )
    output_tmpfs_options = (
        "rw,nosuid,nodev,noexec,"
        f"size={MAX_ARCHIVE_BYTES},uid={uid},gid={gid},mode=0700"
    )
    command = [
        "container",
        "create",
        "--name",
        container_name,
        "--label",
        "harnessbench.managed=true",
        "--label",
        f"harnessbench.run={run_label}",
        "--label",
        f"harnessbench.harness={request.harness}",
        "--label",
        f"harnessbench.task={request.task_id}",
        "--network",
        "bridge",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        memory,
        "--memory-swap",
        memory,
        "--cpus",
        str(request.limits.cpus),
        "--pids-limit",
        str(request.limits.pids_limit),
        "--user",
        identity,
        "--mount",
        f"type=bind,source={workspace},target=/workspace",
        "--tmpfs",
        f"/tmp:{tmpfs_options}",
        "--tmpfs",
        f"/hb-state:{tmpfs_options}",
        "--tmpfs",
        f"/hb-output:{output_tmpfs_options}",
    ]
    if _uses_runner_host_endpoint(request):
        command.extend(
            ["--add-host", f"{CONTAINER_HOST_ALIAS}:host-gateway"]
        )
    if request.harness == "hermes":
        command.extend(["--tmpfs", f"/opt/data:{tmpfs_options}"])
    command.extend(
        [
        "--workdir",
        "/workspace",
        "--entrypoint",
        "sleep",
        image_id,
        "infinity",
        ]
    )
    return command


def _uses_runner_host_endpoint(request: ContainerRunRequest) -> bool:
    return any(
        endpoint is not None and endpoint.get("transport") == "tcp"
        for endpoint in (
            request.business_simulator_endpoint,
            request.task_tool_endpoint,
        )
    )


def validate_worker_gateway_summary(
    control: Mapping[str, object],
    harness: str,
    *,
    auth_mode: str = "model_registry",
    model_call_budget: bool = False,
) -> dict[str, object] | None:
    del model_call_budget
    auth_mode = auth_mode.replace("-", "_")
    gateway_harness = harness in {"codex", "claudecode"}
    if auth_mode == "subscription":
        if "gateway" in control:
            raise ContainerRuntimeError(
                "subscription-auth container worker returned gateway metadata"
            )
        return None
    if not gateway_harness:
        if "gateway" in control:
            raise ContainerRuntimeError(
                "non-gateway container worker returned gateway metadata"
            )
        return None
    gateway = control.get("gateway")
    ready_keys = {
        "implementation",
        "ready",
        "listen_host",
        "listen_port",
    }
    if not isinstance(gateway, dict):
        raise ContainerRuntimeError("container worker gateway metadata is invalid")
    if (
        gateway.get("implementation") != "litellm"
        or gateway.get("listen_host") != "127.0.0.1"
        or type(gateway.get("listen_port")) is not int
        or gateway.get("listen_port") != 4000
    ):
        raise ContainerRuntimeError("container worker gateway metadata is invalid")
    if gateway.get("ready") is True and set(gateway) == ready_keys:
        return {
            "implementation": "litellm",
            "ready": True,
            "listen_host": "127.0.0.1",
            "listen_port": 4000,
        }
    if (
        gateway.get("ready") is False
        and set(gateway) == ready_keys | {"failure_stage"}
        and gateway.get("failure_stage") == "startup"
        and control.get("status") == "failed"
    ):
        return {
            "implementation": "litellm",
            "ready": False,
            "listen_host": "127.0.0.1",
            "listen_port": 4000,
            "failure_stage": "startup",
        }
    raise ContainerRuntimeError("container worker gateway metadata is invalid")


def validate_worker_model_call_budget_summary(
    control: Mapping[str, object],
    configured: Mapping[str, object] | None,
) -> dict[str, object] | None:
    if configured is None:
        if "model_call_budget" in control:
            raise ContainerRuntimeError(
                "unconfigured container worker returned model-call budget metadata"
            )
        return None
    expected = validate_model_call_budget(configured)
    value = control.get("model_call_budget")
    if value is None and control.get("status") == "failed":
        # The compatibility gateway or budget proxy can fail before budget
        # evidence exists. Preserve the worker's failure trace instead of
        # replacing the root cause with a secondary metadata error.
        return None
    required = {
        "schema_version",
        "version",
        "limit",
        "finalization_reserve",
        "requests_used",
        "requests_remaining",
        "rejected_after_limit",
        "state_path",
        "events_path",
    }
    if not isinstance(value, dict):
        raise ContainerRuntimeError(
            "container model-call budget metadata is invalid: expected an object"
        )
    if set(value) != required:
        missing = sorted(required - set(value))
        unexpected = sorted(set(value) - required)
        raise ContainerRuntimeError(
            "container model-call budget metadata is invalid: "
            f"missing={missing}, unexpected={unexpected}"
        )
    if (
        value.get("schema_version") != MODEL_CALL_BUDGET_SCHEMA
        or value.get("version") != expected["version"]
        or value.get("limit") != expected["limit"]
        or value.get("finalization_reserve") != expected["finalization_reserve"]
        or value.get("state_path") != "model_call_budget/state.json"
        or value.get("events_path") != "model_call_budget/events.jsonl"
    ):
        raise ContainerRuntimeError("container model-call budget metadata mismatches task")
    used = value.get("requests_used")
    remaining = value.get("requests_remaining")
    rejected = value.get("rejected_after_limit")
    limit = int(expected["limit"])
    if (
        type(used) is not int
        or type(remaining) is not int
        or type(rejected) is not int
        or not 0 <= used <= limit
        or remaining != limit - used
        or rejected < 0
    ):
        raise ContainerRuntimeError("container model-call budget counts are invalid")
    return dict(value)


def validate_worker_provider_request_summary(
    control: Mapping[str, object],
    *,
    configured: bool = True,
) -> dict[str, object] | None:
    value = control.get("provider_request_evidence")
    if not configured:
        if value is not None:
            raise ContainerRuntimeError(
                "unconfigured container worker returned provider-request evidence"
            )
        return None
    if value is None and control.get("status") == "failed":
        # The recorder can fail before it binds its loopback socket. Preserve
        # the worker failure trace instead of masking the startup cause.
        return None
    if not isinstance(value, dict):
        raise ContainerRuntimeError(
            "container provider-request evidence metadata is invalid"
        )
    try:
        return validate_provider_request_summary(value)
    except ValueError as exc:
        raise ContainerRuntimeError(
            "container provider-request evidence metadata is invalid"
        ) from exc


def _validate_exported_model_call_budget(
    container_artifacts: Path,
    summary: Mapping[str, object],
) -> dict[str, object]:
    state_path = container_artifacts / str(summary["state_path"])
    events_path = container_artifacts / str(summary["events_path"])
    if (
        state_path.is_symlink()
        or events_path.is_symlink()
        or not state_path.is_file()
        or not events_path.is_file()
    ):
        raise ContainerArchiveError("model-call budget evidence is missing or unsafe")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        rows = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContainerArchiveError("model-call budget evidence is invalid") from exc
    if not isinstance(state, dict) or any(not isinstance(row, dict) for row in rows):
        raise ContainerArchiveError("model-call budget evidence has an invalid shape")
    for key in (
        "schema_version",
        "version",
        "limit",
        "finalization_reserve",
        "requests_used",
        "requests_remaining",
        "rejected_after_limit",
    ):
        if state.get(key) != summary.get(key):
            raise ContainerArchiveError(
                "model-call budget state does not match worker metadata"
            )
    if [row.get("seq") for row in rows] != list(range(1, len(rows) + 1)):
        raise ContainerArchiveError("model-call budget event sequence is not contiguous")
    admitted = [row for row in rows if row.get("admitted") is True]
    rejected = [row for row in rows if row.get("admitted") is False]
    if (
        len(admitted) != summary.get("requests_used")
        or len(rejected) != summary.get("rejected_after_limit")
    ):
        raise ContainerArchiveError("model-call budget event counts are inconsistent")
    return {
        **dict(summary),
        "state": str(state_path),
        "events": str(events_path),
    }


def _validate_exported_provider_requests(
    container_artifacts: Path,
    summary: Mapping[str, object],
) -> dict[str, object]:
    events_path = container_artifacts / str(summary["events_path"])
    if events_path.is_symlink() or not events_path.is_file():
        raise ContainerArchiveError(
            "provider-request evidence is missing or unsafe"
        )
    try:
        rows = read_provider_request_events(events_path)
    except ProviderRequestEvidenceError as exc:
        raise ContainerArchiveError(
            "provider-request evidence is invalid"
        ) from exc
    visible_count = sum(row.get("model_visible") is True for row in rows)
    complete_count = sum(
        row.get("model_visible") is True
        and isinstance(row.get("tool_inventory"), dict)
        and row["tool_inventory"].get("complete") is True
        for row in rows
    )
    if (
        len(rows) != summary.get("request_count")
        or visible_count != summary.get("model_visible_request_count")
        or complete_count != summary.get("complete_tool_inventory_count")
    ):
        raise ContainerArchiveError(
            "provider-request evidence does not match worker metadata"
        )
    return {**dict(summary), "events": str(events_path)}


def persist_provider_request_salvage(
    artifacts_dir: Path,
    snapshot: str,
    *,
    secret_values: tuple[str, ...] = (),
) -> dict[str, object] | None:
    """Persist complete, validated recorder rows from an interrupted worker.

    The recorder file contains only numeric HTTP metadata and normalized tool
    declarations.  A concurrently written final line is discarded unless it
    is newline-terminated; every retained row then passes the normal evidence
    validator plus a strict top-level field allowlist.
    """

    if not snapshot:
        return None
    if len(snapshot.encode("utf-8")) > MAX_EXPORTED_FILE_BYTES:
        raise ContainerArchiveError(
            "provider-request salvage snapshot is too large"
        )
    complete_snapshot = snapshot
    if not snapshot.endswith(("\n", "\r")):
        complete_snapshot = snapshot.rsplit("\n", 1)[0] if "\n" in snapshot else ""
    lines = [line for line in complete_snapshot.splitlines() if line.strip()]
    if not lines:
        return None

    root = Path(artifacts_dir)
    target = root / PROVIDER_REQUEST_SALVAGE_RELATIVE_PATH
    salvage_root = target.parent
    if root.is_symlink() or (salvage_root.exists() and salvage_root.is_symlink()):
        raise ContainerArchiveError(
            "provider-request salvage target is unsafe"
        )
    salvage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = salvage_root / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows = read_provider_request_events(temporary)
        for row in rows:
            error_type = row.get("error_type")
            if set(row) != _PROVIDER_REQUEST_EVENT_FIELDS or not (
                error_type is None
                or (
                    isinstance(error_type, str)
                    and SAFE_PROVIDER_ERROR_TYPE.fullmatch(error_type)
                )
            ):
                raise ContainerArchiveError(
                    "provider-request salvage row fields are invalid"
                )
        normalized = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        )
        if any(secret in normalized for secret in secret_values):
            raise ContainerArchiveError(
                "provider-request salvage contains a credential"
            )
        temporary.write_text(normalized, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return load_salvaged_provider_request_evidence(root)


def load_salvaged_provider_request_evidence(
    artifacts_dir: Path,
) -> dict[str, object] | None:
    root = Path(artifacts_dir)
    events_path = root / PROVIDER_REQUEST_SALVAGE_RELATIVE_PATH
    if events_path.is_symlink() or not events_path.is_file():
        return None
    try:
        events_path.resolve().relative_to(root.resolve())
        rows = read_provider_request_events(events_path)
    except (ValueError, OSError, ProviderRequestEvidenceError):
        return None
    return {
        "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
        "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
        "request_count": len(rows),
        "model_visible_request_count": sum(
            row.get("model_visible") is True for row in rows
        ),
        "complete_tool_inventory_count": sum(
            row.get("model_visible") is True
            and isinstance(row.get("tool_inventory"), dict)
            and row["tool_inventory"].get("complete") is True
            for row in rows
        ),
        "events_path": PROVIDER_REQUEST_SALVAGE_RELATIVE_PATH.as_posix(),
        "events": str(events_path.resolve()),
        "status": "salvaged_after_runtime_error",
    }


def validate_export_tree(
    root: Path,
    *,
    secret_values: tuple[str, ...],
    max_file_bytes: int = MAX_EXPORTED_FILE_BYTES,
    max_total_bytes: int = MAX_EXPORTED_TOTAL_BYTES,
) -> dict[str, int]:
    if root.is_symlink() or not root.is_dir():
        raise ContainerArchiveError("container export root is missing or symlinked")
    secret_bytes = tuple(
        value.encode("utf-8") for value in secret_values if isinstance(value, str) and value
    )
    files = 0
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=_raise_export_walk_error,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in [*directory_names, *file_names]:
            path = directory_path / name
            if _path_value_contains_secret(path.relative_to(root), secret_bytes):
                raise ContainerArchiveError(
                    "container export path contains a credential"
                )
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ContainerArchiveError(f"container export contains a symlink: {path}")
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ContainerArchiveError(
                    f"container export contains a special file: {path}"
                )
            relative = path.relative_to(root)
            allowed_file_bytes = _export_file_limit(
                relative,
                ordinary_limit=max_file_bytes,
                total_limit=max_total_bytes,
            )
            if info.st_size > allowed_file_bytes:
                raise ContainerArchiveError(f"container export file is too large: {path}")
            files += 1
            if files > MAX_EXPORTED_FILES:
                raise ContainerArchiveError("container export contains too many files")
            total_bytes += info.st_size
            if total_bytes > max_total_bytes:
                raise ContainerArchiveError("container export exceeds total size limit")
            payload = path.read_bytes()
            if any(secret in payload for secret in secret_bytes):
                raise ContainerArchiveError(
                    f"container export contains model credential bytes: {path}"
                )
    return {"files": files, "total_bytes": total_bytes}


def _raise_export_walk_error(error: OSError) -> None:
    raise ContainerArchiveError("cannot inspect the complete container export") from error


def _export_file_limit(
    relative: Path,
    *,
    ordinary_limit: int = MAX_EXPORTED_FILE_BYTES,
    total_limit: int = MAX_EXPORTED_TOTAL_BYTES,
) -> int:
    """Permit one canonical trace to consume the already-bounded total budget.

    Native multi-agent traces can legitimately exceed the ordinary per-file
    cap because each model-input event preserves its visible conversation.
    Only normalized ``events.json`` paths receive this exception; the archive
    total limit, member count, path checks, and credential scan remain intact.
    """

    return total_limit if TRACE_EVENTS_FILE.fullmatch(relative.as_posix()) else ordinary_limit


def extract_container_archive(
    payload: bytes,
    destination: Path,
    *,
    secret_values: tuple[str, ...],
) -> dict[str, int]:
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise ContainerArchiveError("container archive stream is too large")
    if destination.is_symlink() or not destination.is_dir():
        raise ContainerArchiveError("container archive destination is invalid")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(payload), mode="r:*")
    except tarfile.TarError as exc:
        raise ContainerArchiveError("container archive stream is invalid") from exc
    with archive:
        members: list[tuple[tarfile.TarInfo, Path]] = []
        seen: set[str] = set()
        files = 0
        total_bytes = 0
        secret_bytes = tuple(
            value.encode("utf-8")
            for value in secret_values
            if isinstance(value, str) and value
        )
        for member in archive.getmembers():
            if _path_value_contains_secret(member.name, secret_bytes):
                raise ContainerArchiveError(
                    "container archive path contains a credential"
                )
            relative = _safe_tar_member_path(member.name)
            if relative is None:
                continue
            key = relative.as_posix()
            if key in seen:
                raise ContainerArchiveError(
                    f"container archive contains a duplicate path: {key}"
                )
            seen.add(key)
            if member.issym() or member.islnk():
                raise ContainerArchiveError(
                    f"container archive contains a link: {key}"
                )
            if not member.isdir() and not member.isfile():
                raise ContainerArchiveError(
                    f"container archive contains a special file: {key}"
                )
            if member.isfile():
                files += 1
                total_bytes += member.size
                if files > MAX_EXPORTED_FILES:
                    raise ContainerArchiveError(
                        "container archive contains too many files"
                    )
                allowed_file_bytes = _export_file_limit(relative)
                if member.size > allowed_file_bytes:
                    raise ContainerArchiveError(
                        f"container archive file is too large: {key}"
                    )
                if total_bytes > MAX_EXPORTED_TOTAL_BYTES:
                    raise ContainerArchiveError(
                        "container archive exceeds total size limit"
                    )
            members.append((member, relative))

        for member, relative in members:
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ContainerArchiveError(
                    f"container archive file cannot be read: {relative}"
                )
            try:
                with target.open("xb") as stream:
                    shutil.copyfileobj(source, stream, length=1024 * 1024)
            except FileExistsError as exc:
                raise ContainerArchiveError(
                    f"container archive path conflicts with another entry: {relative}"
                ) from exc
    return validate_export_tree(destination, secret_values=secret_values)


def _path_value_contains_secret(
    value: str | Path,
    secret_values: tuple[bytes, ...],
) -> bool:
    text = value.as_posix() if isinstance(value, Path) else value
    encoded = text.encode("utf-8", errors="surrogateescape")
    return any(secret in encoded for secret in secret_values)


def _safe_tar_member_path(value: str) -> Path | None:
    if not value or value in {".", "./"}:
        return None
    normalized = value[2:] if value.startswith("./") else value
    path = Path(normalized)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ContainerArchiveError(
            f"container archive contains an unsafe path: {value}"
        )
    return path


def _validate_container_metadata(
    metadata: dict,
    *,
    request: ContainerRunRequest,
    image: HarnessImage,
    container_id: str,
    container_name: str,
    run_token: str,
    uid: int,
    gid: int,
) -> None:
    errors: list[str] = []
    config = metadata.get("Config") if isinstance(metadata.get("Config"), dict) else {}
    host = (
        metadata.get("HostConfig")
        if isinstance(metadata.get("HostConfig"), dict)
        else {}
    )
    if metadata.get("Id") != container_id:
        errors.append("container ID mismatch")
    if metadata.get("Image") != image.image_id:
        errors.append("container image ID mismatch")
    if str(metadata.get("Name") or "").removeprefix("/") != container_name:
        errors.append("container name mismatch")
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    expected_labels = {
        "harnessbench.managed": "true",
        "harnessbench.run": run_token,
        "harnessbench.harness": request.harness,
        "harnessbench.task": request.task_id,
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        errors.append("container labels do not match the run")
    if config.get("User") != f"{uid}:{gid}":
        errors.append("container user does not match the invoking uid:gid")
    if host.get("NetworkMode") != "bridge":
        errors.append("container network mode is not bridge")
    expected_extra_hosts = (
        [f"{CONTAINER_HOST_ALIAS}:host-gateway"]
        if _uses_runner_host_endpoint(request)
        else []
    )
    if (host.get("ExtraHosts") or []) != expected_extra_hosts:
        errors.append("container extra-host configuration does not match")
    if host.get("ReadonlyRootfs") is not True:
        errors.append("container root filesystem is writable")
    if {str(value).upper() for value in (host.get("CapDrop") or [])} != {"ALL"}:
        errors.append("container capability drop is not exactly ALL")
    if host.get("CapAdd") not in (None, []):
        errors.append("container adds Linux capabilities")
    security_options = {str(value) for value in (host.get("SecurityOpt") or [])}
    if not any(value.startswith("no-new-privileges") for value in security_options):
        errors.append("container does not enforce no-new-privileges")
    expected_memory = request.limits.memory_bytes
    if host.get("Memory") != expected_memory or host.get("MemorySwap") != expected_memory:
        errors.append("container memory limits do not match")
    expected_nano_cpus = int(request.limits.cpus * 1_000_000_000)
    if host.get("NanoCpus") != expected_nano_cpus:
        errors.append("container CPU limit does not match")
    if host.get("PidsLimit") != request.limits.pids_limit:
        errors.append("container PID limit does not match")
    mounts = metadata.get("Mounts") if isinstance(metadata.get("Mounts"), list) else []
    if len(mounts) != 1 or not isinstance(mounts[0], dict):
        errors.append("container must have exactly one mount")
    else:
        mount = mounts[0]
        try:
            source = Path(str(mount.get("Source") or "")).resolve(strict=True)
        except OSError:
            source = Path("/__invalid_workspace_mount__")
        if (
            mount.get("Type") != "bind"
            or source != request.workspace.resolve()
            or mount.get("Destination") != "/workspace"
            or mount.get("RW") is not True
        ):
            errors.append("container workspace mount is invalid")
    environment = [value for value in (config.get("Env") or []) if isinstance(value, str)]
    if any(
        value.split("=", 1)[0] == INTERNAL_API_KEY_ENV
        or (bool(request.model.api_key) and request.model.api_key in value)
        for value in environment
    ):
        errors.append("container create configuration contains model credentials")
    if errors:
        raise ContainerRuntimeError(
            "container security contract failed: " + "; ".join(errors)
        )


def _security_summary(metadata: dict) -> dict[str, object]:
    host = metadata.get("HostConfig") or {}
    mounts = metadata.get("Mounts") or []
    return {
        "network_mode": host.get("NetworkMode"),
        "read_only_root": host.get("ReadonlyRootfs"),
        "cap_drop": host.get("CapDrop"),
        "cap_add": host.get("CapAdd") or [],
        "security_opt": host.get("SecurityOpt") or [],
        "memory_bytes": host.get("Memory"),
        "memory_swap_bytes": host.get("MemorySwap"),
        "nano_cpus": host.get("NanoCpus"),
        "pids_limit": host.get("PidsLimit"),
        "mounts": [
            {
                "type": mount.get("Type"),
                "source": mount.get("Source"),
                "destination": mount.get("Destination"),
                "rw": mount.get("RW"),
            }
            for mount in mounts
            if isinstance(mount, dict)
        ],
    }


def _safe_export_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ContainerArchiveError("container worker omitted trace_summary")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ContainerArchiveError("container worker returned an unsafe trace path")
    return path


def _rewrite_trace_paths(summary: dict, archive_root: Path) -> None:
    for key in ("events_path", "summary_path"):
        value = summary.get(key)
        if not isinstance(value, str):
            continue
        prefix = "/hb-output/"
        if value.startswith(prefix):
            relative = _safe_export_path(value[len(prefix) :])
            summary[key] = str((archive_root / relative).resolve())


def _write_archive_manifest(root: Path) -> None:
    entries: list[dict[str, object]] = []
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        if path.name == "archive_manifest.json":
            continue
        payload = path.read_bytes()
        entries.append(
            {
                "path": str(path.relative_to(root)),
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    _write_json(
        root / "archive_manifest.json",
        {
            "schema_version": "harnessbench_container_archive_v1",
            "files": entries,
        },
    )


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _completed_detail(completed: subprocess.CompletedProcess[str]) -> str:
    detail = (completed.stderr or completed.stdout or "").strip()
    return detail[:1000] or f"exit {completed.returncode}"


def _binary_completed_detail(completed: subprocess.CompletedProcess[bytes]) -> str:
    payload = completed.stderr or completed.stdout or b""
    detail = payload.decode("utf-8", errors="replace").strip()
    return detail[:1000] or f"exit {completed.returncode}"


def _redact_request_values(
    message: str,
    request: ContainerRunRequest,
    *,
    extra_sensitive_values: tuple[object, ...] = (),
) -> str:
    redacted = message
    subscription_env = (
        request.subscription_auth.get("env")
        if isinstance(request.subscription_auth, dict)
        else None
    )
    sensitive_values = (
        request.model.api_key,
        *request.transport_environment.values(),
        *(
            subscription_env.values()
            if isinstance(subscription_env, dict)
            else ()
        ),
        *((request.business_simulator_endpoint or {}).get("auth_token"),),
        *(
            (request.task_tool_endpoint or {}).get("auth_token"),
        ),
        *extra_sensitive_values,
    )
    for value in sorted(
        (
            item
            for item in sensitive_values
            if isinstance(item, str) and item
        ),
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(value, "<redacted>")
    return redacted


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
