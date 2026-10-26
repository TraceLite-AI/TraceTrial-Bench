from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import tracetrial_bench.batch as batch_module
from harness_tracer.provider_requests import (
    PROVIDER_REQUEST_EVIDENCE_SCHEMA,
    PROVIDER_REQUEST_EVIDENCE_VERSION,
    extract_structured_tool_inventory,
    read_provider_request_events,
)
from tracetrial_bench.container_runtime import (
    ContainerArchiveError,
    ContainerRuntimeError,
    DockerHarnessRuntime,
    load_salvaged_provider_request_evidence,
    persist_provider_request_salvage,
)
from tracetrial_bench.rate_limit_policy import detect_provider_rate_limit
from tracetrial_bench.runner import BenchmarkRunConfig, BenchmarkRunner


def _row(sequence: int, status: int) -> dict[str, object]:
    return {
        "schema_version": PROVIDER_REQUEST_EVIDENCE_SCHEMA,
        "version": PROVIDER_REQUEST_EVIDENCE_VERSION,
        "seq": sequence,
        "timestamp": f"2026-08-17T00:00:0{sequence}Z",
        "method": "POST",
        "path": "/v1/chat/completions",
        "upstream_status": status,
        "model_visible": 200 <= status < 300,
        "error_type": None,
        "tool_inventory": extract_structured_tool_inventory(b'{"tools":[]}'),
    }


def test_salvage_discards_partial_tail_and_recovers_429(tmp_path: Path) -> None:
    snapshot = (
        json.dumps(_row(1, 200))
        + "\n"
        + json.dumps(_row(2, 429))
        + "\n"
        + '{"schema_version":"partial'
    )

    evidence = persist_provider_request_salvage(tmp_path, snapshot)

    assert evidence is not None
    assert evidence["status"] == "salvaged_after_runtime_error"
    assert evidence["request_count"] == 2
    events = Path(str(evidence["events"]))
    assert [row["upstream_status"] for row in read_provider_request_events(events)] == [
        200,
        429,
    ]
    assert load_salvaged_provider_request_evidence(tmp_path) == evidence
    skip = detect_provider_rate_limit(
        {"provider_request_evidence": evidence}
    )
    assert skip is not None
    assert skip["classification"] == "unspecified"
    assert skip["request_seqs"] == [2]


def test_salvage_rejects_extra_fields_and_credentials(tmp_path: Path) -> None:
    extra = _row(1, 429)
    extra["response_body"] = "must not persist"
    with pytest.raises(ContainerArchiveError, match="row fields"):
        persist_provider_request_salvage(
            tmp_path / "extra",
            json.dumps(extra) + "\n",
        )

    secret = _row(1, 429)
    secret["tool_inventory"] = extract_structured_tool_inventory(
        b'{"tools":[{"type":"function","function":{"name":"x","description":"api-secret","parameters":{"type":"object"}}}]}'
    )
    with pytest.raises(ContainerArchiveError, match="credential"):
        persist_provider_request_salvage(
            tmp_path / "secret",
            json.dumps(secret) + "\n",
            secret_values=("api-secret",),
        )


def test_runtime_snapshots_recorder_file_before_container_cleanup(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    snapshot = json.dumps(_row(1, 429)) + "\n"

    def command_runner(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=snapshot,
            stderr="",
        )

    runtime = DockerHarnessRuntime(command_runner=command_runner)
    request = SimpleNamespace(
        artifacts_dir=tmp_path,
        model=SimpleNamespace(api_key="test-secret"),
        transport_environment={},
        business_simulator_endpoint=None,
        task_tool_endpoint=None,
    )

    evidence = runtime._salvage_provider_request_evidence(
        "a" * 64,
        request,
    )

    assert evidence is not None
    assert evidence["request_count"] == 1
    assert commands == [
        [
            "docker",
            "exec",
            "a" * 64,
            "/bin/cat",
            "/hb-output/provider_requests/events.jsonl",
        ]
    ]


def test_runner_marks_pre_archive_timeout_with_salvaged_429_skipped(
    tmp_path: Path,
) -> None:
    task = tmp_path / "task"
    (task / "workspace").mkdir(parents=True)
    (task / "verifier").mkdir()
    (task / "task.yaml").write_text("id: task-1\n", encoding="utf-8")
    (task / "prompt.md").write_text("Say ok.\n", encoding="utf-8")
    verifier = task / "verifier" / "run.sh"
    verifier.write_text(
        "#!/bin/sh\n"
        'touch "$HARNESSBENCH_RUN_DIR/verifier-ran"\n'
        "exit 99\n",
        encoding="utf-8",
    )
    verifier.chmod(0o755)
    registry = tmp_path / "model.toml"
    registry.write_text(
        "[models.test]\n"
        'provider = "provider"\n'
        'model = "model"\n'
        'base_url = "https://example.invalid/v1"\n'
        'api_mode = "chat_completions"\n'
        'api_key = "test-secret"\n',
        encoding="utf-8",
    )
    registry.chmod(0o600)

    class _TimedOutRuntime:
        def run(self, request):
            persist_provider_request_salvage(
                request.artifacts_dir,
                json.dumps(_row(1, 429)) + "\n",
                secret_values=(request.model.api_key,),
            )
            (request.artifacts_dir / "container_lifecycle.json").write_text(
                json.dumps(
                    {
                        "schema_version": "harnessbench_container_lifecycle_v1",
                        "container_id": "a" * 64,
                        "image": {
                            "reference": "harnessbench/codex:test",
                            "image_id": "sha256:" + "b" * 64,
                        },
                        "archive_status": "failed",
                        "cleanup_status": "complete",
                        "provider_request_salvage": {
                            "status": "salvaged_after_runtime_error",
                            "request_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            raise ContainerRuntimeError("container worker timed out")

    result = BenchmarkRunner(container_runtime=_TimedOutRuntime()).run(
        BenchmarkRunConfig(
            task=task,
            harness="codex",
            model="test",
            mode="docker",
            output_dir=tmp_path / "runs",
            model_registry=registry,
            evaluation_mode="run-and-score",
            skip_on_rate_limit=True,
        )
    )

    assert result["status"] == "skipped"
    assert result["execution"]["status"] == "infrastructure_error"
    assert result["evaluation"]["status"] == "skipped"
    assert result["skip"]["reason"] == "provider_rate_limit"
    assert result["skip"]["request_seqs"] == [1]
    assert result["metrics"]["step_count"] == 1
    run_dir = Path(result["result_path"]).parent
    assert not (run_dir / "verifier-ran").exists()
    assert (
        result["artifacts"]["provider_request_evidence"]["status"]
        == "salvaged_after_runtime_error"
    )
    report = (run_dir / "report.html").read_text(encoding="utf-8")
    assert '"status":"skipped"' in report
    assert "Rate-limit class" in report
    assert batch_module._result_is_complete(result["result_path"])
