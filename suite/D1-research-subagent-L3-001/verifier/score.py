#!/usr/bin/env python
"""D1 verifier: 逐角度事实准确率 + 逐字引文率 + ★按角度位置分解(后期是否更差)"""
import argparse, glob, json, os, re
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

def _gw(k):
    g = HERE.parent/"gw.log"
    if not g.is_file(): return 0
    return len(re.findall(k, g.read_text(errors="ignore"), re.I))
gwstat = {"overflow": _gw(r"context overflow detected"), "compact_ok": _gw(r"auto-compaction succeeded"),
          "blocked": _gw(r"already_compacted_recently"), "hard": _gw(r"prompt too large"),
          "truncate": _gw(r"truncate_tool_results")}

rp = ws/"output"/"research.json"
if not rp.exists():
    print(json.dumps({"label": a.label, "verdict": "inconclusive", "reason": "research.json 缺失",
        "gw": gwstat}, ensure_ascii=False, indent=1)); raise SystemExit
try: got = json.loads(rp.read_text(errors="ignore"))
except Exception as e:
    print(json.dumps({"label": a.label, "verdict": "inconclusive",
        "reason": "research.json 解析失败: %s" % e, "gw": gwstat}, ensure_ascii=False, indent=1)); raise SystemExit
# ★事实按 fid 递归定位, 不假定顶层结构。
#   实测模型会嵌成 {"angles":{"A1":{"facts":{"A1-1":{...}}}}} —— 内容完全正确。
#   若只解一层就取不到, 25 条全判 0, 再由"引文率跌到 0"推出"截断/压缩致失真" = 造假 harness 归因。
_FIDS = {f["fid"] for ang in T["angles"] for f in ang["facts"]}
def _collect(obj, out):
    if isinstance(obj, dict):
        # 形态②: 列表项自带 id/fid 字段 —— 实测 OFF 臂就是这么写的
        _id = obj.get("id") or obj.get("fid") or obj.get("fact_id")
        if isinstance(_id, str) and _id in _FIDS and ("value" in obj or "quote" in obj):
            out.setdefault(_id, obj); return out
        # 形态①: fid 直接当键
        for k, v in obj.items():
            if k in _FIDS and isinstance(v, dict) and ("value" in v or "quote" in v): out.setdefault(k, v)
            else: _collect(v, out)
    elif isinstance(obj, list):
        for v in obj: _collect(v, out)
    return out
_flat = _collect(got, {})
shape_as_specified = isinstance(got, dict) and any(k in _FIDS for k in got)
got = _flat

# 源文档原文（verifier 侧存档，用于逐字引文核验）
DOCS = {}
for p in (HERE/"docs").glob("*.txt"):
    DOCS[p.stem] = p.read_text(errors="ignore")

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

per_angle = {}
tot_ok = tot_q = tot_url = n = 0
for ai, ang in enumerate(T["angles"], 1):
    src = ang["source"]; doc = DOCS.get(src, "")
    ok = q_ok = u_ok = 0
    for f in ang["facts"]:
        n += 1
        g = got.get(f["fid"])
        if not isinstance(g, dict): continue
        v = str(g.get("value", "")).strip()
        if f["answer"].lower() in v.lower() or v.lower() in f["answer"].lower():
            if v: ok += 1; tot_ok += 1
        qt = str(g.get("quote", "")).strip()
        if len(qt) >= 12 and doc and qt[:200] in doc:
            q_ok += 1; tot_q += 1
        if ang["url"] in str(g.get("source_url", "")):
            u_ok += 1; tot_url += 1
    m = len(ang["facts"])
    per_angle[ang["id"]] = {"位置": ai, "主题": ang["topic"], "源KB": T["doc_sizes"][src]//1024,
        "事实准确": "%d/%d" % (ok, m), "逐字引文": "%d/%d" % (q_ok, m), "URL正确": "%d/%d" % (u_ok, m),
        "_acc": round(ok/m, 3), "_quote": round(q_ok/m, 3)}
acc = round(tot_ok/max(1, n), 4); qrate = round(tot_q/max(1, n), 4)
# ★上下文压力签名：前半角度 vs 后半角度
half = len(T["angles"])//2
early = [v["_quote"] for k, v in per_angle.items() if v["位置"] <= half]
late  = [v["_quote"] for k, v in per_angle.items() if v["位置"] > half]
drift = (round(sum(late)/len(late) - sum(early)/len(early), 3)
         if early and late else None)
RR = {"RR1_all_facts_answered": sum(1 for f in got if got.get(f)) >= n,
      "RR2_facts_locatable": len(got) >= n,
      "RR3_no_fabricated_quote": qrate >= 0.5}
attr = [{"layer": "capability", "item": "subagent_usage", "verdict": "observation",
         "evidence": "子代理调用 %d 次 | 事实准确率 %.3f | 逐字引文率 %.3f | 后期角度引文漂移 %s | harness侧 %s"
                     % (spawn, acc, qrate, drift, gwstat)}]
if spawn == 0:
    attr.append({"layer": "capability", "item": "subagent_usage", "verdict": "inconclusive(capability)",
        "evidence": "本臂一次未用子代理 → 与对照臂的差异不能归因于该能力"})
if gwstat["hard"] > 0 and qrate < 0.5:
    attr.append({"layer": "HR", "item": "quote_fidelity_under_pressure",
        "verdict": "harness-attributable(候选: 截断/压缩致引文失真)",
        "evidence": "硬溢出 %d 次、truncate %d 次，逐字引文率跌到 %.3f（需与对照臂比）"
                    % (gwstat["hard"], gwstat["truncate"], qrate)})
print(json.dumps({"label": a.label, "harness": a.harness, "RR": RR,
    "rr_pass_all": all(RR.values()),
    "metrics": {"facts_total": n, "facts_located": len(got), "shape_as_specified": shape_as_specified, "fact_accuracy": acc, "verbatim_quote_rate": qrate,
                "url_correct_rate": round(tot_url/max(1, n), 4), "spawn_count": spawn,
                "late_vs_early_quote_drift": drift, "gw": gwstat},
    "per_angle": per_angle, "attribution": attr}, ensure_ascii=False, indent=1))
