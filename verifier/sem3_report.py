# -*- coding: utf-8 -*-
"""语义层 v3 报表：四栏分解 + 与 v2 逐条 diff。

## 四栏 —— 一条判据的结论现在只可能来自这四种渠道，账要分开记
    判官      gate 放行后判官 3 票 +（gate 管辖时 pass/fail 双向）引文卡分
    程序直判  gate 直接出 pass/fail（0% 翻转，证据是 gate 那句话）
    不参评    机制未触发/未暴露/在而未用 —— ★不进任何分母
    无结果    装置取不到材料 / 量具无效 —— 记装置账，修装置不记 harness

## diff 的读法
v2 的 fail 在 v3 里消失，有三种去向，含义完全不同：
    → 不参评   v2 是在机制没触发的题上硬判出的 fail（假阳性，删得对）
    → 无结果   量具无效（timeline 那类），v2 的 fail 本来就不可归因
    → pass    判官在净化后的证据上改判 —— 这类要人工抽查
"""
import json, sys, collections
from pathlib import Path

R = Path.home() / "hb-cases"
sys.path.insert(0, str(R))
from roles import is_arm


def load(name):
    p = R / name
    return {r["case"]: r for r in json.loads(p.read_text(encoding="utf-8"))} if p.is_file() else {}


def main():
    v2 = load("SEM_ALL.json")
    v3 = load("SEM_ALL_V3.json")
    print("v2 %d 题 ｜ v3 %d 题\n" % (len(v2), len(v3)))

    # ── 四栏分解 ──
    lane = collections.Counter()
    val = collections.Counter()
    fails3 = []
    for c, r in v3.items():
        for v in r.get("verdicts", []):
            g = v.get("gate")
            if g == "不参评":
                lane["不参评"] += 1
            elif g == "无结果" or "装置" in (v.get("note") or ""):
                lane["无结果(装置/量具)"] += 1
            elif v.get("program"):
                lane["程序直判"] += 1
                val["prog_" + (v.get("value") or "?")] += 1
            elif v.get("anchor_empty"):
                lane["锚点空(on_empty)"] += 1
            else:
                lane["判官"] += 1
                val["judge_" + (v.get("value") or "无取值")] += 1
            if v.get("value") == "fail":
                fails3.append((c, v["id"], "程序" if v.get("program") else "判官",
                               (v.get("gate_evidence") or v.get("reason") or "")[:90]))

    print("=" * 96)
    print("四栏分解（v3 全部判据结论的来源）")
    print("=" * 96)
    for k, n in lane.most_common():
        print("   %-18s %4d 条" % (k, n))
    print("   判官取值: %s ｜ 程序直判取值: %s" %
          ({k[6:]: n for k, n in val.items() if k.startswith("judge_")},
           {k[5:]: n for k, n in val.items() if k.startswith("prog_")}))

    print("\n" + "=" * 96)
    print("v3 全部 fail（%d 条，每条带来源与证据）" % len(fails3))
    print("=" * 96)
    for c, rid, src, ev in sorted(fails3):
        print("   [%s] %-34s %-30s %s" % (src, c[:34], rid, ev))

    # ── v2 → v3 diff ──
    print("\n" + "=" * 96)
    print("v2 的 fail 到 v3 的去向")
    print("=" * 96)
    gone = collections.Counter()
    detail = []
    for c, r2 in v2.items():
        if c not in v3 or is_arm(c):
            continue
        m3 = {v["id"]: v for v in v3[c].get("verdicts", [])}
        for v in r2.get("verdicts", []):
            if v.get("value") != "fail":
                continue
            n3 = m3.get(v["id"])
            if n3 is None:
                dest = "判据已移除"
            elif n3.get("value") == "fail":
                dest = "仍 fail" + ("(程序)" if n3.get("program") else "(判官)")
            elif n3.get("gate") == "不参评":
                dest = "→不参评(机制未触发,v2是假阳性)"
            elif n3.get("gate") == "无结果" or "量具" in (n3.get("gate_evidence") or ""):
                dest = "→无结果(量具无效)"
            elif n3.get("value") == "pass":
                dest = "→pass(★需人工抽查)"
            else:
                dest = "→无结果(其他)"
            gone[dest] += 1
            detail.append((c, v["id"], dest))
    for k, n in gone.most_common():
        print("   %-36s %d 条" % (k, n))
    print()
    for c, rid, dest in detail:
        if "抽查" in dest or "仍 fail" in dest:
            print("   %-34s %-32s %s" % (c[:34], rid, dest))

    # 新增的 fail（v2 没有、v3 有）
    print("\nv3 新增的 fail（v2 里不是 fail）:")
    for c, rid, src, ev in sorted(fails3):
        v2m = {v["id"]: v.get("value") for v in (v2.get(c) or {}).get("verdicts", [])}
        if v2m.get(rid) != "fail":
            print("   [%s] %-34s %-30s v2=%s" % (src, c[:34], rid, v2m.get(rid, "无此条")))


if __name__ == "__main__":
    main()
