#!/usr/bin/env python
"""D4 verifier: 独立正确性(各模块) × 集成一致性(不变量) —— 两者都过才算赢"""
import argparse, glob, json, os, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(); ap.add_argument("--workspace", required=True)
ap.add_argument("--harness", default="openclaw"); ap.add_argument("--state", default="")
ap.add_argument("--exit-code", type=int, default=0); ap.add_argument("--label", default="")
a = ap.parse_args(); ws = Path(a.workspace)
T = json.loads((HERE/"truth.json").read_text())
if a.exit_code in (124, 137, 143):
    print(json.dumps({"label": a.label, "verdict": "inconclusive",
        "reason": "外部终止 exit=%d" % a.exit_code}, ensure_ascii=False, indent=1)); raise SystemExit

spawn = 0
if a.state:
    for f in glob.glob(os.path.join(a.state, "agents/*/sessions/*.jsonl")):
        if "trajectory" in f: continue
        for l in open(f, errors="ignore"):
            if not l.strip(): continue
            try: r = json.loads(l)
            except Exception: continue
            for it in (r.get("message") or {}).get("content") or []:
                if isinstance(it, dict) and it.get("type") == "toolCall" and it.get("name") in ("sessions_spawn","subagents","task"):
                    spawn += 1

# ① 各模块是否还留着 NotImplementedError
未实现 = []
for m in T["modules"] + ["envelope"]:
    p = ws/"core"/(m + ".py")
    if not p.exists(): 未实现.append(m + "(缺文件)"); continue
    if "NotImplementedError" in p.read_text(errors="ignore"): 未实现.append(m)

# ② 官方集成检查（verifier 自带，不依赖 agent 写的测试）
probe = HERE/"integration_probe.py"
inv = {}; probe_out = ""
if probe.exists():
    r = subprocess.run([sys.executable, str(probe)], capture_output=True, text=True,
                       cwd=str(ws), timeout=180)
    probe_out = ((r.stdout or "") + (r.stderr or ""))[-1500:]
    for line in (r.stdout or "").splitlines():
        m2 = re.match(r"(I[1-4])\s*=\s*(PASS|FAIL)(?:\s*:\s*(.*))?", line.strip())
        if m2: inv[m2.group(1)] = {"ok": m2.group(2) == "PASS", "note": (m2.group(3) or "")[:90]}

# ③ agent 自己的测试
r2 = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                    capture_output=True, text=True, cwd=str(ws), timeout=300)
tout = (r2.stderr or "") + (r2.stdout or "")
mm = re.search(r"Ran (\d+) tests?", tout); ran = int(mm.group(1)) if mm else 0
green = r2.returncode == 0 and ran > 0
has_integration = (ws/"tests"/"test_integration.py").exists()

inv_ok = sum(1 for v in inv.values() if v["ok"])
RR = {"RR1_all_modules_implemented": len(未实现) == 0,
      "RR2_own_tests_green": green,
      "RR3_integration_test_written": has_integration,
      "RR4_invariants_hold": len(inv) == 4 and inv_ok == 4}
attr = [{"layer": "capability", "item": "subagent_usage", "verdict": "observation",
         "evidence": "子代理调用 %d 次 | 模块实现 %d/%d | 官方不变量 %d/4 | 自测 %s(%d 条)"
                     % (spawn, len(T["modules"]) + 1 - len(未实现), len(T["modules"]) + 1,
                        inv_ok, green, ran)}]
if spawn == 0:
    attr.append({"layer": "capability", "item": "subagent_usage", "verdict": "inconclusive(capability)",
        "evidence": "本臂一次未用子代理 → 与对照臂的差异不能归因于该能力"})
if RR["RR2_own_tests_green"] and not RR["RR4_invariants_hold"]:
    attr.append({"layer": "RR", "item": "RR4_invariants_hold",
        "verdict": "model-side(judgment: 各模块独立自洽但集成不一致)",
        "evidence": "自测全绿却违反共享不变量: %s —— 这正是并行实现的典型代价"
                    % {k: v["note"] for k, v in inv.items() if not v["ok"]}})
if 未实现:
    attr.append({"layer": "RR", "item": "RR1_all_modules_implemented", "verdict": "model-side(未实现完)",
        "evidence": "仍留 NotImplementedError: %s" % 未实现})
print(json.dumps({"label": a.label, "harness": a.harness, "RR": RR,
    "rr_pass_all": all(RR.values()),
    "metrics": {"spawn_count": spawn, "unimplemented": 未实现,
                "invariants": inv, "invariants_ok": inv_ok,
                "own_tests_ran": ran, "own_tests_green": green,
                "wrote_integration_test": has_integration},
    "probe_tail": probe_out, "attribution": attr}, ensure_ascii=False, indent=1))
