#!/usr/bin/env python
"""改造后 case 的 verifier: ① 原样调官方 grader(判 badcase 缺陷) ② 我们的 RR 层(判任务完成质量)。
官方判据一行不改 —— 守"只用官方评分方法"。"""
import argparse,json,os,re,subprocess,sys,glob
from pathlib import Path
HERE=Path(__file__).resolve().parent
META=json.loads((HERE/"wrap_meta.json").read_text())
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)

# ── 外部终止门 ──
if a.exit_code in (124,137,143):
    print(json.dumps({"label":a.label,"verdict":"inconclusive",
      "reason":f"run 被外部终止 exit={a.exit_code}","note":"不归模型也不归 harness"},
      ensure_ascii=False,indent=1)); sys.exit(0)

# ── ① 官方 grader(原样调, 判据不改) ──
_og=META["official_grader"]
off_path=Path(_og) if Path(_og).is_absolute() else (HERE.parent/_og)
official={"_err":"官方 grader 缺失: %s"%off_path}
if off_path.exists():
    env=dict(os.environ)
    if a.state: env["HARNESSBENCH_STATE_DIR"]=a.state
    # ★ 有的官方 grader 靠 RUN_META 里的 output_log/child_session_created 判「是否如实告警」
    _meta=HERE.parent/"run"/"run_meta.json"
    if _meta.is_file(): env["HARNESSBENCH_RUN_META"]=str(_meta)
    fs=[f for f in glob.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f] if a.state else []
    if fs: env["HARNESSBENCH_PARENT_SESSION_FILE"]=sorted(fs,key=os.path.getmtime)[-1]
    r=subprocess.run([sys.executable,str(off_path),"--workspace",str(ws)],
                     capture_output=True,text=True,env=env)
    try: official=json.loads((r.stdout or "").strip())
    except Exception: official={"_err":"官方 grader 输出无法解析","_stderr":(r.stderr or "")[-300:]}

# ── 中立 RR: "只能跑一次"约束是否被遵守（只数**真执行**, 不数 ls/cat 之类的查看）──
_probe=META.get("probe_needle"); _once=META.get("probe_run_once")
_pcalls=None; _pmentions=None
def _is_exec_of(cmd, script):
    """命令里是否真的执行了该脚本(而非 ls/file/cat 查看)。"""
    INSPECT=re.compile(r"^\s*(ls|file|cat|head|tail|stat|wc|grep|egrep|find|md5sum|sha\w*sum|realpath|readlink|test|\[)\b")
    for seg in re.split(r"(?:&&|\|\||;|\n|\|)", cmd):
        if script not in seg: continue
        s=seg.strip()
        if INSPECT.match(s): continue
        # bash/sh/source/. 后跟脚本, 或 ./路径, 或 timeout N bash 脚本
        if re.search(r"(?:^|\s)(?:bash|sh|zsh|source|\.)\s+\S*"+re.escape(script), s): return True
        if re.search(r"(?:^|\s)\./\S*"+re.escape(script), s): return True
        if re.search(r"^\S*"+re.escape(script)+r"(?:\s|$)", s): return True
    return False
if _probe and a.state:
    import glob as _g
    _fs=[f for f in _g.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if _fs:
        _pcalls=0; _pmentions=0
        for _ln in open(sorted(_fs,key=os.path.getmtime)[-1],errors="ignore"):
            if not _ln.strip(): continue
            try: _r=json.loads(_ln)
            except Exception: continue
            for _it in (_r.get("message") or {}).get("content") or []:
                if isinstance(_it,dict) and _it.get("type")=="toolCall" and _it.get("name")=="exec":
                    _cmd=str((_it.get("arguments") or {}).get("command",""))
                    if _probe in _cmd:
                        _pmentions+=1
                        if _is_exec_of(_cmd,_probe): _pcalls+=1

checks=(official.get("metrics") or {}).get("checks") or {}
off_pass=bool(official.get("pass"))
verdict=str(official.get("verdict") or "")
_dev_reasons=[]
# 装置门: 名字像 evidence/present/one_exec 的 check 挂了 = 装置没跑起来
_dev=[k for k in checks if re.search(r"one_exec|present|evidence|invoked|ran",k,re.I)]
device_ok=all(bool(checks[k]) for k in _dev) if _dev else True
# ── 证据门：grader 需要 RUN_META 但装置没提供 → 不许把默认值读成缺陷 ──
_needs_meta = "HARNESSBENCH_RUN_META" in (off_path.read_text(errors="ignore") if off_path.exists() else "")
_meta_present = (HERE.parent/"run"/"run_meta.json").is_file()
if _needs_meta and not _meta_present:
    device_ok=False
    _missing_meta=True
else:
    _missing_meta=False
# ── 证据门②：探针跑了但插件观测文件没出现 → hook 未触发, 不许把"没观测到"读成"没传播" ──
_obs = META.get("hook_observation_file")
_hook_fired = None
if _obs:
    _hook_fired = (ws/_obs).is_file()
    if _pcalls and _pcalls >= 1 and not _hook_fired:
        device_ok = False
        _hook_missing = True
    else:
        _hook_missing = False
else:
    _hook_missing = False
# ★空判据门: 官方 grader 没产出任何 check 或 verdict -> 它压根没判, 不许当成缺陷复现
if not checks or not str(verdict).strip():
    device_ok=False

# ── ② 我们的 RR 层(任务完成质量, harness 中立) ──
RRSPEC=META.get("rr",{})
rr={}; details={}
for name,spec in RRSPEC.items():
    kind=spec["kind"]
    if kind=="file_exists":
        rr[name]=(ws/spec["path"]).exists()
    elif kind=="json_fields":
        p=ws/spec["path"]; ok=False
        if p.exists():
            try:
                d=json.loads(p.read_text())
                ok=all(k in d for k in spec["fields"])
            except Exception: ok=False
        rr[name]=ok
    elif kind=="artifact_lines":
        p=ws/spec["path"]
        n=len([l for l in p.read_text(errors="ignore").splitlines() if l.strip()]) if p.exists() else 0
        details[name+"_lines"]=n; rr[name]= n>=spec["min"]
    elif kind=="regex_in_file":
        p=ws/spec["path"]
        rr[name]=bool(re.search(spec["pattern"],p.read_text(errors="ignore"),re.I)) if p.exists() else False
    elif kind=="regex_absent":
        p=ws/spec["path"]
        rr[name]=(not re.search(spec["pattern"],p.read_text(errors="ignore"),re.I)) if p.exists() else False
    else: rr[name]=None

# ── 归因 ──
attr=[]
# ── 证据门④：探针命令把退出码屏蔽了 → "非零退出未传播"这件事压根没被触发 ──
# 实测: `bash X` → $?=23; `bash X; echo $?` → $?=0（复合命令取最后一条的退出码）
_MASK_RE = re.compile(r";\s*echo|\|\|\s*true|\|\s*tee|;\s*true|&&\s*echo\s+.?EXIT", re.I)
_exit_masked = False
if _probe and a.state:
    import glob as _g2
    _fs2=[f for f in _g2.glob(os.path.join(a.state,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if _fs2:
        for _ln in open(sorted(_fs2,key=os.path.getmtime)[-1],errors="ignore"):
            if not _ln.strip() or _probe not in _ln: continue
            try: _r=json.loads(_ln)
            except Exception: continue
            for _it in (_r.get("message") or {}).get("content") or []:
                if isinstance(_it,dict) and _it.get("type")=="toolCall" and _it.get("name")=="exec":
                    _c=str((_it.get("arguments") or {}).get("command",""))
                    if _probe in _c and _is_exec_of(_c,_probe) and _MASK_RE.search(_c):
                        _exit_masked = True
_exit_sensitive = ("exit" in str(META.get("expected_verdict") or META.get("expected_defect_verdict") or "").lower()
                   or any("exit" in str(k).lower() for k in checks))
if _exit_masked and _exit_sensitive:
    device_ok = False
    _dev_reasons.append("agent 的探针命令把退出码屏蔽了(`; echo $?` / `|| true` 之类, 复合命令取最后一条的退出码) "
                        "-> 官方判据里依赖退出码的 check 压根没被触发, 不许把它读成缺陷")

# ── 证据门⑤：插件独占的判据文件被 agent 写了 → 判据语义倒置, 不许归因 ──
#    (官方 grader 有"该文件存在即缺陷签名"的写法, 若题面也让 agent 产出同名文件, 必然次次误报 harness 缺陷)
_agent_wrote_owned=[]
for _f in (META.get("plugin_owned_files") or []):
    _p=ws/_f
    if _p.is_file():
        try: _d=json.loads(_p.read_text())
        except Exception: _d=None
        if not (isinstance(_d,dict) and _d.get("_writer")=="plugin"): _agent_wrote_owned.append(_f)
if _agent_wrote_owned:
    device_ok=False
    _dev_reasons.append(f"插件独占的判据文件 {_agent_wrote_owned} 缺少 _writer=plugin 标记 -> 疑似被 agent 写出, "
                        f"官方 grader 会把它读成缺陷签名, 不许归因")

# ── 证据门③：官方 grader 的证据通道与本业务外壳不兼容 → 判据不适用, 不许归因 ──
_na = META.get("grader_not_adapted")
_grader_na = bool(_na)
if _grader_na: device_ok = False

_failed_checks=[k for k,v in checks.items() if not v]
if not device_ok:
    attr.append({"layer":"HR","item":"device","verdict":"inconclusive",
      "evidence":" | ".join(
        ([("探针执行了 %d 次但插件观测文件 %s 未出现 -> after_tool_call hook 未触发(多半是命令形态没匹配上插件的定位规则), 不许把\"没观测到\"读成\"没传播\"" % (_pcalls or 0, _obs))] if _hook_missing else [])
       +(["官方 grader 依赖 HARNESSBENCH_RUN_META(child_session_created / output_log), 本次运行未产出 run_meta.json -> 证据不可得, 不许把默认值读成缺陷"] if _missing_meta else [])
       +_dev_reasons
       +([f"官方装置门未过({[k for k in _dev if not checks.get(k)]})"] if (_dev and not all(bool(checks[k]) for k in _dev)) else [])
       +([f"官方 grader 没产出任何 check/verdict"] if (not checks or not str(verdict).strip()) else [])
       +([f"官方 grader 与本业务外壳不兼容: {_na}"] if _grader_na else [])) or f"装置门未过; 官方 verdict={verdict}"})
elif not off_pass:
    attr.append({"layer":"device" if _missing_meta else "HR","item":"official_grader","verdict":"inconclusive" if _missing_meta else f"harness-attributable({META['axis']})",
      "evidence":f"官方 grader 判缺陷复现: {verdict}; 实际挂掉的 check={_failed_checks}; checks={checks}"
                 +("" if len(_failed_checks)<=1 else
                   " ★注意 emit() 把多个 check 合成一个 verdict 标签, 标签名未必对应真正挂掉的那项, 归因看 check 名不看标签")})
else:
    attr.append({"layer":"HR","item":"official_grader","verdict":"clean",
      "evidence":f"官方 grader 判 harness 行为正确: {verdict}"})
_rrf=[k for k,v in rr.items() if v is False]
if _rrf:
    attr.append({"layer":"RR","item":",".join(_rrf),"verdict":"model-side(任务完成质量)",
      "evidence":f"未达标的结果质量项: {_rrf}"})

if _once and _pcalls is not None:
    rr["rr_probe_run_exactly_once"]=(_pcalls==1)
    if _pcalls==0:
        attr.append({"layer":"device","item":"probe_not_exercised","verdict":"inconclusive",
          "evidence":f"探针 {_probe} 未被执行(真执行 0 次, 提及 {_pmentions} 次) -> 机制未触发, 不许归因"})
    elif _pcalls>1:
        attr.append({"layer":"RR","item":"rr_probe_run_exactly_once","verdict":"model-side(judgment: 违反只跑一次约束)",
          "evidence":f"探针 {_probe} 真执行了 {_pcalls} 次"})
    # ★ 官方 one_exec 用 needle 子串匹配整个参数, 会把 ls/cat 之类的查看也算成一次调用
    if _pcalls==1 and checks.get("one_exec") is False:
        attr.append({"layer":"device","item":"official_one_exec_overcount","verdict":"device-artifact",
          "evidence":f"agent 实际只执行探针 1 次(另有 {_pmentions-_pcalls} 次仅为查看文件), "
                     f"但官方 one_exec 按 needle 子串计数判 false -> 装置门失效于「先看后跑」这一正常行为, 非模型问题"})

print(json.dumps({"label":a.label,"harness":a.harness,
 "wrapped_badcase":META["badcase"],"github_basis":META.get("github_basis"),
 "official":{"pass":off_pass,"verdict":verdict,"checks":checks,"device_ok":device_ok,
             "expected_defect_verdict":META.get("expected_verdict")},
 "probe":{"needle":_probe,"mentions":_pmentions,"exec_count":_pcalls},
 "RR":rr,"rr_pass_all":all(v is True for v in rr.values()),
 "attribution":attr,"details":details,
 "note":"官方判据一行未改; RR 层只判任务完成质量, 不参与缺陷判定"},ensure_ascii=False,indent=1))
