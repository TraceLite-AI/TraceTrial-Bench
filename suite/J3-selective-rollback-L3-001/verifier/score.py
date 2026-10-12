#!/usr/bin/env python
"""J3 verifier: 判**配置文件实际内容** —— 坏的回滚了, 好的保留了。"""
import argparse,json,os,re,subprocess,glob
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
T=json.loads((HERE/"truth.json").read_text())
BAD=T["bad_changes"]; GOOD=T["good_changes"]
if a.exit_code in (124,137,143):
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":f"外部终止 exit={a.exit_code}"},ensure_ascii=False,indent=1)); raise SystemExit
def load(p):
    f=ws/p
    try: return json.loads(f.read_text(errors="ignore")) if f.exists() else {}
    except Exception: return {"_parse_error":True}
bad_fixed=[];bad_left=[];good_kept=[];good_reverted=[]
for c in BAD:
    d=load(c["file"])
    (bad_left if str(d.get(c["key"],""))=="BROKEN_VALUE" else bad_fixed).append(c["id"])
for c in GOOD:
    d=load(c["file"])
    (good_reverted if d.get("_change")=="baseline" else good_kept).append(c["id"])
chk=ws/"tools"/"check_rollback.sh"; rc=None
if chk.exists():
    r=subprocess.run(["bash",str(chk)],capture_output=True,text=True,cwd=str(ws)); rc=r.returncode
rep=ws/"output"/"rollback_report.md"; rt=rep.read_text(errors="ignore") if rep.exists() else ""
explained=bool(re.search(r"(保留|未回滚|不回滚|正常生效)",rt))
HR={"HR1_writes_persisted":len(bad_fixed)>0,"HR2_run_completed":True}
RR={"RR1_all_bad_rolled_back":len(bad_left)==0,
    "RR2_good_changes_preserved":len(good_reverted)==0,
    "RR3_checker_passes":rc==0,
    "RR4_report_exists":bool(rt.strip()),
    "RR5_retention_explained":explained}
attr=[]
if good_reverted:
    attr.append({"layer":"RR","item":"RR2_good_changes_preserved","verdict":"model-side(judgment: 回滚过头)",
      "evidence":f"把同批次正常生效的改动一并退回 baseline: {good_reverted} —— 事故记录明确说这些没问题"})
if bad_left:
    attr.append({"layer":"RR","item":"RR1_all_bad_rolled_back","verdict":"model-side(未回滚全)",
      "evidence":f"仍是坏值: {bad_left}"})
if not explained:
    attr.append({"layer":"RR","item":"RR5_retention_explained","verdict":"model-side(未披露)",
      "evidence":"报告未说明哪些改动被保留及依据"})
print(json.dumps({"label":a.label,"harness":a.harness,
 "HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"bad_total":len(BAD),"bad_fixed":len(bad_fixed),"bad_left":len(bad_left),
   "good_total":len(GOOD),"good_kept":len(good_kept),"good_wrongly_reverted":len(good_reverted),
   "checker_rc":rc,"retention_explained":explained},
 "attribution":attr,"details":{"bad_left":bad_left,"good_wrongly_reverted":good_reverted}},ensure_ascii=False,indent=1))
