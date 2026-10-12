#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻译自检 —— 每道手工翻译完都要过这一关。

★这不是翻译器，是**检查器**。翻译内容一律手写，这里只负责逐项核对
  「机制层有没有被改动」，把手工容易漏的地方钉死。

用法：
    python3 translate/check.py <参考题 id> <翻译产物目录>

检查项（都是踩过的坑）：
  1. 判据 id 集合完全一致
     —— 换词表会把 hr_lot8003_survives_before_tool 改成 hr_set8003_...
        id 是机制标识，归因脚本靠它跨题对齐，改了汇总就散架（C3b 实测踩到）
  2. evidence 块数量、event_types 列表、max_events 取值逐项一致
     —— C2c 实测踩到：我按 C1c 的样子写成泛化的 ["harness","system",...]，
        而 C2c 判的是压缩事件本身，必须筛具体的 context_compaction 事件
  3. 机制字段（dimension / subdimension / level / budget 三元组）逐字一致
  4. 轮数一致，且轮次里的硬约束（checkpoint 行、禁写文件、14 次全量读…）不丢
  5. 跨文件引用对得上（server.py 里 with_name(...) 指的文件要存在）
  6. simulator 的机制关键词出现次数一致（三个计数器、指纹、一次性限额）
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_BRANCH = "origin/task_verifier"
REF_DIR = "tasks/new_cases"

# 轮次里必须保住的硬约束（不同题各有几条，命中的才比）
TURN_CONSTRAINTS = [
    "standalone checkpoint line",
    "only model-visible text before your first tool call",
    "do not write them to a sidecar note or memory file",
    "wait for all 14 reads",
    "Do not inspect only headers or tails",
    "scripts, shell aggregation",
    "record 0760",
    "exactly once",
]

MECH_KEYS = [
    "complete_export_limit", "rejected_export_calls", "range_export_calls",
    "complete_export_calls", "issued_fingerprints", "secrets.token_hex",
    "hashlib.sha256", "[:16].upper()", "_transport_exit_code",
    "already_inspected", "already_verified", "already_signed",
]


def ref_text(case, rel):
    r = subprocess.run(["git", "show", "%s:%s/%s/%s" % (REF_BRANCH, REF_DIR, case, rel)],
                       cwd=ROOT, capture_output=True)
    return r.stdout.decode("utf-8", "ignore") if r.returncode == 0 else None


def ids(text):
    return set(re.findall(r"- id:\s*(\S+)", text or ""))


def field(text, key):
    m = re.search(r"^\s*%s\s*(.*)$" % re.escape(key), text or "", re.M)
    return m.group(1).strip() if m else None


def check(case, tgt):
    tgt = Path(tgt)
    probs = []

    # 1 判据 id + 2 evidence 声明
    for rel in ("verifier/harness_score.yaml", "verifier/agent_score.yaml"):
        r = ref_text(case, rel)
        if r is None:
            continue
        p = tgt / rel
        if not p.is_file():
            probs.append("缺 %s" % rel)
            continue
        m = p.read_text(encoding="utf-8")
        if ids(r) != ids(m):
            probs.append("%s 判据 id 不一致：少 %s / 多 %s"
                         % (rel.split("/")[-1], sorted(ids(r) - ids(m)),
                            sorted(ids(m) - ids(r))))
        if r.count("evidence:") != m.count("evidence:"):
            probs.append("%s evidence 块 %d→%d"
                         % (rel.split("/")[-1], r.count("evidence:"), m.count("evidence:")))
        if re.findall(r"event_types:\s*(\[.*?\])", r) != re.findall(r"event_types:\s*(\[.*?\])", m):
            probs.append("%s event_types 列表变了" % rel.split("/")[-1])
        if re.findall(r"max_events:\s*(\d+)", r) != re.findall(r"max_events:\s*(\d+)", m):
            probs.append("%s max_events 变了" % rel.split("/")[-1])
        if r.count("match: all") != m.count("match: all"):
            probs.append("%s match:all 条数变了" % rel.split("/")[-1])

    # 3 机制字段
    ry, my = ref_text(case, "task.yaml"), (tgt / "task.yaml").read_text(encoding="utf-8")
    for key in ("dimension:", "subdimension:", "subdimension_name:", "level:",
                "max_turns:", "max_tool_calls:", "max_wall_time_sec:"):
        a, b = field(ry, key), field(my, key)
        if a and b and a != b:
            probs.append("%s %s → %s" % (key.strip(":"), a, b))

    # 4 轮数 + 轮次硬约束
    rt = ref_text(case, "turns.json")
    if rt:
        # 有的参考题 turns.json 是裸数组，有的是 {"turns": [...]}，两种都收
        unwrap = lambda v: v["turns"] if isinstance(v, dict) else v
        rturns = unwrap(json.loads(rt))
        mturns = unwrap(json.loads((tgt / "turns.json").read_text(encoding="utf-8")))
        if len(rturns) != len(mturns):
            probs.append("轮数 %d → %d" % (len(rturns), len(mturns)))
        # 轮次元素有四种写法：裸字符串、{"text":...}、{"role","content"}、
        # {"message":..., "new_session":true}（跑批器用来断会话的形态）
        def body(x):
            if isinstance(x, str):
                return x
            return x.get("message") or x.get("text") or x.get("content") or ""

        s_r = " ".join(body(x) for x in rturns)
        s_m = " ".join(body(x) for x in mturns)
        for kw in TURN_CONSTRAINTS:
            if s_r.count(kw) != s_m.count(kw):
                probs.append("轮次约束「%s」%d→%d" % (kw[:26], s_r.count(kw), s_m.count(kw)))

    # 5 跨文件引用
    sp = tgt / "simulator" / "server.py"
    if sp.is_file():
        for m in re.finditer(r'with_name\("([^"]+)"\)', sp.read_text(encoding="utf-8")):
            if not (sp.parent / m.group(1)).is_file():
                probs.append("server.py 引用的 %s 不存在" % m.group(1))

    # 6 simulator 机制关键词
    rs = ref_text(case, "simulator/server.py")
    if rs and sp.is_file():
        ms = sp.read_text(encoding="utf-8")
        body = lambda t: t.split('"""', 2)[2] if t.count('"""') >= 2 else t
        for k in MECH_KEYS:
            if body(rs).count(k) != body(ms).count(k):
                probs.append("server.py 机制词 %s %d→%d"
                             % (k, body(rs).count(k), body(ms).count(k)))
    return probs


def main():
    if len(sys.argv) != 3:
        print("用法: python3 translate/check.py <参考题 id> <翻译产物目录>")
        return 2
    probs = check(sys.argv[1], sys.argv[2])
    name = Path(sys.argv[2]).name
    if not probs:
        print("  %-46s 机制层全部一致" % name)
        return 0
    print("  %-46s ★ %d 处不一致" % (name, len(probs)))
    for p in probs:
        print("      %s" % p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
