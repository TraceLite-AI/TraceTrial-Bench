#!/usr/bin/env python3
"""TraceTrial_Bench 归因报告 —— 三种打分器格式统一判读。

HR 三态语义（README 定的）：
  机制触发了 → 判它有没有把该送的送到
  未触发 且 任务完成 → pass（设计避开了压力，记功，不算缺陷）
  未触发 且 任务未完成 → fail
  trace/证据不可得 → N/A，只留 RR
"""
import json, pathlib, sys, collections

root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
import re as _re
_HERR = {"溢出": r"context overflow detected", "guard拦": r"already_compacted_recently",
         "硬溢出": r"prompt too large", "写锁": r"SessionWriteLockTimeoutError",
         "工具重名": r"plugin tool name conflict", "stall": r"stalled session"}
def _harness_err(case_dir):
    """harness 侧错误计数（gw.log）。★clean 只说明 agent 结果对, 不代表 harness 没出错。"""
    gw = case_dir/"gw.log"
    if not gw.is_file(): return {}
    try: t = gw.read_text(errors="ignore")
    except Exception: return {}
    return {k: n for k, p in _HERR.items() if (n := len(_re.findall(p, t, _re.I)))}

CLASSES = ["harness-attributable", "harness-attributable(feature)", "capability-delta",
           "model-side", "clean", "device-artifact", "inconclusive", "clean(harness 有错但被吸收)", "ineligible"]
MEAN = {
 "harness-attributable": "判据坐实的 harness 缺陷",
 "harness-attributable(feature)": "不是 bug，是设计取舍诱发的能力缺口",
 "capability-delta": "双臂对照下的组件增益/损失",
 "model-side": "结果质量问题归模型",
 "clean": "**agent 结果**完成且达标（★不代表 harness 没出错，见 harness_err 列）",
 "device-artifact": "判据自身缺陷造成的假信号，不归模型也不归 harness",
 "inconclusive": "机制未触发/装置门未过/外部终止 → **不许归因**",
 "clean(harness 有错但被吸收)": "★agent 结果达标，但 harness 侧抛过错误（被重取/重试吸收）——不能读成 harness 正常",
 "ineligible": "装置根本没跑起来",
}
buckets = collections.OrderedDict((c, []) for c in CLASSES)

def classify_wrapped(d):
    """W 系列 / 自建 case：靠 attribution（可能是 list[dict]，也可能是一句字符串）"""
    if d.get("verdict") == "inconclusive":
        return "inconclusive", d.get("reason", "装置门")
    att = d.get("attribution") or []
    if isinstance(att, str):
        _t = att.strip()
        for pfx, cls in (("device-artifact", "device-artifact"),
                         ("harness-attributable(feature)", "harness-attributable(feature)"),
                         ("harness-attributable", "harness-attributable"),
                         ("capability-delta", "capability-delta"),
                         ("model-side", "model-side"),
                         ("inconclusive", "inconclusive"),
                         ("clean", "clean")):
            if _t.startswith(pfx): return cls, _t[:170]
        return ("clean", _t[:170]) if d.get("rr_pass_all") is not False else ("model-side", _t[:170])
    att = [a for a in att if isinstance(a, dict)]
    v = [a.get("verdict", "") for a in att]
    def pick(pfx):
        return next(a for a in att if a["verdict"].startswith(pfx))
    for pfx, cls in (("device-artifact", "device-artifact"),
                     ("harness-attributable(feature)", "harness-attributable(feature)"),
                     ("harness-attributable", "harness-attributable"),
                     ("capability-delta", "capability-delta")):
        if any(x.startswith(pfx) for x in v):
            a = pick(pfx); return cls, a["evidence"][:170]
    if any(x == "inconclusive" for x in v):
        return "inconclusive", pick("inconclusive")["evidence"][:170]
    ms = [a for a in att if a.get("verdict", "").startswith("model-side")]
    if ms:
        return "model-side", " ; ".join("%s: %s" % (a["verdict"], a["evidence"][:70]) for a in ms[:2])
    if d.get("rr_pass_all") is True:
        return "clean", "RR 全过，无 harness/模型问题"
    return "inconclusive", "打分器未产出归因项"

def classify_m(d):
    """M 系列（hb40v2 打分器）"""
    m = d.get("metrics") or {}
    tq = m.get("task_quality") or {}
    if d.get("eligible") is False:
        return "ineligible", d.get("eligibility_reason") or "不合格"
    if (tq.get("trajectory_iterations") or 0) == 0:
        return "ineligible", "trajectory_iterations=0（run 没产生轨迹）"
    hr = (m.get("harness_rubric") or {}).get("items") or {}
    rr = (m.get("result_rubric") or {}).get("items") or {}
    core = (m.get("harness_rubric") or {}).get("core_items") or []
    hr_fail = [k for k, v in hr.items() if v is False]
    rr_fail = [k for k, v in rr.items() if v is False]
    rr_known = [v for v in rr.values() if v is not None]
    core_unex = [k for k in core if hr.get(k) is None]
    task_done = bool(rr_known) and all(v is True for v in rr_known)
    # 核心机制没触发
    if core_unex:
        if task_done:
            return "clean", "核心机制未触发但任务完成 → HR 记功（未触发: %s）" % core_unex[:3]
        return "inconclusive", "核心机制未触发且任务未完成: %s" % (d.get("failure_reason") or core_unex[:3])
    # 有的打分器把归因串放在 harness_rubric.attribution 里
    _emb = (m.get("harness_rubric") or {}).get("attribution")
    if isinstance(_emb, str) and _emb.strip():
        _t = _emb.strip()
        for pfx, cls in (("inconclusive", "inconclusive"), ("device-artifact", "device-artifact"),
                         ("harness-attributable(feature)", "harness-attributable(feature)"),
                         ("harness-attributable", "harness-attributable"),
                         ("capability-delta", "capability-delta"),
                         ("model-side", "model-side"), ("clean", "clean")):
            if _t.startswith(pfx): return cls, _t[:170]
    if hr_fail:
        return "harness-attributable", "HR 未达标: %s" % hr_fail[:4]
    if rr_fail:
        return "model-side", "RR 未达标: %s" % rr_fail[:4]
    if task_done:
        return "clean", "HR 全过 + RR 全过"
    return "inconclusive", d.get("failure_reason") or "无判定依据"

VER = {}
_vp = root / "VERIFIED.json"
if _vp.exists():
    VER = {k: v for k, v in json.loads(_vp.read_text()).items() if not k.startswith("_")}

rows = []
overrides = []
for f in sorted(root.glob("*/score.json")):
    cid = f.parent.name
    try:
        d = json.loads(f.read_text())
    except Exception as e:
        buckets["clean(harness 有错但被吸收)", "ineligible"].append((cid, "score.json 读不了: %s" % e, "")); continue
    if "official" in d or "attribution" in d or "rr_pass_all" in d:
        cls, why = classify_wrapped(d)
        o = d.get("official") or {}
        p = d.get("probe") or {}
        extra = ("官方verdict=%s device_ok=%s 探针真执行=%s" % (o.get("verdict") or "-", o.get("device_ok"), p.get("exec_count"))
                 if o else "rr_pass_all=%s HR=%s" % (d.get("rr_pass_all"), json.dumps(d.get("HR") or {}, ensure_ascii=False)[:70]))
    else:
        cls, why = classify_m(d)
        m = d.get("metrics") or {}
        hr = (m.get("harness_rubric") or {}).get("items") or {}
        extra = "pass=%s score=%s 轮次=%s HR未触发=%d/%d" % (
            d.get("pass"), d.get("score"), (m.get("task_quality") or {}).get("trajectory_iterations"),
            sum(1 for v in hr.values() if v is None), len(hr))
    if cid in VER:
        v = VER[cid]
        overrides.append((cid, cls, v["class"], v["reason"], v.get("finding", "")))
        cls, why = v["class"], "【已人工核验·%s】%s" % (v.get("finding", "-"), v["reason"])
    _he = _harness_err(f.parent)
    if _he:
        extra = (extra + "  ⚠harness侧: " + " ".join("%s%d" % (k, v) for k, v in _he.items()))
        if cls == "clean": cls = "clean(harness 有错但被吸收)"
    buckets.setdefault(cls, [])
    buckets[cls].append((cid, why, extra))
    rows.append((cid, cls))

n = sum(len(v) for v in buckets.values())
print("# TraceTrial_Bench 归因报告\n")
print("已出分 **%d** 个 case\n" % n)
print("| 归因类别 | 数量 | 含义 |\n|---|---|---|")
for c in CLASSES:
    if buckets[c]: print("| `%s` | %d | %s |" % (c, len(buckets[c]), MEAN[c]))
print()
if overrides:
    print("## 人工核验覆盖（判据原判 → 核验判）\n")
    print("| case | 判据原判 | 核验判 | 依据 |\n|---|---|---|---|")
    for cid, a, b, r, fd in overrides:
        print("| `%s` | %s | **%s** | %s（详见 FINDINGS.md %s）|" % (cid, a, b, r[:80], fd))
    print()

# ── ② 类对照：压缩轴 A/B 双臂（保持模型不变，只调 harness 的 contextWindow）──
_ab = []
for _a in sorted(root.glob("*/score.ctx1M.json")):
    _cid = _a.parent.name
    _b = _a.parent / "score.ctx64k.json"
    if not _b.exists(): continue
    try:
        A = json.loads(_a.read_text()); B = json.loads(_b.read_text())
    except Exception:
        continue
    def _m(d, k):
        return (d.get("metrics") or {}).get(k)
    def _rr(d):
        r = d.get("RR") or {}
        if r: return "%d/%d" % (sum(1 for v in r.values() if v is True), len(r))
        rr = ((d.get("metrics") or {}).get("result_rubric") or {}).get("items") or {}
        kn = [v for v in rr.values() if v is not None]
        return "%d/%d" % (sum(1 for v in kn if v is True), len(kn)) if kn else "-"
    _ab.append((_cid,
                _m(A, "compaction_events"), _m(B, "compaction_events"),
                _rr(A), _rr(B),
                A.get("rr_pass_all"), B.get("rr_pass_all")))
if _ab:
    print("## ② 压缩轴对照臂（A=1M 窗口 / B=64K 窗口，**同模型同题，只调 harness 上下文预算**）\n")
    print("| case | A 压缩次数 | B 压缩次数 | A RR | B RR | A 全过 | B 全过 |")
    print("|---|---|---|---|---|---|---|")
    for cid, ca, cb, ra, rb, pa, pb in _ab:
        print("| `%s` | %s | %s | %s | %s | %s | %s |" % (cid, ca, cb, ra, rb, pa, pb))
    print()
    _fired = [x for x in _ab if (x[2] or 0) > 0]
    if _fired:
        _hurt = [x for x in _fired if x[5] is True and x[6] is False]
        print("**B 臂真触发压缩的**：%d/%d；其中 **A 过而 B 不过**（压缩带来的能力损失）：%d\n"
              % (len(_fired), len(_ab), len(_hurt)))
        for x in _hurt:
            print("- `%s` —— 1M 下 RR %s 全过，64K 下压缩 %s 次后降到 %s" % (x[0], x[3], x[2], x[4]))
    else:
        print("**⚠ B 臂仍未触发压缩**（`compaction_events` 全为 0）——"
              "OpenClaw 会先裁剪旧工具结果，可能仍撑不到阈值；需再调小窗口或改用对话历史堆积型材料。\n")

for c in CLASSES:
    if not buckets[c]: continue
    print("## %s（%d）\n" % (c, len(buckets[c])))
    for cid, why, ex in buckets[c]:
        print("- **%s** — %s" % (cid, why))
        if ex: print("  - `%s`" % ex)
    print()
