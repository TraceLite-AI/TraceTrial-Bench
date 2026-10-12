#!/usr/bin/env python3
"""按 RUBRIC_SPEC.md 实现: 第0步能力探测 -> HR(三态, 条件适用) + RR(harness 中立) -> 归因。"""
import argparse,json,re,subprocess,sys,os,glob
from pathlib import Path
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
import trace_adapter as ta

ap=argparse.ArgumentParser()
ap.add_argument("--workspace",required=True); ap.add_argument("--label",default="")
ap.add_argument("--harness",choices=["openclaw","claudecode"],required=True)
ap.add_argument("--state",default=""); ap.add_argument("--expect",type=int,default=24)
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--wall",type=int,default=0)
a=ap.parse_args(); ws=Path(a.workspace)
PROJ=["Cline","Continue","Aider","Goose","OpenHands","Roo Code"]
FIELD=["latest_version","license","mcp_support","primary_language"]
NA="N/A"

# ══ 第0步: harness 能力探测 ══
def cc_transcript():
    c=sorted(glob.glob(os.path.expanduser("~/.claude/projects/*%s*/*.jsonl"%os.path.basename(str(ws)))),key=os.path.getmtime)
    return open(c[-1],errors="ignore").read() if c else ""
raw_tr = cc_transcript() if a.harness=="claudecode" else ""
ev = ta.extract_openclaw(a.state) if a.harness=="openclaw" else ta.extract_claudecode(str(ws))
# ★trace 不可得时必须判 unknown, 不许静默默认(否则 CC 会被误探成 raw)
_trace_ok = bool(ev) or bool(raw_tr)
if not _trace_ok:
    probe={"fetch_mode":"unknown","context_policy":"unknown","subagent":"unknown",
           "note":"未找到 trace(state/transcript), 无法探测 harness 模式 -> HR 层不作判定"}
else:
    probe={
     "fetch_mode": ("summarizing" if ('"name":"WebFetch"' in raw_tr and '"prompt"' in raw_tr)
                    else ("raw" if a.harness=="openclaw" else "raw")),
     "context_policy": ("compacting" if any(e["kind"]=="compaction" for e in ev) else "compacting_untriggered"),
     "subagent": "available",
    }
fetch_seq=[e["url"] for e in ev if e["kind"]=="fetch"]
comp=[i for i,e in enumerate(ev) if e["kind"]=="compaction"]
n_sub=sum(1 for e in ev if e["kind"]=="subagent")
first=comp[0] if comp else None
before={e["url"] for e in ev[:first] if e["kind"]=="fetch"} if first is not None else set()
after={e["url"] for e in ev[first:] if e["kind"]=="fetch"} if first is not None else set()
refetch=before&after
empty_sum=any("no prior history" in str(ev[i].get("summary","")).lower() for i in comp)
pending=[]
if a.harness=="claudecode" and raw_tr:
    u={};r=set()
    for ln in raw_tr.splitlines():
        try: j=json.loads(ln)
        except Exception: continue
        m=j.get("message") or {}; ct=m.get("content") if isinstance(m,dict) else None
        if isinstance(ct,list):
            for it in ct:
                if not isinstance(it,dict): continue
                if it.get("type")=="tool_use": u[it.get("id")]=(it.get("name"),j.get("timestamp",""))
                if it.get("type")=="tool_result": r.add(it.get("tool_use_id"))
    pending=[(i,v) for i,v in u.items() if i not in r]

# ══ 读产出 ══
art=ws/"output"/"research.json"; rawtxt=art.read_text(errors="ignore") if art.exists() else ""
perr=None; rows=[]
if rawtxt.strip():
    try:
        d=json.loads(rawtxt); rows=d.get("findings") if isinstance(d,dict) else (d if isinstance(d,list) else [])
    except Exception as e: perr=str(e)[:120]

# ══ HR6 先判: run 没自然结束 -> inconclusive, 停 ══
killed=a.exit_code in (124,137,143) or (a.exit_code!=0 and not rawtxt.strip())
if killed or not rawtxt.strip():
    print(json.dumps({"label":a.label,"harness":a.harness,"probe":probe,"verdict":"inconclusive",
      "reason":("run 被外部终止(exit=%s)"%a.exit_code if killed else "产出物未写出")+
               (f"; 悬挂工具调用 {[(n,t[:19]) for _,(n,t) in pending][:2]}" if pending else ""),
      "note":"HR6_run_completed=fail -> 不对模型也不对 harness 特性归因"},ensure_ascii=False,indent=1)); sys.exit(0)

# ══ 核实每个数据点 ══
cache={}
def norm(s): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",str(s))).strip().lower()
def nows(s): return re.sub(r"\s+","",s)
def page(u):
    if u in cache: return cache[u]
    try:
        r=subprocess.run(["curl","-sL","--max-time","25","-A","Mozilla/5.0",u],capture_output=True,text=True)
        cache[u]=norm(r.stdout)
    except Exception: cache[u]=""
    return cache[u]
BOOL={"yes","no","true","false","是","否"}
AUTH={"license":(r"license|package\.json|api\.github|pyproject|cargo\.toml",),
      "latest_version":(r"releases|tags|api\.github|changelog|pypi|npmjs",),
      "primary_language":(r"api\.github|github\.com/[^/]+/[^/]+/?$|languages",),
      "mcp_support":(r".",)}
ck=[]
for r0 in rows:
    if not isinstance(r0,dict): continue
    p,f=str(r0.get("project","")),str(r0.get("field",""))
    v,q,u=str(r0.get("value","")),str(r0.get("quote","")),str(r0.get("source_url",""))
    pg=page(u) if u.startswith("http") else ""
    qn,vn=norm(q),norm(v)
    qok=bool(qn) and bool(pg) and (qn in pg or nows(qn) in nows(pg))
    if not qok and qn and pg:
        w=qn.split()
        if len(w)>=6: qok=any(" ".join(w[i:i+12]) in pg for i in range(max(1,len(w)-11)))
    von=bool(vn) and bool(pg) and (vn in pg or nows(vn) in nows(pg))
    if vn in BOOL: von = bool(pg) and ("mcp" in pg or "model context protocol" in pg) if vn in ("yes","true","是") else von
    auth=bool(re.search(AUTH.get(f,(r".",))[0],u,re.I))
    ck.append({"project":p,"field":f,"value":v,"url":u,"page_ok":bool(pg),
               "quote_verbatim":qok,"value_on_page":von,"source_authoritative":auth})
n=len(ck)
have={(c["project"].lower(),c["field"]) for c in ck}
missing=[(p,f) for p in PROJ for f in FIELD if (p.lower(),f) not in have]
def slug(p): return p.lower().replace(" ","")[:6]
lost=[(p,f) for (p,f) in missing if any(slug(p) in x.lower() for x in before)]
def plaus(f,v):
    v=v.strip()
    if f=="latest_version": return bool(re.match(r"^v?\d+\.\d+",v))
    if f=="license": return bool(re.match(r"^(apache|mit|gpl|agpl|bsd|mpl|isc|unlicense)",v,re.I))
    if f=="mcp_support": return v.lower() in BOOL
    if f=="primary_language": return v.lower() in {"typescript","javascript","python","rust","go","java","c++","kotlin","swift"}
    return True
implaus=[c for c in ck if not plaus(c["field"],c["value"])]
susp=[c for c in ck if c["field"]=="latest_version" and re.search(r"cloud|nightly|beta|rc|-vscode",c["value"],re.I)]
seen={};contra=[]
for c in ck:
    k=(c["project"].lower(),c["field"])
    if k in seen and seen[k]!=c["value"]: contra.append({"key":list(k),"values":[seen[k],c["value"]]})
    seen[k]=c["value"]

# ══ 第1层 HR(三态, 条件适用) ══
comp_fired=len(comp)>=1
_probe_unknown = probe.get("fetch_mode")=="unknown"
# ★"没触发"的判定(用户 2026-07-27 定): 没触发 + run 完成 + 覆盖达标 = pass(设计避开了压力, 记功);
#   没触发但 run 失败/覆盖不达标 = fail(不是设计好, 是没走到那一步)
_reached = (len(missing)==0)          # 覆盖达标 = 真的把任务做到了
_untriggered_pass = _reached
HR={}
HR["HR1_fetch_fidelity"] = (
    # summarizing: 逐字引文不可得, 但若"值都能在所引页面核实到"则视为保真达标(换了实现方式仍达到目的)
    (all(c["value_on_page"] for c in ck if c["page_ok"]) if n else False)
    if probe["fetch_mode"]=="summarizing"
    else (all(c["quote_verbatim"] for c in ck if c["page_ok"]) if n else False))
HR["HR2_context_preserved"] = (len(lost)==0) if comp_fired else _untriggered_pass
HR["HR3_no_rework"] = (len(refetch) <= max(2,int(0.1*max(1,len(set(fetch_seq)))))) if comp_fired else _untriggered_pass
HR["HR4_summary_nonempty"] = (not empty_sum) if comp_fired else _untriggered_pass
HR["HR5_tool_result_intact"] = perr is None
HR["HR6_run_completed"] = True
if _probe_unknown:
    for _k in ("HR1_fetch_fidelity","HR2_context_preserved","HR3_no_rework","HR4_summary_nonempty"):
        HR[_k]=NA
HR_measure={"HR7_fetches_per_datapoint": round(len(fetch_seq)/max(1,n),2),
            "fetches":len(fetch_seq),"unique":len(set(fetch_seq)),"refetched":len(refetch),
            "compaction_events":len(comp),"subagent_calls":n_sub}

# ══ 第2层 RR(harness 中立, 全二值) ══
RR={
 "RR1_coverage":        len(missing)==0,
 "RR2_format_valid":    perr is None and all(all(k in (r0 or {}) for k in ("project","field","value","source_url","quote")) for r0 in rows if isinstance(r0,dict)),
 "RR3_value_grounded":  all(c["value_on_page"] for c in ck if c["page_ok"]) if n else False,
 "RR4_source_authoritative": all(c["source_authoritative"] for c in ck) if n else False,
 "RR5_values_plausible": len(implaus)==0 and n>0,
 "RR6_release_line":    len(susp)==0,
 "RR7_consistent":      len(contra)==0,
 "RR8_uncertainty_declared": True if len(missing)==0 else bool(re.search(r"note|未确认|无法确认|unknown|not found",rawtxt,re.I)),
}
rr_pass=all(RR.values())
rr_failed=[k for k,v in RR.items() if not v]
# 不再输出"过了几项/总项数"这种假标量(等权重 + 丢幅度, 会造出两家同分的假等价)。
# 要幅度就看下面这几个真实比例。
auth_rate=(sum(1 for c in ck if c["source_authoritative"])/max(1,n)) if n else 0.0
cov=min(1.0,n/max(1,a.expect))
grounded=(sum(1 for c in ck if c["value_on_page"])/max(1,n)) if n else 0.0
verb=(sum(1 for c in ck if c["quote_verbatim"])/max(1,n)) if n else 0.0

# ══ 归因 ══
attr=[]
if _probe_unknown:
    attr.append({"layer":"HR","item":"(能力探测失败)","verdict":NA,
      "evidence":"未找到 trace -> 无法判定 harness 属性层; 只有 RR(结果质量)有效"})
if HR["HR2_context_preserved"] is False:
    attr.append({"layer":"HR","item":"HR2_context_preserved","verdict":"harness-attributable(feature: compaction)",
      "evidence":f"压缩 {len(comp)} 次; 压缩前已抓来源、最终缺失: {lost}"})
if HR["HR3_no_rework"] is False:
    attr.append({"layer":"HR","item":"HR3_no_rework","verdict":"harness-attributable(feature: compaction)",
      "evidence":f"压缩后重抓已抓 URL {len(refetch)}/{len(set(fetch_seq))}"})
if HR["HR4_summary_nonempty"] is False:
    attr.append({"layer":"HR","item":"HR4_summary_nonempty","verdict":"harness-attributable(feature: compaction)",
      "evidence":"压缩摘要为 'No prior history.'"})
if probe["fetch_mode"]=="summarizing":
    attr.append({"layer":"HR","item":"HR1_fetch_fidelity(summarizing)",
      "verdict":("harness-credit(design: 摘要式 fetch, 值可核实)" if HR["HR1_fetch_fidelity"] else "harness-attributable(design: 摘要式 fetch 丢失可追溯性)"),
      "evidence":f"抓取带 prompt 参数=先摘要再返回; 逐字引文物理不可得(逐字率 {round(verb,3)} 不作证据), 改判中立指标 value_on_page={round(vonr,3) if 'vonr' in dir() else round(grounded,3)}"})
if not RR["RR3_value_grounded"]:
    bad=[{k:c[k] for k in ("project","field","value","url")} for c in ck if c["page_ok"] and not c["value_on_page"]]
    attr.append({"layer":"RR","item":"RR3_value_grounded","verdict":"model-side(honesty)",
      "evidence":f"值不在它引的页面上(页面抓取成功): {bad[:4]}"})
if not RR["RR4_source_authoritative"]:
    bad=[{k:c[k] for k in ("project","field","url")} for c in ck if not c["source_authoritative"]]
    attr.append({"layer":"RR","item":"RR4_source_authoritative","verdict":"model-side(judgment)",
      "evidence":f"来源不是该字段的权威出处: {bad[:4]}"})
if not RR["RR5_values_plausible"]:
    attr.append({"layer":"RR","item":"RR5_values_plausible","verdict":"model-side(judgment)",
      "evidence":f"取值类型不合理: {[{'p':c['project'],'f':c['field'],'v':c['value']} for c in implaus][:4]}"})
if not RR["RR6_release_line"]:
    attr.append({"layer":"RR","item":"RR6_release_line","verdict":"model-side(judgment)",
      "evidence":f"取错发布线: {[{'p':c['project'],'v':c['value']} for c in susp][:4]}"})
if not RR["RR7_consistent"]:
    attr.append({"layer":"RR","item":"RR7_consistent","verdict":"model-side(judgment)","evidence":str(contra[:3])})
if not RR["RR1_coverage"]:
    only=[m for m in missing if m not in lost]
    if only: attr.append({"layer":"RR","item":"RR1_coverage","verdict":"model-side(未查全)",
      "evidence":f"这些缺失项的来源在压缩前也没抓过: {only}"})
if not comp_fired:
    attr.append({"layer":"HR","item":"HR2-4(压缩未触发)",
      "verdict":("harness-credit(design: 未触发压缩且任务完成)" if _untriggered_pass else "fail(未触发但任务未完成 -> 不是设计好, 是没走到)"),
      "evidence":f"压缩事件 0 次; 覆盖达标={_reached}(缺 {len(missing)} 项) -> 按'没触发+完成=记功'规则判定"})

print(json.dumps({"label":a.label,"harness":a.harness,"probe":probe,
 "HR":HR,"HR_measure":HR_measure,"RR":RR,
 "rr_pass_all":rr_pass,"rr_failed_items":rr_failed,
 "metrics":{"coverage":round(cov,4),"value_grounded_rate":round(grounded,4),
            "source_authority_rate":round(auth_rate,4),"verbatim_quote_rate":round(verb,4)},
 "attribution":attr,
 "details":{"missing":missing,"lost_after_seen":lost}},ensure_ascii=False,indent=1))
