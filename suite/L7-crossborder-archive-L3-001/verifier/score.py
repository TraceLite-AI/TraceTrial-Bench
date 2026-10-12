#!/usr/bin/env python
"""L7 verifier: 压缩保真的真实场景判定 —— 早期给的合规批号是否被每条归档记录正确带上。
两层: HR(harness 属性: 压缩是否吃掉早期规格/批号) + RR(结果质量: 覆盖/批号正确/字段正确)。"""
import argparse,json,os,re,glob
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
def _gw_compaction_stats(case_dir):
    """从 case 自己的 gw.log 读 harness 侧压缩证据（trace 里搜关键词会被抓回的网页正文污染）"""
    g = case_dir/"gw.log"
    if not g.is_file(): return {}
    try: t = g.read_text(errors="ignore")
    except Exception: return {}
    return {
        "overflow_detected": len(re.findall(r"context overflow detected", t, re.I)),
        "compaction_ok":     len(re.findall(r"auto-compaction succeeded", t, re.I)),
        "compaction_failed": len(re.findall(r"auto-compaction failed", t, re.I)),
        "blocked_by_guard":  len(re.findall(r"already_compacted_recently", t, re.I)),
        "hard_overflow":     len(re.findall(r"prompt too large", t, re.I)),
    }
T=json.loads((HERE/"truth.json").read_text())
CODE=T["batch_code"]; TRUTH={o["order_id"]:o for b in T["orders"].values() for o in b}
QUAL=set(T["qualifying"])
NB=T["batches"]

art=ws/"output"/"archive.jsonl"
rows=[]
if art.exists():
    for ln in art.read_text(errors="ignore").splitlines():
        ln=ln.strip()
        if not ln: continue
        try: rows.append(json.loads(ln))
        except Exception: pass
# ── trace: 压缩事件 ──
comp=0; sess=""
if a.state:
    fs=[f for f in glob.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if fs:
        sess=sorted(fs,key=os.path.getmtime)[-1]
        for ln in open(sess,errors="ignore"):
            try: j=json.loads(ln)
            except Exception: continue
            if (j.get("type") or "")=="compaction": comp+=1
_killed = a.exit_code in (124,137,143)
if _killed or not rows:
    print(json.dumps({"label":a.label,"verdict":"inconclusive",
      "reason":("run 被外部终止 exit=%d"%a.exit_code if _killed else "归档文件为空/不存在"),
      "note":"不归模型也不归 harness"},ensure_ascii=False,indent=1)); raise SystemExit
# ── 逐单核对 ──
seen={}; badcode=[]; badfield=[]
for r in rows:
    oid=str(r.get("order_id","")).strip()
    if oid not in TRUTH: continue
    seen[oid]=r
    if str(r.get("batch_code","")).strip()!=CODE: badcode.append(oid)
    t=TRUTH[oid]
    for k in ("dest","hs_code","currency"):
        if str(r.get(k,"")).strip()!=str(t[k]): badfield.append((oid,k)); break
# 只该归档 qualifying 集合
hit=set(seen)&QUAL; extra=set(seen)-QUAL
cov=len(hit)/max(1,len(QUAL))
code_ok=1-len(badcode)/max(1,len(seen))
# 批号错误集中在哪一批(定位退化点)
def bat(oid):
    m=re.match(r"ORD-(\d{2})",oid); return int(m.group(1)) if m else 0
bad_by_batch={}
for oid in badcode: bad_by_batch.setdefault(bat(oid),0); bad_by_batch[bat(oid)]+=1
first_bad=min(bad_by_batch) if bad_by_batch else None
batches_done=len({bat(o) for o in seen})

HR={
 "HR1_early_spec_survived": (len(badcode)==0),           # 早期给的批号/规格是否被后续记录正确沿用
 "HR2_all_batches_processed": batches_done>=NB,
 "HR3_run_completed": True,
}
RR={
 "RR1_coverage_complete": len(hit)==len(QUAL),
 "RR6_filter_applied_correctly": len(extra)==0,
 "RR2_format_valid": all(isinstance(r,dict) and r.get("order_id") for r in rows) and len(rows)>0,
 "RR3_batch_code_correct": len(badcode)==0,
 "RR4_fields_correct": len(badfield)==0,
 "RR5_no_phantom_orders": all(str(r.get("order_id","")) in TRUTH for r in rows),
}
_gw = _gw_compaction_stats(HERE.parent)
_harness_broke = (_gw.get("hard_overflow",0) > 0 or _gw.get("blocked_by_guard",0) > 0)
attr=[]
if not HR["HR1_early_spec_survived"]:
    if comp>=1:
        attr.append({"layer":"HR","item":"HR1_early_spec_survived",
          "verdict":"harness-attributable(feature: compaction)",
          "evidence":f"压缩 {comp} 次; 批号错/缺 {len(badcode)} 单, 首个出错批次=第{first_bad}批, 按批分布={bad_by_batch}"})
    else:
        attr.append({"layer":"HR","item":"HR1_early_spec_survived","verdict":"model-side",
          "evidence":f"批号错 {len(badcode)} 单但未见压缩事件 -> 模型自己没沿用早期规格"})
if not RR["RR1_coverage_complete"]:
    attr.append({"layer":"RR","item":"RR1_coverage_complete","verdict":("harness-attributable(compaction: guard 拦二次压缩 + 硬溢出被报成 ok)" if _harness_broke else "model-side(未处理全)"),
      "evidence":f"应归档 {len(QUAL)} 单, 命中 {len(hit)}, 覆盖批次 {batches_done}/{NB}" + (f" | harness 侧: {_gw}" if _harness_broke else "")})
if extra:
    attr.append({"layer":"RR","item":"RR6_filter_applied_correctly","verdict":"model-side(筛选规则未沿用)",
      "evidence":f"归档了 {len(extra)} 单不符合筛选条件(declared_value>=2000 且 DDP), 例: {sorted(extra)[:5]}"})
if not RR["RR4_fields_correct"]:
    attr.append({"layer":"RR","item":"RR4_fields_correct","verdict":"model-side(抄录错误)",
      "evidence":f"字段与明细不符: {badfield[:5]}"})
if comp==0:
    attr.append({"layer":"HR","item":"(压缩未触发)",
      "verdict":("harness-credit(未触发压缩且任务完成)" if HR["HR2_all_batches_processed"] else "fail(未触发但未做完)"),
      "evidence":f"压缩 0 次; 批次完成 {batches_done}/{NB}"})
print(json.dumps({"label":a.label,"harness":a.harness,
 "HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"coverage_of_qualifying":round(cov,4),"batch_code_correct_rate":round(code_ok,4),
   "qualifying_expected":len(QUAL),"qualifying_hit":len(hit),"over_archived":len(extra),
   "batches_processed":batches_done,"batches_expected":NB,
    "records_written":len(seen),
   "compaction_events":comp,"gw_compaction":_gw,"first_bad_batch":first_bad},
 "attribution":attr,
 "details":{"bad_code_by_batch":bad_by_batch,"bad_fields":badfield[:8]}},ensure_ascii=False,indent=1))
