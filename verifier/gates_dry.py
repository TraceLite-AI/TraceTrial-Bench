# -*- coding: utf-8 -*-
"""gate 干跑（零 API）—— 新 trace 落地后先跑这个，看门的行为对不对，再放判官。

看什么：
  · 参评数异常高的门（像上次 L.goal 46/57 —— 散文误开门）
  · 直接fail/直接pass 逐条过眼（程序直判 = 0% 翻转，但门错了就是 100% 错）
  · 无结果集中的门（装置/量具问题，先修装置）
"""
import json, sys, collections
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
from roles import is_arm
import gates as G


def main():
    prog = {r["case"]: r for r in json.loads((R / "VERIFY_ALL.json").read_text(encoding="utf-8"))}
    agg = collections.defaultdict(collections.Counter)
    hard, dead = [], []
    for c in sorted(prog):
        if is_arm(c) or prog[c]["state"] != "ok":
            continue
        for rid in G.GATES:
            st, ev = G.run_gate(rid, R / c)
            agg[rid][st] += 1
            if st in ("直接fail", "直接pass"):
                hard.append((c, rid, st, ev))
            elif st == "无结果":
                dead.append((c, rid, ev))

    n_case = sum(1 for c in prog if not is_arm(c) and prog[c]["state"] == "ok")
    print("gate 干跑：%d 题 × %d 门（零 API）" % (n_case, len(G.GATES)))
    print("=" * 100)
    print("%-36s %6s %8s %8s %8s %6s" % ("判据", "参评", "不参评", "直fail", "直pass", "无结果"))
    print("-" * 100)
    for rid in sorted(agg):
        a = agg[rid]
        warn = "  ★参评过半，查门是否误开" if a["参评"] > n_case * 0.5 else ""
        print("%-36s %6d %8d %8d %8d %6d%s" % (rid, a["参评"], a["不参评"],
              a["直接fail"], a["直接pass"], a["无结果"], warn))
    print("\n程序直判逐条（过眼）:")
    for c, rid, st, ev in hard:
        print("  %-9s %-32s %-30s %s" % (st, c[:32], rid, ev[:76]))
    if dead:
        dd = collections.Counter(rid for _, rid, _ in dead)
        print("\n无结果集中的门（装置/量具问题）: %s" % dict(dd))


if __name__ == "__main__":
    main()
