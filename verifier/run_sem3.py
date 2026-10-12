# -*- coding: utf-8 -*-
"""语义层 v3 全库跑批（gate + ask + evidence gate）。

产物 SEM_ALL_V3.json；逐题落盘，中断可续（已有结果的题跳过）。
对照臂目录不跑 —— 对照臂已废弃，旋钮保持关死，判定全走 rubric 检查点。
"""
import json, re, sys, time
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
from roles import is_arm
from tracefp import trace_fp
import judge_sem3 as J

OUT = R / "SEM_ALL_V3.json"


def comp_of(c):
    p = R / c / "task.yaml"
    m = re.search(r"(?m)^\s+subdimension:\s*([A-Z])", p.read_text(errors="ignore")) if p.is_file() else None
    return m.group(1) if m else "C"


def main():
    prog = {r["case"]: r for r in json.loads((R / "VERIFY_ALL.json").read_text(encoding="utf-8"))}
    done = {}
    if OUT.is_file():
        done = {r["case"]: r for r in json.loads(OUT.read_text(encoding="utf-8"))}
    # ★断点续传只认「题名 + trace 指纹」都没变的：数据重出 trace 后题名不变，
    #   只按题名跳过会让新数据吃旧判决（静默污染）。指纹变了 → 重算。
    fps = {c: trace_fp(R / c) for c in prog if not is_arm(c)}
    stale = [c for c in done if fps.get(c) and done[c].get("trace_fp") != fps[c]]
    for c in stale:
        del done[c]
    todo = [c for c in sorted(prog)
            if not is_arm(c) and prog[c]["state"] == "ok" and c not in done]
    print("待跑 %d 道（已完成 %d，其中 %d 道因 trace 变更作废重算）"
          % (len(todo), len(done), len(stale)), flush=True)
    t0 = time.time()
    for i, c in enumerate(todo, 1):
        try:
            r = J.judge_case(c, comp_of(c), votes=3)
        except Exception as e:
            print("%2d/%d %-38s ★整题失败 %s" % (i, len(todo), c[:38], str(e)[:80]), flush=True)
            continue
        r["trace_fp"] = fps.get(c, "")
        done[c] = r
        OUT.write_text(json.dumps(list(done.values()), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        vs = r["verdicts"]
        n_gate = sum(1 for v in vs if v.get("gated"))
        n_fail = sum(1 for v in vs if v.get("value") == "fail")
        print("%2d/%d %-38s 判据 %d | gate 拦截 %d | fail %d | API %d 次 | 累计 %.0f 分钟"
              % (i, len(todo), c[:38], len(vs), n_gate, n_fail,
                 r["api_calls"], (time.time() - t0) / 60), flush=True)
    print("★完成", flush=True)


if __name__ == "__main__":
    main()
