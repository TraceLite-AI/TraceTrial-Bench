#!/usr/bin/env python
"""CAP verifier: 能力增量测量 —— 只判答案准确率(客观), 不判机制。"""
import argparse, json, os, re
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True); a=ap.parse_args()
ws=Path(a.workspace); T=json.loads((HERE/"truth.json").read_text())
truth=T["conflicts"]
sess=os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE","")
rows=[]
if sess and Path(sess).exists():
    for ln in Path(sess).read_text(errors="ignore").splitlines():
        try: rows.append(json.loads(ln))
        except: pass
calls=[]
for r in rows:
    for cand in (r.get("message"),(r.get("data") or {}).get("message"),r):
        if not isinstance(cand,dict): continue
        ct=cand.get("content")
        if isinstance(ct,list):
            for it in ct:
                if isinstance(it,dict) and it.get("type") in ("toolCall","tool_use"):
                    calls.append(str(it.get("name") or it.get("toolName") or ""))
def n(x): return sum(1 for c in calls if c==x)
art=(ws/"output"/"audit.json")
got=[]
if art.exists():
    try:
        d=json.loads(art.read_text())
        # ★递归找带 field 的冲突项, 不假定它一定在顶层 conflicts 键下
        _FIELDS={str(c.get("field","")).strip() for c in truth}
        def _collect(o,out):
            if isinstance(o,dict):
                if str(o.get("field","")).strip() in _FIELDS and o.get("field"): out.append(o); return out
                for v in o.values(): _collect(v,out)
            elif isinstance(o,list):
                for v in o: _collect(v,out)
            return out
        got=_collect(d,[])
        if not got: got=d.get("conflicts") if isinstance(d,dict) else (d if isinstance(d,list) else [])
    except Exception: got=[]
def key(c):
    try: return (str(c.get("field","")).strip(), tuple(sorted(str(x).strip() for x in (c.get("docs") or []))))
    except Exception: return ("","")
tk={key(c):c for c in truth}
gk={}
for c in got:
    if isinstance(c,dict): gk[key(c)]=c
tp=[k for k in gk if k in tk]
fp=[k for k in gk if k not in tk]
fn=[k for k in tk if k not in gk]
# 裁定正确性(只在正确识别的冲突对上算)
ruling_ok=[k for k in tp if str(gk[k].get("authoritative","")).strip()==tk[k]["authoritative"]
                        and str(gk[k].get("value","")).strip()==str(tk[k]["value"]).strip()]
P=len(tp)/max(1,len(gk)); R=len(tp)/max(1,len(tk))
F1=(2*P*R/(P+R)) if (P+R)>0 else 0.0
ruling_acc=len(ruling_ok)/max(1,len(tp))
# 综合准确率: 既要找全找对, 又要裁定对
overall=round(F1*ruling_acc,4)
# ★能力参与度门: ON 臂若一次都没用该能力, 本轮测不到增益 -> inconclusive(不是"没用")
_armoff=bool(os.environ.get("CAP_ARM_OFF"))
used=n("sessions_spawn")+n("subagents")
verdict=("capability-delta 题: 用准确率对比开/关臂"
         if (_armoff or used>0) else
         "inconclusive(capability) - ON 臂一次未使用子代理(spawn=0), 该能力未被触发, 本轮测不到增益")
out={"pass":overall>=0.999,"score":overall,"capability_exercised":(used>0),"capability_note":verdict,
 "capability_metrics":{
   "conflict_precision":round(P,4),"conflict_recall":round(R,4),"conflict_f1":round(F1,4),
   "ruling_accuracy":round(ruling_acc,4),"overall_accuracy":overall,
   "true_conflicts":len(tk),"reported":len(gk),"tp":len(tp),"fp":len(fp),"fn":len(fn),
   "missed":[list(k) for k in fn][:8],"spurious":[list(k) for k in fp][:8]},
 "effort":{"spawn_calls":n("sessions_spawn"),"subagents_calls":n("subagents"),
   "spec_read":n("spec_read"),"spec_list":n("spec_list"),"total_tool_calls":len(calls)},
 "metrics":{"harness_rubric":{"core_pass":True,"attribution":verdict},
   "result_rubric":{"overall_accuracy":overall},"task_quality":{"trajectory_iterations":len(calls)}}}
print(json.dumps(out,ensure_ascii=False,indent=2))
