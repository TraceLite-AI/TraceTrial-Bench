#!/usr/bin/env python3
"""跑前预检: 打分器能否跑通 / 产出是否交代 / 插件依赖 / 包外绝对路径"""
import json, os, pathlib, re, subprocess, sys
ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
bad = []
for d in sorted(ROOT.iterdir()):
    if not d.is_dir() or not (d / "task.yaml").is_file(): continue
    probs = []
    sp = d / "verifier" / "score.py"
    if not sp.is_file():
        probs.append("无 score.py")
    else:
        src = sp.read_text(errors="ignore")
        cmd = [sys.executable, "verifier/score.py", "--workspace", "workspace"]
        if "--harness" in src: cmd += ["--harness", "openclaw"]
        if "--label" in src: cmd += ["--label", d.name]
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(d), timeout=300)
        out = (r.stdout or "").strip()
        if re.search(r"Traceback|ModuleNotFoundError|FileNotFoundError", (r.stderr or "")):
            probs.append("打分器崩: " + (r.stderr or "").strip().splitlines()[-1][:70])
        elif not out:
            probs.append("打分器无输出")
        else:
            try: json.loads(out)
            except Exception: probs.append("打分器输出非 JSON")
    # 包外绝对路径
    for vf in (d / "verifier").glob("*.json"):
        try: txt = vf.read_text(errors="ignore")
        except Exception: continue
        txt = re.sub(r'"[a-z_]*_origin"\s*:\s*"[^"]*"', '', txt)
        for m in re.findall(r'"(/(?:Users|home)/[^"]+)"', txt):
            if not m.startswith(str(d.resolve())): probs.append("包外绝对路径(%s): %s" % (vf.name, m[:56]))
    # 产出是否交代
    turns = json.load(open(d / "turns.json"))["turns"] if (d / "turns.json").is_file() else [(d / "prompt.md").read_text()]
    T = "\n".join(turns)
    for vf in ("rubric.py", "score.py"):
        p = d / "verifier" / vf
        if p.is_file():
            for x in set(re.findall(r'"output"\s*/\s*"([A-Za-z0-9_.-]+)"', p.read_text(errors="ignore"))):
                if x not in T: probs.append("产出未交代: " + x)
    # 声明了插件却没带
    ex = d / "runtime" / "execution.json"
    wm = d / "verifier" / "wrap_meta.json"
    if wm.is_file():
        m = json.loads(wm.read_text())
        og = m.get("official_grader", "")
        if og and not (d / og).is_file() and not pathlib.Path(og).is_file():
            probs.append("官方 grader 找不到: " + og)
    if probs: bad.append((d.name, sorted(set(probs))))
print("预检 %d 个 case | 有问题 %d 个\n" % (sum(1 for x in ROOT.iterdir() if x.is_dir() and (x/"task.yaml").is_file()), len(bad)))
for n, p in bad:
    print("  ★ %s" % n)
    for x in p: print("      %s" % x)
