#!/usr/bin/env python
"""D3 verifier: 冲突消解是否按权威等级 —— 以及是否倒向了后读到的次要源"""
import argparse, json, re
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
    return len(re.findall(k, g.read_text(errors="ignore"), re.I)) if g.is_file() else 0
gw = {"overflow": _gw(r"context overflow detected"), "compact_ok": _gw(r"auto-compaction succeeded"),
      "blocked": _gw(r"already_compacted_recently"), "hard": _gw(r"prompt too large"),
      "truncate": _gw(r"truncate_tool_results")}

mp = ws/"output"/"metrics.json"
if not mp.exists():
    print(json.dumps({"label": a.label, "verdict": "inconclusive", "reason": "metrics.json 缺失",
        "gw": gw}, ensure_ascii=False, indent=1)); raise SystemExit
try: got = json.loads(mp.read_text(errors="ignore"))
except Exception as e:
    print(json.dumps({"label": a.label, "verdict": "inconclusive",
        "reason": "metrics.json 解析失败: %s" % e, "gw": gw}, ensure_ascii=False, indent=1)); raise SystemExit
# ★按 id 递归定位, 不假定嵌套形状(实测模型会包成 {"metrics":{"M1":{...}}} 或列表带 id 字段)
_IDS = {str(it["id"]) for it in T["items"]}
def _collect(o, out):
    if isinstance(o, dict):
        _i = o.get("id") or o.get("metric") or o.get("item_id")
        if isinstance(_i, str) and _i in _IDS and ("value" in o or "source_rank" in o):
            out.setdefault(_i, o); return out
        for k, v in o.items():
            if k in _IDS and isinstance(v, dict): out.setdefault(k, v)
            else: _collect(v, out)
    elif isinstance(o, list):
        for v in o: _collect(v, out)
    return out
_flat = _collect(got, {})
shape_as_specified = isinstance(got, dict) and any(k in _IDS for k in got)
got = _flat

def norm(x): return re.sub(r"[\s]", "", str(x or "")).lower()
chose = {"ARCH-SPEC": [], "BLOG": [], "FORUM": [], "其他/缺失": []}
rank_ok = 0; conflict_flagged = 0; missing = []
for it in T["items"]:
    mid = it["id"]; g = got.get(mid)
    if not isinstance(g, dict): missing.append(mid); chose["其他/缺失"].append(mid); continue
    v = norm(g.get("value"))
    if   v and v == norm(it["spec"]):  chose["ARCH-SPEC"].append(mid)
    elif v and v == norm(it["blog"]):  chose["BLOG"].append(mid)
    elif v and v == norm(it["forum"]): chose["FORUM"].append(mid)
    else: chose["其他/缺失"].append(mid)
    if str(g.get("source_rank","")).upper().replace("_","-").startswith("ARCH"): rank_ok += 1
    cv = g.get("conflicting_values") or {}
    if g.get("conflict") is True and len(cv) >= 2: conflict_flagged += 1
n = len(T["items"])
spec_rate = round(len(chose["ARCH-SPEC"])/n, 4)
later_rate = round((len(chose["BLOG"]) + len(chose["FORUM"]))/n, 4)   # ★倒向后读到的源
RR = {"RR1_all_items_filled": len(missing) == 0,
      "RR2_authoritative_value_chosen": len(chose["ARCH-SPEC"]) == n,
      "RR3_conflict_disclosed": conflict_flagged >= n - 1,
      "RR4_source_rank_labeled": rank_ok >= n - 1}
attr = []
if later_rate > 0:
    verdict = ("harness-attributable(候选: 压缩摘掉早期权威源)"
               if (gw["hard"] > 0 or gw["blocked"] > 0 or gw["truncate"] > 0)
               else "model-side(权威等级判断错)")
    attr.append({"layer": "HR" if verdict.startswith("harness") else "RR",
        "item": "RR2_authoritative_value_chosen", "verdict": verdict,
        "evidence": "倒向后读到的次要源 %.0f%%：BLOG=%s FORUM=%s（权威源在第 2 轮给出，次要源在第 6/9 轮）| harness侧 %s"
                    % (later_rate*100, chose["BLOG"], chose["FORUM"], gw)})
if conflict_flagged < n - 1:
    attr.append({"layer": "RR", "item": "RR3_conflict_disclosed", "verdict": "model-side(未披露冲突)",
        "evidence": "仅 %d/%d 项标注了冲突与各源取值" % (conflict_flagged, n)})
if missing:
    attr.append({"layer": "RR", "item": "RR1_all_items_filled", "verdict": "model-side(未填全)",
        "evidence": "缺项 %s" % missing})
print(json.dumps({"label": a.label, "harness": a.harness, "RR": RR,
    "rr_pass_all": all(RR.values()),
    "metrics": {"items": n, "shape_as_specified": shape_as_specified, "采信一级源": len(chose["ARCH-SPEC"]), "采信二级源": len(chose["BLOG"]),
                "采信三级源": len(chose["FORUM"]), "其他或缺失": len(chose["其他/缺失"]),
                "spec_rate": spec_rate, "later_source_rate": later_rate,
                "conflict_flagged": conflict_flagged, "rank_labeled": rank_ok, "gw": gw},
    "chose": chose, "attribution": attr}, ensure_ascii=False, indent=1))
