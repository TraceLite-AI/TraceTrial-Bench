#!/usr/bin/env python
"""WFA-incident 多阶段长程 verifier: 阶段完整性 + 覆盖 + 依赖序 + 长程 + 压缩特性归因."""
import argparse, json, os, re
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True); a=ap.parse_args()
ws=Path(a.workspace); truth=json.loads((HERE/"truth.json").read_text())
sess=os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE","")
rows=[]
if sess and Path(sess).exists():
    for ln in Path(sess).read_text(errors="ignore").splitlines():
        try: rows.append(json.loads(ln))
        except: pass
calls=[]; blob=[]
for r in rows:
    for cand in (r.get("message"),(r.get("data") or {}).get("message"),r):
        if not isinstance(cand,dict): continue
        ct=cand.get("content")
        if isinstance(ct,list):
            for it in ct:
                if isinstance(it,dict) and it.get("type") in ("toolCall","tool_use"):
                    calls.append({"name":str(it.get("name") or it.get("toolName") or ""),"args":it.get("arguments") or it.get("input") or {}})
        if cand.get("role")=="toolResult":
            t=cand.get("content"); blob.append(t if isinstance(t,str) else json.dumps(t,ensure_ascii=False))
blob="\n".join(blob)
def ncalls(n): return sum(1 for c in calls if c["name"]==n)
iters=len(calls)
art=json.loads((ws/"output"/"incidents.json").read_text()) if (ws/"output"/"incidents.json").exists() else {}
base=art.get("items") if isinstance(art.get("items"),dict) else art
report=(ws/"output"/"rootcause_report.md")
def item_ok(aid):
    v=base.get(aid)
    if not isinstance(v,dict): return False
    t=truth[aid]
    return all(str(v.get(k,"")).strip()==str(t[k]).strip() for k in ("root_cause_code","deploy","owner"))
covered=[k for k in truth if item_ok(k)]
filed=re.findall(r"FILED alert=(ALERT-\d+)",blob)
dep_items=[k for k in truth if truth[k]["depends_on"]]
dep_ok=[k for k in dep_items if k in filed and truth[k]["depends_on"] in filed and filed.index(truth[k]["depends_on"])<filed.index(k)]
NT=len(truth)
_ptot=max([int(m) for m in re.findall(r"PAGE \d+/(\d+)",blob)] or [0])+1
HR={
 "manifest_paging_reached_end": ncalls("alert_manifest")>=(max(2,_ptot-1) if _ptot else 2),
 "details_delivered_via_channel": sum(1 for k in truth if truth[k]["root_cause_code"].lower() in blob.lower())>=max(1,int(NT*0.8)),
 "deploy_owner_lookups_delivered": ncalls("deploy_lookup")>=max(1,int(NT*0.6)) and ncalls("owner_lookup")>=min(len(set(t["owner"] for t in truth.values())),max(1,int(NT*0.6))),
 "submit_receipts_delivered": len(filed)>=max(1,int(NT*0.8)),
 "dependency_order_enforced_and_met": len(dep_items)==0 or len(dep_ok)>=max(1,int(len(dep_items)*0.8)),
 "reconcile_phase_ran": ncalls("reconcile")>=1,
 "channel_not_bypassed": not any(re.search(r"index\.js|/plugin/",str(c["args"]),re.I) for c in calls),
}
RR={
 "all_escalated_covered": len(covered)==NT,
 "root_cause_codes_exact": all(str(base.get(k,{}).get("root_cause_code",""))==truth[k]["root_cause_code"] for k in truth) if base else False,
 "deploy_owner_correct": all((k in covered) for k in truth) if base else False,
 "causal_chain_in_report": report.exists() and sum(1 for k in dep_items if k in report.read_text(errors="ignore"))>=max(1,int(len(dep_items)*0.6)),
 "report_covers_all": report.exists() and sum(1 for k in truth if k in report.read_text(errors="ignore"))>=max(1,int(NT*0.8)),
}
_evf=os.environ.get("HARNESSBENCH_HARNESS_EVENTS","")
_ev={}
if _evf and Path(_evf).exists():
    try: _ev=json.loads(Path(_evf).read_text())
    except Exception: _ev={}
_OVF=re.compile(r"compaction (complete|failed)|Context overflow|compactionCount|prompt too large|exhausted provider overflow|livenessState=blocked",re.I)
_ovf=any(bool(v) for v in _ev.values()) or bool(_OVF.search(blob)) or any(_OVF.search(json.dumps(r,ensure_ascii=False)) for r in rows)
early=[k for k in list(truth)[:max(1,NT//3)]]
early_survived=all(str(base.get(k,{}).get("root_cause_code",""))==truth[k]["root_cause_code"] for k in early) if base else False
HR["early_material_survived_longhorizon"]=early_survived
core_HR=["manifest_paging_reached_end","details_delivered_via_channel","submit_receipts_delivered","reconcile_phase_ran","early_material_survived_longhorizon"]
core_ok=all(HR[k] for k in core_HR)
reached_longhorizon = ncalls("alert_manifest")>=(max(4,int(_ptot*0.6)) if _ptot else 4) and len(filed)>=max(4,int(NT*0.6))
bypass = not HR["channel_not_bypassed"]; rr_ok=all(RR.values())
if bypass: attrib="ineligible - bypass detected (读插件源码/绕过工具通道), rerun"
elif core_ok and rr_ok: attrib="clean - 长程压力下 harness 与模型都扛住(对照臂应落此)"
elif not early_survived and _ovf: attrib="harness-attributable(feature) - 滚动压缩(OC 特性)触发, 早期发现的 root_cause_code/依赖链被压成摘要丢失 -> 长程记忆能力缺口(非bug, 特性诱发)"
elif not early_survived and not _ovf: attrib="model-side - 早期材料丢失但压缩未触发, 是模型自己没跟踪住发现, 非 harness"
elif not reached_longhorizon: attrib="inconclusive - 未走到长程阶段且未完成, 特性未被充分触发, 不足以归因"
elif core_ok and not rr_ok: attrib="model-side - harness 过程项全过, 结果质量未达标(覆盖/因果链/报告)"
else: attrib="harness-attributable - 某个 core 过程项未成立(见 harness_rubric)"
phases=sum([HR["manifest_paging_reached_end"],HR["details_delivered_via_channel"],HR["submit_receipts_delivered"],HR["reconcile_phase_ran"]])
_score=round(sum(1 for v in RR.values() if v)/max(1,len(RR)),3)
HRx=dict(HR); HRx["core_pass"]=core_ok; HRx["attribution"]=attrib
out={"pass":core_ok and rr_ok,"score":_score,"attribution":attrib,"core_HR_ok":core_ok,
 "harness_feature":{"targets":"rolling_compaction_longhorizon_memory","is_feature_not_bug":True,
   "compaction_or_overflow_fired":_ovf,"early_material_survived":early_survived,"reached_longhorizon":reached_longhorizon},
 "metrics":{"harness_rubric":HRx,"result_rubric":RR,
   "task_quality":{"trajectory_iterations":iters},
   "workflow":{"phases_exercised":phases,"manifest_pages":ncalls("alert_manifest"),"detail_calls":ncalls("alert_detail"),
     "deploy_calls":ncalls("deploy_lookup"),"owner_calls":ncalls("owner_lookup"),"submits":len(filed),"reconcile":ncalls("reconcile"),
     "escalated_covered":len(covered),"dep_items":len(dep_items),"dep_order_ok":len(dep_ok),
     "total_tool_calls":len(calls),"trajectory_iterations":iters}}}
print(json.dumps(out,ensure_ascii=False,indent=2))
