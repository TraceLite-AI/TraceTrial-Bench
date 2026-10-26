from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from tracetrial_bench.container_runtime import ContainerRuntimeError, HarnessImage
from tracetrial_bench.model_registry import (
    SUPPORTED_CONTAINER_HARNESSES,
    ContainerBuildConfig,
)


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ImageDefinition:
    reference: str
    dockerfile: str


DEFAULT_IMAGES = {
    "openclaw": ImageDefinition(
        reference="harnessbench/openclaw:2026.6.9",
        dockerfile="openclaw.Dockerfile",
    ),
    "hermes": ImageDefinition(
        reference="harnessbench/hermes:0.19.0",
        dockerfile="hermes.Dockerfile",
    ),
    "codex": ImageDefinition(
        reference="harnessbench/codex:0.145.0-litellm-1.93.0",
        dockerfile="codex.Dockerfile",
    ),
    "claudecode": ImageDefinition(
        reference="harnessbench/claudecode:2.1.220-litellm-1.93.0",
        dockerfile="claudecode.Dockerfile",
    ),
    "deepseek": ImageDefinition(
        reference="harnessbench/deepseek:0.1.0-rc.6",
        dockerfile="deepseek.Dockerfile",
    ),
}

if set(DEFAULT_IMAGES) != set(SUPPORTED_CONTAINER_HARNESSES):
    raise RuntimeError("container image catalog does not match the supported harness set")

WORKER_SOURCE_PATHS = (
    Path("__init__.py"),
    Path("business_simulator.py"),
    Path("container_environment.py"),
    Path("conversation_runner.py"),
    Path("container_worker.py"),
    Path("deepseek_bridge.mjs"),
    Path("deepseek_config.py"),
    Path("deepseek_runner.py"),
    Path("litellm_gateway.py"),
    Path("model_call_budget.py"),
    Path("model_registry.py"),
    Path("openclaw_gateway.py"),
    Path("provider_request_recorder.py"),
    Path("task_tool_mcp.py"),
    Path("types.py"),
)

_MIRROR_SENSITIVE_DOCKERFILES = frozenset(
    {
        "openclaw.Dockerfile",
        "codex.Dockerfile",
        "claudecode.Dockerfile",
        "deepseek.Dockerfile",
    }
)

_PROXY_BUILD_ARGUMENTS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)
_MIRROR_BUILD_ARGUMENTS = (
    ("DEBIAN_MIRROR", "debian_mirror"),
    ("DEBIAN_SECURITY_MIRROR", "debian_security_mirror"),
    ("NPM_REGISTRY", "npm_registry"),
)


class HarnessImageManager:
    def __init__(
        self,
        *,
        repository_root: Path | None = None,
        cache_root: Path | None = None,
        docker_bin: str = "docker",
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        self.repository_root = (
            repository_root or Path(__file__).resolve().parents[1]
        ).resolve()
        self.cache_root = (
            cache_root or Path.home() / ".cache" / "harnessbench" / "images"
        ).expanduser()
        self.docker_bin = docker_bin
        self.command_runner = command_runner

    def ensure(
        self,
        harness: str,
        *,
        override: str | None,
        build_log: Path,
        build_config: ContainerBuildConfig | None = None,
    ) -> HarnessImage:
        definition = DEFAULT_IMAGES.get(harness)
        if definition is None:
            raise ValueError(f"no container image definition for harness: {harness}")
        normalized_build = build_config or ContainerBuildConfig()
        context_sha256 = compute_image_context_sha256(
            self.repository_root,
            definition,
            build_config=normalized_build,
        )
        reference = override or f"{definition.reference}-{context_sha256[:12]}"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.cache_root / f"{harness}.lock"
        with lock_path.open("a+") as lock_stream:
            fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            metadata = self._inspect(reference)
            if metadata is not None and _metadata_matches(
                metadata,
                harness,
                context_sha256=(context_sha256 if override is None else None),
            ):
                image = _image_from_metadata(reference, metadata, built=False)
                _write_reuse_log(build_log, image)
                _write_image_lock(
                    build_log.parent / "image.lock.json",
                    image,
                    harness,
                    context_sha256=(context_sha256 if override is None else None),
                )
                return image
            if override is not None:
                raise ContainerRuntimeError(
                    f"custom harness image is missing required labels: {reference}"
                )
            self._build(
                definition,
                reference=reference,
                context_sha256=context_sha256,
                build_log=build_log,
                build_config=normalized_build,
            )
            metadata = self._inspect(reference)
            if metadata is None or not _metadata_matches(
                metadata, harness, context_sha256=context_sha256
            ):
                raise ContainerRuntimeError(
                    f"built harness image failed metadata validation: {reference}"
                )
            image = _image_from_metadata(reference, metadata, built=True)
            _write_image_lock(
                build_log.parent / "image.lock.json",
                image,
                harness,
                context_sha256=context_sha256,
            )
            return image

    def _inspect(self, reference: str) -> dict | None:
        completed = self._run(["image", "inspect", reference], timeout=60)
        if completed.returncode != 0:
            detail = f"{completed.stdout}\n{completed.stderr}".lower()
            if "no such image" in detail or "no such object" in detail:
                return None
            raise ContainerRuntimeError(
                f"docker image inspect failed for {reference}: "
                + (completed.stderr or completed.stdout).strip()[:1000]
            )
        try:
            values = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerRuntimeError("docker image inspect returned invalid JSON") from exc
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise ContainerRuntimeError("docker image inspect returned an invalid shape")
        return values[0]

    def _build(
        self,
        definition: ImageDefinition,
        *,
        reference: str,
        context_sha256: str,
        build_log: Path,
        build_config: ContainerBuildConfig,
    ) -> None:
        dockerfile_source = (
            self.repository_root / "docker" / "tracetrial_bench" / definition.dockerfile
        )
        if not dockerfile_source.is_file():
            raise ContainerRuntimeError(
                f"harness Dockerfile is missing: {dockerfile_source}"
            )
        with tempfile.TemporaryDirectory(
            prefix="build-context-", dir=self.cache_root
        ) as tmp:
            context = Path(tmp)
            shutil.copy2(dockerfile_source, context / "Dockerfile")
            _copy_worker_source(
                self.repository_root / "tracetrial_bench", context / "tracetrial_bench"
            )
            _copy_source_tree(
                self.repository_root / "harness_tracer", context / "harness_tracer"
            )
            command = [
                "image",
                "build",
                "--pull=false",
                "--build-arg",
                f"HARNESSBENCH_CONTEXT_SHA256={context_sha256}",
            ]
            for name in _PROXY_BUILD_ARGUMENTS:
                if os.environ.get(name):
                    command.extend(["--build-arg", name])
            build_environment: dict[str, str] | None = None
            if build_config.configured:
                build_environment = dict(os.environ)
                for environment_name, attribute_name in _MIRROR_BUILD_ARGUMENTS:
                    value = getattr(build_config, attribute_name)
                    if not value:
                        raise ValueError(
                            "configured container build requires all mirror values"
                        )
                    build_environment[environment_name] = value
                    command.extend(["--build-arg", environment_name])
            command.extend(
                [
                    "--tag",
                    reference,
                    "--file",
                    str(context / "Dockerfile"),
                    str(context),
                ]
            )
            completed = self._run(
                command,
                timeout=1800,
                env=build_environment,
            )
        build_log.parent.mkdir(parents=True, exist_ok=True)
        build_output = (completed.stdout or "") + (completed.stderr or "")
        for value in _build_log_redaction_values(build_config):
            if value:
                build_output = build_output.replace(value, "<redacted>")
        build_log.write_text(
            build_output,
            encoding="utf-8",
        )
        if completed.returncode != 0:
            raise ContainerRuntimeError(
                f"Docker image build failed for {definition.reference}; see {build_log}"
            )

    def _run(
        self,
        args: list[str],
        *,
        timeout: int,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.command_runner(
            [self.docker_bin, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=dict(env) if env is not None else None,
        )


def compute_image_context_sha256(
    repository_root: Path,
    definition: ImageDefinition,
    *,
    build_config: ContainerBuildConfig | None = None,
) -> str:
    repository = repository_root.resolve()
    sources = (
        (
            repository / "docker" / "tracetrial_bench" / definition.dockerfile,
            Path("Dockerfile"),
        ),
        *(
            (
                repository / "tracetrial_bench" / relative,
                Path("tracetrial_bench") / relative,
            )
            for relative in WORKER_SOURCE_PATHS
        ),
    )
    files: list[tuple[Path, Path]] = list(sources)
    package_root = repository / "harness_tracer"
    if not package_root.is_dir() or package_root.is_symlink():
        raise ContainerRuntimeError(
            f"image source package is missing or symlinked: {package_root}"
        )
    for path in sorted(package_root.rglob("*")):
        relative = path.relative_to(repository)
        if _ignored_source_path(relative):
            continue
        if path.is_symlink():
            raise ContainerRuntimeError(
                f"image build source may not be a symlink: {path}"
            )
        if path.is_file():
            files.append((path, relative))
    digest = hashlib.sha256()
    for source, relative in sorted(files, key=lambda item: str(item[1])):
        if source.is_symlink() or not source.is_file():
            raise ContainerRuntimeError(f"image build source is missing: {source}")
        payload = source.read_bytes()
        digest.update(str(relative).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    if definition.dockerfile in _MIRROR_SENSITIVE_DOCKERFILES:
        config_payload = json.dumps(
            (build_config or ContainerBuildConfig()).hash_payload(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        digest.update(b"container_build\0")
        digest.update(config_payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _copy_source_tree(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def _copy_worker_source(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for relative in WORKER_SOURCE_PATHS:
        source_path = source / relative
        if source_path.is_symlink() or not source_path.is_file():
            raise ContainerRuntimeError(
                f"container worker source is missing or symlinked: {source_path}"
            )
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)


def _ignored_source_path(relative: Path) -> bool:
    return "__pycache__" in relative.parts or relative.suffix in {".pyc", ".pyo"}


def _build_log_redaction_values(
    build_config: ContainerBuildConfig,
) -> tuple[str, ...]:
    values = set(build_config.hash_payload().values())
    for value in (
        build_config.debian_mirror,
        build_config.debian_security_mirror,
    ):
        if not value:
            continue
        parsed = urlsplit(value)
        values.add(
            urlunsplit(
                (
                    "http",
                    parsed.netloc,
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
        )
    return tuple(sorted((value for value in values if value), key=len, reverse=True))


def _metadata_matches(
    metadata: dict, harness: str, *, context_sha256: str | None
) -> bool:
    image_id = metadata.get("Id")
    labels = ((metadata.get("Config") or {}).get("Labels") or {})
    matches = bool(
        isinstance(image_id, str)
        and image_id.startswith("sha256:")
        and len(image_id) == 71
        and isinstance(labels, dict)
        and labels.get("org.harnessbench.harness") == harness
        and labels.get("org.harnessbench.worker") == "v1"
    )
    if context_sha256 is not None:
        matches = bool(
            matches
            and labels.get("org.harnessbench.context-sha256") == context_sha256
        )
    return matches


def _image_from_metadata(reference: str, metadata: dict, *, built: bool) -> HarnessImage:
    repo_digests = metadata.get("RepoDigests") or []
    digest = None
    if repo_digests and isinstance(repo_digests[0], str) and "@" in repo_digests[0]:
        digest = repo_digests[0].split("@", 1)[1]
    return HarnessImage(
        reference=reference,
        image_id=metadata["Id"],
        digest=digest,
        built=built,
    )


def _write_image_lock(
    path: Path,
    image: HarnessImage,
    harness: str,
    *,
    context_sha256: str | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": "harnessbench_image_lock_v1",
                "harness": harness,
                "reference": image.reference,
                "image_id": image.image_id,
                "digest": image.digest,
                "built": image.built,
                "context_sha256": context_sha256,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_reuse_log(path: Path, image: HarnessImage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "reused validated local image\n"
        f"reference={image.reference}\n"
        f"image_id={image.image_id}\n"
        f"digest={image.digest or 'none'}\n",
        encoding="utf-8",
    )
    temporary.replace(path)
