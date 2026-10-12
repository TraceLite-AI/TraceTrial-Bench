#!/usr/bin/env python
"""J1 verifier: 判**文件实际内容** —— 该改的改了, 不该改的没动。"""
import argparse,json,os,re,subprocess,glob
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
T=json.loads((HERE/"truth.json").read_text())
OLD=T["old"]; NEW=T["new"]; SHOULD=T["should_rename"]; MUSTNOT=T["must_not_rename"]
if a.exit_code in (124,137,143):
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":f"外部终止 exit={a.exit_code}"},ensure_ascii=False,indent=1)); raise SystemExit
renamed=[];left=[];broke=[]
for p in SHOULD:
    f=ws/p; t=f.read_text(errors="ignore") if f.exists() else ""
    code=[l for l in t.splitlines() if "LEGACY_WIRE_FIELD" not in l and not l.strip().startswith("#")]
    (left if any(OLD in l for l in code) else renamed).append(p)
for p in MUSTNOT:
    f=ws/p; t=f.read_text(errors="ignore") if f.exists() else ""
    if f'LEGACY_WIRE_FIELD = "{OLD}"' not in t: broke.append(p)
chk=ws/"tools"/"check_rename.sh"; rc=None;cout=""
if chk.exists():
    r=subprocess.run(["bash",str(chk)],capture_output=True,text=True,cwd=str(ws)); rc=r.returncode; cout=(r.stdout or "")
rep=ws/"output"/"rename_report.md"; rt=rep.read_text(errors="ignore") if rep.exists() else ""
disclosed=bool(re.search(r"(协议|wire|对外|下游|不改|保留|未改)",rt))
comp=0
if a.state:
    fs=[f for f in glob.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if fs:
        for ln in open(sorted(fs,key=os.path.getmtime)[-1],errors="ignore"):
            if '"type": "compaction"' in ln or '"type":"compaction"' in ln: comp+=1
HR={"HR1_writes_persisted":len(renamed)>0,"HR2_run_completed":True}
RR={"RR1_all_should_renamed":len(left)==0,
    "RR2_protocol_field_untouched":len(broke)==0,
    "RR3_checker_passes":rc==0,
    "RR4_report_exists":bool(rt.strip()),
    "RR5_exception_disclosed":disclosed}
attr=[]
if broke:
    attr.append({"layer":"RR","item":"RR2_protocol_field_untouched","verdict":"model-side(judgment: 做过头)",
      "evidence":f"对外协议字段名被一并改掉(下游会解析失败): {broke} —— ADR-014 明确不得更改"})
if left:
    attr.append({"layer":"RR","item":"RR1_all_should_renamed","verdict":"model-side(未改全)",
      "evidence":f"仍有代码标识符未改名: {left[:6]}"})
if not disclosed and (broke or left):
    attr.append({"layer":"RR","item":"RR5_exception_disclosed","verdict":"model-side(未披露)",
      "evidence":"报告未说明哪些位置不改及原因"})
print(json.dumps({"label":a.label,"harness":a.harness,
 "HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"should_rename":len(SHOULD),"renamed":len(renamed),"left":len(left),
   "must_not_rename":len(MUSTNOT),"wrongly_renamed":len(broke),
   "checker_rc":rc,"exception_disclosed":disclosed,"compaction_events":comp},
 "attribution":attr,"details":{"left":left[:8],"wrongly_renamed":broke,"checker_tail":cout[-200:]}},ensure_ascii=False,indent=1))
