from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from harness_tracer.summary import build_summary, write_last_message, write_summary
from harness_tracer.timeutils import utc_now_iso
from harness_tracer.writer import EventWriter

from tracetrial_bench.codex_app_server import (
    CodexAppServerUnsupported,
    CodexDelegatedRolesDriver,
    CodexFreshSessionsDriver,
    CodexStagedContextDriver,
    CodexStagedContextResult,
)
from tracetrial_bench.types import TaskSpec
from tracetrial_bench.workspace import RunLayout


class UnsupportedConversationProtocol(ValueError):
    pass


class StagedContextDriver(Protocol):
    adapter_name: str

    def execute(
        self,
        *,
        episodes: list[dict[str, Any]],
        compaction_checkpoints: list[int],
        final_prompt: str,
        final_output_schema: dict[str, Any],
        writer: EventWriter,
    ) -> CodexStagedContextResult: ...


@dataclass(frozen=True)
class ProtocolRunConfig:
    task: TaskSpec
    harness: str
    model: str
    layout: RunLayout
    harness_path: str | None = None
    timeout: int | None = None
    env: dict[str, str] = field(default_factory=dict)


DriverFactory = Callable[[ProtocolRunConfig], StagedContextDriver]


class ProtocolRunner:
    def __init__(self, *, driver_factory: DriverFactory | None = None) -> None:
        self.driver_factory = driver_factory or _driver_for

    def run(self, config: ProtocolRunConfig) -> dict[str, Any]:
        protocol_spec = config.task.conversation_protocol
        if protocol_spec is None:
            raise ValueError("ProtocolRunner requires a task conversation_protocol")

        run_id = f"run-{uuid.uuid4().hex}"
        trace_dir = config.layout.artifacts_dir / "trace" / run_id
        trace_dir.mkdir(parents=True, exist_ok=False)
        events_path = trace_dir / "events.json"
        summary_path = trace_dir / "summary.json"
        last_message_path = trace_dir / "last_message.txt"
        evidence_path = (
            config.layout.run_dir / protocol_spec.normalized_evidence_path
        ).resolve()
        _require_below(
            evidence_path, config.layout.artifacts_dir.resolve(), "protocol evidence"
        )
        evidence_path.parent.mkdir(parents=True, exist_ok=True)

        started_at = utc_now_iso()
        started_monotonic = time.monotonic()
        writer = EventWriter(events_path, run_id=run_id)
        status = "failed"
        trace_complete = False
        error_type: str | None = None
        error_message: str | None = None
        session_id: str | None = None
        argv: list[str] = []
        final_answer: str | None = None
        evidence: dict[str, Any] = {
            "protocol_version": protocol_spec.version,
            "evidence_version": "runner_evidence_v2",
            "evidence_owner": "harnessbench-runner",
            "driver_status": "failed",
            "run_id": run_id,
            "task_id": config.task.task_id,
            "harness": config.harness,
            "model": config.model,
            "same_session": False,
            "episodes_injected": 0,
            "episode_ids_in_order": False,
            "history_tools_disabled": False,
            "stimulus_mounted_in_agent_workspace": True,
            "episode_receipts": [],
            "compactions": [],
            "final_prompt_sent": False,
        }

        try:
            if protocol_spec.version not in {
                "staged_context_v1", "fresh_sessions_v1", "delegated_roles_v1",
            }:
                raise UnsupportedConversationProtocol(
                    f"runner does not implement conversation protocol {protocol_spec.version!r}"
                )
            protocol = _read_object(protocol_spec.specification_path)
            episodes = _read_episodes(protocol_spec.episodes_path)
            _validate_episode_fixture(protocol, episodes)
            checkpoints = (
                _compaction_checkpoints(protocol)
                if protocol_spec.version == "staged_context_v1"
                else []
            )
            final_artifact, final_output_schema, declared_final_tools = _final_output_contract(
                protocol, version=protocol_spec.version
            )
            stimulus_mounted = _workspace_contains_digest(
                config.layout.workspace_dir,
                _sha256_bytes(protocol_spec.episodes_path.read_bytes()),
            )
            driver = self.driver_factory(config)
            writer.write(
                "run_started",
                harness=config.harness,
                model=config.model,
                protocol=protocol_spec.version,
                task_id=config.task.task_id,
                cwd=str(config.layout.agent_dir),
            )
            result = driver.execute(
                episodes=episodes,
                compaction_checkpoints=checkpoints,
                final_prompt=config.task.prompt,
                final_output_schema=final_output_schema,
                writer=writer,
            )
            session_id = result.session_id
            argv = result.argv
            final_answer = result.final_answer
            episode_ids = [str(item.get("episode_id") or "") for item in episodes]
            receipt_ids = [
                str(item.get("episode_id") or "") for item in result.episode_receipts
            ]
            final_payload = _parse_final_json(result.final_answer)
            final_artifact_path = (config.layout.agent_dir / final_artifact).resolve()
            _require_below(
                final_artifact_path,
                config.layout.workspace_dir.resolve(),
                "final staged-context artifact",
            )
            final_artifact_path.parent.mkdir(parents=True, exist_ok=True)
            serialized_final = (
                json.dumps(
                    final_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            final_artifact_path.write_text(serialized_final, encoding="utf-8")
            fresh_sessions = protocol_spec.version in {
                "fresh_sessions_v1", "delegated_roles_v1",
            }
            delegated_roles = protocol_spec.version == "delegated_roles_v1"
            receipt_threads = [
                item.get("thread_id")
                for item in result.episode_receipts
                if isinstance(item, dict)
            ]
            evidence.update(
                {
                    "driver_status": "completed",
                    "adapter": driver.adapter_name,
                    "server_user_agent": result.server_user_agent,
                    "session_id": result.session_id,
                    "same_session": all(
                        item.get("thread_id") == result.session_id
                        for item in result.episode_receipts
                    ),
                    "fresh_sessions": (
                        len(receipt_threads) == len(episodes)
                        and len(set(receipt_threads)) == len(episodes)
                        and result.session_id not in set(receipt_threads)
                    ) if fresh_sessions else False,
                    "delegated_roles": delegated_roles,
                    "role_ids": (
                        [
                            item.get("role_id")
                            for item in result.episode_receipts
                            if isinstance(item, dict)
                        ]
                        if delegated_roles else []
                    ),
                    "unique_role_views": (
                        len({
                            item.get("role_view_sha256")
                            for item in result.episode_receipts
                            if isinstance(item, dict)
                        })
                        if delegated_roles else 0
                    ),
                    "parent_join_after_children": (
                        bool(delegated_roles and result.final_prompt_event)
                    ),
                    "native_session_count": (
                        len(set(receipt_threads + [result.session_id]))
                        if fresh_sessions else 1
                    ),
                    "episodes_injected": len(result.episode_receipts),
                    "episode_ids_in_order": receipt_ids == episode_ids,
                    "history_tools_disabled": not fresh_sessions,
                    "history_delivery": (
                        "native_fresh_thread_turns_with_durable_workspace"
                        if fresh_sessions else "native_thread_inject_items"
                    ),
                    "stimulus_mounted_in_agent_workspace": stimulus_mounted,
                    "stimulus_sha256": _sha256_bytes(
                        protocol_spec.episodes_path.read_bytes()
                    ),
                    "protocol_sha256": _sha256_bytes(
                        protocol_spec.specification_path.read_bytes()
                    ),
                    "manifest_sha256": (
                        _sha256_bytes(protocol_spec.manifest_path.read_bytes())
                        if protocol_spec.manifest_path
                        else None
                    ),
                    "final_prompt_sha256": _sha256_text(config.task.prompt),
                    "episode_receipts": result.episode_receipts,
                    "compactions": result.compactions,
                    "final_prompt_sent": True,
                    "final_prompt_event": result.final_prompt_event,
                    "final_session_id": result.session_id,
                    "final_turn_id": result.final_turn_id,
                    "final_tools_enabled": (
                        result.final_tools_enabled
                        if fresh_sessions else []
                    ),
                    "final_output_materialized_by_runner": True,
                    "final_output_path": str(
                        final_artifact_path.relative_to(
                            config.layout.agent_dir.resolve()
                        )
                    ),
                    "final_output_sha256": _sha256_text(serialized_final),
                    "trace_events": str(events_path.relative_to(config.layout.run_dir)),
                }
            )
            if not _evidence_is_complete(
                evidence,
                expected_episodes=len(episodes),
                checkpoints=checkpoints,
                version=protocol_spec.version,
                expected_final_tools=declared_final_tools,
            ):
                raise RuntimeError(
                    f"driver returned incomplete {protocol_spec.version} evidence"
                )
            status = "finished"
            trace_complete = True
            writer.write(
                "run_finished",
                session_id=session_id,
                protocol=protocol_spec.version,
                evidence_path=str(
                    evidence_path.relative_to(config.layout.run_dir.resolve())
                ),
                trace_complete=True,
            )
        except (
            UnsupportedConversationProtocol,
            CodexAppServerUnsupported,
            FileNotFoundError,
        ) as exc:
            status = "ineligible"
            error_type = "unsupported_conversation_protocol"
            error_message = str(exc)
            evidence["driver_status"] = "ineligible"
            evidence["ineligible_reason"] = error_message
            writer.write(
                "run_failed", error_type=error_type, error_message=error_message
            )
        except Exception as exc:
            status = "failed"
            error_type = type(exc).__name__
            error_message = str(exc)
            evidence["driver_status"] = "failed"
            evidence["error_type"] = error_type
            evidence["error_message"] = error_message
            writer.write(
                "run_failed", error_type=error_type, error_message=error_message
            )
        finally:
            evidence_path.write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            writer.close()

        finished_at = utc_now_iso()
        summary = build_summary(
            writer=writer,
            run_id=run_id,
            run_dir=trace_dir,
            harness=config.harness,
            model=config.model,
            input_query=config.task.prompt,
            status=status,
            trace_complete=trace_complete,
            structured_trace_status="available",
            exit_code=0 if status == "finished" else None,
            timed_out=error_type == "CodexAppServerTimeout",
            error_type=error_type,
            error_message=error_message,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((time.monotonic() - started_monotonic) * 1000),
            session_id=session_id,
            cwd=str(config.layout.agent_dir),
            argv=argv,
            environment={},
            summary_path=summary_path,
        )
        if final_answer and not summary.get("final_answer"):
            summary["final_answer"] = final_answer
        summary["protocol_evidence_path"] = str(evidence_path)
        write_summary(summary, summary_path)
        write_last_message(summary, last_message_path)
        return summary


def _driver_for(config: ProtocolRunConfig) -> StagedContextDriver:
    if config.harness != "codex":
        raise UnsupportedConversationProtocol(
            f"no {config.task.conversation_protocol.version} adapter is installed "
            f"for harness {config.harness!r}"
        )
    if config.task.conversation_protocol.version == "fresh_sessions_v1":
        protocol = _read_object(
            config.task.conversation_protocol.specification_path
        )
        _, _, final_tools = _final_output_contract(
            protocol, version="fresh_sessions_v1"
        )
        return CodexFreshSessionsDriver(
            model=config.model,
            harness_path=config.harness_path,
            cwd=config.layout.agent_dir,
            env=config.env,
            timeout=config.timeout,
            final_tools_enabled=final_tools,
        )
    if config.task.conversation_protocol.version == "delegated_roles_v1":
        protocol = _read_object(
            config.task.conversation_protocol.specification_path
        )
        _, _, final_tools = _final_output_contract(
            protocol, version="delegated_roles_v1"
        )
        return CodexDelegatedRolesDriver(
            model=config.model,
            harness_path=config.harness_path,
            cwd=config.layout.agent_dir,
            role_root=config.layout.artifacts_dir / "delegated_roles",
            env=config.env,
            timeout=config.timeout,
            final_tools_enabled=final_tools,
        )
    return CodexStagedContextDriver(
        model=config.model,
        harness_path=config.harness_path,
        cwd=config.layout.agent_dir,
        env=config.env,
        timeout=config.timeout,
    )


def _read_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _read_episodes(path: Path) -> list[dict[str, Any]]:
    episodes: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError(f"episode line {line_number} is not an object")
        episodes.append(item)
    return episodes


def _validate_episode_fixture(
    protocol: dict[str, Any], episodes: list[dict[str, Any]]
) -> None:
    stimulus = (
        protocol.get("stimulus") if isinstance(protocol.get("stimulus"), dict) else {}
    )
    expected_count = stimulus.get("episode_count")
    if not isinstance(expected_count, int) or expected_count != len(episodes):
        raise ValueError(
            f"protocol expects {expected_count!r} episodes but fixture contains {len(episodes)}"
        )
    expected_sequences = list(range(1, len(episodes) + 1))
    actual_sequences = [item.get("seq") for item in episodes]
    if actual_sequences != expected_sequences:
        raise ValueError("episode seq values are not contiguous and ordered")
    expected_ids = [f"EVT-{index:03d}" for index in expected_sequences]
    actual_ids = [item.get("episode_id") for item in episodes]
    if actual_ids != expected_ids:
        raise ValueError("episode ids are not the expected ordered EVT sequence")
    if any(item.get("role") != "user" for item in episodes):
        raise ValueError("staged_context_v1 accepts only user history episodes")
    if any(not isinstance(item.get("content"), str) for item in episodes):
        raise ValueError("every episode must contain string content")


def _compaction_checkpoints(protocol: dict[str, Any]) -> list[int]:
    stages = protocol.get("stages") if isinstance(protocol.get("stages"), list) else []
    checkpoints = [
        item.get("after_episode")
        for item in stages
        if isinstance(item, dict)
        and item.get("action") == "harness_native_force_compaction"
    ]
    if not checkpoints or any(not isinstance(value, int) for value in checkpoints):
        raise ValueError("protocol does not define valid native compaction checkpoints")
    return list(checkpoints)


def _evidence_is_complete(
    evidence: dict[str, Any],
    *,
    expected_episodes: int,
    checkpoints: list[int],
    version: str,
    expected_final_tools: list[str],
) -> bool:
    compactions = evidence.get("compactions")
    compactions = compactions if isinstance(compactions, list) else []
    observed = {
        item.get("after_episode")
        for item in compactions
        if isinstance(item, dict)
        and item.get("observed") is True
        and item.get("native") is True
        and item.get("thread_id") == evidence.get("session_id")
        and bool(item.get("source_event"))
    }
    common = bool(
        evidence.get("evidence_owner") == "harnessbench-runner"
        and evidence.get("episodes_injected") == expected_episodes
        and evidence.get("episode_ids_in_order") is True
        and evidence.get("stimulus_mounted_in_agent_workspace") is False
        and evidence.get("final_prompt_sent") is True
        and evidence.get("final_output_materialized_by_runner") is True
        and isinstance(evidence.get("final_output_sha256"), str)
    )
    if version in {"fresh_sessions_v1", "delegated_roles_v1"}:
        receipts = evidence.get("episode_receipts")
        receipts = receipts if isinstance(receipts, list) else []
        if version == "delegated_roles_v1":
            role_ids = [
                item.get("role_id") for item in receipts if isinstance(item, dict)
            ]
            role_views = [
                item.get("role_view_sha256")
                for item in receipts if isinstance(item, dict)
            ]
            role_results = [
                item.get("result_sha256")
                for item in receipts if isinstance(item, dict)
            ]
            return bool(
                common
                and evidence.get("same_session") is False
                and evidence.get("fresh_sessions") is True
                and evidence.get("delegated_roles") is True
                and evidence.get("native_session_count") == expected_episodes + 1
                and evidence.get("final_session_id") == evidence.get("session_id")
                and evidence.get("final_tools_enabled") == expected_final_tools
                and len(role_ids) == expected_episodes
                and len(set(role_ids)) == expected_episodes
                and len(set(role_views)) == expected_episodes
                and all(isinstance(value, str) and len(value) == 64
                        for value in role_views + role_results)
                and evidence.get("parent_join_after_children") is True
            )
        memory_snapshots = [
            item.get("durable_memory_sha256")
            for item in receipts if isinstance(item, dict)
        ]
        return bool(
            common
            and evidence.get("same_session") is False
            and evidence.get("fresh_sessions") is True
            and evidence.get("native_session_count") == expected_episodes + 1
            and evidence.get("history_tools_disabled") is False
            and evidence.get("final_session_id") == evidence.get("session_id")
            and evidence.get("final_tools_enabled") == expected_final_tools
            and len(memory_snapshots) == expected_episodes
            and all(
                isinstance(value, str) and len(value) == 64
                for value in memory_snapshots
            )
        )
    return bool(
        common
        and evidence.get("same_session") is True
        and evidence.get("history_tools_disabled") is True
        and evidence.get("final_session_id") == evidence.get("session_id")
        and evidence.get("final_tools_enabled") == []
        and set(checkpoints).issubset(observed)
    )


def _workspace_contains_digest(workspace: Path, digest: str) -> bool:
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            if _sha256_bytes(path.read_bytes()) == digest:
                return True
        except OSError:
            continue
    return False


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _final_output_contract(
    protocol: dict[str, Any], *, version: str
) -> tuple[Path, dict[str, Any], list[str]]:
    stages = protocol.get("stages") if isinstance(protocol.get("stages"), list) else []
    final = next(
        (
            item
            for item in stages
            if isinstance(item, dict) and item.get("name") == "final_query"
        ),
        None,
    )
    if not isinstance(final, dict):
        raise ValueError("protocol is missing the final_query stage")
    artifact = final.get("expected_artifact")
    schema = final.get("output_schema")
    if not isinstance(artifact, str) or not artifact.startswith("workspace/"):
        raise ValueError("final_query expected_artifact must be below workspace/")
    if not isinstance(schema, dict):
        raise ValueError("final_query requires an output_schema")
    tools = final.get("tools_enabled")
    allowed_tools = (
        [["read_only_workspace"], ["workspace_without_memory_mutation"]]
        if version == "fresh_sessions_v1"
        else [["read_only_workspace"], ["workspace_after_join"]]
        if version == "delegated_roles_v1"
        else [[]]
    )
    if tools not in allowed_tools:
        raise ValueError(
            f"{version} final_query has unsupported tools_enabled={tools!r}"
        )
    if final.get("artifact_materializer") != "harnessbench-runner":
        raise ValueError("staged_context_v1 final artifact must be runner-materialized")
    return Path(artifact), schema, list(tools)


def _parse_final_json(value: str | None) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("final model turn did not return structured JSON")
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("final structured response must be a JSON object")
    return payload


def _require_below(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"{label} escapes runner-owned artifacts directory: {path}"
        ) from exc
