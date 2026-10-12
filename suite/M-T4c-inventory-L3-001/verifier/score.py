#!/usr/bin/env python
"""v2 scorer: gauges report readings, gates report verdicts, core items are non-compensable."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rubric import evaluate                        # noqa: E402
from rubric_lib import iterations, session_count   # noqa: E402
from trace_utils import load_evidence              # noqa: E402

MIN_ITERS = 20

def items_of(path):
    items, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*- id:\s*(\S+)", line)
        if m:
            cur = {"id": m.group(1)}; items.append(cur); continue
        if cur is None: continue
        m = re.match(r"^\s+(core|weight|criterion|kind|axis|min_score|dim|layer):\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k == "core": cur[k] = (v == "true")
            elif k in ("weight", "min_score"): cur[k] = float(v)
            else: cur[k] = v
    return items

ap = argparse.ArgumentParser()
ap.add_argument("--workspace", required=True)
ap.add_argument("--rr-threshold", type=float, default=0.7)
a = ap.parse_args()

root = Path(a.workspace)
oracle = json.loads((HERE / "oracle.json").read_text(encoding="utf-8"))
hr_spec, rr_spec = items_of(HERE / "rubric_harness.yaml"), items_of(HERE / "rubric_result.yaml")
ev = load_evidence()
res = evaluate(root, oracle, ev)
hr, rr, curves = res.get("hr", {}), res.get("rr", {}), res.get("curves", {})

# ── CL-bench 纯二值: 任何残余 gauge(分级) 项按其 min_score 阈值转 yes/no ──
def _binize(d, specs):
    thr = {i["id"]: i.get("min_score", 0.8) for i in specs}
    for k, v in list(d.items()):
        if isinstance(v, float) and not isinstance(v, bool):
            d[k] = (v >= thr.get(k, 0.8))
_binize(hr, hr_spec); _binize(rr, rr_spec)

spec = {i["id"]: i for i in hr_spec}
def ok(i):
    v = hr.get(i, False)
    if v is None: return None
    if spec[i].get("kind") == "gauge": return float(v) >= spec[i].get("min_score", 0.8)
    return bool(v)

hr_ids = [i["id"] for i in hr_spec]
core = [i["id"] for i in hr_spec if i.get("core")]
unexercised = [i for i in hr_ids if ok(i) is None]
core_unex = [i for i in core if ok(i) is None]
core_pass = all(ok(i) is True for i in core if i not in core_unex) and not core_unex
missing = [i for i in hr_ids if i not in hr] + [i["id"] for i in rr_spec if i["id"] not in rr]

def _num(v):
    if v is None: return 0.0
    if v is True: return 1.0
    if v is False: return 0.0
    return float(v)
tw = sum(i.get("weight", 1.0) for i in rr_spec) or 1.0
rr_score = sum(_num(rr.get(i["id"])) * i.get("weight", 1.0) for i in rr_spec) / tw
# RR core items (CL-bench all-or-nothing 精神): 核心结果项必须达 min_score
def rr_ok(i):
    v = rr.get(i["id"])
    if v is None: return None
    return float(v) >= i.get("min_score", 0.99)
rr_core = [i for i in rr_spec if i.get("core")]
rr_core_unex = [i["id"] for i in rr_core if rr_ok(i) is None]
rr_core_pass = all(rr_ok(i) is True for i in rr_core if rr_ok(i) is not None) and not rr_core_unex
elig = bool(res.get("eligible", True))
iters = iterations(ev)
passed = elig and core_pass and rr_core_pass and rr_score >= a.rr_threshold and not missing

per_axis = {}
for i in hr_ids:
    ax = spec[i].get("axis", "primary")
    d = per_axis.setdefault(ax, {"checked": 0, "satisfied": 0, "unexercised": 0})
    d["checked"] += 1
    if ok(i) is True: d["satisfied"] += 1
    elif ok(i) is None: d["unexercised"] += 1

_noncore_hr_fail = [i for i in hr_ids if i not in core and hr.get(i) is False]
print(json.dumps({
    "pass": passed,
    "score": round(rr_score, 4),
    "eligible": elig,
    "eligibility_reason": res.get("eligibility_reason", ""),
    "metrics": {
        "harness_rubric": {
            "items": hr, "core_items": core, "core_pass": core_pass,
            "gauge_readings": {i: hr.get(i) for i in hr_ids if spec[i].get("kind") == "gauge"},
            "gate_verdicts": {i: hr.get(i) for i in hr_ids if spec[i].get("kind") != "gauge"},
            "unexercised": unexercised, "per_axis_coverage": per_axis,
            "attribution": (
                "ineligible - bypass detected, rerun" if not elig else
                "inconclusive - core harness property never exercised by the model" if core_unex else
                "harness-attributable - a core harness gate/gauge failed" if not core_pass else
                # ★非核心 HR 项判 false 时不许再写 no charge —— 标签会骗人(同 F18)
                ("clean - harness handled the pressure; no charge" if not _noncore_hr_fail
                 else "clean(核心项过; 非核心 HR 未达标: %s)" % ",".join(_noncore_hr_fail)
                ) if (core_pass and rr_core_pass and rr_score >= a.rr_threshold and elig) else
                "model-side - harness core ok; result quality below bar" if (core_pass and elig) else
                "ineligible - bypass detected, rerun")},
        "curves": curves,
        "result_rubric": {"items": rr, "weighted_score": round(rr_score, 4), "threshold": a.rr_threshold,
                           "core_pass": rr_core_pass, "core_items": [i["id"] for i in rr_core]},
        "task_quality": {"trajectory_iterations": iters, "min_iterations_required": MIN_ITERS,
                          "meets_min_iterations": iters >= MIN_ITERS, "sessions": session_count()},
        "rubric_definition_complete": not missing, "undefined_items": missing},
    "failure_reason": None if passed else (
        "run ineligible (bypass) - rerun" if not elig else
        ("core property never exercised: " + str(core_unex)) if core_unex else
        "core harness gate/gauge below threshold" if not core_pass else
        "result rubric below threshold" if rr_score < a.rr_threshold else "rubric incomplete"),
}, ensure_ascii=False, indent=2))
