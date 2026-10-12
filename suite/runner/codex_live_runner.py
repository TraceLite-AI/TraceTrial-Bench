#!/usr/bin/env python3
"""Run HarnessBench case packages against a real local OpenClaw installation.

Unlike the legacy batch runner, this runner keeps an explicit evidence manifest
and captures parent/child transcripts plus durable task state when a case uses
sessions_spawn. It never guesses nested evidence paths during grading.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPENCLAW = Path("/Users/conglin/.nvm/versions/node/v22.22.2/bin/openclaw")
DEFAULT_MODEL = "qianfan/glm-5.1"


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            row = json.loads(line)
            rows.append(row if isinstance(row, dict) else {})
        except Exception:
            continue
    return rows


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(x.get("text", "")) for x in value if isinstance(x, dict))
    return ""


def spawn_details(parent_session: Path | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for row in read_jsonl(parent_session):
        message = row.get("message") or {}
        if message.get("role") != "toolResult" or message.get("toolName") != "sessions_spawn":
            continue
        text = content_text(message.get("content"))
        start = text.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(text[start:])
            if isinstance(value, dict):
                found.append(value)
        except Exception:
            continue
    return found


def session_source(agent_id: str, session_key: str) -> Path | None:
    index = Path.home() / ".openclaw" / "agents" / agent_id / "sessions" / "sessions.json"
    data = read_json(index)
    entry = data.get(session_key) if isinstance(data, dict) else None
    source = Path(entry.get("sessionFile")) if isinstance(entry, dict) and entry.get("sessionFile") else None
    return source if source and source.exists() else None


def copy_session(agent_id: str, session_key: str, destination: Path) -> Path | None:
    source = session_source(agent_id, session_key)
    if source:
        shutil.copy2(source, destination)
    return source


def parse_json_object(text: str) -> dict[str, Any] | None:
    for match in re.finditer(r"\{", text):
        try:
            value = json.loads(text[match.start():])
            if isinstance(value, dict):
                return value
        except Exception:
            continue
    return None


def next_run_index(evidence_dir: Path) -> int:
    indexes = []
    for path in evidence_dir.glob("openclaw-run*"):
        match = re.fullmatch(r"openclaw-run(\d+)", path.name)
        if match:
            indexes.append(int(match.group(1)))
    return max(indexes, default=0) + 1


def copy_workspace(case_dir: Path, destination: Path) -> None:
    source = case_dir / "workspace"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    if source.exists():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)


def prepare_openclaw_env(case_dir: Path, agent_workspace: Path) -> tuple[dict[str, str], str | None]:
    """给每道题生成一份临时 config，无论它带不带插件。

    ★为什么无条件生成：全局 `~/.openclaw/openclaw.json` 里 `agents.defaults.workspace`
    写着绝对路径，它会压过 `agents add --workspace <tmp>`，agent 于是跑到全局 workspace 里干活，
    题目材料一个都看不到 —— 交付物必然为空，分数近 0，看着像模型不行，其实是装置假阴性。
    所以这里把该键删掉，让 per-agent 的 --workspace 生效。

    凭据不落进 evidence；临时 config 随 agent workspace 一起删。
    """
    env = dict(os.environ)
    base_path = Path.home() / ".openclaw" / "openclaw.json"
    config = read_json(base_path)
    if not isinstance(config, dict):
        raise RuntimeError(f"cannot read base OpenClaw config: {base_path}")
    defaults = config.setdefault("agents", {}).setdefault("defaults", {})
    dropped_workspace = defaults.pop("workspace", None)

    # ★仪器设置（非自变量）：把 exec 审批关掉。
    # 实测教训：全局 config 是 tools.profile="coding"，它会要求 exec 审批；
    # 而 `openclaw agent --local` 没有交互审批客户端，于是每条 exec 都被拦：
    #   "Exec approval is required, but no interactive approval client is currently available."
    # 结果 agent 一条命令都跑不了，看着像模型不干活。审批姿态若要当自变量，
    # 应由题目在 config_overrides 里显式声明，不能靠宿主机全局配置默认值。
    # 键名依 docs/tools/exec.md：tools.exec.mode ∈ {deny,allowlist,ask,auto,full}，tools.exec.ask 默认 off。
    # 校验器规定：tools.exec.mode 不能与 legacy 的 security/ask 同时出现，二选一。
    exec_cfg = config.setdefault("tools", {}).setdefault("exec", {})
    exec_cfg.pop("security", None)
    exec_cfg.pop("ask", None)
    exec_cfg["mode"] = "full"

    plugin_root = next(
        (case_dir / rel for rel in ("runtime/plugin", "openclaw/plugin")
         if (case_dir / rel / "openclaw.plugin.json").is_file()),
        case_dir / "runtime" / "plugin")
    manifest = read_json(plugin_root / "openclaw.plugin.json")
    if not isinstance(manifest, dict) or not manifest.get("id"):
        # 没插件也要用临时 config —— 只为把写死的 workspace 摘掉
        config_path = agent_workspace / "openclaw.case.json"
        write_json(config_path, config)
        env["OPENCLAW_CONFIG_PATH"] = str(config_path)
        env["HB_DROPPED_DEFAULT_WORKSPACE"] = str(dropped_workspace or "")
        return env, None
    plugin_id = str(manifest["id"])
    plugins = config.setdefault("plugins", {})
    load_config = plugins.setdefault("load", {})
    paths = list(load_config.get("paths") or [])
    if str(plugin_root.resolve()) not in paths:
        paths.append(str(plugin_root.resolve()))
    load_config["paths"] = paths
    allowed = list(plugins.get("allow") or [])
    if plugin_id not in allowed:
        allowed.append(plugin_id)
    plugins["allow"] = allowed
    plugins.setdefault("entries", {}).setdefault(plugin_id, {})["enabled"] = True
    config_path = agent_workspace / "openclaw.case.json"
    write_json(config_path, config)
    env["OPENCLAW_CONFIG_PATH"] = str(config_path)
    env["HB_DROPPED_DEFAULT_WORKSPACE"] = str(dropped_workspace or "")
    return env, plugin_id


APPROVALS_FILE = Path.home() / ".openclaw" / "exec-approvals.json"


def scope_exec_approval(agent_id: str, enable: bool) -> dict[str, Any] | None:
    """给本次 run 的临时 agent 单独放开 exec 审批，跑完撤掉。

    ★为什么必须做：host 审批文件 ~/.openclaw/exec-approvals.json 的 defaults 是
    ask="always"，而 `openclaw agent --local` 没有交互审批客户端，于是每条 exec 都返回
    "Exec approval is required, but no interactive approval client is currently available."
    —— agent 一条命令都跑不了（连 ls 都不行），分数全 0，看着像模型不干活。
    askFallback 在 --local 形态下走不到决策点，所以指望 fail-open 不管用。

    只给 hb-* 临时 agent 加 agents.<id> 作用域（文档：窄作用域优先），
    **不改用户的 defaults**；run 结束把这条删掉，宿主机恢复原样。
    这是仪器设置，写进 run_meta；审批姿态若要当自变量，须由题目显式声明。
    """
    data = read_json(APPROVALS_FILE)
    if not isinstance(data, dict):
        return None
    agents = data.setdefault("agents", {})
    if enable:
        agents[agent_id] = {"security": "full", "ask": "off", "askFallback": "full"}
    else:
        agents.pop(agent_id, None)
    APPROVALS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    return {"scope": f"agents.{agent_id}", "ask": "off"} if enable else None


TURN_SPEC_CANDIDATES = ("runner/turns.json", "turns.json")


def resolve_turn_spec(case_dir: Path) -> tuple[Path | None, Any, str]:
    """找轮次清单。两种位置都认，两种形态都认。

    老套件: <case>/runner/turns.json 顶层是数组
    新套件: <case>/turns.json        顶层是 {"turns": [...]}
    找不到就返回 (None, None, "absent") —— 由调用方决定是退化成单轮还是判装置故障。
    """
    for rel in TURN_SPEC_CANDIDATES:
        path = case_dir / rel
        spec = read_json(path)
        if isinstance(spec, list):
            return path, spec, "list"
        if isinstance(spec, dict) and isinstance(spec.get("turns"), list):
            return path, spec["turns"], "dict.turns"
        if spec is not None:
            raise RuntimeError(
                f"{path} 存在但形态不认识（顶层既不是数组也不是 {{'turns': [...]}}）: {type(spec).__name__}")
    return None, None, "absent"


def declared_multi_turn(case_dir: Path) -> bool:
    """题目自己声明了多轮吗（runtime/execution.json 的 conversation 字段）。"""
    profile = read_json(case_dir / "runtime" / "execution.json")
    if isinstance(profile, dict):
        return str(profile.get("conversation", "")).strip() == "multi_turn"
    return False


def load_turn_messages(case_dir: Path, workspace: Path, fallback: str) -> list[str]:
    spec_path, spec, shape = resolve_turn_spec(case_dir)
    if spec is None:
        return [fallback]
    messages: list[str] = []
    for item in spec:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            repeat = str(item.get("repeat", ""))
            count = int(item.get("repeat_count", 0))
            value = str(item.get("prefix", "")) + repeat * count + str(item.get("suffix", ""))
        else:
            raise RuntimeError(f"invalid turn spec in {spec_path}: {item!r}")
        messages.append(value.replace("$WORKSPACE", str(workspace)))
    if not messages:
        raise RuntimeError(f"turn spec is empty: {spec_path}")
    return messages


def run_hook(case_dir: Path, hook: str, run_dir: Path, workspace: Path) -> None:
    script = case_dir / "runner" / hook
    if not script.exists():
        return
    result = subprocess.run(
        ["bash", str(script), str(run_dir), str(workspace)],
        cwd=case_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (run_dir / f"{hook}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (run_dir / f"{hook}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{case_dir.name} {hook} failed: {result.stderr}")


def manifest_for(run_dir: Path, workspace: Path) -> dict[str, Any]:
    candidates = {
        "workspace": workspace,
        "parent_session": run_dir / "parent_session.jsonl",
        "child_session": run_dir / "child_session.jsonl",
        "task_record": run_dir / "task_record.json",
        "run_meta": run_dir / "run_meta.json",
        "trace_dir": run_dir / "trace",
        "run_dir": run_dir,
    }
    return {
        key: os.path.relpath(value, run_dir) if value.exists() else None
        for key, value in candidates.items()
    }


def evidence_path(run_dir: Path, value: str | None) -> str:
    return str((run_dir / value).resolve()) if value else ""


def grade(case_dir: Path, run_dir: Path, workspace: Path) -> dict[str, Any]:
    manifest = manifest_for(run_dir, workspace)
    write_json(run_dir / "evidence_manifest.json", manifest)
    env = dict(os.environ)
    env.update({
        "HARNESSBENCH_PARENT_SESSION_FILE": evidence_path(run_dir, manifest["parent_session"]),
        "HARNESSBENCH_CHILD_SESSION_FILE": evidence_path(run_dir, manifest["child_session"]),
        "HARNESSBENCH_TASK_RECORD": evidence_path(run_dir, manifest["task_record"]),
        "HARNESSBENCH_RUN_META": evidence_path(run_dir, manifest["run_meta"]),
        "HARNESSBENCH_TRACE_DIR": evidence_path(run_dir, manifest["trace_dir"]),
        "HARNESSBENCH_RUN_DIR": evidence_path(run_dir, manifest["run_dir"]),
    })
    result = subprocess.run(
        ["python3", str(case_dir / "verifier" / "score.py"), "--workspace", str(workspace)],
        cwd=case_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (run_dir / "grader.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (run_dir / "grader.stderr.txt").write_text(result.stderr, encoding="utf-8")
    verdict = parse_json_object(result.stdout)

    # ★通用装置门：exec 被审批拦住时，agent 压根没法干活，任何分数都无意义。
    # 这一轮必须显式作废，不许当成模型/harness 的表现（铁律①）。
    blocked = 0
    session_file = run_dir / "parent_session.jsonl"
    if session_file.exists():
        blocked = session_file.read_text(encoding="utf-8", errors="ignore").count(
            "approval is required")
    if blocked:
        verdict = {
            "pass": None, "score": None, "verdict": "DEVICE_FAILURE",
            "device_failure": {"kind": "exec_approval_blocked", "occurrences": blocked,
                               "detail": "exec 被审批门拦住且无交互审批客户端；"
                                         "跑批前须在临时 config 里关掉审批"},
            "note": "本 run 作废重跑，不进分母",
            "grader_output_suppressed": verdict,
        }
    if verdict is None or (result.returncode and verdict.get("verdict") != "DEVICE_FAILURE"):
        verdict = {
            "pass": False,
            "score": 0.0,
            "verdict": "GRADER_ERROR",
            "failure_reason": result.stderr or "grader did not return a JSON object",
        }
    write_json(run_dir / "verifier_result.json", verdict)
    return verdict


def build_control_arms(case_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    evidence = case_dir / "evidence"
    baseline = evidence / "baseline"
    oracle = evidence / "oracle"
    for arm in (baseline, oracle):
        if arm.exists():
            shutil.rmtree(arm)
        arm.mkdir(parents=True)

    baseline_ws = baseline / "workspace-after"
    copy_workspace(case_dir, baseline_ws)
    baseline_result = grade(case_dir, baseline, baseline_ws)

    oracle_ws = oracle / "workspace-after"
    copy_workspace(case_dir, oracle_ws)
    oracle_script = case_dir / "verifier" / "oracle.py"
    if not oracle_script.exists():
        # 新套件多数题没有 oracle.py。有 selftest.py 的用它当「判据能否判对」的对照；
        # 都没有就显式记 absent —— 不许静默当成对照通过。
        selftest = case_dir / "verifier" / "selftest.py"
        if selftest.is_file():
            proc = subprocess.run(["python3", str(selftest)], cwd=case_dir,
                                  capture_output=True, text=True, timeout=600)
            (oracle / "selftest.stdout.txt").write_text(proc.stdout, encoding="utf-8")
            (oracle / "selftest.stderr.txt").write_text(proc.stderr, encoding="utf-8")
            return baseline_result, {"kind": "selftest", "pass": proc.returncode == 0,
                                     "returncode": proc.returncode}
        return baseline_result, {"kind": "absent", "pass": None,
                                 "note": "既无 verifier/oracle.py 也无 verifier/selftest.py，判据正向对照缺失"}
    oracle_proc = subprocess.run(
        ["python3", str(oracle_script), str(oracle)],
        cwd=case_dir,
        capture_output=True,
        text=True,
        timeout=60,
    )
    (oracle / "oracle.stdout.txt").write_text(oracle_proc.stdout, encoding="utf-8")
    (oracle / "oracle.stderr.txt").write_text(oracle_proc.stderr, encoding="utf-8")
    if oracle_proc.returncode:
        raise RuntimeError(f"{case_dir.name} oracle failed: {oracle_proc.stderr}")
    oracle_result = grade(case_dir, oracle, oracle_ws)
    return baseline_result, oracle_result


def collect_subagent(openclaw: Path, agent_id: str, run_dir: Path, openclaw_env: dict[str, str], wait_seconds: int = 90) -> None:
    parent_path = run_dir / "parent_session.jsonl"
    details = spawn_details(parent_path)
    if not details:
        return
    records: list[tuple[dict[str, Any], dict[str, Any] | None, subprocess.CompletedProcess[str] | None]] = [
        (item, None, None) for item in details
    ]
    deadline = time.time() + wait_seconds
    while True:
        pending = False
        next_records = []
        for item, previous, previous_proc in records:
            run_id = str(item.get("runId") or "")
            if not run_id:
                next_records.append((item, previous, previous_proc))
                continue
            task_proc = subprocess.run(
                [str(openclaw), "tasks", "show", run_id, "--json"],
                capture_output=True,
                text=True,
                timeout=30,
                env=openclaw_env,
            )
            task = parse_json_object(task_proc.stdout + task_proc.stderr)
            status = str((task or {}).get("status", "")).lower()
            if status not in {"succeeded", "completed", "failed", "cancelled", "timed_out"}:
                pending = True
            next_records.append((item, task, task_proc))
        records = next_records
        if not pending or time.time() >= deadline:
            break
        time.sleep(2)

    task_dir = run_dir / "trace" / "task_records"
    child_dir = run_dir / "trace" / "child_sessions"
    task_dir.mkdir(parents=True, exist_ok=True)
    child_dir.mkdir(parents=True, exist_ok=True)
    for index, (item, task, task_proc) in enumerate(records, 1):
        child_key = str((task or {}).get("childSessionKey") or item.get("childSessionKey") or "")
        if task_proc is not None:
            (task_dir / f"task-{index}.raw.txt").write_text(
                task_proc.stdout + task_proc.stderr, encoding="utf-8"
            )
        if task:
            write_json(task_dir / f"task-{index}.json", task)
            if index == 1:
                write_json(run_dir / "task_record.json", task)
        if child_key:
            source = session_source(agent_id, child_key)
            if source:
                shutil.copy2(source, child_dir / f"child-{index}.jsonl")
                if index == 1:
                    shutil.copy2(source, run_dir / "child_session.jsonl")


def run_once(
    case_dir: Path,
    openclaw: Path,
    model: str,
    timeout_seconds: int,
    index: int,
) -> dict[str, Any]:
    evidence = case_dir / "evidence"
    run_dir = evidence / f"openclaw-run{index}"
    if run_dir.exists():
        raise RuntimeError(f"refusing to overwrite {run_dir}")
    run_dir.mkdir(parents=True)

    # agent_root 只放临时 config 等运行期文件；live_workspace 是题目材料，
    # ★必须把 live_workspace 交给 agents add —— 题面里的路径都是相对工作区根写的
    #   （老套件题面写 $WORKSPACE/... 绝对路径，所以这个错位一直没暴露）。
    agent_root = Path(tempfile.mkdtemp(prefix=f"hb-{case_dir.name}-"))
    live_workspace = agent_root / "workspace"
    copy_workspace(case_dir, live_workspace)
    agent_id = f"hb-{case_dir.name[:24].lower()}-{uuid.uuid4().hex[:8]}"
    session_key = f"agent:{agent_id}:case-{uuid.uuid4().hex[:10]}"
    prompt = (case_dir / "prompt.md").read_text(encoding="utf-8").replace("$WORKSPACE", str(live_workspace))
    turn_messages = load_turn_messages(case_dir, live_workspace, prompt)
    openclaw_env, plugin_id = prepare_openclaw_env(case_dir, agent_root)

    # 装置门：材料必须就在 agent 的工作区根下，否则 agent 看不见 → 交付必空 → 假阴性
    staged = {q.name for q in live_workspace.iterdir()} if live_workspace.is_dir() else set()
    expected = {q.name for q in (case_dir / "workspace").iterdir()} if (case_dir / "workspace").is_dir() else set()
    if expected and not expected.issubset(staged):
        shutil.rmtree(agent_root, ignore_errors=True)
        raise RuntimeError(f"{case_dir.name} 材料没进 agent 工作区根: 缺 {sorted(expected - staged)}")

    approval_scope = scope_exec_approval(agent_id, True)

    add = subprocess.run(
        [str(openclaw), "agents", "add", agent_id, "--workspace", str(live_workspace),
         "--model", model, "--non-interactive", "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        env=openclaw_env,
    )
    (run_dir / "agent_add.stdout.txt").write_text(add.stdout, encoding="utf-8")
    (run_dir / "agent_add.stderr.txt").write_text(add.stderr, encoding="utf-8")
    if add.returncode:
        scope_exec_approval(agent_id, False)
        shutil.rmtree(agent_root, ignore_errors=True)
        raise RuntimeError(f"failed to add OpenClaw agent: {add.stderr}")

    started = time.time()
    returncode = -1
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        if plugin_id:
            inspect = subprocess.run(
                [str(openclaw), "plugins", "inspect", plugin_id, "--runtime", "--json"],
                capture_output=True, text=True, timeout=60, env=openclaw_env,
            )
            (run_dir / "plugin_inspect.stdout.json").write_text(inspect.stdout, encoding="utf-8")
            (run_dir / "plugin_inspect.stderr.txt").write_text(inspect.stderr, encoding="utf-8")
        run_hook(case_dir, "prerun.sh", run_dir, live_workspace)
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        turns_attempted = 0
        for turn_index, message in enumerate(turn_messages, 1):
            turns_attempted = turn_index
            command = [
                str(openclaw), "agent", "--local", "--json", "--agent", agent_id,
                "--model", model, "--session-key", session_key,
                "--timeout", str(timeout_seconds), "--message", message,
            ]
            try:
                proc = subprocess.run(
                    command,
                    cwd=live_workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds + 60,
                    env=openclaw_env,
                )
                returncode = proc.returncode
                turn_stdout, turn_stderr = proc.stdout, proc.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                turn_stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                turn_stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                returncode = 124
            (run_dir / f"turn-{turn_index}.stdout.txt").write_text(turn_stdout, encoding="utf-8")
            (run_dir / f"turn-{turn_index}.stderr.txt").write_text(turn_stderr, encoding="utf-8")
            stdout_parts.append(turn_stdout)
            stderr_parts.append(turn_stderr)
            if returncode != 0:
                break
        stdout = stdout_parts[-1] if stdout_parts else ""
        stderr = "\n".join(stderr_parts)
        (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
        (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        copy_session(agent_id, session_key, run_dir / "parent_session.jsonl")
        collect_subagent(openclaw, agent_id, run_dir, openclaw_env)
        shutil.copytree(live_workspace, run_dir / "workspace-after", symlinks=True, dirs_exist_ok=True)
        meta = {
            "case_id": case_dir.name,
            "openclaw_version": subprocess.run(
                [str(openclaw), "--version"], capture_output=True, text=True, timeout=30
                , env=openclaw_env
            ).stdout.strip(),
            "model": model,
            "session_key": session_key,
            "exit_code": returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "timeout_sec": timeout_seconds,
            "timed_out": timed_out,
            "turns_planned": len(turn_messages),
            "turns_attempted": turns_attempted,
            "instrument": {"exec_approval_scope": approval_scope,
                           "dropped_default_workspace": openclaw_env.get("HB_DROPPED_DEFAULT_WORKSPACE"),
                           "tools_exec_mode": "full"},
            "turn_spec": {"source": str(resolve_turn_spec(case_dir)[0] or ""),
                          "shape": resolve_turn_spec(case_dir)[2],
                          "declared_multi_turn": declared_multi_turn(case_dir)},
        }
        write_json(run_dir / "run_meta.json", meta)
        run_hook(case_dir, "postrun.sh", run_dir, live_workspace)
        verdict = grade(case_dir, run_dir, run_dir / "workspace-after")
        return {"run": run_dir.name, "meta": meta, "verdict": verdict}
    finally:
        delete = subprocess.run(
            [str(openclaw), "agents", "delete", agent_id, "--force", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            env=openclaw_env,
        )
        (run_dir / "agent_delete.stdout.txt").write_text(delete.stdout, encoding="utf-8")
        (run_dir / "agent_delete.stderr.txt").write_text(delete.stderr, encoding="utf-8")
        scope_exec_approval(agent_id, False)
        shutil.rmtree(agent_root, ignore_errors=True)


def select_cases(names: list[str] | None) -> list[Path]:
    if names:
        cases = [ROOT / name for name in names]
    else:
        # 跳过 _ 前缀（退役/暂存）与工具目录
        cases = sorted(path.parent for path in ROOT.glob("*/task.yaml")
                       if not path.parent.name.startswith("_")
                       and path.parent.name not in {"judge", "regression", "runner"})
    missing = [str(path) for path in cases if not path.exists()]
    if missing:
        raise SystemExit(f"unknown case directories: {', '.join(missing)}")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", help="case directory name; repeatable")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--openclaw", type=Path, default=DEFAULT_OPENCLAW)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--skip-controls", action="store_true")
    parser.add_argument("--list-turns", action="store_true",
                        help="只解析轮次清单并检查多轮装置门，不碰 openclaw")
    args = parser.parse_args()

    if args.list_turns:
        bad = 0
        print(f"{'case':40}{'轮数':>5}  {'形态':12} {'声明':8} spec 位置")
        for case_dir in select_cases(args.case):
            spec_path, spec, shape = resolve_turn_spec(case_dir)
            planned = len(spec) if spec else 1
            declared = declared_multi_turn(case_dir)
            flag = ""
            if declared and planned <= 1:
                flag = "  ← 装置门拦住：声明多轮但只解析出 1 轮"
                bad += 1
            src = str(spec_path.relative_to(case_dir)) if spec_path else "(无)"
            print(f"{case_dir.name:40}{planned:>5}  {shape:12} "
                  f"{'multi' if declared else 'single':8} {src}{flag}")
        print(f"\n合计 {len(select_cases(args.case))} 道 · 会被装置门拦住的 {bad} 道")
        return 0 if bad == 0 else 2

    if args.runs < 1:
        raise SystemExit("--runs must be positive")
    results = []
    for case_dir in select_cases(args.case):
        required = ["task.yaml", "prompt.md", "verifier/score.py"]
        absent = [value for value in required if not (case_dir / value).is_file()]
        if absent:
            print(f"[{case_dir.name}] NOT_RUN missing={','.join(absent)}", flush=True)
            results.append({"case": case_dir.name, "status": "not_run", "missing": absent})
            continue
        # ★装置门：题目声明多轮，却只解析出 1 轮 → 装置故障，不许出分。
        # 这正是迁移前那个静默退化（74 道题的 turns.json 全没被读到，每道只跑 1 轮还照样打分）。
        spec_path, spec, shape = resolve_turn_spec(case_dir)
        planned = len(spec) if spec else 1
        if declared_multi_turn(case_dir) and planned <= 1:
            print(f"[{case_dir.name}] DEVICE_FAILURE 题面声明 multi_turn，"
                  f"但轮次清单解析出 {planned} 轮（spec={shape}）", flush=True)
            results.append({"case": case_dir.name, "status": "device_failure",
                            "reason": "declared_multi_turn_but_single_turn_spec",
                            "turn_spec": {"source": str(spec_path) if spec_path else None,
                                          "shape": shape, "planned": planned}})
            continue
        print(f"[{case_dir.name}] controls · 轮次 {planned}（{shape}）", flush=True)
        baseline = oracle = None
        if not args.skip_controls:
            baseline, oracle = build_control_arms(case_dir)
            print(f"  baseline={baseline.get('pass')} oracle={oracle.get('pass')}", flush=True)
            if baseline.get("pass") or oracle.get("pass") is False:
                results.append({"case": case_dir.name, "status": "invalid_controls",
                                "baseline": baseline, "oracle": oracle})
                continue
        start = next_run_index(case_dir / "evidence")
        live = []
        for offset in range(args.runs):
            item = run_once(case_dir, args.openclaw, args.model, args.timeout, start + offset)
            live.append(item)
            print(f"  {item['run']} pass={item['verdict'].get('pass')} "
                  f"verdict={item['verdict'].get('verdict')}", flush=True)
        results.append({"case": case_dir.name, "status": "ran", "baseline": baseline,
                        "oracle": oracle, "live": live})

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": args.model,
        "openclaw": str(args.openclaw),
        "results": results,
    }
    write_json(ROOT / "reports" / "codex-live-summary.json", summary)
    return 0 if all(item["status"] in {"ran"} for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
