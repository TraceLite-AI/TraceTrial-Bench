#!/usr/bin/env python
"""K2 verifier: 逐份判断准确率。★核心指标 = 锚定对(违规份紧跟雷同合规份)上的准确率。"""
import argparse,glob,json,os,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
T=json.loads((HERE/"truth.json").read_text())
if a.exit_code in (124,137,143):
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":f"外部终止 exit={a.exit_code}"},ensure_ascii=False,indent=1)); raise SystemExit
rp=ws/"output"/"review.json"
if not rp.exists():
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":"review.json 缺失"},ensure_ascii=False,indent=1)); raise SystemExit
try: got=json.loads(rp.read_text(errors="ignore"))
except Exception as e:
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":f"review.json 解析失败: {e}"},ensure_ascii=False,indent=1)); raise SystemExit
# ★按 V-id 递归定位, 不假定嵌套形状
_IDS=set(T)
def _collect(o,out):
    if isinstance(o,dict):
        _i=o.get("id") or o.get("vendor") or o.get("vendor_id")
        if isinstance(_i,str) and _i in _IDS and ("verdict" in o or "rule" in o):
            out.setdefault(_i,o); return out
        for k,v in o.items():
            if k in _IDS and isinstance(v,(dict,str)): out.setdefault(k,v)
            else: _collect(v,out)
    elif isinstance(o,list):
        for v in o: _collect(v,out)
    return out
_flat=_collect(got,{})
shape_as_specified=isinstance(got,dict) and any(k in _IDS for k in got)
got=_flat
# ★先验机制触发: 数子代理调用
spawn=0
if a.state:
    for f in glob.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")):
        if "trajectory" in f: continue
        for l in open(f,errors="ignore"):
            if not l.strip(): continue
            try: r=json.loads(l)
            except Exception: continue
            for it in (r.get("message") or {}).get("content") or []:
                if isinstance(it,dict) and it.get("type")=="toolCall" and it.get("name") in ("sessions_spawn","subagents","task"):
                    spawn+=1
def norm(v):
    if isinstance(v,dict): return (str(v.get("verdict","")).strip().lower(), v.get("rule"))
    return (str(v).strip().lower(), None)
correct=[];wrong=[];missing=[]
trap_ok=[];trap_bad=[]
for vid,t in T.items():
    if vid not in got: missing.append(vid); continue
    vd,rule=norm(got[vid])
    ok = (vd==t["verdict"])
    if ok and t["verdict"]=="fail":
        try: ok = (int(rule)==int(t["rule"]))
        except Exception: ok=False
    (correct if ok else wrong).append(vid)
    if t.get("twin_of"):                       # 锚定对里的违规份
        (trap_ok if ok else trap_bad).append(vid)
n=len(T)
acc=len(correct)/float(n)
trap_n=len(trap_ok)+len(trap_bad)
trap_acc=(len(trap_ok)/float(trap_n)) if trap_n else None
# 假阴/假阳分解
fp=[v for v in wrong if T[v]["verdict"]=="pass"]   # 干净的被判 fail
fn=[v for v in wrong if T[v]["verdict"]=="fail"]   # 违规的被判 pass 或规则填错
RR={"RR1_all_reviewed":len(missing)==0,
    "RR2_items_locatable":len(got)>=n,
    "RR3_no_false_reject":len(fp)==0}
attr=[{"layer":"capability","item":"subagent_usage","verdict":"observation",
       "evidence":f"本臂子代理调用 {spawn} 次; 逐份准确率 {acc:.3f}, 锚定对准确率 "
                  f"{('%.3f'%trap_acc) if trap_acc is not None else 'n/a'}"}]
if spawn==0:
    attr.append({"layer":"capability","item":"subagent_usage","verdict":"inconclusive(capability)",
      "evidence":"本臂一次未使用子代理 -> 若与对照臂无差异, 不能归因于该能力"})
if missing:
    attr.append({"layer":"RR","item":"RR1_all_reviewed","verdict":"model-side(未审全)","evidence":f"漏审 {missing[:8]}"})
print(json.dumps({"label":a.label,"harness":a.harness,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"total":n,"correct":len(correct),"overall_accuracy":round(acc,4),
   "trap_pairs":trap_n,"trap_correct":len(trap_ok),"trap_accuracy":(round(trap_acc,4) if trap_acc is not None else None),
   "false_reject":len(fp),"false_accept_or_wrong_rule":len(fn),"missing":len(missing),"spawn_count":spawn},
 "attribution":attr,"details":{"wrong":wrong[:12],"trap_bad":trap_bad[:12],"missing":missing[:8]}},ensure_ascii=False,indent=1))
