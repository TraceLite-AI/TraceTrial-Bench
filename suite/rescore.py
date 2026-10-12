#!/usr/bin/env python3
"""用当前(已修复的) verifier 对已完成 case 重新打分, 覆盖 score.json。
★必须完全复刻 runner 调 verifier 时的环境变量, 否则官方 grader 找不到 preflight/trace, 会被静默降级。"""
import json, os, subprocess, sys, pathlib, glob, re
ROOT = pathlib.Path(os.path.expanduser("~/hb-cases"))
only = sys.argv[1:] if len(sys.argv) > 1 else None
running = set()
try:
    ps = subprocess.run(["pgrep", "-u", os.environ.get("USER", ""), "-af", "run_case.py"],
                        capture_output=True, text=True).stdout
    for ln in ps.splitlines():
        for d in ROOT.iterdir():
            if d.is_dir() and d.name in ln and not (d/"score.json").exists():
                running.add(d.name)
except Exception:
    pass
def argspec(p):
    s = p.read_text(errors="ignore")
    return {"harness": "--harness" in s, "state": "--state" in s,
            "exit": "--exit-code" in s, "label": "--label" in s, "truth": "--truth" in s}
n_ok = n_skip = 0
for d in sorted(ROOT.iterdir()):
    if not d.is_dir() or not (d/"verifier"/"score.py").is_file(): continue
    if d.name.startswith("_"): continue
    if only and d.name not in only: continue
    if not (d/"score.json").exists(): n_skip += 1; continue
    if d.name in running: print("  跳过(在跑): %s" % d.name); n_skip += 1; continue
    va = argspec(d/"verifier"/"score.py")
    # ★必须传绝对路径：verifier 里常有 chk=ws/"tools"/x 配 cwd=ws 的写法，
    # 相对路径会让它从 workspace/ 里再找 workspace/tools/x → bash 返回 127（实测坏了 E1/J1/J3）
    cmd = [sys.executable, "verifier/score.py", "--workspace", str((d/"workspace").resolve())]
    if va["harness"]: cmd += ["--harness", "openclaw"]
    if va["state"] and (d/"state").is_dir(): cmd += ["--state", str((d/"state").resolve())]
    if va["label"]: cmd += ["--label", d.name]
    if va["truth"]:
        for cand in (d/"verifier"/"truth.json", d/"workspace"/"truth.json", d/"truth.json"):
            if cand.is_file(): cmd += ["--truth", str(cand)]; break
    # ★ 复刻 runner 的 env
    env = dict(os.environ,
               HARNESSBENCH_WORKSPACE=str(d/"workspace"),
               HARNESSBENCH_STATE_DIR=str(d/"state"),
               HARNESSBENCH_RUN_DIR=str(d/"run"),
               HARNESSBENCH_HARNESS_EVENTS=str(d/"harness_events.json"))
    rm = d/"run"/"run_meta.json"
    if rm.is_file(): env["HARNESSBENCH_RUN_META"] = str(rm)
    fs = [f for f in glob.glob(str(d/"state"/"agents"/"*"/"sessions"/"*.jsonl")) if "trajectory" not in f]
    if fs: env["HARNESSBENCH_PARENT_SESSION_FILE"] = sorted(fs, key=os.path.getmtime)[-1]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(d), timeout=600, env=env)
    out = (r.stdout or "").strip()
    if not out:
        print("  ★ %s 打分器无输出: %s" % (d.name, (r.stderr or "")[-140:])); continue
    try: json.loads(out)
    except Exception:
        print("  ★ %s 输出非 JSON: %s" % (d.name, out[:110])); continue
    (d/"score.json").write_text(out); n_ok += 1
print("重打分 %d 个 | 跳过 %d 个" % (n_ok, n_skip))
