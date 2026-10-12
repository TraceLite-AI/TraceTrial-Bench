from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping

from harnessbench.evaluator import run_verifier
from harnessbench.llm_judge import JudgeRequester, JudgeRuntime, run_layered_judge
from harnessbench.runner_policy import (
    apply_evaluation_policy,
    load_simulator_manifest,
    persist_runner_policy,
)
from harnessbench.types import EvaluationResult, TaskSpec
from harnessbench.workspace import RunLayout


@dataclass(frozen=True)
class ScoringOutcome:
    effective: EvaluationResult
    raw: EvaluationResult
    policy: dict[str, Any]
    policy_path: Path


SCORING_ARTIFACTS = (
    Path("artifacts/verifier_output"),
    Path("artifacts/llm_judge"),
    Path("artifacts/runner_policy.json"),
    Path("artifacts/scoring_error.json"),
)

SCORING_TRANSACTION_PATHS = (
    *SCORING_ARTIFACTS,
    Path("result.json"),
    Path("config.resolved.json"),
    Path("report.html"),
    Path("artifacts/trace_report_error.log"),
)


def reset_scoring_artifacts(run_dir: Path) -> None:
    for relative_path in SCORING_ARTIFACTS:
        path = run_dir / relative_path
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def create_scoring_stage(run_dir: Path) -> Path:
    artifacts_dir = run_dir / "artifacts"
    if artifacts_dir.is_symlink() or not artifacts_dir.is_dir():
        raise ValueError("run artifacts directory is missing or invalid")
    stage = artifacts_dir / f".scoring-stage-{uuid.uuid4().hex}"
    stage.mkdir(mode=0o700)
    return stage


def discard_scoring_stage(stage: Path) -> None:
    if stage.is_symlink():
        stage.unlink()
    elif stage.exists():
        shutil.rmtree(stage)


@contextmanager
def commit_scoring_artifacts(run_dir: Path, stage: Path) -> Iterator[None]:
    artifacts_dir = run_dir / "artifacts"
    try:
        stage.resolve(strict=True).relative_to(artifacts_dir.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError("scoring stage must stay below run artifacts") from exc
    if stage.is_symlink() or not stage.is_dir():
        raise ValueError("scoring stage is missing or invalid")

    backup = artifacts_dir / f".scoring-backup-{uuid.uuid4().hex}"
    backup.mkdir(mode=0o700)
    replaced: list[tuple[Path, Path | None]] = []
    rolled_back = False
    try:
        for relative in SCORING_TRANSACTION_PATHS:
            destination = run_dir / relative
            previous: Path | None = None
            if destination.is_symlink() or destination.exists():
                previous = backup / relative
                previous.parent.mkdir(parents=True, exist_ok=True)
                destination.replace(previous)
            replaced.append((destination, previous))
            if relative not in SCORING_ARTIFACTS:
                continue
            staged = stage / relative.relative_to("artifacts")
            if staged.is_symlink() or staged.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                staged.replace(destination)
        try:
            yield
        except BaseException:
            _rollback_scoring_transaction(replaced)
            rolled_back = True
            raise
    except BaseException:
        if not rolled_back:
            _rollback_scoring_transaction(replaced)
        raise
    finally:
        discard_scoring_stage(stage)
        if backup.exists():
            shutil.rmtree(backup)


def _rollback_scoring_transaction(
    replaced: list[tuple[Path, Path | None]],
) -> None:
    for destination, previous in reversed(replaced):
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        elif destination.is_dir():
            shutil.rmtree(destination)
        if previous is not None and previous.exists():
            previous.parent.mkdir(parents=True, exist_ok=True)
            previous.replace(destination)


def rebase_scoring_outcome(
    outcome: ScoringOutcome,
    *,
    stage: Path,
    artifacts_dir: Path,
) -> ScoringOutcome:
    def rebase_path(path: Path | None) -> Path | None:
        if path is None:
            return None
        try:
            relative = path.relative_to(stage)
        except ValueError as exc:
            raise ValueError("scoring output escaped its staging directory") from exc
        return artifacts_dir / relative

    def rebase_evaluation(value: EvaluationResult) -> EvaluationResult:
        return replace(
            value,
            stdout_path=rebase_path(value.stdout_path),
            stderr_path=rebase_path(value.stderr_path),
            score_path=rebase_path(value.score_path),
        )

    return replace(
        outcome,
        effective=rebase_evaluation(outcome.effective),
        raw=rebase_evaluation(outcome.raw),
        policy_path=rebase_path(outcome.policy_path),
    )


def score_layout(
    *,
    task: TaskSpec,
    layout: RunLayout,
    trace_summary: Mapping[str, Any],
    invocation_report: Mapping[str, Any] | None,
    simulator_evidence: Mapping[str, str] | None,
    harness: str,
    model: str,
    timeout: int | None,
    judge_runtime: JudgeRuntime | None = None,
    judge_requester: JudgeRequester | None = None,
) -> ScoringOutcome:
    evaluation = run_verifier(
        task,
        layout,
        timeout=timeout,
        harness=harness,
        model=model,
    )
    if task.scoring is not None:
        if judge_runtime is None:
            raise ValueError("LLM judge runtime is required for this task")
        evaluation = run_layered_judge(
            task=task,
            layout=layout,
            objective=evaluation,
            trace_summary=trace_summary,
            runtime=judge_runtime,
            requester=judge_requester,
        )

    policy_result = apply_evaluation_policy(
        task=task,
        trace_summary=trace_summary,
        raw_evaluation=evaluation,
        simulator_manifest=load_simulator_manifest(simulator_evidence),
        invocation_report=invocation_report,
    )
    policy_path = Path(
        persist_runner_policy(policy_result.report, layout.artifacts_dir)
    )
    return ScoringOutcome(
        effective=policy_result.effective,
        raw=policy_result.raw,
        policy=policy_result.report,
        policy_path=policy_path,
    )
