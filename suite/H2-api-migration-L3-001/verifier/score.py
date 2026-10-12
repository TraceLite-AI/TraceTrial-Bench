#!/usr/bin/env python
"""H2 verifier: 判 models/ 与 common/ 的**实际文件内容**。自查脚本查不出「迁过头」。"""
import argparse,ast,json,re,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True)
ap.add_argument("--harness",default="openclaw"); ap.add_argument("--state",default="")
ap.add_argument("--exit-code",type=int,default=0); ap.add_argument("--label",default="")
a=ap.parse_args(); ws=Path(a.workspace)
def _count_compactions(case_dir, trace_blob=""):
    """★只认 harness 侧证据。trace 里搜 'compact' 会命中抓回来的网页正文(实测 H1 误报 2 次)。"""
    n = 0
    he = case_dir/"harness_events.json"
    if he.is_file():
        try:
            import json as _j
            d = _j.loads(he.read_text() or "{}")
            n += sum(v for k, v in d.items() if "compaction" in str(k).lower())
        except Exception: pass
    gw = case_dir/"gw.log"
    if gw.is_file():
        try:
            n += len(re.findall(r"compaction (complete|failed|started)", gw.read_text(errors="ignore"), re.I))
        except Exception: pass
    return n
T=json.loads((HERE/"truth.json").read_text())
if a.exit_code in (124,137,143):
    print(json.dumps({"label":a.label,"verdict":"inconclusive","reason":f"外部终止 exit={a.exit_code}"},ensure_ascii=False,indent=1)); raise SystemExit
def rd(p):
    f=ws/p; return f.read_text(errors="ignore") if f.exists() else None
rename_ok=[];rename_miss=[];unchanged_lost=[];removed_carried=[];meth_miss=[];syntax_bad=[]
for e in T["files"]:
    s=rd(e["file"])
    if s is None: syntax_bad.append((e["file"],"missing")); continue
    try: ast.parse(s)
    except SyntaxError as ex: syntax_bad.append((e["file"],str(ex))); continue
    o,n=e["renamed"]
    (rename_ok if (re.search(rf"\b{n}\b",s) and not re.search(rf"\b{o}\s*=",s)) else rename_miss).append(f"{e['file']}:{o}")
    if not re.search(rf"\b{e['unchanged']}\b",s): unchanged_lost.append(f"{e['file']}:{e['unchanged']}")
    if e["removed"] and re.search(rf"\b{e['removed']}\s*[=:]",s): removed_carried.append(f"{e['file']}:{e['removed']}")
    mo,mn=e["method"]
    if re.search(rf"(?<![\w.])[A-Z]\w*\.{mo}\(",s) or re.search(rf"\bobj\.{mo}\(",s): meth_miss.append(f"{e['file']}:.{mo}()")
# ★盲替换陷阱: 非 pydantic 的 RawEnvelope 自有 API 不得被改名
env=rd("common/envelope.py") or ""
env_intact=all(re.search(rf"def {m}\(self",env) for m in ("dict","json","copy"))
env_callsites_intact=True; env_broken=[]
for e in T["files"]:
    if not e["uses_envelope"]: continue
    s=rd(e["file"]) or ""
    if re.search(r"env\.model_dump\(|env\.model_dump_json\(|env\.model_copy\(",s):
        env_callsites_intact=False; env_broken.append(e["file"])
config_migrated=all((rd(e["file"]) or "") and not re.search(r"^\s*class Config\s*:",rd(e["file"]) or "",re.M) for e in T["files"])
fields_migrated=all("__fields__" not in (rd(e["file"]) or "") for e in T["files"])
chk=ws/"tools"/"v2_check.py"; rc=None;out=""
if chk.exists():
    r=subprocess.run([sys.executable,"tools/v2_check.py"],capture_output=True,text=True,cwd=str(ws))
    rc=r.returncode; out=(r.stdout or "")[-1500:]
rep=ws/"output"/"migration_report.md"; rt=rep.read_text(errors="ignore") if rep.exists() else ""
cites=len(re.findall(r"(pydantic/v2\.0\.3|migration\.md|迁移指南|migration guide)",rt,re.I))
notes_nomap=bool(re.search(r"(没有对应|无对应|已移除|不能直接|no direct|removed)",rt))
st=Path(a.state) if a.state else None; tr=""
if st and st.exists():
    for f in sorted(st.rglob("*.json*"))[:400]:
        try: tr+=f.read_text(errors="ignore")[:200000]
        except Exception: pass
fetches=len(re.findall(r"(web_fetch|webfetch|raw\.githubusercontent\.com/pydantic)",tr,re.I))
compactions=_count_compactions(HERE.parent)
trace_ok=bool(tr.strip())
allgood = not(rename_miss or unchanged_lost or removed_carried or meth_miss)
HR={"HR1_web_channel_used":"N/A","HR2_guide_detail_survived":"N/A"} if not trace_ok else {
 "HR1_web_channel_used":"pass" if (fetches>0 or allgood) else "fail",
 "HR2_guide_detail_survived":("pass" if allgood else "fail") if compactions>0 else ("pass" if allgood else "fail")}
RR={"RR1_renames_applied":not rename_miss,
    "RR2_unchanged_keys_preserved":not unchanged_lost,
    "RR3_removed_keys_not_carried":not removed_carried,
    "RR4_methods_migrated":not meth_miss,
    "RR5_non_pydantic_api_untouched":env_intact and env_callsites_intact,
    "RR6_config_class_migrated":config_migrated and fields_migrated,
    "RR7_no_syntax_break":not syntax_bad,
    "RR8_checker_passes":rc==0,
    "RR9_report_cites_guide":bool(rt.strip()) and cites>0,
    "RR10_notes_unmappable":notes_nomap}
attr=[]
if not (env_intact and env_callsites_intact): attr.append({"layer":"RR","item":"RR5_non_pydantic_api_untouched",
  "verdict":"model-side(judgment: 盲替换)","evidence":f"把非 pydantic 的 RawEnvelope 自有 API 一并改名: envelope_intact={env_intact} 调用点={env_broken}"})
if removed_carried: attr.append({"layer":"RR","item":"RR3_removed_keys_not_carried","verdict":"model-side(未读全指南)",
  "evidence":f"v2 已移除的配置项被原样搬过去: {removed_carried}"})
if unchanged_lost: attr.append({"layer":"RR","item":"RR2_unchanged_keys_preserved","verdict":"model-side(改过头)",
  "evidence":f"v1/v2 同名不变的配置项丢了: {unchanged_lost}"})
if rename_miss: attr.append({"layer":"RR","item":"RR1_renames_applied","verdict":"model-side(未迁全)","evidence":f"{rename_miss}"})
if trace_ok and compactions>0 and (rename_miss or removed_carried): attr.append({"layer":"HR",
  "item":"HR2_guide_detail_survived","verdict":"harness-attributable(候选)",
  "evidence":f"压缩 {compactions} 次后指南细节丢失, 需与 control 臂对比确认"})
print(json.dumps({"label":a.label,"harness":a.harness,"HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"models":len(T["files"]),"rename_ok":len(rename_ok),"rename_miss":len(rename_miss),
  "unchanged_lost":len(unchanged_lost),"removed_carried":len(removed_carried),"method_miss":len(meth_miss),
  "envelope_api_intact":env_intact,"envelope_callsites_intact":env_callsites_intact,
  "config_migrated":config_migrated,"syntax_bad":len(syntax_bad),"checker_rc":rc,
  "guide_citations":cites,"web_fetches":fetches,"compactions":compactions,"trace_available":trace_ok},
 "attribution":attr,"details":{"rename_miss":rename_miss,"unchanged_lost":unchanged_lost,
  "removed_carried":removed_carried,"method_miss":meth_miss,"env_broken":env_broken,"checker_tail":out}},ensure_ascii=False,indent=1))
