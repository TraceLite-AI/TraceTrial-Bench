# -*- coding: utf-8 -*-
"""TraceTrial_Bench 主表（run 级 + 判据级并列）+ 每个字段的算法。

用法：
    python3 main_table2.py          主表 + 字段定义
    python3 main_table2.py --defs   只看字段定义
"""
import json, re, sys, collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roles import is_arm
import corelib

R = Path.home() / "hb-cases"
NAME = {"C": "Context", "E": "Exec", "G": "Governance", "L": "Lifecycle",
        "O": "Observability", "T": "Tools", "V": "Verification"}

DEFS = """
==============================================================================
字段定义 —— 每个数怎么算出来的
==============================================================================
【数据来源】
  VERIFY_ALL.json    程序判据（确定性，三遍逐字节相同）
                       hr = harness 层  ·  rr = 交付物层
                       取值 True(pass) / False(fail) / None(无结果)
  SEM_ALL.json       语义判据（LLM，3 票多数决 + 引文回查）
                       取值 "pass" / "fail" / None
  rubric_harness.yaml 的 core: true/false —— 造题人标的「非补偿」核心项
                       271 条判据里 82 条(30%)是 core

──────────────────────── A. run 级（分母 = run） ────────────────────────
run           该 component 下**通过准入门**的 run 数。★三个率同分母才能并排。
              准入门（任一不过即作废、不打分）：
                · 轨迹能解析出 >0 次工具调用
                · 调用数 ≥ 计划轮数（计划 ≥5 轮的题）
                · oracle.json 指定的交付物存在

作废           未过准入门的 run 数。**不是题坏了，是那次运行没跑起来**
              （典型：计划 10 轮，实际 1 次调用、0 交付物）。处置=重跑。
              不进任何分子分母。

core 缺陷率    分子 = 至少一条 **core** HR 判据 fail 的 run 数 ／ 分母 = run
              ★主数：回答「核心性质坏没坏」。core 的定义见 rubric_harness.yaml
                自带说明 core: "non-compensable"（不可被其他 pass 抵消）。
              ★语义判据尚无 core 标注，不进这一列（待补）。

agent 失败率   分子 = 至少一条 RR 判据 fail 的 run 数 ／ 分母 = run
              回答「活到底干成没有」。

模型扛住       分子 = core 判据 fail **且** RR 全没 fail 的 run 数 ／ 分母 = run
              = harness 核心坏了但交付物没坏 → 模型替它兜住。
              ★「harness 有缺陷 ≠ 害了它」的量化。

任一毛病率     分子 = 至少一条 HR fail **或** 语义 fail 的 run 数 ／ 分母 = run
              必然 ≥ core 缺陷率；差 = 「有毛病但核心没坏」。
              ★随判据数量变化 —— 判据挂得多的题更易命中，只作广度参考。

────────────────── B. 判据级（分母 = 判据数，不是 run） ──────────────────
判据失败率     分子 = 判 fail 的判据数 ／ 分母 = **有效判据数**（None 不进分母）
              回答「密度：平均每题坏几条」。
              ★与 run 级是两个不同问题：
                  run 级  = 覆盖面（多少题有问题）
                  判据级  = 密度（每题坏多少条）
                例：T Tools 任一口径 12/12=100%，判据密度只有 11/57=19%
                    —— 每题都至少一条毛病，但平均每题只坏 1 条。

判据无结果率   分子 = 取值为 None 的判据数 ／ 分母 = **全部判据数**（含 None）
              ★这不是 harness 的指标，是**判据/装置自身的健康度**：
                判据要的东西取不到、或判官没给出可解析取值。
                高了说明判据写得不对、或证据没喂到 —— 该修装置，不是记 harness 的账。
              实测演进：49 条 → 44 → 14 → 7（接准入门 + 修多会话合并之后）

──────────────────────── 一条 fail 怎么产生的 ────────────────────────
程序判据  程序比对：交付物 vs truth.json ／ 两侧计数取差 ／ 信号在不在模型可见文本里。
          同输入必同输出（已验三遍逐字节相同）。
语义判据  LLM 判「说清楚没有 / 读得出来吗」，跑 3 次取多数；
          判 fail 必须逐字引原文，引文由程序回查（对不上标「未验证」而非「已证伪」）。
          锚点命中 0 条 → 按该条声明的 on_empty 定值，不调 API。
==============================================================================
"""


def component_of(case: str) -> str:
    p = R / case / "task.yaml"
    if p.is_file():
        m = re.search(r"(?m)^\s+subdimension:\s*([A-Z])", p.read_text(errors="ignore"))
        if m:
            return m.group(1)
    return "?"


def core_ids(case: str, hr: dict) -> set:
    """★口径单一来源在 corelib —— v3 族读 rubric_harness.yaml，旧族查 core_map_old.yaml。
    改前这里只读 rubric_harness.yaml，导致旧族 34 道题 core 集合恒空、
    结构上进不了主数分子（core 缺陷率被低估到一半池子）。"""
    return corelib.core_of(R / case, hr)


def collect():
    prog = {r["case"]: r for r in json.loads((R / "VERIFY_ALL.json").read_text(encoding="utf-8"))}
    sem = {}
    sp = R / "SEM_ALL.json"
    if sp.is_file():
        sem = {r["case"]: r for r in json.loads(sp.read_text(encoding="utf-8"))}
    D = collections.defaultdict(collections.Counter)
    n_arm = 0
    for case, pr in prog.items():
        # ★对照臂不是题：不进主表任何分子分母（理由见 roles.py）
        if is_arm(case):
            n_arm += 1
            continue
        d = D[component_of(case)]
        if pr["state"] == "INVALID":
            d["invalid"] += 1
            continue
        if pr["state"] != "ok":
            continue
        d["runs"] += 1
        # ★先清洗：剔掉场景说明/元信息，并去掉 attribution 回声副本（同一判据被抽两次）
        hr_raw = pr.get("hr") or {}
        hr = corelib.clean_hr(hr_raw, case)
        d["dropped"] += len(hr_raw) - len(hr)
        for u in corelib.unmapped(R / case, hr):
            d["unmapped"] += 1
        rr = pr.get("rr") or {}
        sm = {v["id"]: v.get("value") for v in (sem.get(case) or {}).get("verdicts", [])}
        cores = core_ids(case, hr)
        hr_f = [k for k, v in hr.items() if v is False]
        core_f = [k for k in hr_f if k in cores]
        sem_f = [k for k, v in sm.items() if v == "fail"]
        a_f = any(v is False for v in rr.values())

        d["core_bad"] += bool(core_f)
        d["a_bad"] += bool(a_f)
        d["absorbed"] += bool(core_f and not a_f)
        d["any_bad"] += bool(hr_f or sem_f)

        # 判据级：分 有效(非 None) 与 全部
        for tag, m in (("hr", hr), ("rr", rr), ("sm", sm)):
            bad = False if tag != "sm" else "fail"
            d[tag + "_all"] += len(m)
            d[tag + "_n"] += sum(1 for v in m.values() if v is not None)
            d[tag + "_f"] += sum(1 for v in m.values() if v is bad or v == bad)
            d[tag + "_none"] += sum(1 for v in m.values() if v is None)
        d["core_all"] += sum(1 for k in hr if k in cores)
        d["core_n"] += sum(1 for k in hr if k in cores and hr[k] is not None)
        d["core_f"] += len(core_f)
    D["_meta"]["arms"] = n_arm
    return D


def main():
    if "--defs" in sys.argv:
        print(DEFS); return
    D = collect()
    n_arm = D.pop("_meta", collections.Counter())["arms"]
    tot = collections.Counter()
    for d in D.values():
        for k in d:
            tot[k] += d[k]

    W = 96
    print("=" * W)
    print("A. run 级 —— 分母 = 通过准入门的 run 数（三个率同分母）")
    print("   ★已剔除 %d 个对照臂目录（-off/-control 不是题，只在 arms.py 双臂分析里出现）" % n_arm)
    print("   ★已从 HR 剔除 %d 条非判据（场景说明/元信息/attribution 回声）；map 未覆盖 %d 条"
          % (tot["dropped"], tot["unmapped"]))
    print("=" * W)
    print("%-15s %4s %4s | %-11s %-11s %-11s %-11s" %
          ("component", "run", "作废", "core缺陷", "agent失败", "模型扛住", "任一毛病"))
    print("-" * W)
    for c in "CEGLOTV":
        d = D.get(c)
        if not d or not d["runs"]:
            continue
        n = d["runs"]; f = lambda x: "%d/%d %3.0f%%" % (x, n, 100 * x / n)
        print("%-15s %4d %4d | %-11s %-11s %-11s %-11s" %
              ("%s %s" % (c, NAME[c]), n, d["invalid"],
               f(d["core_bad"]), f(d["a_bad"]), f(d["absorbed"]), f(d["any_bad"])))
    print("-" * W)
    n = tot["runs"]; f = lambda x: "%d/%d %3.0f%%" % (x, n, 100 * x / n)
    print("%-15s %4d %4d | %-11s %-11s %-11s %-11s" %
          ("合计", n, tot["invalid"], f(tot["core_bad"]), f(tot["a_bad"]),
           f(tot["absorbed"]), f(tot["any_bad"])))

    print("\n" + "=" * W)
    print("B. 判据级失败率 —— 分母 = 有效判据数（None 不计）")
    print("=" * W)
    print("%-15s %13s %13s %13s %13s" % ("component", "core HR", "全部 HR", "交付物 RR", "语义"))
    print("-" * W)
    for c in "CEGLOTV":
        d = D.get(c)
        if not d or not d["runs"]:
            continue
        g = lambda a, b: "%d/%d %3.0f%%" % (d[a], d[b], 100 * d[a] / d[b]) if d[b] else "—"
        print("%-15s %13s %13s %13s %13s" % ("%s %s" % (c, NAME[c]),
              g("core_f", "core_n"), g("hr_f", "hr_n"), g("rr_f", "rr_n"), g("sm_f", "sm_n")))
    print("-" * W)
    g = lambda a, b: "%d/%d %3.0f%%" % (tot[a], tot[b], 100 * tot[a] / tot[b]) if tot[b] else "—"
    print("%-15s %13s %13s %13s %13s" % ("合计",
          g("core_f", "core_n"), g("hr_f", "hr_n"), g("rr_f", "rr_n"), g("sm_f", "sm_n")))

    print("\n" + "=" * W)
    print("C. 判据无结果率 —— ★判据/装置自身的健康度，不是 harness 的账")
    print("=" * W)
    print("%-15s %13s %13s %13s" % ("component", "HR", "交付物 RR", "语义"))
    print("-" * W)
    for c in "CEGLOTV":
        d = D.get(c)
        if not d or not d["runs"]:
            continue
        g = lambda a, b: "%d/%d %3.0f%%" % (d[a], d[b], 100 * d[a] / d[b]) if d[b] else "—"
        print("%-15s %13s %13s %13s" % ("%s %s" % (c, NAME[c]),
              g("hr_none", "hr_all"), g("rr_none", "rr_all"), g("sm_none", "sm_all")))
    print("-" * W)
    g = lambda a, b: "%d/%d %3.0f%%" % (tot[a], tot[b], 100 * tot[a] / tot[b]) if tot[b] else "—"
    print("%-15s %13s %13s %13s" % ("合计",
          g("hr_none", "hr_all"), g("rr_none", "rr_all"), g("sm_none", "sm_all")))
    print(DEFS)


if __name__ == "__main__":
    main()
