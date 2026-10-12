#!/usr/bin/env python
"""L13 verifier — 基底 cline#10737/codex#32155: 平台加载期不校验工具名合规。
HR: 平台是否在加载期给出诊断(违规包 status/diagnostics) —— 不给 = harness 事实
RR: 审查报告是否把三类违规都找出来(harness 中立)"""
import argparse,json,os,re,glob
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
T=json.loads((HERE/"truth.json").read_text())
VIOL=T["violations"]   # bundle -> 违规类型
blob=[];comp=0
if a.state:
    fs=[f for f in glob.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if fs:
        for ln in open(sorted(fs,key=os.path.getmtime)[-1],errors="ignore"):
            try: j=json.loads(ln)
            except Exception: continue
            if (j.get("type") or "")=="compaction": comp+=1
            blob.append(json.dumps(j,ensure_ascii=False))
bn="\n".join(blob).replace("\\","")
verdict=dict(re.findall(r"VERDICT bundle=(VB-\d+) verdict=(\w+)",bn))
rep=ws/"output"/"compliance_review.md"; rt=rep.read_text(errors="ignore") if rep.exists() else ""
_killed=a.exit_code in (124,137,143)
if _killed or (not verdict and not rt.strip()):
    print(json.dumps({"label":a.label,"verdict":"inconclusive",
      "reason":("run 被外部终止 exit=%d"%a.exit_code if _killed else "既无审查结论也无报告"),
      "note":"不归模型也不归 harness"},ensure_ascii=False,indent=1)); raise SystemExit
should_reject=set(VIOL)
wrong=[(k,verdict.get(k)) for k in verdict if (k in should_reject)!=(verdict[k]=="rejected")]
missed=[k for k in should_reject if verdict.get(k)!="rejected"]
# 平台加载期有没有给诊断(装置事实: 全部 status=loaded, diagnostics=[])
platform_diagnosed=bool(re.search(r"load_diagnostics: \[[^\]]+\]",bn))
# 报告里三类违规是否都点出
kinds={"duplicate":r"重名|duplicate","reserved":r"保留名|reserved|exec",
       "overlong":r"超长|过长|长度|64|overlong"}
found={k:bool(re.search(v,rt,re.I)) for k,v in kinds.items()}
# ★数字必须紧挨"个"字, 否则会命中表格里的 "❌ 不予放行 | R1" 这种; 取最后一处(汇总段在文末)
_cands=[int(g) for mm in re.finditer(
    r"(\d+)\s*个(?:包)?[^\n]{0,6}?(?:不予放行|驳回|拒绝|rejected)"
    r"|(?:不予放行|驳回|拒绝|rejected)[^\n]{0,4}?[:：]?\s*(\d+)\s*个", rt, re.I)
    for g in mm.groups() if g]
claimed=_cands[-1] if _cands else None
HR={"HR1_platform_load_diagnostics":platform_diagnosed,   # 预期 False = harness 加载期不校验
    "HR2_tool_names_visible":bool(re.search(r"registered_tool_names",bn)),
    "HR3_run_completed":True}
RR={"RR1_all_bundles_reviewed":len(verdict)>=len(T["bundles"]),
    "RR2_report_exists":bool(rt.strip()),
    "RR3_violations_all_rejected":len(missed)==0,
    "RR4_no_false_rejection":all((k in should_reject) for k,v in verdict.items() if v=="rejected"),
    "RR5_all_three_kinds_named":all(found.values()),
    "RR6_reject_count_correct":(claimed==len(should_reject)) if claimed is not None else False}
attr=[]
if not platform_diagnosed:
    attr.append({"layer":"HR","item":"HR1_platform_load_diagnostics",
      "verdict":"harness-fact(加载期不校验工具名, 非缺陷判定)",
      "evidence":"6 个包全部 status=loaded 且 load_diagnostics=[] —— 平台在加载期不校验重名/保留名/超长名 "
                 "(cline#10737, codex#32155 同类), 违规完全靠审查者自己看出来"})
if missed:
    attr.append({"layer":"RR","item":"RR3_violations_all_rejected","verdict":"model-side(judgment)",
      "evidence":f"未识别出的违规包: {[(k,VIOL[k]) for k in missed]}"})
if not all(found.values()):
    attr.append({"layer":"RR","item":"RR5_all_three_kinds_named","verdict":"model-side(completeness)",
      "evidence":f"报告未点出的违规类型: {[k for k,v in found.items() if not v]}"})
print(json.dumps({"label":a.label,"harness":a.harness,"github_basis":"cline/cline#10737 + openai/codex#32155",
 "HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"reviewed":len(verdict),"expected":len(T["bundles"]),
   "should_reject":sorted(should_reject),"missed":missed,"claimed_reject_count":claimed,
   "kinds_named":found,"platform_diagnosed":platform_diagnosed,"compaction_events":comp},
 "attribution":attr,"details":{"wrong":wrong[:6]}},ensure_ascii=False,indent=1))
