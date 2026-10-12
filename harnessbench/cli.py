from __future__ import annotations

import argparse
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from harnessbench.batch import execute_batch, expand_work_items, resolve_task_inputs
from harnessbench.container_images import HarnessImageManager
from harnessbench.container_runtime import ContainerRuntimeError
from harnessbench.model_registry import (
    SUPPORTED_CONTAINER_HARNESSES,
    ModelRegistry,
    default_model_registry_path,
)
from harnessbench.models import normalize_harness_name
from harnessbench.runner import (
    BenchmarkRunConfig,
    BenchmarkRunner,
    BenchmarkScoreConfig,
)
from harnessbench.trace_report import generate_trace_report


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "image" and args.image_command == "build":
        return _image_build(args)
    if args.command == "report":
        return _report(args)
    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="harnessbench")
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="run one or more benchmark tasks")
    task_source = run.add_mutually_exclusive_group()
    task_source.add_argument(
        "--task",
        dest="tasks",
        action="append",
        type=Path,
        default=[],
        help="task directory; repeat to run multiple tasks",
    )
    task_source.add_argument(
        "--smoke",
        action="store_true",
        help="run the fixed C1a-academic-L3-001 layered 100-point smoke task",
    )
    task_source.add_argument(
        "--task-manifest",
        type=Path,
        help="UTF-8 file containing one task path per line",
    )
    run.set_defaults(smoke=False)
    run.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        default=[],
        help="harness name; repeat to compare harnesses",
    )
    run.add_argument(
        "--model",
        dest="models",
        action="append",
        default=[],
        help="model name (local) or registry alias (docker); repeat as needed",
    )
    run.add_argument(
        "--reasoning-intensity",
        help=(
            "optional reasoning intensity mapped per harness "
            "(codex config, Claude Code effort, OpenClaw thinking, DeepSeek config)"
        ),
    )
    run.add_argument("--mode", choices=["local", "docker"], default="local")
    run.add_argument(
        "--docker-auth",
        choices=["model-registry", "subscription"],
        default="model-registry",
        help=(
            "Docker authentication mode for codex/claudecode: use the model "
            "registry API gateway or native subscription account state"
        ),
    )
    run.add_argument(
        "--evaluation-mode",
        choices=["run-only", "score-only", "run-and-score"],
        default="run-and-score",
        help="Docker evaluation workflow (default: run-and-score)",
    )
    run.add_argument(
        "--run-dir",
        dest="run_dirs",
        action="append",
        type=Path,
        default=[],
        metavar="RUN_DIR",
        help="existing Docker run directory to score; repeat as needed",
    )
    run.add_argument("--output-dir", type=Path)
    run.add_argument("--harness-path")
    run.add_argument(
        "--judge-harness",
        help="deprecated compatibility option; judges now call the model API directly",
    )
    run.add_argument(
        "--judge-model",
        help=(
            "judge model registry alias in Docker mode; "
            "defaults to the evaluated model"
        ),
    )
    run.add_argument(
        "--judge-harness-path",
        help="deprecated compatibility option; ignored by direct API judges",
    )
    run.add_argument(
        "--harness-isolation",
        choices=["none", "macos"],
        default="none",
        help="isolate the harness process and create fresh harness state per run",
    )
    run.add_argument("--timeout", type=int)
    run.add_argument(
        "--docker-image",
        action="append",
        default=[],
        help="harness image override (IMAGE or HARNESS=IMAGE); repeat as needed",
    )
    run.add_argument(
        "--model-registry",
        type=Path,
        help=(
            "Docker model registry (execution default: "
            "~/.config/harnessbench/models.toml; score-only default: saved config)"
        ),
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="maximum simultaneous task-model runs or scoring jobs",
    )
    run.add_argument(
        "--skip-on-rate-limit",
        action="store_true",
        help=(
            "mark a model-registry Docker run skipped and bypass verifier/judge "
            "when the upstream provider returns HTTP 429"
        ),
    )
    run.add_argument(
        "--resume",
        type=Path,
        help="resume an existing batch directory",
    )
    run.add_argument(
        "--provider-source",
        choices=["ccswitch"],
        help="resolve harness provider config from an external source",
    )
    run.add_argument(
        "--provider-id", help="provider id in the selected provider source"
    )
    run.add_argument(
        "--provider-name", help="provider display name in the selected provider source"
    )
    run.add_argument(
        "--ccswitch-home", type=Path, help="override CC Switch config directory"
    )
    run.add_argument(
        "--validate-model",
        action="store_true",
        help="run harness-specific model preflight checks when available",
    )
    run.add_argument("--extra-arg", action="append", default=[])
    run.add_argument(
        "--instance-seed-file",
        type=Path,
        help="0600 runner-private seed store below the output directory",
    )
    run.add_argument(
        "--instance-seed-index",
        type=int,
        help="one-based seed index in --instance-seed-file",
    )

    image = subparsers.add_parser("image", help="manage local harness images")
    image_subparsers = image.add_subparsers(
        dest="image_command",
        required=True,
    )
    image_build = image_subparsers.add_parser(
        "build",
        help="build and validate reusable local harness images",
    )
    image_build.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        required=True,
        help="harness image to build; repeat as needed",
    )
    image_build.add_argument(
        "--model-registry",
        type=Path,
        help="model registry containing optional container build settings",
    )
    image_build.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/image-builds"),
        help="directory for image build summaries and logs",
    )
    report = subparsers.add_parser(
        "report",
        help="generate an offline HTML report for an existing run",
    )
    report.add_argument("run_dir", type=Path, help="completed benchmark run directory")
    return parser


def _run(args: argparse.Namespace) -> int:
    try:
        _validate_run_options(args)
        if args.concurrency < 1:
            raise ValueError("concurrency must be a positive integer")
        if args.evaluation_mode == "score-only":
            return _score_existing_runs(args)
        repository_root = Path(__file__).resolve().parents[1]
        tasks = resolve_task_inputs(
            tasks=args.tasks,
            smoke=args.smoke,
            task_manifest=args.task_manifest,
            repository_root=repository_root,
        )
        harnesses = [normalize_harness_name(value) for value in args.harnesses]
        work_items = expand_work_items(
            tasks=tasks,
            harnesses=harnesses,
            models=args.models,
        )
        image_overrides = _parse_docker_images(args.docker_image, harnesses)
        instance_seed = _resolve_instance_seed(args)
        extra_args = _parse_extra_args(args.extra_arg)
        output_dir = args.output_dir.expanduser().resolve(strict=False)
        model_registry = (
            args.model_registry.expanduser().resolve(strict=False)
            if args.model_registry is not None
            else None
        )

        def execute(item):
            return BenchmarkRunner().run(
                BenchmarkRunConfig(
                    task=item.task,
                    harness=item.harness,
                    model=item.model,
                    mode=args.mode,
                    docker_auth=args.docker_auth,
                    reasoning_intensity=args.reasoning_intensity,
                    output_dir=output_dir,
                    harness_path=args.harness_path,
                    timeout=args.timeout,
                    docker_image=image_overrides.get(item.harness),
                    model_registry=model_registry,
                    validate_model=args.validate_model,
                    provider_source=args.provider_source,
                    provider_id=args.provider_id,
                    provider_name=args.provider_name,
                    ccswitch_home=args.ccswitch_home,
                    harness_isolation=args.harness_isolation,
                    instance_seed=instance_seed,
                    extra_args=extra_args,
                    judge_harness=args.judge_harness,
                    judge_model=args.judge_model,
                    judge_harness_path=args.judge_harness_path,
                    evaluation_mode=args.evaluation_mode,
                    skip_on_rate_limit=args.skip_on_rate_limit,
                )
            )

        summary, summary_path = execute_batch(
            work_items=work_items,
            concurrency=args.concurrency,
            output_dir=output_dir,
            execute=execute,
            resume=args.resume,
            evaluation_mode=args.evaluation_mode,
            skip_on_rate_limit=args.skip_on_rate_limit,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: executable not found: {exc.filename}", file=sys.stderr)
        return 127

    if len(summary["results"]) == 1:
        print(summary["results"][0]["result_path"] or summary_path)
    else:
        print(summary_path)
    return 0 if summary["counts"]["non_pass"] == 0 else 1


def _score_existing_runs(args: argparse.Namespace) -> int:
    run_dirs = [path.expanduser().resolve(strict=False) for path in args.run_dirs]

    def score(run_dir: Path) -> dict:
        try:
            return BenchmarkRunner().score(
                BenchmarkScoreConfig(
                    run_dir=run_dir,
                    model_registry=args.model_registry,
                    judge_model=args.judge_model,
                )
            )
        except Exception as exc:
            return {
                "status": "error",
                "result_path": None,
                "run_dir": str(run_dir),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }

    if args.concurrency == 1:
        results = [score(run_dir) for run_dir in run_dirs]
    else:
        with ThreadPoolExecutor(
            max_workers=args.concurrency,
            thread_name_prefix="harnessbench-score",
        ) as executor:
            results = list(executor.map(score, run_dirs))

    for run_dir, result in zip(run_dirs, results):
        result_path = result.get("result_path")
        if isinstance(result_path, str) and result_path:
            print(result_path)
        else:
            print(
                f"error: {run_dir}: {result.get('error_type', 'Error')}: "
                f"{result.get('error_message', 'scoring failed')}",
                file=sys.stderr,
            )
    return 0 if all(result.get("status") == "pass" for result in results) else 1


def _image_build(args: argparse.Namespace) -> int:
    try:
        harnesses = list(
            dict.fromkeys(
                normalize_harness_name(value.strip()) for value in args.harnesses
            )
        )
        unsupported = sorted(set(harnesses) - SUPPORTED_CONTAINER_HARNESSES)
        if unsupported:
            raise ValueError(
                "image build supports "
                + ", ".join(sorted(SUPPORTED_CONTAINER_HARNESSES))
                + "; unsupported: "
                + ", ".join(unsupported)
            )
        registry = ModelRegistry.load(
            args.model_registry or default_model_registry_path()
        )
        build_dir = args.output_dir.expanduser() / f"build-{uuid.uuid4().hex[:12]}"
        build_dir.mkdir(parents=True, exist_ok=False)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: cannot create image build output: {exc}", file=sys.stderr)
        return 1

    started_at = _utc_now()
    records = [
        {
            "harness": harness,
            "status": "pending",
            "reference": None,
            "image_id": None,
            "digest": None,
            "built": None,
            "artifacts": {
                "build_log": f"{harness}/image_build.log",
                "image_lock": f"{harness}/image.lock.json",
            },
        }
        for harness in harnesses
    ]
    summary = {
        "schema_version": "harnessbench_image_build_v1",
        "build_id": build_dir.name,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "images": records,
    }
    summary_path = build_dir / "summary.json"
    _write_json_file(summary_path, summary)
    manager = HarnessImageManager()

    for record in records:
        harness = record["harness"]
        record["status"] = "building"
        _write_json_file(summary_path, summary)
        try:
            image = manager.ensure(
                harness,
                override=None,
                build_log=build_dir / harness / "image_build.log",
                build_config=registry.container_build,
            )
        except FileNotFoundError as exc:
            message = f"executable not found: {exc.filename or 'docker'}"
            error_type = type(exc).__name__
            exit_code = 127
        except ContainerRuntimeError as exc:
            message = str(exc)
            error_type = type(exc).__name__
            exit_code = 1
        except ValueError as exc:
            message = str(exc)
            error_type = type(exc).__name__
            exit_code = 2
        except OSError as exc:
            message = f"image build artifact error: {exc}"
            error_type = type(exc).__name__
            exit_code = 1
        else:
            record.update(
                {
                    "status": "validated",
                    "reference": image.reference,
                    "image_id": image.image_id,
                    "digest": image.digest,
                    "built": image.built,
                }
            )
            _write_json_file(summary_path, summary)
            continue

        safe_message = _redact_container_build_values(
            message,
            registry.container_build.hash_payload().values(),
        )
        record.update(
            {
                "status": "failed",
                "error_type": error_type,
                "error_message": safe_message,
            }
        )
        summary["status"] = "failed"
        summary["finished_at"] = _utc_now()
        _write_json_file(summary_path, summary)
        print(f"error: {safe_message}; summary: {summary_path}", file=sys.stderr)
        return exit_code

    summary["status"] = "complete"
    summary["finished_at"] = _utc_now()
    _write_json_file(summary_path, summary)
    print(summary_path)
    return 0


def _report(args: argparse.Namespace) -> int:
    try:
        report_path = generate_trace_report(args.run_dir)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(report_path)
    return 0


def _parse_docker_images(
    values: list[str], harnesses: list[str]
) -> dict[str, str]:
    parsed: dict[str, str] = {}
    unique_harnesses = list(dict.fromkeys(harnesses))
    for value in values:
        if "=" in value:
            harness, image = value.split("=", 1)
            if harness not in unique_harnesses or not image:
                raise ValueError(f"invalid docker image override: {value}")
            parsed[harness] = image
            continue
        if len(unique_harnesses) != 1 or not value:
            raise ValueError(
                "a bare --docker-image requires exactly one selected harness"
            )
        parsed[unique_harnesses[0]] = value
    return parsed


def _validate_run_options(args: argparse.Namespace) -> None:
    evaluation_mode = args.evaluation_mode
    if evaluation_mode == "score-only":
        if args.mode != "docker":
            raise ValueError("score-only requires --mode docker")
        if not args.run_dirs:
            raise ValueError("score-only requires at least one --run-dir")
        resolved_run_dirs = [
            path.expanduser().resolve(strict=False) for path in args.run_dirs
        ]
        if len(set(resolved_run_dirs)) != len(resolved_run_dirs):
            raise ValueError("score-only run directories must be unique")
        forbidden = (
            (bool(args.tasks), "--task"),
            (args.smoke, "--smoke"),
            (args.task_manifest is not None, "--task-manifest"),
            (bool(args.harnesses), "--harness"),
            (bool(args.models), "--model"),
            (args.output_dir is not None, "--output-dir"),
            (args.harness_path is not None, "--harness-path"),
            (args.judge_harness is not None, "--judge-harness"),
            (args.judge_harness_path is not None, "--judge-harness-path"),
            (args.harness_isolation != "none", "--harness-isolation"),
            (args.timeout is not None, "--timeout"),
            (bool(args.docker_image), "--docker-image"),
            (args.docker_auth != "model-registry", "--docker-auth"),
            (args.reasoning_intensity is not None, "--reasoning-intensity"),
            (args.resume is not None, "--resume"),
            (args.provider_source is not None, "--provider-source"),
            (args.provider_id is not None, "--provider-id"),
            (args.provider_name is not None, "--provider-name"),
            (args.ccswitch_home is not None, "--ccswitch-home"),
            (args.validate_model, "--validate-model"),
            (bool(args.extra_arg), "--extra-arg"),
            (args.instance_seed_file is not None, "--instance-seed-file"),
            (args.instance_seed_index is not None, "--instance-seed-index"),
            (args.skip_on_rate_limit, "--skip-on-rate-limit"),
        )
        used = [name for enabled, name in forbidden if enabled]
        if used:
            raise ValueError(
                ", ".join(used) + " cannot be used with score-only"
            )
        return

    if args.run_dirs:
        raise ValueError("--run-dir requires score-only")
    has_task_source = bool(args.tasks) or args.smoke or args.task_manifest is not None
    has_execution_matrix = (
        has_task_source
        and args.harnesses
        and args.models
        and args.output_dir is not None
    )
    if not has_execution_matrix:
        raise ValueError(
            f"{evaluation_mode} requires a task source, --harness, --model, "
            "and --output-dir"
        )
    if evaluation_mode == "run-only" and args.mode != "docker":
        raise ValueError("run-only requires --mode docker")

    if args.mode == "local":
        if args.docker_image:
            raise ValueError("--docker-image requires --mode docker")
        if args.model_registry is not None:
            raise ValueError("--model-registry requires --mode docker")
        if args.docker_auth != "model-registry":
            raise ValueError("--docker-auth requires --mode docker")
        if args.skip_on_rate_limit:
            raise ValueError("--skip-on-rate-limit requires --mode docker")
        return

    harnesses = [normalize_harness_name(value) for value in args.harnesses]
    unsupported = sorted(set(harnesses) - SUPPORTED_CONTAINER_HARNESSES)
    if unsupported:
        raise ValueError(
            "Docker mode supports "
            + ", ".join(sorted(SUPPORTED_CONTAINER_HARNESSES))
            + "; unsupported: "
            + ", ".join(unsupported)
        )
    forbidden = (
        (args.harness_path is not None, "--harness-path"),
        (args.harness_isolation != "none", "--harness-isolation"),
        (args.validate_model, "--validate-model"),
        (args.provider_source is not None, "--provider-source"),
        (args.provider_id is not None, "--provider-id"),
        (args.provider_name is not None, "--provider-name"),
        (args.ccswitch_home is not None, "--ccswitch-home"),
    )
    used = [name for enabled, name in forbidden if enabled]
    if used:
        raise ValueError(
            ", ".join(used)
            + " cannot be used in Docker mode; configure the model registry"
        )
    if args.docker_auth == "subscription":
        if args.skip_on_rate_limit:
            raise ValueError(
                "--skip-on-rate-limit requires --docker-auth model-registry"
            )
        unsupported_subscription = sorted(set(harnesses) - {"codex", "claudecode"})
        if unsupported_subscription:
            raise ValueError(
                "--docker-auth subscription supports only codex and claudecode; "
                "unsupported: " + ", ".join(unsupported_subscription)
            )
        if args.judge_model and args.model_registry is None:
            raise ValueError(
                "--judge-model in subscription Docker mode requires --model-registry"
            )
        if args.model_registry is not None:
            registry = ModelRegistry.load(args.model_registry)
            if args.judge_model:
                for harness in dict.fromkeys(harnesses):
                    registry.resolve(args.judge_model, harness)
        return

    registry = ModelRegistry.load(
        args.model_registry or default_model_registry_path()
    )
    for harness in dict.fromkeys(harnesses):
        for model in dict.fromkeys(args.models):
            registry.resolve(model, harness)


def _parse_extra_args(values: list[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"extra-arg must be KEY=VALUE: {value}")
        key, arg_value = value.split("=", 1)
        if not key:
            raise ValueError(f"extra-arg key must not be empty: {value}")
        parsed[key] = arg_value
    return parsed


def _resolve_instance_seed(args: argparse.Namespace) -> str | None:
    path = args.instance_seed_file
    index = args.instance_seed_index
    if path is None and index is None:
        return None
    if path is None or index is None:
        raise ValueError("instance seed file and index must be supplied together")
    if not isinstance(index, int) or index < 1:
        raise ValueError("instance seed index must be a positive integer")
    if path.is_symlink():
        raise ValueError("instance seed file may not be a symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(args.output_dir.resolve())
    except (FileNotFoundError, ValueError) as exc:
        raise ValueError(
            "instance seed file must be a regular file below the output directory"
        ) from exc
    if not resolved.is_file() or resolved.stat().st_mode & 0o077:
        raise ValueError("instance seed file must be regular and mode 0600")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("instance seed file is invalid") from exc
    values = payload.get("seeds") if isinstance(payload, dict) else None
    if not isinstance(values, list) or any(
        not isinstance(item, str) or not item for item in values
    ):
        raise ValueError("instance seed file is invalid")
    if index > len(values):
        raise ValueError("instance seed index is out of range")
    return values[index - 1]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_file(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _redact_container_build_values(text: str, values) -> str:
    redacted = text
    for value in sorted((value for value in values if value), key=len, reverse=True):
        redacted = redacted.replace(value, "<redacted>")
    return redacted


if __name__ == "__main__":
    raise SystemExit(main())
