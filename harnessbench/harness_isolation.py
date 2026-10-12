from __future__ import annotations

import ast
import hashlib
import importlib.machinery
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import sys
import sysconfig
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


MACOS_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
OPENCLAW_PRESERVED_TABLES = {
    "schema_meta",
    "auth_profile_store",
    "auth_profile_state",
}

_SAFE_INHERITED_ENV = {
    "LANG",
    "LANGUAGE",
    "TERM",
    "COLORTERM",
    "SHELL",
    "USER",
    "LOGNAME",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
}


# Formal lanes deliberately do not inherit PATH.  Both harnesses use absolute
# entry points; this small POSIX tool set is only for their shebangs and child
# commands.  Keep separate policy identifiers even though today's directory
# sets are equal, so a future harness-specific change cannot silently widen the
# other lane.
_HARNESS_FIXED_PATHS: dict[str, tuple[str, ...]] = {
    "openclaw": ("/usr/bin", "/bin"),
    "hermes": ("/usr/bin", "/bin"),
}
_RUNTIME_ALLOWLIST_SCHEMA_VERSION = "4"
_FORMAL_COMMAND_POLICY_MESSAGE = "blocked by formal command policy"
_HERMES_COMMAND_GUARD_PLUGIN = "harnessbench-command-guard"
_HERMES_FORMAL_TOOLSET = "harnessbench-formal"
_FORMAL_COMMAND_BIN_DIR = "formal-command-bin"

# User-/administrator-extended host trees are outside the benchmark input.
# A selected Harness runtime may be re-allowed below through its fingerprinted
# concrete closure (for example Hermes' pinned Python prefix), but unrelated
# tools and data in these roots must not be reachable by absolute path.
_HOST_EXTENSION_ROOTS = (
    Path("/Applications"),
    Path("/Library"),
    Path("/Network"),
    Path("/Users/Shared"),
    Path("/Volumes"),
    Path("/opt"),
    Path("/usr/local"),
)

_RUNNER_CONTROL_ENV = {
    "PATH",
    "HOME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "HERMES_HOME",
    "OPENCLAW_STATE_DIR",
    "OPENCLAW_CONFIG_PATH",
    "OPENCLAW_CONFIG",
    "OPENCLAW_WORKSPACE_DIR",
}


@dataclass(frozen=True)
class SourceFingerprint:
    basename: str
    sha256: str

    def as_json(self) -> dict[str, str]:
        return {"basename": self.basename, "sha256": self.sha256}


@dataclass(frozen=True)
class FreshHarnessState:
    root: Path
    environment: dict[str, str]
    source_files: tuple[SourceFingerprint, ...]
    initial_session_records: int
    initial_memory_records: int
    workspace_binding: dict[str, Any]
    retained_config_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessIsolationContext:
    mode: str
    environment: dict[str, str]
    command_prefix: tuple[str, ...]
    replace_environment: bool
    trace_info: dict[str, Any]
    redact_paths: tuple[str, ...]
    manifest: dict[str, Any] | None
    state_root: Path | None = None
    profile_path: Path | None = None
    runtime_paths: tuple[Path, ...] = ()
    runtime_alias_paths: tuple[Path, ...] = ()
    command_runtime_paths: tuple[Path, ...] = ()
    execution_paths: tuple[Path, ...] = ()
    command_wrapper: Path | None = None
    runtime_allowlist_sha256: str | None = None

    @classmethod
    def none(cls, environment: Mapping[str, str]) -> "HarnessIsolationContext":
        return cls(
            mode="none",
            environment=dict(environment),
            command_prefix=(),
            replace_environment=False,
            trace_info={"mode": "none", "fresh_state": False},
            redact_paths=(),
            manifest=None,
        )


def _validate_formal_qianfan_override(
    *, base_url: str | None, api_key_placeholder: str | None
) -> tuple[str, str] | None:
    if base_url is None and api_key_placeholder is None:
        return None
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError(
            "formal Qianfan base URL and API-key placeholder must be provided together"
        )
    if not isinstance(api_key_placeholder, str) or not api_key_placeholder.strip():
        raise ValueError(
            "formal Qianfan base URL and API-key placeholder must be provided together"
        )
    return base_url.strip(), api_key_placeholder


def prepare_harness_isolation(
    *,
    mode: str,
    harness: str,
    agent_dir: Path,
    private_dir: Path,
    benchmark_repo: Path,
    output_root: Path,
    harness_path: str | None,
    provider_environment: Mapping[str, str] | None = None,
    openclaw_home: Path | None = None,
    hermes_home: Path | None = None,
    parent_environment: Mapping[str, str] | None = None,
    formal_qianfan_base_url: str | None = None,
    formal_qianfan_api_key_placeholder: str | None = None,
) -> HarnessIsolationContext:
    """Prepare runner-owned state and a macOS Seatbelt command prefix.

    The returned private paths are for runner plumbing only.  Callers must pass
    ``trace_info`` (not this object or its paths) to persisted trace metadata.
    """

    formal_qianfan_override = _validate_formal_qianfan_override(
        base_url=formal_qianfan_base_url,
        api_key_placeholder=formal_qianfan_api_key_placeholder,
    )
    provider_environment = dict(provider_environment or {})
    if mode == "none":
        return HarnessIsolationContext.none(provider_environment)
    if mode != "macos":
        raise ValueError(f"unsupported harness isolation mode: {mode}")
    if sys.platform != "darwin":
        raise ValueError("--harness-isolation macos requires Darwin")
    if not MACOS_SANDBOX_EXEC.is_file() or not os.access(MACOS_SANDBOX_EXEC, os.X_OK):
        raise ValueError("--harness-isolation macos requires /usr/bin/sandbox-exec")
    if harness not in {"openclaw", "hermes"}:
        raise ValueError(
            "macos harness isolation currently supports only openclaw and hermes"
        )

    agent_dir = agent_dir.resolve()
    private_dir = private_dir.resolve()
    benchmark_repo = benchmark_repo.resolve()
    output_root = output_root.resolve()
    executable = resolve_executable(harness_path or harness)
    if _is_relative_to(executable, benchmark_repo):
        raise ValueError(
            "strict harness executable must be outside the benchmark repository"
        )

    state_root = private_dir / f"harness-state-{uuid.uuid4().hex}"
    state_root.mkdir(parents=True, mode=0o700)
    source_env = dict(provider_environment)
    if harness == "openclaw":
        source_home = (openclaw_home or (Path.home() / ".openclaw")).resolve()
        provider_config = source_env.get("OPENCLAW_CONFIG_PATH") or source_env.get(
            "OPENCLAW_CONFIG"
        )
        config_source = Path(provider_config).resolve() if provider_config else None
        fresh = prepare_openclaw_fresh_state(
            source_home=source_home,
            state_root=state_root,
            agent_dir=agent_dir,
            config_source=config_source,
            formal_qianfan_base_url=(
                formal_qianfan_override[0] if formal_qianfan_override else None
            ),
            formal_qianfan_api_key_placeholder=(
                formal_qianfan_override[1] if formal_qianfan_override else None
            ),
        )
    else:
        source_home = (hermes_home or (Path.home() / ".hermes")).resolve()
        fresh = prepare_hermes_fresh_state(
            source_home=source_home,
            state_root=state_root,
            agent_dir=agent_dir,
            formal_qianfan_base_url=(
                formal_qianfan_override[0] if formal_qianfan_override else None
            ),
            formal_qianfan_api_key_placeholder=(
                formal_qianfan_override[1] if formal_qianfan_override else None
            ),
        )

    environment = sanitized_harness_environment(
        state=fresh,
        provider_environment=provider_environment,
        parent_environment=parent_environment,
        harness=harness,
    )
    # Discover the selected client while PATH still equals the fixed base
    # policy. Hermes receives the runner-owned command directory only after
    # discovery, so that directory can never be mistaken for client runtime.
    runtime_paths = discover_harness_runtime_paths(
        harness=harness,
        executable=executable,
        source_home=source_home,
        environment=environment,
    )
    runtime_alias_paths = discover_harness_runtime_alias_paths(executable)
    command_wrapper = _install_formal_command_wrapper(
        state_root=state_root,
        agent_dir=agent_dir,
    )
    command_runtime_paths = discover_formal_command_runtime_paths(command_wrapper)
    execution_paths = discover_harness_execution_paths(
        harness=harness,
        executable=executable,
        command_wrapper=command_wrapper,
    )

    # OpenClaw honors SHELL directly. Hermes resolves bash from PATH first, so
    # its per-run directory is the sole non-system PATH entry. Both routes lead
    # to the same runner-owned non-shell dispatcher; model command text is
    # never evaluated by a general-purpose host shell.
    environment["SHELL"] = str(command_wrapper)
    environment["OPENCLAW_EXEC_SHELL_SNAPSHOT"] = "off"
    if harness == "hermes":
        environment["PATH"] = os.pathsep.join(
            [str(command_wrapper.parent), *_HARNESS_FIXED_PATHS[harness]]
        )
    sibling_home = (
        (hermes_home or (Path.home() / ".hermes"))
        if harness == "openclaw"
        else (openclaw_home or (Path.home() / ".openclaw"))
    )
    runtime_forbidden_roots = (
        benchmark_repo,
        output_root,
        private_dir,
        state_root,
    )
    validate_harness_runtime_allowlist(
        harness=harness,
        executable=executable,
        environment=environment,
        runtime_paths=runtime_paths,
        user_home=Path.home(),
        forbidden_roots=runtime_forbidden_roots,
        sibling_runtime_paths=(sibling_home,),
    )
    validate_harness_runtime_aliases(
        harness=harness,
        alias_paths=runtime_alias_paths,
        runtime_paths=runtime_paths,
        forbidden_roots=runtime_forbidden_roots,
        sibling_runtime_paths=(sibling_home,),
    )
    validate_formal_command_runtime(
        command_runtime_paths=command_runtime_paths,
        execution_paths=execution_paths,
        command_wrapper=command_wrapper,
        executable=executable,
        forbidden_roots=(benchmark_repo, output_root),
    )
    runtime_allowlist_sha = harness_runtime_allowlist_fingerprint(
        harness=harness,
        executable=executable,
        environment=environment,
        runtime_paths=runtime_paths,
        runtime_alias_paths=runtime_alias_paths,
        command_runtime_paths=command_runtime_paths,
        execution_paths=execution_paths,
        command_wrapper=command_wrapper,
    )
    profile = build_macos_profile(
        user_home=Path.home(),
        benchmark_repo=benchmark_repo,
        output_root=output_root,
        private_dir=private_dir,
        agent_dir=agent_dir,
        state_root=state_root,
        global_openclaw_home=(openclaw_home or (Path.home() / ".openclaw")),
        global_hermes_home=(hermes_home or (Path.home() / ".hermes")),
        runtime_paths=runtime_paths,
        runtime_alias_paths=runtime_alias_paths,
        command_runtime_paths=command_runtime_paths,
        execution_paths=execution_paths,
    )
    profile_path = private_dir / f"harness-{uuid.uuid4().hex}.sb"
    profile_path.write_text(profile, encoding="utf-8")
    profile_path.chmod(0o600)
    profile_sha = _sha256_bytes(profile.encode("utf-8"))
    manifest = {
        "schema_version": "1",
        "mode": "macos",
        "harness": harness,
        "fresh_state": True,
        "source_files": [item.as_json() for item in fresh.source_files],
        "initial_state": {
            "session_records": fresh.initial_session_records,
            "memory_records": fresh.initial_memory_records,
        },
        "profile_sha256": profile_sha,
        "runtime_allowlist": {
            "schema_version": _RUNTIME_ALLOWLIST_SCHEMA_VERSION,
            "policy": f"{harness}-pinned-runtime-command-graph-v4",
            "security_scope": (
                "benchmark_private_state_host_extensions_and_process_execution"
            ),
            "complete_host_execution_closure": True,
            "path_sha256": _sha256_bytes(environment["PATH"].encode("utf-8")),
            "entry_count": len(runtime_paths),
            "alias_count": len(runtime_alias_paths),
            "command_runtime_entry_count": len(command_runtime_paths),
            "execution_entry_count": len(execution_paths),
            "fingerprint_sha256": runtime_allowlist_sha,
        },
        "command_policy": {
            "model_tools": ["exec" if harness == "openclaw" else "terminal"],
            "command_entrypoint": "workspace/taskctl",
            "host_process_default": "deny",
            "runner_owned_dispatcher": True,
        },
        "workspace_binding": fresh.workspace_binding,
        "retained_config_top_level_keys": list(fresh.retained_config_keys),
        "environment": {
            "inherit_parent": False,
            "home": "synthetic_per_run",
            "state": "synthetic_per_run",
        },
    }
    return HarnessIsolationContext(
        mode="macos",
        environment=environment,
        command_prefix=(str(MACOS_SANDBOX_EXEC), "-f", str(profile_path)),
        replace_environment=True,
        trace_info={
            "mode": "macos",
            "fresh_state": True,
            "profile_sha256": profile_sha,
            "runtime_allowlist_sha256": runtime_allowlist_sha,
        },
        redact_paths=tuple(
            str(path)
            for path in _deduplicate_paths(
                [
                    private_dir,
                    state_root,
                    profile_path,
                    benchmark_repo,
                    output_root,
                    Path.home(),
                    (openclaw_home or (Path.home() / ".openclaw")),
                    (hermes_home or (Path.home() / ".hermes")),
                ]
            )
        ),
        manifest=manifest,
        state_root=state_root,
        profile_path=profile_path,
        runtime_paths=runtime_paths,
        runtime_alias_paths=runtime_alias_paths,
        command_runtime_paths=command_runtime_paths,
        execution_paths=execution_paths,
        command_wrapper=command_wrapper,
        runtime_allowlist_sha256=runtime_allowlist_sha,
    )


def prepare_openclaw_fresh_state(
    *,
    source_home: Path,
    state_root: Path,
    agent_dir: Path,
    config_source: Path | None = None,
    formal_qianfan_base_url: str | None = None,
    formal_qianfan_api_key_placeholder: str | None = None,
) -> FreshHarnessState:
    formal_qianfan_override = _validate_formal_qianfan_override(
        base_url=formal_qianfan_base_url,
        api_key_placeholder=formal_qianfan_api_key_placeholder,
    )
    source_home = source_home.resolve()
    state_root = state_root.resolve()
    agent_dir = agent_dir.resolve()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    source_config = (config_source or (source_home / "openclaw.json")).resolve()
    source_db = source_home / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    _require_regular_file(source_config, "OpenClaw config")
    _require_regular_file(source_db, "OpenClaw agent database")

    try:
        config = json.loads(source_config.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"invalid OpenClaw config: {source_config.name}: {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise ValueError("OpenClaw config root must be an object")
    clean_config = _clean_openclaw_config(
        config,
        agent_dir=agent_dir,
        formal_qianfan_override=formal_qianfan_override,
    )
    destination_config = state_root / "openclaw.json"
    destination_config.write_text(
        json.dumps(clean_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    destination_config.chmod(0o600)

    # OpenClaw has two independent exec policy layers.  Keep the exposed tool
    # surface to exec only, then bind its durable approval to the one synthetic
    # business-simulator client.  The NUL-delimited argument expression
    # requires at least one non-empty argv item and cannot match a different
    # executable segment in a compound shell command.
    taskctl = agent_dir / "workspace" / "taskctl"
    approvals = {
        "version": 1,
        "defaults": {
            "security": "allowlist",
            "ask": "off",
            "askFallback": "deny",
            "autoAllowSkills": False,
        },
        "agents": {
            "main": {
                "security": "allowlist",
                "ask": "off",
                "askFallback": "deny",
                "autoAllowSkills": False,
                "allowlist": [
                    {
                        "pattern": str(taskctl),
                        "argPattern": "^(?:[^\x00]+\x00)+$",
                    }
                ],
            }
        },
    }
    approvals_path = state_root / "exec-approvals.json"
    approvals_path.write_text(
        json.dumps(approvals, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    approvals_path.chmod(0o600)

    destination_db = state_root / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    destination_db.parent.mkdir(parents=True, mode=0o700)
    _backup_and_scrub_openclaw_db(
        source_db,
        destination_db,
        clear_auth_profiles=formal_qianfan_override is not None,
    )
    destination_db.chmod(0o600)

    session_records, memory_records = _openclaw_private_record_counts(destination_db)
    if session_records or memory_records:
        raise RuntimeError("OpenClaw fresh-state scrub left session or memory records")

    os_home = _create_synthetic_os_home(state_root)
    environment = {
        "HOME": str(os_home),
        "OPENCLAW_STATE_DIR": str(state_root),
        "OPENCLAW_CONFIG_PATH": str(destination_config),
        "OPENCLAW_CONFIG": str(destination_config),
        "OPENCLAW_WORKSPACE_DIR": str(agent_dir),
    }
    if formal_qianfan_override is not None:
        environment["QIANFAN_API_KEY"] = formal_qianfan_override[1]
    return FreshHarnessState(
        root=state_root,
        environment=environment,
        source_files=(
            _fingerprint(source_config),
            _fingerprint(source_db),
        ),
        initial_session_records=session_records,
        initial_memory_records=memory_records,
        workspace_binding={
            "kind": "openclaw_config_and_environment",
            "target": "agent",
            "exact": True,
            "config_sha256": _sha256_bytes(destination_config.read_bytes()),
            "envelope_timestamp": "off",
        },
        retained_config_keys=tuple(sorted(clean_config)),
    )


def prepare_hermes_fresh_state(
    *,
    source_home: Path,
    state_root: Path,
    agent_dir: Path,
    formal_qianfan_base_url: str | None = None,
    formal_qianfan_api_key_placeholder: str | None = None,
) -> FreshHarnessState:
    formal_qianfan_override = _validate_formal_qianfan_override(
        base_url=formal_qianfan_base_url,
        api_key_placeholder=formal_qianfan_api_key_placeholder,
    )
    source_home = source_home.resolve()
    state_root = state_root.resolve()
    agent_dir = agent_dir.resolve()
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    sources = (source_home / "config.yaml", source_home / ".env")
    for source in sources:
        _require_regular_file(source, f"Hermes {source.name}")
    raw_config = _parse_yaml_subset(sources[0].read_text(encoding="utf-8"))
    raw_env = _parse_dotenv(sources[1].read_text(encoding="utf-8"))
    available_env_names = set(raw_env)
    if formal_qianfan_override is not None:
        available_env_names.add("QIANFAN_API_KEY")
    clean_config, credential_names = _clean_hermes_config(
        raw_config,
        available_env_names,
        agent_dir=agent_dir,
    )
    if formal_qianfan_override is not None:
        _apply_formal_hermes_qianfan_override(
            clean_config,
            base_url=formal_qianfan_override[0],
        )
        credential_names = {"QIANFAN_API_KEY"}
    destination_config = state_root / "config.yaml"
    # JSON is a strict subset of YAML and avoids carrying comments, tags,
    # anchors, block scalars, or other source-level prompt material forward.
    destination_config.write_text(
        json.dumps(clean_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    destination_config.chmod(0o600)
    destination_env = state_root / ".env"
    destination_credentials = (
        {"QIANFAN_API_KEY": formal_qianfan_override[1]}
        if formal_qianfan_override is not None
        else {name: raw_env[name] for name in credential_names}
    )
    destination_env.write_text(
        "".join(
            f"{name}={destination_credentials[name]}\n"
            for name in sorted(destination_credentials)
        ),
        encoding="utf-8",
    )
    destination_env.chmod(0o600)
    _install_hermes_command_guard(state_root)

    os_home = _create_synthetic_os_home(state_root)
    environment = {"HOME": str(os_home), "HERMES_HOME": str(state_root)}
    if formal_qianfan_override is not None:
        environment["QIANFAN_API_KEY"] = formal_qianfan_override[1]
    return FreshHarnessState(
        root=state_root,
        environment=environment,
        source_files=tuple(_fingerprint(source) for source in sources),
        initial_session_records=0,
        initial_memory_records=0,
        workspace_binding={
            "kind": "process_cwd",
            "target": "agent",
            "exact": True,
        },
        retained_config_keys=tuple(sorted(clean_config)),
    )


def sanitized_harness_environment(
    *,
    state: FreshHarnessState,
    provider_environment: Mapping[str, str] | None,
    parent_environment: Mapping[str, str] | None = None,
    harness: str | None = None,
) -> dict[str, str]:
    """Build a replacement environment rather than inheriting host secrets."""

    parent = parent_environment if parent_environment is not None else os.environ
    environment: dict[str, str] = {}
    for key, value in parent.items():
        if key in _SAFE_INHERITED_ENV or key.startswith("LC_"):
            environment[key] = value
    selected_harness = harness or "openclaw"
    try:
        fixed_path = _HARNESS_FIXED_PATHS[selected_harness]
    except KeyError as exc:
        raise ValueError(
            f"unsupported harness runtime policy: {selected_harness}"
        ) from exc
    environment["PATH"] = os.pathsep.join(fixed_path)
    environment.setdefault("LANG", "C.UTF-8")

    # Provider configuration is explicit runner input.  It may legitimately
    # contain the one credential required for the selected provider, but it
    # may not redirect runner-owned state or the synthetic HOME.
    for key, value in (provider_environment or {}).items():
        if key not in _RUNNER_CONTROL_ENV:
            environment[key] = value

    environment.update(state.environment)
    environment["PATH"] = os.pathsep.join(fixed_path)
    xdg_root = state.root / "xdg"
    tmp_root = state.root / "tmp"
    for path in (
        xdg_root / "config",
        xdg_root / "data",
        xdg_root / "state",
        xdg_root / "cache",
        tmp_root,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment.update(
        {
            "XDG_CONFIG_HOME": str(xdg_root / "config"),
            "XDG_DATA_HOME": str(xdg_root / "data"),
            "XDG_STATE_HOME": str(xdg_root / "state"),
            "XDG_CACHE_HOME": str(xdg_root / "cache"),
            "TMPDIR": str(tmp_root),
        }
    )
    return environment


def _install_hermes_command_guard(state_root: Path) -> Path:
    """Install the runner-owned fail-closed Hermes pre-dispatch policy."""

    plugin_dir = state_root / "plugins" / _HERMES_COMMAND_GUARD_PLUGIN
    plugin_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    manifest = plugin_dir / "plugin.yaml"
    manifest.write_text(
        "\n".join(
            [
                f"name: {_HERMES_COMMAND_GUARD_PLUGIN}",
                "version: 1.0.0",
                'description: "Runner-owned formal command boundary"',
                "kind: standalone",
                "provides_hooks:",
                "  - pre_tool_call",
                "",
            ]
        ),
        encoding="utf-8",
    )
    source = plugin_dir / "__init__.py"
    plugin_source = """from __future__ import annotations

import shlex

_BLOCK = @@BLOCK@@
_TOOLSET = @@TOOLSET@@
_FORBIDDEN = frozenset("\\x00\\n\\r;&|<>`$()\\\\")
_ALLOWED_KEYS = frozenset({
    "command", "timeout", "background", "workdir", "pty",
    "notify_on_complete", "watch_patterns",
})


def _blocked():
    return {"action": "block", "message": _BLOCK}


def _valid_command(command):
    if not isinstance(command, str) or not command.strip():
        return False
    if not command.startswith("./workspace/taskctl "):
        return False
    if len(command.encode("utf-8")) > 16384:
        return False
    if any(
        character in _FORBIDDEN
        or ord(character) < 32
        or ord(character) == 127
        for character in command
    ):
        return False
    try:
        argv = shlex.split(command, posix=True)
    except (TypeError, ValueError):
        return False
    if not 2 <= len(argv) <= 129 or argv[0] != "./workspace/taskctl":
        return False
    return all(
        value
        and len(value.encode("utf-8")) <= 4096
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in argv[1:]
    )


def _pre_tool_call(tool_name="", args=None, **_kwargs):
    # Hermes deliberately treats plugin exceptions as non-fatal. Catch every
    # internal policy error here so a guard bug cannot turn into permission.
    try:
        if tool_name != "terminal" or not isinstance(args, dict):
            return _blocked()
        if set(args) - _ALLOWED_KEYS:
            return _blocked()
        if args.get("workdir") is not None:
            return _blocked()
        if args.get("background") not in (None, False):
            return _blocked()
        if args.get("pty") not in (None, False):
            return _blocked()
        if args.get("notify_on_complete") not in (None, False):
            return _blocked()
        if args.get("watch_patterns") not in (None, []):
            return _blocked()
        timeout = args.get("timeout")
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= 300
        ):
            return _blocked()
        if not _valid_command(args.get("command")):
            return _blocked()
        return None
    except BaseException:
        return _blocked()


def register(ctx):
    # A custom one-tool toolset avoids Hermes' built-in `terminal` toolset,
    # which also contains the long-lived `process` tool. Register the guard
    # before publishing that toolset: Hermes treats plugin failures as
    # non-fatal, so the opposite order could leave a visible terminal without
    # its pre-dispatch hook when hook registration fails.
    from toolsets import create_custom_toolset

    ctx.register_hook("pre_tool_call", _pre_tool_call)
    create_custom_toolset(
        _TOOLSET,
        "Runner-owned formal terminal boundary",
        tools=["terminal"],
        includes=[],
    )
"""
    plugin_source = plugin_source.replace(
        "@@BLOCK@@", repr(_FORMAL_COMMAND_POLICY_MESSAGE)
    ).replace("@@TOOLSET@@", repr(_HERMES_FORMAL_TOOLSET))
    source.write_text(plugin_source, encoding="utf-8")
    for path in (manifest, source):
        path.chmod(0o400)
    plugin_dir.chmod(0o500)
    return plugin_dir


def _validate_formal_command_interpreter(candidate: Path) -> Path:
    """Resolve one real, immutable-enough Python executable for the dispatcher.

    ``/usr/bin/python3`` is an xcode-select shim on supported macOS releases.
    Executing it can open an installer path when developer tools are absent, so
    it is never an admissible formal command interpreter even when the shim
    itself is a regular executable.
    """

    logical = candidate.expanduser().absolute()
    if any(character.isspace() for character in str(logical)):
        raise ValueError("formal command interpreter path is not shebang-safe")
    try:
        resolved = logical.resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise ValueError("formal command interpreter is unavailable") from exc
    if sys.platform == "darwin" and resolved == Path("/usr/bin/python3"):
        raise ValueError("formal command interpreter resolves to a system shim")
    if (
        not stat.S_ISREG(info.st_mode)
        or not os.access(resolved, os.X_OK)
        or info.st_mode & 0o022
    ):
        raise ValueError("formal command interpreter is not a sealed executable")
    return resolved


def _select_formal_command_interpreter() -> Path:
    """Pin the real Python executable already running the trusted runner."""

    rejected: list[ValueError] = []
    for raw in (sys.executable, getattr(sys, "_base_executable", "")):
        if not isinstance(raw, str) or not raw:
            continue
        try:
            return _validate_formal_command_interpreter(Path(raw))
        except ValueError as exc:
            rejected.append(exc)
    if rejected:
        raise ValueError(
            "no non-shim formal command interpreter is available"
        ) from rejected[0]
    raise ValueError("formal command interpreter identity is unavailable")


def _formal_wrapper_interpreter(command_wrapper: Path) -> Path:
    """Recover and validate the exact interpreter frozen in a wrapper."""

    try:
        with command_wrapper.open("rb") as handle:
            first_line = handle.readline(4097)
    except OSError as exc:
        raise ValueError("formal command dispatcher is unavailable") from exc
    if len(first_line) > 4096 or not first_line.endswith(b"\n"):
        raise ValueError("formal command dispatcher shebang is invalid")
    try:
        decoded = first_line.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        raise ValueError("formal command dispatcher shebang is invalid") from exc
    if not decoded.startswith("#!") or not decoded.endswith(" -IS"):
        raise ValueError("formal command dispatcher interpreter is not isolated")
    raw = decoded[2:-4]
    if not raw.startswith("/") or not raw:
        raise ValueError("formal command dispatcher interpreter is not absolute")
    return _validate_formal_command_interpreter(Path(raw))


def _install_formal_command_wrapper(*, state_root: Path, agent_dir: Path) -> Path:
    """Install a non-shell dispatcher used as both clients' exec shell.

    The wrapper accepts the fixed invocation forms emitted by OpenClaw and
    Hermes, extracts one direct taskctl argv, and sends it to the exact
    runner-owned simulator Unix socket. It never evaluates command text or
    executes the workspace taskctl through a general-purpose host shell.
    """

    bin_dir = state_root / _FORMAL_COMMAND_BIN_DIR
    bin_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    wrapper = bin_dir / "bash"
    taskctl = agent_dir / "workspace" / "taskctl"
    _validate_taskctl_file(taskctl)
    taskctl_bytes = taskctl.read_bytes()
    if len(taskctl_bytes) > 1024 * 1024:
        raise ValueError("formal task interface is unexpectedly large")
    interpreter = _select_formal_command_interpreter()
    interpreter_info = interpreter.stat()
    interpreter_identity = (
        interpreter_info.st_dev,
        interpreter_info.st_ino,
        interpreter_info.st_mode,
        interpreter_info.st_uid,
        interpreter_info.st_size,
        interpreter_info.st_mtime_ns,
        interpreter_info.st_ctime_ns,
    )
    quoted_cwd = _shell_quote(str(agent_dir))
    wrapper_source = """#!@@INTERPRETER@@ -IS
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import stat
import sys

AGENT_DIR = @@AGENT_DIR@@
QUOTED_AGENT_DIR = @@QUOTED_AGENT_DIR@@
INTERPRETER = @@INTERPRETER_REPR@@
INTERPRETER_IDENTITY = @@INTERPRETER_IDENTITY@@
TASKCTL = @@TASKCTL@@
TASKCTL_BYTES = @@TASKCTL_BYTES@@
SOCKET_PATH = ".harnessbench-business-simulator.sock"
BLOCK = @@BLOCK@@
FORBIDDEN = frozenset("\\x00\\n\\r;&|<>`$()\\\\")
MAX_COMMAND_BYTES = 16384
MAX_ARGUMENT_BYTES = 4096
MAX_ARGUMENTS = 128
MAX_RESPONSE_BYTES = 1024 * 1024


def deny():
    sys.stderr.write(BLOCK + "\\n")
    return 126


def unwrap_hermes(command):
    lines = command.splitlines()
    if len(lines) != 6:
        return command
    if lines[0] != "builtin cd -- " + QUOTED_AGENT_DIR + " || exit 126":
        return None
    if not (lines[1].startswith("eval '") and lines[1].endswith("'")):
        return None
    if lines[2:4] != ["__hermes_ec=$?", "umask 077"]:
        return None
    prefix = "printf '\\\\n"
    suffix = "\\\\n' \\\"$(pwd -P)\\\""
    if not lines[4].startswith(prefix) or not lines[4].endswith(suffix):
        return None
    payload = lines[4][len(prefix):-len(suffix)]
    marker = re.fullmatch(
        r"(__HERMES_CWD_[0-9a-f]{12}__)%s(__HERMES_CWD_[0-9a-f]{12}__)",
        payload,
    )
    if marker is None or marker.group(1) != marker.group(2):
        return None
    if lines[5] != "exit $__hermes_ec":
        return None
    escaped = lines[1][6:-1]
    decoded = escaped.replace("'\\\\''", "'")
    if decoded.replace("'", "'\\\\''") != escaped:
        return None
    return decoded


def extract_command(argv):
    accepted = (
        ["-c"],
        ["-l", "-c"],
        ["-i", "-c"],
        ["--noprofile", "--norc", "-c"],
        ["-lc"],
        ["-lic"],
    )
    if len(argv) < 2 or argv[:-1] not in accepted:
        return None
    return unwrap_hermes(argv[-1])


def main():
    try:
        interpreter_info = os.lstat(INTERPRETER)
        observed_interpreter_identity = (
            interpreter_info.st_dev,
            interpreter_info.st_ino,
            interpreter_info.st_mode,
            interpreter_info.st_uid,
            interpreter_info.st_size,
            interpreter_info.st_mtime_ns,
            interpreter_info.st_ctime_ns,
        )
        if (
            os.path.realpath(sys.executable) != INTERPRETER
            or not stat.S_ISREG(interpreter_info.st_mode)
            or interpreter_info.st_mode & 0o022
            or observed_interpreter_identity != INTERPRETER_IDENTITY
        ):
            return deny()
    except OSError:
        return deny()
    command = extract_command(sys.argv[1:])
    if not isinstance(command, str) or not command.strip():
        return deny()
    # OpenClaw's enforced authorization renderer canonicalizes the approved
    # relative executable to this frozen absolute TASKCTL path. Hermes keeps
    # the relative spelling. Accept exactly those two spellings; both are
    # subsequently parsed and compared as a complete argv[0].
    allowed_entrypoints = ("./workspace/taskctl", TASKCTL)
    if not any(command.startswith(entrypoint + " ") for entrypoint in allowed_entrypoints):
        return deny()
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        return deny()
    if os.path.realpath(os.getcwd()) != AGENT_DIR:
        return deny()
    if any(ch in FORBIDDEN or ord(ch) < 32 or ord(ch) == 127 for ch in command):
        return deny()
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return deny()
    if not 2 <= len(argv) <= MAX_ARGUMENTS + 1:
        return deny()
    if argv[0] not in allowed_entrypoints:
        return deny()
    if any(
        not value
        or len(value.encode("utf-8")) > MAX_ARGUMENT_BYTES
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        for value in argv[1:]
    ):
        return deny()
    try:
        info = os.lstat(TASKCTL)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or not info.st_mode & stat.S_IXUSR
        ):
            return deny()
        with open(TASKCTL, "rb") as handle:
            observed_taskctl = handle.read(len(TASKCTL_BYTES) + 1)
        if observed_taskctl != TASKCTL_BYTES:
            return deny()
    except OSError:
        return deny()
    request = {"argv": argv[1:]}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(30)
            client.connect(SOCKET_PATH)
            payload = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
            client.sendall(payload.encode("utf-8") + b"\\n")
            with client.makefile("rb") as stream:
                raw = stream.readline(MAX_RESPONSE_BYTES + 1)
    except (OSError, TimeoutError):
        sys.stderr.write("business simulator unavailable\\n")
        return 2
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        sys.stderr.write("business simulator unavailable\\n")
        return 2
    try:
        response = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        sys.stderr.write("business simulator unavailable\\n")
        return 2
    sys.stdout.write(
        json.dumps(response, ensure_ascii=False, indent=2, sort_keys=True) + "\\n"
    )
    return 1 if isinstance(response, dict) and response.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
"""
    wrapper_source = (
        wrapper_source.replace("@@INTERPRETER@@", str(interpreter))
        .replace("@@AGENT_DIR@@", repr(str(agent_dir)))
        .replace("@@QUOTED_AGENT_DIR@@", repr(quoted_cwd))
        .replace("@@INTERPRETER_REPR@@", repr(str(interpreter)))
        .replace("@@INTERPRETER_IDENTITY@@", repr(interpreter_identity))
        .replace("@@TASKCTL@@", repr(str(taskctl)))
        .replace("@@TASKCTL_BYTES@@", repr(taskctl_bytes))
        .replace("@@BLOCK@@", repr(_FORMAL_COMMAND_POLICY_MESSAGE))
    )
    wrapper.write_text(wrapper_source, encoding="utf-8")
    wrapper.chmod(0o500)
    bin_dir.chmod(0o500)
    return wrapper


def _validate_taskctl_file(taskctl: Path) -> None:
    try:
        info = taskctl.lstat()
    except OSError as exc:
        raise ValueError("formal task interface is unavailable") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or not info.st_mode & stat.S_IXUSR
    ):
        raise ValueError("formal task interface is not sealed")


def discover_formal_command_runtime_paths(
    command_wrapper: Path,
) -> tuple[Path, ...]:
    """Return the pinned interpreter's read-only standard-library closure."""

    interpreter = _formal_wrapper_interpreter(command_wrapper)
    current_interpreter = _select_formal_command_interpreter()
    if interpreter != current_interpreter:
        raise ValueError("formal command dispatcher interpreter identity drifted")
    candidates: list[Path] = [command_wrapper, interpreter]
    for key in ("stdlib", "platstdlib"):
        value = sysconfig.get_path(key)
        if value:
            path = Path(value).resolve()
            if path.is_dir():
                candidates.append(path)
    library_dir = sysconfig.get_config_var("LIBDIR")
    library_name = sysconfig.get_config_var("LDLIBRARY")
    if isinstance(library_dir, str) and isinstance(library_name, str):
        library = (Path(library_dir) / library_name).resolve()
        if library.is_file():
            candidates.append(library)
    return tuple(_minimal_path_cover(candidates))


def discover_harness_execution_paths(
    *,
    harness: str,
    executable: Path,
    command_wrapper: Path,
) -> tuple[Path, ...]:
    """Discover the exact executable-file closure for one formal lane."""

    if harness not in _HARNESS_FIXED_PATHS:
        raise ValueError(f"unsupported harness runtime policy: {harness}")
    candidates: list[Path] = [
        Path("/bin/sh"),
        # On current macOS, sandbox-exec launches /bin/bash as the selected
        # executable variant for /bin/sh.  Both identities must therefore be
        # present in an exact process-exec allowlist for every lane.
        Path("/bin/bash"),
        # The paired runner executes these exact binaries only during its
        # pre-launch isolation canaries.  They must be in the same finite
        # process-exec closure as the harness or a fail-closed profile would
        # reject the canary before it could test filesystem/network policy.
        Path("/bin/cat"),
        Path("/usr/bin/touch"),
        Path("/bin/ls"),
        Path("/usr/bin/nc"),
        _formal_wrapper_interpreter(command_wrapper),
        command_wrapper,
        executable.expanduser().absolute(),
    ]
    script_path = executable.resolve()
    script_text = ""
    try:
        if script_path.stat().st_size <= 1_000_000:
            script_text = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    first_line = script_text.splitlines()[0] if script_text else ""
    if first_line.startswith("#!"):
        try:
            shebang = shlex.split(first_line[2:].strip(), posix=True)
        except ValueError:
            shebang = []
        if shebang and shebang[0].startswith("/"):
            candidates.append(Path(shebang[0]))
            if Path(shebang[0]).name == "env":
                program = next(
                    (item for item in shebang[1:] if not item.startswith("-")),
                    "",
                )
                if program:
                    for directory in _HARNESS_FIXED_PATHS[harness]:
                        found = Path(directory) / program
                        if found.is_file() and os.access(found, os.X_OK):
                            candidates.append(found)
                            break
    for raw in _absolute_paths_in_text(script_text):
        candidate = Path(raw)
        if (
            candidate.is_file()
            and os.access(candidate, os.X_OK)
            and candidate.name in {"node", "python", "python3"}
        ):
            candidates.append(candidate)

    # Include the finite executable hand-off set for Apple's developer Python.
    for support in discover_formal_command_runtime_paths(command_wrapper):
        if not support.is_dir():
            continue
        for pattern in (
            "bin/python3*",
            "Resources/Python.app/Contents/MacOS/Python",
        ):
            for candidate in support.glob(pattern):
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    candidates.append(candidate)

    unique: dict[str, Path] = {}
    for raw in candidates:
        logical = raw.expanduser().absolute()
        if logical.is_file() and os.access(logical, os.X_OK):
            unique.setdefault(str(logical), logical)
            try:
                resolved = logical.resolve(strict=True)
            except OSError:
                continue
            unique.setdefault(str(resolved), resolved)
    result = tuple(unique.values())
    _validate_exact_execution_paths(result)
    return result


def _validate_exact_execution_paths(paths: Sequence[Path]) -> None:
    if not paths:
        raise ValueError("formal execution allowlist is empty")
    seen: set[str] = set()
    for raw in paths:
        logical = raw.expanduser().absolute()
        if str(logical) in seen:
            raise ValueError("formal execution allowlist is not unique")
        seen.add(str(logical))
        try:
            target = logical.resolve(strict=True)
            info = target.stat()
        except OSError as exc:
            raise ValueError("formal execution identity is unavailable") from exc
        if not stat.S_ISREG(info.st_mode) or not os.access(target, os.X_OK):
            raise ValueError("formal execution allowlist contains a non-executable")
        if info.st_mode & 0o022:
            raise ValueError("formal execution allowlist contains a writable file")


def validate_formal_command_runtime(
    *,
    command_runtime_paths: Sequence[Path],
    execution_paths: Sequence[Path],
    command_wrapper: Path,
    executable: Path,
    forbidden_roots: Sequence[Path] = (),
) -> None:
    """Fail closed if command support or exact execution identities drift."""

    if tuple(_minimal_path_cover(command_runtime_paths)) != tuple(
        _deduplicate_paths(command_runtime_paths)
    ):
        raise ValueError("formal command runtime is not canonical and minimal")
    for path in command_runtime_paths:
        if not path.exists() or not (path.is_file() or path.is_dir()):
            raise ValueError("formal command runtime entry is unavailable")
    _validate_exact_execution_paths(execution_paths)
    resolved_execution = {path.resolve() for path in execution_paths}
    interpreter = _formal_wrapper_interpreter(command_wrapper)
    required = {
        Path("/bin/sh").resolve(),
        command_wrapper.resolve(),
        interpreter,
        executable.resolve(),
    }
    if not required.issubset(resolved_execution):
        raise ValueError("formal execution allowlist is missing a required entry")
    wrapper_resolved = command_wrapper.resolve()
    if interpreter not in {path.resolve() for path in command_runtime_paths}:
        raise ValueError("formal command runtime is missing its interpreter")
    for path in (*command_runtime_paths, *execution_paths):
        resolved = path.resolve()
        if resolved == wrapper_resolved:
            continue
        if any(_paths_overlap(resolved, root.resolve()) for root in forbidden_roots):
            raise ValueError("formal command runtime overlaps a forbidden root")


def _shell_quote(value: str) -> str:
    """Small local equivalent of shlex.quote without importing shell code."""

    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", value):
        return value
    return "'" + value.replace("'", "'\\\"'\\\"'") + "'"


def build_macos_profile(
    *,
    user_home: Path,
    benchmark_repo: Path,
    output_root: Path,
    private_dir: Path,
    agent_dir: Path,
    state_root: Path,
    global_openclaw_home: Path,
    global_hermes_home: Path,
    runtime_paths: Sequence[Path],
    runtime_alias_paths: Sequence[Path] = (),
    command_runtime_paths: Sequence[Path] = (),
    execution_paths: Sequence[Path] = (),
) -> str:
    """Build the benchmark/private-state deny overlay for macOS Seatbelt.

    System resources remain readable under ``allow default`` while protected
    host trees are denied. Process execution is a separate fail-closed layer:
    it is globally denied and re-opened only for exact runner-discovered files.
    """

    deny_read = _deduplicate_paths(
        [
            user_home,
            benchmark_repo,
            output_root,
            private_dir,
            global_openclaw_home,
            global_hermes_home,
            *_generic_temp_roots(),
            *_HOST_EXTENSION_ROOTS,
        ]
    )
    deny_write = _deduplicate_paths(
        [
            user_home,
            benchmark_repo,
            output_root,
            private_dir,
            *_generic_temp_roots(),
            *_HOST_EXTENSION_ROOTS,
        ]
    )
    allow_read = _deduplicate_paths(
        [agent_dir, state_root, *runtime_paths, *command_runtime_paths]
    )
    allow_write = _deduplicate_paths([agent_dir, state_root])
    selected_execution = tuple(execution_paths) or (
        # Compatibility fallback for direct profile construction in host
        # isolation canaries.  Formal runs always pass the runner-discovered
        # closure explicitly.
        Path("/bin/sh"),
        Path("/bin/bash"),
        Path("/bin/cat"),
        Path("/usr/bin/touch"),
        Path("/bin/ls"),
        Path("/usr/bin/nc"),
        *(path for path in runtime_paths if path.is_file()),
    )
    _validate_exact_execution_paths(selected_execution)
    metadata_traversal = _ancestor_paths(
        [*allow_read, *allow_write, *selected_execution]
    )
    alias_directories = [path for path in runtime_alias_paths if path.is_dir()]
    alias_files = [path for path in runtime_alias_paths if not path.is_dir()]
    logical_metadata_traversal = _logical_ancestor_paths(runtime_alias_paths)
    lines = ["(version 1)", "(allow default)", "(deny process-exec)"]
    lines.extend(_profile_rule("deny", "file-read*", deny_read))
    lines.extend(_profile_rule("deny", "file-write*", deny_write))
    lines.extend(
        _profile_literal_rule("allow", "file-read-metadata", metadata_traversal)
    )
    lines.extend(
        _profile_logical_literal_rule(
            "allow",
            "file-read-metadata",
            logical_metadata_traversal,
        )
    )
    lines.extend(_profile_rule("allow", "file-read*", allow_read))
    lines.extend(_profile_logical_rule("allow", "file-read*", alias_directories))
    lines.extend(_profile_logical_literal_rule("allow", "file-read*", alias_files))
    lines.extend(_profile_rule("allow", "file-write*", allow_write))
    lines.extend(_profile_literal_rule("allow", "process-exec", selected_execution))
    lines.extend(
        _profile_logical_literal_rule("allow", "process-exec", selected_execution)
    )
    return "\n".join(lines) + "\n"


def discover_harness_runtime_paths(
    *,
    harness: str,
    executable: Path,
    source_home: Path,
    environment: Mapping[str, str],
) -> tuple[Path, ...]:
    """Discover only the selected harness' concrete runtime closure.

    The executable's containing directory, the harness' whole state tree and
    host PATH directories are intentionally excluded.  Adding any of those as
    a Seatbelt ``subpath`` exception would expose sibling executables, caches,
    and unrelated user-installed commands.
    """

    if harness not in _HARNESS_FIXED_PATHS:
        raise ValueError(f"unsupported harness runtime policy: {harness}")
    _validate_formal_harness_path(harness, environment)

    paths: list[Path] = [executable]
    try:
        resolved_executable = executable.resolve(strict=True)
    except OSError:
        resolved_executable = executable.resolve()
    paths.append(resolved_executable)

    script_text = ""
    try:
        if executable.stat().st_size <= 1_000_000:
            script_text = executable.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    for raw in _absolute_paths_in_text(script_text):
        candidate = Path(raw)
        # Runtime launchers need interpreters and source entry points, not
        # arbitrary absolute data paths which happen to occur in comments or
        # arguments.
        if candidate.name in {"node", "python", "python3"} or candidate.suffix in {
            ".js",
            ".mjs",
            ".cjs",
            ".py",
        }:
            paths.extend(_runtime_scope_for_path(candidate))
    first_line = script_text.splitlines()[0] if script_text else ""
    if first_line.startswith("#!"):
        interpreter_text = first_line[2:].strip().split()[0]
        if interpreter_text.startswith("/"):
            interpreter = Path(interpreter_text)
            fixed_system_roots = [
                Path(item).resolve() for item in _HARNESS_FIXED_PATHS[harness]
            ]
            if not any(
                _is_relative_to(interpreter.resolve(), root)
                for root in fixed_system_roots
            ):
                paths.extend(_runtime_scope_for_path(interpreter))

    if harness == "hermes":
        venv_roots = [path for path in paths if "venvs" in path.parts]
        for path in tuple(venv_roots):
            venv_root = _venv_root(path)
            if venv_root is None:
                continue
            paths.append(venv_root)
            discovered = _editable_install_roots(venv_root)
            paths.extend(discovered)
            interpreter = venv_root / "bin" / "python3"
            if interpreter.exists():
                resolved = interpreter.resolve()
                paths.extend([resolved, _installation_prefix(resolved)])

    return tuple(_minimal_path_cover(paths))


def discover_harness_runtime_alias_paths(executable: Path) -> tuple[Path, ...]:
    """Return logical symlink hops needed to reach canonical runtime paths."""

    candidates = [executable.expanduser().absolute()]
    script_text = ""
    try:
        if executable.stat().st_size <= 1_000_000:
            script_text = executable.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        pass
    for raw in _absolute_paths_in_text(script_text):
        candidate = Path(raw)
        if candidate.name in {"node", "python", "python3"} or candidate.suffix in {
            ".js",
            ".mjs",
            ".cjs",
            ".py",
        }:
            candidates.append(candidate)
    first_line = script_text.splitlines()[0] if script_text else ""
    if first_line.startswith("#!"):
        interpreter = first_line[2:].strip().split()[0]
        if interpreter.startswith("/"):
            candidates.append(Path(interpreter))

    aliases: list[Path] = []
    for candidate in candidates:
        aliases.extend(_symlink_hops(candidate))
    unique: dict[str, Path] = {}
    for alias in aliases:
        logical = alias.expanduser().absolute()
        unique.setdefault(str(logical), logical)
    return tuple(unique.values())


def validate_harness_runtime_allowlist(
    *,
    harness: str,
    executable: Path,
    environment: Mapping[str, str],
    runtime_paths: Sequence[Path],
    user_home: Path,
    forbidden_roots: Sequence[Path] = (),
    sibling_runtime_paths: Sequence[Path] = (),
) -> None:
    """Fail closed when a runtime exception could reopen protected state."""

    _validate_formal_harness_path(harness, environment)
    if not runtime_paths:
        raise ValueError(f"{harness} runtime allowlist is empty")

    canonical = tuple(_minimal_path_cover(runtime_paths))
    supplied = tuple(_deduplicate_paths(runtime_paths))
    if canonical != supplied:
        raise ValueError(f"{harness} runtime allowlist is not canonical and minimal")
    for path in canonical:
        if not path.exists():
            raise ValueError(f"{harness} runtime allowlist entry does not exist")
        if not (path.is_file() or path.is_dir()):
            raise ValueError(f"{harness} runtime allowlist entry is not regular")

    executable = executable.expanduser().resolve()
    if not any(
        executable == path or _is_relative_to(executable, path) for path in canonical
    ):
        raise ValueError(f"{harness} executable is outside its runtime allowlist")

    protected = _deduplicate_paths(forbidden_roots)
    for path in canonical:
        for root in protected:
            if _paths_overlap(path, root):
                raise ValueError(
                    f"{harness} runtime allowlist overlaps a forbidden root"
                )

    sibling_paths = _deduplicate_paths(sibling_runtime_paths)
    for path in canonical:
        for sibling in sibling_paths:
            if _paths_overlap(path, sibling):
                raise ValueError(
                    f"{harness} runtime allowlist overlaps a sibling runtime"
                )

    home = user_home.expanduser().resolve()
    if any(path == home or _is_relative_to(home, path) for path in canonical):
        raise ValueError(f"{harness} runtime allowlist covers the user home")

    generic_bin_roots = _deduplicate_paths([home / "bin", home / ".local" / "bin"])
    cache_roots = _deduplicate_paths([home / ".cache", home / "Library" / "Caches"])
    temp_roots = _generic_temp_roots()
    for path in canonical:
        for root in [*cache_roots, *temp_roots]:
            if _paths_overlap(path, root):
                raise ValueError(
                    f"{harness} runtime allowlist overlaps user cache or temporary state"
                )
        for root in generic_bin_roots:
            if not _paths_overlap(path, root):
                continue
            # A package-manager shim may be the selected harness entry point,
            # but its containing general-purpose bin directory is never an
            # approved runtime root.
            if path != executable:
                raise ValueError(
                    f"{harness} runtime allowlist includes a general user bin path"
                )


def _validate_formal_harness_path(harness: str, environment: Mapping[str, str]) -> None:
    """Accept only the fixed PATH, plus Hermes' runner-owned bash dispatcher."""

    try:
        fixed = os.pathsep.join(_HARNESS_FIXED_PATHS[harness])
    except KeyError as exc:
        raise ValueError(f"unsupported harness runtime policy: {harness}") from exc
    actual = environment.get("PATH")
    if actual == fixed:
        return
    if harness != "hermes":
        raise ValueError(f"{harness} formal PATH drifted from its minimal policy")
    shell_value = environment.get("SHELL")
    if not isinstance(shell_value, str) or not shell_value:
        raise ValueError("hermes formal PATH is missing its command dispatcher")
    shell = Path(shell_value)
    expected = os.pathsep.join((str(shell.parent), fixed))
    try:
        info = shell.lstat()
    except OSError as exc:
        raise ValueError("hermes command dispatcher is unavailable") from exc
    if (
        actual != expected
        or shell.name != "bash"
        or shell.parent.name != _FORMAL_COMMAND_BIN_DIR
        or not shell.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise ValueError("hermes formal PATH drifted from its dispatcher policy")


def validate_harness_runtime_aliases(
    *,
    harness: str,
    alias_paths: Sequence[Path],
    runtime_paths: Sequence[Path],
    forbidden_roots: Sequence[Path] = (),
    sibling_runtime_paths: Sequence[Path] = (),
) -> None:
    """Validate narrow logical symlink exceptions used by runtime shims."""

    forbidden = _deduplicate_paths(forbidden_roots)
    siblings = _deduplicate_paths(sibling_runtime_paths)
    runtime = _deduplicate_paths(runtime_paths)
    seen: set[str] = set()
    for raw in alias_paths:
        path = raw.expanduser().absolute()
        if str(path) in seen:
            raise ValueError(f"{harness} runtime alias allowlist is not unique")
        seen.add(str(path))
        if not path.is_symlink() or not path.exists():
            raise ValueError(f"{harness} runtime alias is not a live symlink")
        resolved = path.resolve()
        if not any(
            resolved == root or _is_relative_to(resolved, root) for root in runtime
        ):
            raise ValueError(
                f"{harness} runtime alias target is outside the runtime allowlist"
            )
        if any(_paths_overlap(resolved, root) for root in forbidden):
            raise ValueError(f"{harness} runtime alias resolves into a forbidden root")
        if any(_paths_overlap(resolved, root) for root in siblings):
            raise ValueError(f"{harness} runtime alias resolves into a sibling runtime")


def harness_runtime_allowlist_fingerprint(
    *,
    harness: str,
    executable: Path,
    environment: Mapping[str, str],
    runtime_paths: Sequence[Path],
    runtime_alias_paths: Sequence[Path] = (),
    command_runtime_paths: Sequence[Path] = (),
    execution_paths: Sequence[Path] = (),
    command_wrapper: Path | None = None,
) -> str:
    """Hash the policy and filesystem identities without persisting host paths."""

    records: list[dict[str, Any]] = []
    for path in _minimal_path_cover(runtime_paths):
        try:
            info = path.stat()
            link_info = path.lstat()
        except OSError as exc:
            raise ValueError(f"{harness} runtime identity could not be read") from exc
        record: dict[str, Any] = {
            "path": str(path),
            "resolved_path": str(path.resolve()),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": info.st_mode,
            "uid": info.st_uid,
            "link_device": link_info.st_dev,
            "link_inode": link_info.st_ino,
            "link_mode": link_info.st_mode,
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            record["content_sha256"] = _sha256_file(path)
        records.append(record)
    aliases: list[dict[str, Any]] = []
    for path in runtime_alias_paths:
        logical = path.expanduser().absolute()
        try:
            info = logical.lstat()
            target = os.readlink(logical)
            resolved = logical.resolve(strict=True)
        except OSError as exc:
            raise ValueError(
                f"{harness} runtime alias identity could not be read"
            ) from exc
        aliases.append(
            {
                "path": str(logical),
                "device": info.st_dev,
                "inode": info.st_ino,
                "mode": info.st_mode,
                "uid": info.st_uid,
                "target": target,
                "resolved": str(resolved),
            }
        )
    command_runtime: list[dict[str, Any]] = []
    for path in _minimal_path_cover(command_runtime_paths):
        try:
            info = path.stat()
            link_info = path.lstat()
        except OSError as exc:
            raise ValueError(
                f"{harness} command runtime identity could not be read"
            ) from exc
        record = {
            "path": str(path),
            "resolved_path": str(path.resolve()),
            "device": info.st_dev,
            "inode": info.st_ino,
            "mode": info.st_mode,
            "uid": info.st_uid,
            "link_device": link_info.st_dev,
            "link_inode": link_info.st_ino,
            "link_mode": link_info.st_mode,
            "kind": "directory" if path.is_dir() else "file",
        }
        if path.is_file():
            record["content_sha256"] = _sha256_file(path)
        command_runtime.append(record)
    execution: list[dict[str, Any]] = []
    for path in execution_paths:
        logical = path.expanduser().absolute()
        try:
            link_info = logical.lstat()
            resolved = logical.resolve(strict=True)
            info = resolved.stat()
        except OSError as exc:
            raise ValueError(f"{harness} execution identity could not be read") from exc
        execution.append(
            {
                "path": str(logical),
                "resolved_path": str(resolved),
                "device": info.st_dev,
                "inode": info.st_ino,
                "mode": info.st_mode,
                "uid": info.st_uid,
                "link_device": link_info.st_dev,
                "link_inode": link_info.st_ino,
                "link_mode": link_info.st_mode,
                "content_sha256": _sha256_file(resolved),
            }
        )
    payload = {
        "schema_version": _RUNTIME_ALLOWLIST_SCHEMA_VERSION,
        "harness": harness,
        "policy": f"{harness}-pinned-runtime-command-graph-v4",
        "path": environment.get("PATH"),
        "executable": str(executable.expanduser().resolve()),
        "runtime": records,
        "runtime_aliases": aliases,
        "command_runtime": command_runtime,
        "execution": execution,
        "command_wrapper": (
            str(command_wrapper.expanduser().absolute())
            if command_wrapper is not None
            else None
        ),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def verify_harness_runtime_allowlist(
    *,
    harness: str,
    executable: Path,
    environment: Mapping[str, str],
    runtime_paths: Sequence[Path],
    runtime_alias_paths: Sequence[Path] = (),
    command_runtime_paths: Sequence[Path] = (),
    execution_paths: Sequence[Path] = (),
    command_wrapper: Path | None = None,
    expected_sha256: str,
    user_home: Path,
    forbidden_roots: Sequence[Path] = (),
    sibling_runtime_paths: Sequence[Path] = (),
) -> None:
    """Revalidate the allowlist immediately before a formal harness launch."""

    validate_harness_runtime_allowlist(
        harness=harness,
        executable=executable,
        environment=environment,
        runtime_paths=runtime_paths,
        user_home=user_home,
        forbidden_roots=forbidden_roots,
        sibling_runtime_paths=sibling_runtime_paths,
    )
    validate_harness_runtime_aliases(
        harness=harness,
        alias_paths=runtime_alias_paths,
        runtime_paths=runtime_paths,
        forbidden_roots=forbidden_roots,
        sibling_runtime_paths=sibling_runtime_paths,
    )
    if command_wrapper is not None or command_runtime_paths or execution_paths:
        if command_wrapper is None:
            raise ValueError("formal command dispatcher identity is missing")
        validate_formal_command_runtime(
            command_runtime_paths=command_runtime_paths,
            execution_paths=execution_paths,
            command_wrapper=command_wrapper,
            executable=executable,
            forbidden_roots=forbidden_roots,
        )
    observed = harness_runtime_allowlist_fingerprint(
        harness=harness,
        executable=executable,
        environment=environment,
        runtime_paths=runtime_paths,
        runtime_alias_paths=runtime_alias_paths,
        command_runtime_paths=command_runtime_paths,
        execution_paths=execution_paths,
        command_wrapper=command_wrapper,
    )
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        or observed != expected_sha256
    ):
        raise ValueError(f"{harness} runtime allowlist fingerprint drifted")


def resolve_executable(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        invocation = candidate.absolute()
        if not invocation.is_file() or not os.access(invocation, os.X_OK):
            raise FileNotFoundError(value)
        return invocation
    found = shutil.which(value)
    if not found:
        raise FileNotFoundError(value)
    return Path(found).absolute()


def persist_harness_isolation_manifest(
    context: HarnessIsolationContext,
    *,
    artifacts_dir: Path,
) -> str | None:
    if context.manifest is None:
        return None
    output_dir = artifacts_dir / "harness_isolation"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(context.manifest, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return str(Path("artifacts") / "harness_isolation" / "manifest.json")


def _clean_openclaw_config(
    config: dict[str, Any],
    *,
    agent_dir: Path,
    formal_qianfan_override: tuple[str, str] | None = None,
) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    raw_models = config.get("models") if isinstance(config.get("models"), dict) else {}
    raw_providers = (
        raw_models.get("providers")
        if isinstance(raw_models.get("providers"), dict)
        else {}
    )
    providers = {
        str(name): _clean_openclaw_provider(
            value,
            provider_name=str(name),
            formal_qianfan_override=formal_qianfan_override,
        )
        for name, value in raw_providers.items()
        if isinstance(name, str) and isinstance(value, dict)
    }
    if formal_qianfan_override is not None and not any(
        _normalize_provider_name(name) == "qianfan" for name in providers
    ):
        raise ValueError(
            "formal Qianfan override requires an OpenClaw qianfan provider"
        )
    if providers:
        clean["models"] = {"providers": providers}

    raw_auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
    raw_profiles = (
        raw_auth.get("profiles") if isinstance(raw_auth.get("profiles"), dict) else {}
    )
    profiles: dict[str, dict[str, Any]] = {}
    for name, value in raw_profiles.items():
        if not isinstance(name, str) or not isinstance(value, dict):
            continue
        provider = value.get("provider")
        if provider not in providers:
            continue
        profiles[name] = {
            key: value[key]
            for key in ("provider", "mode")
            if isinstance(value.get(key), str)
        }
    if profiles:
        clean["auth"] = {"profiles": profiles}

    raw_plugins = (
        config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    )
    raw_entries = (
        raw_plugins.get("entries")
        if isinstance(raw_plugins.get("entries"), dict)
        else {}
    )
    plugin_entries: dict[str, dict[str, bool]] = {}
    for provider in providers:
        entry = raw_entries.get(provider)
        if isinstance(entry, dict):
            plugin_entries[provider] = {"enabled": bool(entry.get("enabled", True))}
    if plugin_entries:
        clean["plugins"] = {"entries": plugin_entries}
    original_agents = (
        config.get("agents") if isinstance(config.get("agents"), dict) else {}
    )
    original_defaults = (
        original_agents.get("defaults")
        if isinstance(original_agents.get("defaults"), dict)
        else {}
    )
    allowed_default_keys = {
        "model",
        "models",
    }
    defaults = {
        key: value
        for key, value in original_defaults.items()
        if key in allowed_default_keys
    }
    if isinstance(defaults.get("model"), dict):
        raw_default_model = defaults["model"]
        defaults["model"] = {
            key: raw_default_model[key]
            for key in ("primary", "fallbacks")
            if (
                isinstance(raw_default_model.get(key), str)
                or (
                    key == "fallbacks"
                    and isinstance(raw_default_model.get(key), list)
                    and all(isinstance(item, str) for item in raw_default_model[key])
                )
            )
        }
    if isinstance(defaults.get("models"), dict):
        defaults["models"] = {
            str(name): (
                {"alias": value["alias"]}
                if isinstance(value, dict) and isinstance(value.get("alias"), str)
                else {}
            )
            for name, value in defaults["models"].items()
            if isinstance(name, str)
        }
    defaults.update(
        {
            "workspace": str(agent_dir),
            "repoRoot": str(agent_dir),
            "skipBootstrap": True,
            "contextInjection": "never",
            # OpenClaw otherwise prepends a wall-clock timestamp to the first
            # user message at the provider boundary.  Formal paired lanes must
            # bind the same delivered prompt bytes, so disable that intrinsic
            # envelope rather than teaching the proxy to accept a prefix.
            "envelopeTimestamp": "off",
            "skills": [],
        }
    )
    clean["agents"] = {"defaults": defaults}
    clean["gateway"] = {"mode": "local"}
    clean["tools"] = {
        "allow": ["exec"],
        # The allow list is authoritative; the explicit groups make policy
        # drift fail closed if OpenClaw later introduces an implicit tool.
        "deny": [
            "group:fs",
            "group:web",
            "group:memory",
            "group:sessions",
            "group:messaging",
            "group:automation",
            "group:ui",
            "browser",
            "process",
            "tool_search",
        ],
        "exec": {
            "host": "gateway",
            "security": "allowlist",
            "ask": "off",
            "safeBins": [],
            "pathPrepend": [],
            "strictInlineEval": True,
            "applyPatch": {"enabled": False, "workspaceOnly": True},
        },
    }
    return clean


def _clean_openclaw_provider(
    value: dict[str, Any],
    *,
    provider_name: str,
    formal_qianfan_override: tuple[str, str] | None,
) -> dict[str, Any]:
    allowed_scalars = ("baseUrl", "api", "apiKey", "auth")
    clean = {
        key: value[key] for key in allowed_scalars if isinstance(value.get(key), str)
    }
    headers = value.get("headers")
    if isinstance(headers, dict):
        clean["headers"] = {
            str(key): str(item)
            for key, item in headers.items()
            if isinstance(key, str) and isinstance(item, (str, int, float, bool))
        }
    if (
        formal_qianfan_override is not None
        and _normalize_provider_name(provider_name) == "qianfan"
    ):
        clean["baseUrl"] = formal_qianfan_override[0]
        clean["apiKey"] = formal_qianfan_override[1]
        # Provider headers may contain a second copy of the source credential.
        # The loopback broker owns upstream authentication in formal mode.
        clean.pop("headers", None)
    models = value.get("models")
    if isinstance(models, list):
        clean_models: list[dict[str, Any]] = []
        allowed_model_keys = {
            "id",
            "name",
            "reasoning",
            "input",
            "cost",
            "contextWindow",
            "maxTokens",
            "compat",
            "modalities",
            "supportsTools",
        }
        for model in models:
            if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                continue
            clean_model: dict[str, Any] = {}
            for key, item in model.items():
                if key not in allowed_model_keys:
                    continue
                if key == "compat" and isinstance(item, dict):
                    compat_keys = {
                        "maxTokensField",
                        "requiresReasoningContentOnAssistantMessages",
                        "supportsReasoningEffort",
                        "supportsUsageInStreaming",
                        "thinkingFormat",
                    }
                    clean_model[key] = {
                        nested_key: nested_value
                        for nested_key, nested_value in item.items()
                        if nested_key in compat_keys
                        and isinstance(nested_value, (str, bool, int, float))
                    }
                    continue
                if key == "cost" and isinstance(item, dict):
                    clean_model[key] = {
                        nested_key: nested_value
                        for nested_key, nested_value in item.items()
                        if nested_key in {"input", "output", "cacheRead", "cacheWrite"}
                        and isinstance(nested_value, (int, float))
                    }
                    continue
                if key in {"input", "modalities"} and isinstance(item, list):
                    clean_model[key] = [
                        value for value in item if isinstance(value, str)
                    ]
                    continue
                if isinstance(item, (str, bool, int, float)):
                    clean_model[key] = item
            clean_models.append(clean_model)
        clean["models"] = clean_models
    return clean


def _clean_hermes_config(
    config: dict[str, Any],
    available_env_names: set[str],
    *,
    agent_dir: Path | None = None,
) -> tuple[dict[str, Any], set[str]]:
    model_value = config.get("model")
    model = model_value if isinstance(model_value, dict) else {}
    clean_model = {
        key: model[key]
        for key in ("default", "name", "provider", "base_url", "api_mode")
        if isinstance(model.get(key), str)
    }
    selected_provider = str(clean_model.get("provider", "") or "").strip()
    selected_name = (
        selected_provider.split(":", 1)[1]
        if selected_provider.startswith("custom:")
        else selected_provider
    )
    selected_normalized = _normalize_provider_name(selected_name)

    clean: dict[str, Any] = {}
    credentials: set[str] = set()
    raw_custom = config.get("custom_providers")
    if isinstance(raw_custom, list):
        entries: list[dict[str, Any]] = []
        for entry in raw_custom:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "") or "")
            if (
                selected_normalized
                and _normalize_provider_name(name) != selected_normalized
            ):
                continue
            cleaned, referenced = _clean_hermes_provider_entry(
                entry,
                available_env_names=available_env_names,
                provider_name=name,
                keyed=False,
            )
            if cleaned:
                entries.append(cleaned)
                credentials.update(referenced)
        if entries:
            clean["custom_providers"] = entries

    raw_providers = config.get("providers")
    if isinstance(raw_providers, dict):
        providers: dict[str, dict[str, Any]] = {}
        for name, entry in raw_providers.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                continue
            if (
                selected_normalized
                and _normalize_provider_name(name) != selected_normalized
            ):
                continue
            cleaned, referenced = _clean_hermes_provider_entry(
                entry,
                available_env_names=available_env_names,
                provider_name=name,
                keyed=True,
            )
            if cleaned:
                providers[name] = cleaned
                credentials.update(referenced)
        if providers:
            clean["providers"] = providers

    inferred = _inferred_provider_env_name(selected_name)
    if not credentials and inferred in available_env_names:
        credentials.add(inferred)
        _inject_hermes_key_env(clean, inferred)
    if selected_provider.startswith("custom:") and not (
        clean.get("custom_providers") or clean.get("providers")
    ):
        raise ValueError(
            f"selected Hermes provider {selected_provider!r} is not defined"
        )
    if clean_model:
        clean["model"] = clean_model
    if not clean:
        raise ValueError("Hermes config has no provider/model binding to retain")
    clean["plugins"] = {
        "enabled": [_HERMES_COMMAND_GUARD_PLUGIN],
        "disabled": [],
    }
    clean["platform_toolsets"] = {"cli": [_HERMES_FORMAL_TOOLSET, "no_mcp"]}
    clean["terminal"] = {
        "backend": "local",
        "auto_source_bashrc": False,
        "shell_init_files": [],
        **({"cwd": str(agent_dir)} if agent_dir is not None else {}),
    }
    return clean, credentials


def _apply_formal_hermes_qianfan_override(
    config: dict[str, Any], *, base_url: str
) -> None:
    """Bind the selected Hermes Qianfan provider to a credential broker."""

    matched = False
    custom = config.get("custom_providers")
    if isinstance(custom, list):
        for entry in custom:
            if not isinstance(entry, dict):
                continue
            if _normalize_provider_name(str(entry.get("name", ""))) != "qianfan":
                continue
            entry["base_url"] = base_url
            entry["key_env"] = "QIANFAN_API_KEY"
            matched = True
    providers = config.get("providers")
    if isinstance(providers, dict):
        for name, entry in providers.items():
            if (
                not isinstance(name, str)
                or not isinstance(entry, dict)
                or _normalize_provider_name(name) != "qianfan"
            ):
                continue
            entry["base_url"] = base_url
            entry["key_env"] = "QIANFAN_API_KEY"
            matched = True

    model = config.get("model")
    if isinstance(model, dict):
        selected = str(model.get("provider", "") or "")
        if selected.startswith("custom:"):
            selected = selected.split(":", 1)[1]
        if _normalize_provider_name(selected) == "qianfan":
            model["base_url"] = base_url
        else:
            raise ValueError(
                "formal Qianfan override requires Hermes to select qianfan"
            )
    if not matched:
        raise ValueError("formal Qianfan override requires a Hermes qianfan provider")


def _clean_hermes_provider_entry(
    entry: dict[str, Any],
    *,
    available_env_names: set[str],
    provider_name: str,
    keyed: bool,
) -> tuple[dict[str, Any], set[str]]:
    scalar_keys = (
        (
            "api",
            "url",
            "base_url",
            "name",
            "key_env",
            "transport",
            "api_mode",
            "default_model",
            "model",
        )
        if keyed
        else ("name", "base_url", "key_env", "api_mode", "model")
    )
    clean = {key: entry[key] for key in scalar_keys if isinstance(entry.get(key), str)}
    for key in ("discover_models", "ssl_verify"):
        if isinstance(entry.get(key), bool):
            clean[key] = entry[key]
    for key in ("context_length", "rate_limit_delay"):
        if isinstance(entry.get(key), (int, float)) and not isinstance(
            entry.get(key), bool
        ):
            clean[key] = entry[key]
    models = entry.get("models")
    if isinstance(models, dict):
        clean["models"] = {str(name): {} for name in models if isinstance(name, str)}
    elif isinstance(models, list):
        names = [
            (
                item
                if isinstance(item, str)
                else item.get("id") if isinstance(item, dict) else None
            )
            for item in models
        ]
        clean["models"] = {name: {} for name in names if isinstance(name, str)}
    referenced: set[str] = set()
    key_env = clean.get("key_env")
    if (
        isinstance(key_env, str)
        and _valid_env_name(key_env)
        and key_env in available_env_names
    ):
        referenced.add(key_env)
    else:
        clean.pop("key_env", None)
        inferred = _inferred_provider_env_name(provider_name)
        if inferred in available_env_names:
            clean["key_env"] = inferred
            referenced.add(inferred)
    return clean, referenced


def _inject_hermes_key_env(config: dict[str, Any], name: str) -> None:
    custom = config.get("custom_providers")
    if isinstance(custom, list) and custom and isinstance(custom[0], dict):
        custom[0]["key_env"] = name
        return
    providers = config.get("providers")
    if isinstance(providers, dict) and providers:
        first = next(iter(providers.values()))
        if isinstance(first, dict):
            first["key_env"] = name


def _normalize_provider_name(value: str) -> str:
    return value.strip().lower().replace(" ", "-")


def _inferred_provider_env_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    return f"{normalized}_API_KEY" if normalized else ""


def _valid_env_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) is not None


def _parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"unsupported .env line {line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not _valid_env_name(name):
            raise ValueError(f"invalid .env name on line {line_number}")
        values[name] = value
    return values


def _parse_yaml_subset(text: str) -> dict[str, Any]:
    """Parse the conservative YAML subset used for provider/model config.

    Unsupported YAML features fail closed.  The sanitized destination is JSON,
    so tags, anchors, block scalars, merge keys, and executable constructors
    can never survive this boundary.
    """

    lines: list[tuple[int, str, int]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if "\t" in raw_line[: len(raw_line) - len(raw_line.lstrip())]:
            raise ValueError(
                f"tabs are not allowed in YAML indentation (line {line_number})"
            )
        stripped = _strip_yaml_comment(raw_line).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.lstrip(" "), line_number))
    if not lines:
        return {}
    value, index = _parse_yaml_block(lines, 0, lines[0][0])
    if index != len(lines) or not isinstance(value, dict):
        raise ValueError("Hermes config must be a YAML mapping")
    return value


def _parse_yaml_block(
    lines: list[tuple[int, str, int]],
    index: int,
    indent: int,
) -> tuple[Any, int]:
    if index >= len(lines) or lines[index][0] != indent:
        raise ValueError("invalid YAML indentation")
    if lines[index][1].startswith("-"):
        return _parse_yaml_list(lines, index, indent)
    return _parse_yaml_mapping(lines, index, indent)


def _parse_yaml_mapping(
    lines: list[tuple[int, str, int]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content, line_number = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or content.startswith("-"):
            raise ValueError(f"invalid YAML mapping indentation on line {line_number}")
        key, raw_value = _split_yaml_key_value(content, line_number)
        if key in result:
            raise ValueError(f"duplicate YAML key {key!r} on line {line_number}")
        index += 1
        if raw_value == "":
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_yaml_block(lines, index, lines[index][0])
            else:
                value = None
        else:
            value = _parse_yaml_scalar(raw_value, line_number)
        result[key] = value
    return result, index


def _parse_yaml_list(
    lines: list[tuple[int, str, int]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        current_indent, content, line_number = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent or not content.startswith("-"):
            raise ValueError(f"invalid YAML list indentation on line {line_number}")
        item_text = content[1:].lstrip()
        index += 1
        if item_text == "":
            if index >= len(lines) or lines[index][0] <= indent:
                result.append(None)
            else:
                value, index = _parse_yaml_block(lines, index, lines[index][0])
                result.append(value)
            continue
        if ":" not in item_text:
            result.append(_parse_yaml_scalar(item_text, line_number))
            continue

        key, raw_value = _split_yaml_key_value(item_text, line_number)
        mapping: dict[str, Any] = {}
        if raw_value == "":
            if index < len(lines) and lines[index][0] > indent:
                value, index = _parse_yaml_block(lines, index, lines[index][0])
            else:
                value = None
        else:
            value = _parse_yaml_scalar(raw_value, line_number)
        mapping[key] = value
        if index < len(lines) and lines[index][0] > indent:
            continuation_indent = lines[index][0]
            continuation, index = _parse_yaml_mapping(lines, index, continuation_indent)
            for continuation_key, continuation_value in continuation.items():
                if continuation_key in mapping:
                    raise ValueError(f"duplicate YAML key {continuation_key!r}")
                mapping[continuation_key] = continuation_value
        result.append(mapping)
    return result, index


def _split_yaml_key_value(content: str, line_number: int) -> tuple[str, str]:
    key_text, separator, value = content.partition(":")
    if not separator:
        raise ValueError(f"expected YAML key/value on line {line_number}")
    key_text = key_text.strip()
    if not key_text or key_text in {"<<", "!"}:
        raise ValueError(f"invalid YAML key on line {line_number}")
    if key_text[0] in {'"', "'"}:
        key = _parse_yaml_scalar(key_text, line_number)
        if not isinstance(key, str):
            raise ValueError(f"non-string YAML key on line {line_number}")
    else:
        key = key_text
    return key, value.strip()


def _parse_yaml_scalar(value: str, line_number: int) -> Any:
    if value in {"|", ">"} or value.startswith(("!", "&", "*")):
        raise ValueError(f"unsupported YAML feature on line {line_number}")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    if value.startswith(('"', "'")):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(
                f"invalid quoted YAML scalar on line {line_number}"
            ) from exc
        if not isinstance(parsed, str):
            raise ValueError(f"unsupported quoted YAML scalar on line {line_number}")
        return parsed
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"unsupported inline YAML on line {line_number}") from exc
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


def _strip_yaml_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote == '"':
            escaped = True
            continue
        if character in {'"', "'"}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            continue
        if (
            character == "#"
            and quote is None
            and (index == 0 or line[index - 1].isspace())
        ):
            return line[:index]
    return line


def _backup_and_scrub_openclaw_db(
    source: Path,
    destination: Path,
    *,
    clear_auth_profiles: bool = False,
) -> None:
    source_uri = f"file:{source}?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA foreign_keys=OFF")
        destination_connection.execute("PRAGMA secure_delete=ON")
        table_rows = list(destination_connection.execute("PRAGMA table_list"))
        for row in table_rows:
            # schema, name, type, ncol, wr, strict
            name = str(row[1])
            table_type = str(row[2])
            if name.startswith("sqlite_"):
                continue
            if table_type == "shadow":
                continue
            if name in OPENCLAW_PRESERVED_TABLES:
                if clear_auth_profiles and name in {
                    "auth_profile_store",
                    "auth_profile_state",
                }:
                    destination_connection.execute(
                        f'DELETE FROM "{name.replace(chr(34), chr(34) * 2)}"'
                    )
                continue
            try:
                destination_connection.execute(
                    f'DELETE FROM "{name.replace(chr(34), chr(34) * 2)}"'
                )
            except sqlite3.OperationalError as exc:
                raise RuntimeError(
                    f"failed to scrub OpenClaw table {name!r}: {exc}"
                ) from exc
        destination_connection.commit()
        destination_connection.execute("VACUUM")
        destination_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        destination_connection.close()
        source_connection.close()


def _openclaw_private_record_counts(database: Path) -> tuple[int, int]:
    connection = sqlite3.connect(database)
    try:
        rows = list(connection.execute("PRAGMA table_list"))
        session_count = 0
        memory_count = 0
        for row in rows:
            name = str(row[1])
            table_type = str(row[2])
            if name.startswith("sqlite_") or table_type == "shadow":
                continue
            lowered = name.lower()
            if (
                "session" not in lowered
                and "transcript" not in lowered
                and "memory" not in lowered
            ):
                continue
            try:
                count = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                )
            except sqlite3.OperationalError:
                continue
            if "memory" in lowered:
                memory_count += count
            else:
                session_count += count
        return session_count, memory_count
    finally:
        connection.close()


def _create_synthetic_os_home(state_root: Path) -> Path:
    home = state_root / "os-home"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    return home


def _fingerprint(path: Path) -> SourceFingerprint:
    return SourceFingerprint(basename=path.name, sha256=_sha256_file(path))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_regular_file(path: Path, description: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{description} not found: {path.name}")


def _profile_rule(effect: str, operation: str, paths: Sequence[Path]) -> list[str]:
    if not paths:
        return []
    filters = " ".join(f"(subpath {_scheme_string(path)})" for path in paths)
    return [f"({effect} {operation} {filters})"]


def _profile_literal_rule(
    effect: str, operation: str, paths: Sequence[Path]
) -> list[str]:
    if not paths:
        return []
    filters = " ".join(f"(literal {_scheme_string(path)})" for path in paths)
    return [f"({effect} {operation} {filters})"]


def _profile_logical_rule(
    effect: str,
    operation: str,
    paths: Sequence[Path],
) -> list[str]:
    if not paths:
        return []
    filters = " ".join(f"(subpath {_scheme_logical_string(path)})" for path in paths)
    return [f"({effect} {operation} {filters})"]


def _profile_logical_literal_rule(
    effect: str,
    operation: str,
    paths: Sequence[Path],
) -> list[str]:
    if not paths:
        return []
    filters = " ".join(f"(literal {_scheme_logical_string(path)})" for path in paths)
    return [f"({effect} {operation} {filters})"]


def _scheme_string(path: Path) -> str:
    # JSON string escaping is compatible with the subset used by Seatbelt.
    return json.dumps(str(path.expanduser().resolve()))


def _scheme_logical_string(path: Path) -> str:
    return json.dumps(str(path.expanduser().absolute()))


def _deduplicate_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for path in paths:
        try:
            normalized = path.expanduser().resolve()
        except OSError:
            normalized = path.expanduser().absolute()
        unique.setdefault(str(normalized), normalized)
    return list(unique.values())


def _minimal_path_cover(paths: Iterable[Path]) -> list[Path]:
    ordered = sorted(
        _deduplicate_paths(paths), key=lambda path: (len(path.parts), str(path))
    )
    minimal: list[Path] = []
    for path in ordered:
        if any(path == parent or _is_relative_to(path, parent) for parent in minimal):
            continue
        minimal.append(path)
    return minimal


def _paths_overlap(first: Path, second: Path) -> bool:
    return (
        first == second
        or _is_relative_to(first, second)
        or _is_relative_to(second, first)
    )


def _generic_temp_roots() -> list[Path]:
    return _deduplicate_paths(
        [Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())]
    )


def _ancestor_paths(paths: Iterable[Path]) -> list[Path]:
    ancestors: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        ancestors.append(path)
        ancestors.extend(path.parents)
    return _deduplicate_paths(ancestors)


def _logical_ancestor_paths(paths: Iterable[Path]) -> list[Path]:
    unique: dict[str, Path] = {}
    for raw in paths:
        path = raw.expanduser().absolute()
        for parent in (path, *path.parents):
            if parent == Path(parent.anchor):
                continue
            unique.setdefault(str(parent), parent)
    return list(unique.values())


def _symlink_hops(path: Path) -> list[Path]:
    logical = path.expanduser().absolute()
    aliases: list[Path] = []
    seen: set[str] = set()
    while True:
        found: Path | None = None
        current = Path(logical.anchor)
        for part in logical.parts[1:]:
            current /= part
            if current.is_symlink():
                found = current
                break
        if found is None or str(found) in seen:
            return aliases
        seen.add(str(found))
        if len(found.parts) > 2:
            aliases.append(found)
        target = Path(os.readlink(found))
        if not target.is_absolute():
            target = found.parent / target
        remaining = logical.relative_to(found)
        logical = target.joinpath(remaining).absolute()


def _absolute_paths_in_text(text: str) -> list[str]:
    if not text:
        return []
    matches = re.findall(r"(?:(?<=['\"])|(?<=\s))(/[A-Za-z0-9_.,+@%:=~\-/]+)", text)
    return [match.rstrip(';,)"') for match in matches]


def _runtime_scope_for_path(path: Path) -> list[Path]:
    paths = [path]
    if path.name in {"node", "python", "python3"} and "bin" in path.parts:
        prefix = _installation_prefix(path.resolve())
        paths.append(prefix)
    elif path.suffix in {".js", ".mjs", ".cjs", ".py"}:
        paths.append(path.parent)
        if path.parent.name in {"dist", "build"}:
            project = path.parent.parent
            for dependency in (
                project / "node_modules",
                project / "dist-runtime",
                project / "package.json",
            ):
                if dependency.exists():
                    paths.append(dependency)
            paths.extend(_workspace_package_targets(project / "node_modules"))
    else:
        paths.append(path.parent)
    return paths


def _workspace_package_targets(node_modules: Path) -> list[Path]:
    """Resolve shallow workspace-package links without opening the project root."""

    if not node_modules.is_dir():
        return []
    project = node_modules.parent.resolve()
    links: list[Path] = []
    try:
        top_level = list(node_modules.iterdir())
    except OSError:
        return []
    for entry in top_level:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            links.append(entry)
            continue
        if not (entry.name.startswith("@") and entry.is_dir()):
            continue
        try:
            links.extend(child for child in entry.iterdir() if child.is_symlink())
        except OSError:
            continue

    targets: list[Path] = []
    for link in links:
        try:
            target = link.resolve(strict=True)
            relative = target.relative_to(project)
        except (OSError, ValueError):
            continue
        if not relative.parts or relative.parts[0] in {
            ".git",
            ".pnpm-store",
            "node_modules",
        }:
            continue
        if target == project or not (target / "package.json").is_file():
            continue
        targets.append(target)
    return _deduplicate_paths(targets)


def _installation_prefix(executable: Path) -> Path:
    parent = executable.parent
    return parent.parent if parent.name == "bin" else parent


def _venv_root(path: Path) -> Path | None:
    resolved = path.expanduser().resolve()
    parts = resolved.parts
    try:
        index = parts.index("venvs")
    except ValueError:
        return None
    if len(parts) <= index + 1:
        return None
    return Path(*parts[: index + 2])


def _editable_install_roots(venv_root: Path) -> list[Path]:
    roots: list[Path] = []
    for finder in venv_root.glob("lib/python*/site-packages/__editable__*.py"):
        try:
            tree = ast.parse(finder.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(
                isinstance(target, ast.Name) and target.id == "MAPPING"
                for target in targets
            ):
                continue
            value_node = node.value
            if value_node is None:
                continue
            try:
                mapping = ast.literal_eval(value_node)
            except (ValueError, TypeError):
                continue
            if isinstance(mapping, dict):
                for value in mapping.values():
                    if isinstance(value, str) and value.startswith("/"):
                        candidate = Path(value)
                        if candidate.exists():
                            roots.append(candidate)
                            continue
                        roots.extend(
                            module
                            for suffix in importlib.machinery.all_suffixes()
                            if (module := candidate.with_suffix(suffix)).exists()
                        )
    return roots


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
