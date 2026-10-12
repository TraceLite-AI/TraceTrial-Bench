#!/usr/bin/env python
"""hb40 适配层: 调用 badcase 官方 score.py, 把官方 verdict 映射成 HB40 的 HR/RR 归因 schema。
   不重新实现判据 —— 官方 checks 就是判据(feedback_official_grader_only)。"""
import argparse, json, os, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
META=json.loads((HERE/"adapter_meta.json").read_text())
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True); a=ap.parse_args()

official=Path(META["official_score_py"])
if not official.exists():
    print(json.dumps({"pass":False,"score":0.0,"attribution":"ineligible - 官方 grader 缺失: %s"%official,
      "metrics":{"harness_rubric":{"core_pass":False},"result_rubric":{},"task_quality":{"trajectory_iterations":0}}},ensure_ascii=False))
    sys.exit(0)
env=dict(os.environ)
r=subprocess.run([sys.executable,str(official),"--workspace",a.workspace],capture_output=True,text=True,env=env)
raw=(r.stdout or "").strip()
try: off=json.loads(raw)
except Exception:
    print(json.dumps({"pass":False,"score":0.0,"attribution":"ineligible - 官方 grader 输出无法解析",
      "metrics":{"harness_rubric":{"core_pass":False},"result_rubric":{},
        "task_quality":{"trajectory_iterations":0},"official_stderr":(r.stderr or "")[-400:]}},ensure_ascii=False))
    sys.exit(0)

checks=(off.get("metrics") or {}).get("checks") or {}
verdict=str(off.get("verdict") or "")
expect=META["expected_verdict"]          # = 官方 grader 的 verdict_bad(缺陷名)
_raw_pass=bool(off.get("pass"))
_sem=META.get("pass_means","harness_correct")
# 语义归一: official_pass 统一表示 "harness 行为正确"
official_pass = (not _raw_pass) if _sem=="defect_reproduced" else _raw_pass

# 装置有效性: 名字像"证据/前置存在"的 check 挂了 -> 这轮根本没跑起来, 不能归因
import re as _re
_dev=[k for k in checks if _re.search(r"evidence|present|preflight|ran|invoked|one_exec",k,_re.I)]
device_valid = all(bool(checks[k]) for k in _dev) if _dev else True
# 探针/装置未触发类 verdict -> 装置无效(不是"复现了缺陷")
if _re.search(r"NOT_FIRED|NO_EVIDENCE|NOT_RUN|SETUP|MISSING_ARTIFACT",verdict,_re.I): device_valid=False
# 反向语义包(pass=复现): 复现失败时必须确认"对照臂真的跑对了", 否则是装置没跑而非 harness 正确
if _sem=="defect_reproduced" and not _raw_pass:
    _m=off.get("metrics") or {}
    if not any(bool(v) for k,v in _m.items() if k!="checks"): device_valid=False
    # 对照臂门: 名字像 control/plain/baseline/A_ 的 check 必须为 True(它证明装置在正常路径上工作)
    _ctrl=[k for k in checks if _re.search(r"^A_|_plain|control|baseline",k,_re.I)]
    if _ctrl and not all(bool(checks[k]) for k in _ctrl): device_valid=False
# trace 为空也算装置没跑
if META.get("driver")!="own_runner":     # 进程级题的 trace 在 evidence/, 不适用此门
    _sf=os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE","")
    if not _sf or not Path(_sf).exists() or not Path(_sf).read_text(errors="ignore").strip(): device_valid=False
# 官方 grader 的 pass/fail 就是权威: fail = 该 badcase 的缺陷在本轮复现。
# 不再用手打的预期名做匹配(名字来源不可靠, 且 9 个包根本不用 emit 体系)。
defect_reproduced = (not official_pass) and device_valid
expected_note = ("(登记预期缺陷名: %s)"%expect) if expect else ""

# HR: 官方 checks 原样搬(它们判的就是 harness 行为) + 派生项
HR={("official_"+k):bool(v) for k,v in checks.items()}
HR["device_valid"]=device_valid
HR["harness_behaved_correctly"]=official_pass
HR["core_pass"]=official_pass
RR={"official_verdict_is_good_verdict":official_pass,
    "no_known_defect_reproduced":not defect_reproduced}

if not device_valid:
    attrib="inconclusive - 装置未跑起来(%s), 不足以归因"%(off.get("failure_reason") or verdict)
elif official_pass:
    attrib="clean - 官方 grader 判 harness 行为正确(verdict=%s)"%verdict
else:
    attrib="harness-attributable(%s) - 官方 grader 判缺陷复现, verdict=%s %s"%(META["leaf"],verdict,expected_note)
out={"pass":official_pass,"score":float(off.get("score") or 0.0),"attribution":attrib,
 "core_HR_ok":official_pass,
 "badcase_origin":{"package":META["package"],"leaf":META["leaf"],"registered_defect_verdict":expect,
   "official_verdict":verdict,"official_pass":official_pass,"defect_reproduced":defect_reproduced,
   "grader":str(official)},
 "metrics":{"harness_rubric":HR,"result_rubric":RR,
   "task_quality":{"trajectory_iterations":int(((off.get("metrics") or {}).get("iterations") or 0))},
   "official_metrics":off.get("metrics") or {}}}
print(json.dumps(out,ensure_ascii=False,indent=2))
