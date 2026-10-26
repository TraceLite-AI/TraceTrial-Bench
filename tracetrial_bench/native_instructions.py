from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tracetrial_bench.types import TaskSpec


def install_native_instructions(
    task: TaskSpec,
    *,
    harness: str,
    agent_dir: Path,
    artifacts_dir: Path,
    instance_seed: str | None,
) -> dict[str, Any] | None:
    """Install only the instruction convention native to the selected harness.

    Source templates remain task-private.  The installed files are visible to
    the harness because they are its native configuration surface; explicit
    agent reads can be detected from the native trace by the task verifier.
    """

    spec = task.native_instructions
    if spec is None:
        return None
    manifest = json.loads(spec.manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "scoped_files_v1":
        raise ValueError("invalid native instruction manifest schema")
    conventions = manifest.get("harnesses")
    layers = manifest.get("layers")
    if not isinstance(conventions, dict) or not isinstance(layers, list):
        raise ValueError("native instruction manifest requires harnesses and layers")
    convention = conventions.get(harness)
    if not isinstance(convention, dict):
        raise ValueError(
            f"task has no native instruction convention for harness {harness}"
        )
    filename = convention.get("filename")
    if not isinstance(filename, str) or not filename or "/" in filename:
        raise ValueError("native instruction filename must be a plain file name")

    installed: list[dict[str, Any]] = []
    task_root = task.task_dir.resolve()
    agent_root = agent_dir.resolve()
    for index, layer in enumerate(layers, start=1):
        if not isinstance(layer, dict):
            raise ValueError("native instruction layer must be an object")
        scope = _safe_relative(layer.get("scope"), "scope")
        template_rel = _safe_relative(layer.get("template"), "template")
        template = (task_root / template_rel).resolve()
        try:
            template.relative_to(task_root)
        except ValueError as exc:
            raise ValueError("native instruction template escapes task") from exc
        if not template.is_file():
            raise ValueError(f"missing native instruction template: {template_rel}")
        destination_dir = (agent_root / scope).resolve()
        try:
            destination_dir.relative_to(agent_root)
        except ValueError as exc:
            raise ValueError("native instruction scope escapes agent directory") from exc
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        if destination.exists():
            raise ValueError(
                f"native instruction install would overwrite {destination}"
            )
        raw_token = layer.get("token")
        token = (
            _materialize_opaque(task.task_id, instance_seed, raw_token)
            if isinstance(raw_token, str) and raw_token
            else ""
        )
        content = template.read_text(encoding="utf-8").replace("{{TOKEN}}", token)
        destination.write_text(content, encoding="utf-8")
        installed.append(
            {
                "order": index,
                "scope": str(scope),
                "native_filename": filename,
                "template": str(template_rel),
                "content_sha256": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "token_sha256": (
                    hashlib.sha256(token.encode("utf-8")).hexdigest()
                    if token
                    else None
                ),
            }
        )

    evidence = {
        "schema_version": "harnessbench_native_instructions_v1",
        "task_id": task.task_id,
        "harness": harness,
        "convention": filename,
        "seeded": instance_seed is not None,
        "layers": installed,
    }
    output_dir = artifacts_dir / "native_instructions"
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return evidence


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"native instruction {label} must be a relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"native instruction {label} must not escape")
    return path


def _materialize_opaque(task_id: str, seed: str | None, value: str) -> str:
    if seed is None:
        return value
    return "inst-" + hashlib.sha256(
        f"{task_id}\0{seed}\0{value}".encode("utf-8")
    ).hexdigest()[:20]
