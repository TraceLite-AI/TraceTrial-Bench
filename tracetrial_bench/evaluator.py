from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from tracetrial_bench.types import EvaluationResult, TaskSpec
from tracetrial_bench.workspace import RunLayout


def run_verifier(
    task: TaskSpec,
    layout: RunLayout,
    *,
    timeout: int | None = None,
    mode: str = "local",
    harness: str | None = None,
    model: str | None = None,
) -> EvaluationResult:
    if mode != "local":
        raise ValueError("verifier execution is host-only; Docker mode is forbidden")
    output_dir = layout.artifacts_dir / "verifier_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    score_path = output_dir / "score.json"

    command = shlex.split(task.verifier.entrypoint)
    if not command:
        raise ValueError("verifier entrypoint must not be empty")
    if command[0].endswith(".sh"):
        command = ["bash", *command]

    try:
        env = _verifier_env(
            layout,
            output_dir,
            harness=harness,
            model=model,
        )
        completed = subprocess.run(
            command,
            cwd=str(layout.private_dir),
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        stdout_path.write_text(completed.stdout)
        stderr_path.write_text(completed.stderr)
        parsed = _extract_verifier_payload(completed.stdout, score_path)
        score = parsed["score"]
        status = _evaluation_status(completed.returncode, parsed["passed"])
        return EvaluationResult(
            status=status,
            score=score,
            exit_code=completed.returncode,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            score_path=score_path if score_path.exists() else None,
            error_message=_verifier_error_message(
                completed.returncode, completed.stderr
            ),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else exc.stdout
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else exc.stderr
        )
        stdout_path.write_text(stdout or "")
        stderr_path.write_text(stderr or "")
        return EvaluationResult(
            status="error",
            score=None,
            exit_code=None,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            score_path=None,
            timed_out=True,
            error_message=f"verifier timed out after {timeout} seconds",
        )

def _extract_verifier_payload(
    stdout: str, score_path: Path
) -> dict[str, float | bool | None]:
    stripped_stdout = stdout.strip()
    if stripped_stdout:
        try:
            payload = json.loads(stripped_stdout)
        except json.JSONDecodeError:
            pass
        else:
            return _payload_result(payload, score_path)

    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        return _payload_result(payload, score_path)
    return {"score": 1.0 if "PASS" in stdout else None, "passed": None}


def _payload_result(payload: dict, score_path: Path) -> dict[str, float | bool | None]:
    score_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    passed = _passed_from_payload(payload)
    value = payload.get("score")
    if isinstance(value, (int, float)):
        return {"score": float(value), "passed": passed}
    if passed is True:
        return {"score": 1.0, "passed": True}
    if passed is False:
        return {"score": 0.0, "passed": False}
    return {"score": None, "passed": passed}


def _passed_from_payload(payload: dict) -> bool | None:
    if isinstance(payload.get("pass"), bool):
        return payload["pass"]
    if isinstance(payload.get("passed"), bool):
        return payload["passed"]
    status = payload.get("status")
    if status == "pass":
        return True
    if status == "fail":
        return False
    return None


def _evaluation_status(exit_code: int, passed: float | bool | None) -> str:
    if passed is True:
        return "pass"
    if passed is False:
        return "fail"
    return "pass" if exit_code == 0 else "fail"


def _verifier_error_message(exit_code: int, stderr: str) -> str | None:
    if exit_code == 0:
        return None
    stripped = stderr.strip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0]
    return first_line[:500]


def _openclaw_session_file(run_dir: Path) -> Path | None:
    """本次 OpenClaw 运行自己写的会话记录 —— 取证的无损源。

    实测: M-T4a 的 M11/M12(仅有的两条带高位字节的载荷)在 artifacts/trace/*/events.json
    里 tool_result 被压成空对象 {}, 而 OpenClaw 自己的 session jsonl 内容完整
    (verify 值都在)。CLI 的 JSON 流被那些字节弄坏, 桥丢了整条结果。
    这类丢失最毒 —— 恰好发生在"考字节保真"的题上, 会凭空造出一个假 harness 缺陷。
    """
    session_id = None
    for events in sorted((run_dir / "artifacts" / "trace").glob("*/events.json")):
        try:
            payload = json.loads(events.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        for event in payload:
            if isinstance(event, dict) and event.get("event_type") == "run_started":
                meta = (event.get("raw_event") or {}).get("agentMeta") or {}
                if meta.get("sessionId"):
                    session_id = str(meta["sessionId"]); break
        if session_id:
            break
    if not session_id:
        return None
    home = Path(os.environ.get("OPENCLAW_HOME") or (Path.home() / ".openclaw"))
    for candidate in sorted(home.glob(f"agents/*/sessions/{session_id}.jsonl")):
        if candidate.is_file():
            return candidate
    return None


def _verifier_env(
    layout: RunLayout,
    output_dir: Path,
    *,
    harness: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    env = dict(os.environ)
    # Verifiers execute with ``layout.private_dir`` as their cwd.  Supplying
    # paths relative to the caller's cwd makes every artifact disappear when
    # the CLI itself was given a relative --output-dir.
    run_dir = layout.run_dir.resolve()
    agent_dir = layout.agent_dir.resolve()
    workspace_dir = layout.workspace_dir.resolve()
    verifier_dir = layout.verifier_dir.resolve()
    resolved_output_dir = output_dir.resolve()
    env["HARNESSBENCH_RUN_DIR"] = str(run_dir)
    env["HARNESSBENCH_AGENT_DIR"] = str(agent_dir)
    env["HARNESSBENCH_WORKSPACE"] = str(workspace_dir)
    env["HARNESSBENCH_VERIFIER_DIR"] = str(verifier_dir)
    env["HARNESSBENCH_OUTPUT_DIR"] = str(resolved_output_dir)
    if harness:
        env["HARNESSBENCH_HARNESS"] = harness
    if model:
        env["HARNESSBENCH_MODEL"] = model
    env.setdefault("WORKSPACE_PATH", str(workspace_dir))
    if harness == "openclaw":
        session_file = _openclaw_session_file(run_dir)
        if session_file is not None:
            env["HARNESSBENCH_PARENT_SESSION_FILE"] = str(session_file)
    if shutil.which("python") is None and shutil.which("python3") is not None:
        shim_dir = layout.private_dir / ".harnessbench-bin"
        shim_dir.mkdir(exist_ok=True)
        shim = shim_dir / "python"
        if not shim.exists():
            shim.write_text('#!/usr/bin/env bash\nexec python3 "$@"\n')
            shim.chmod(0o755)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env.get('PATH', '')}"
    return env
