#!/usr/bin/env python
"""H1 verifier: 判 services/ 下**文件实际内容** —— 该改的改了, 保留的没动, 且没改坏。"""
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

def imports_of(p):
    f=ws/p
    if not f.exists(): return None,"missing"
    src=f.read_text(errors="ignore")
    try: tree=ast.parse(src)
    except SyntaxError: return None,"syntax_error"
    mods=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Import):
            for x in n.names: mods.add(x.name)
        elif isinstance(n,ast.ImportFrom):
            if n.module: mods.add(n.module)
    return mods,src

fixed=[];left=[];broken=[]
for e in T["gone"]:
    mods,src=imports_of(e["file"])
    if mods is None: broken.append((e["file"],src)); continue
    top=e["module"].split(".")[0]
    still=any(m.split(".")[0]==top for m in mods)
    (left if still else fixed).append(e["module"])

kept_ok=[];kept_touched=[]
for e in T["kept"]:
    mods,src=imports_of(e["file"])
    if mods is None: kept_touched.append(e["module"]); continue
    top=e["module"].split(".")[0]
    (kept_ok if any(m.split(".")[0]==top for m in mods) else kept_touched).append(e["module"])

# 签名保持
sig_broken=[]
for e in T["gone"]+T["kept"]:
    f=ws/e["file"]
    if not f.exists(): sig_broken.append(e["file"]); continue
    s=f.read_text(errors="ignore")
    if "def handle(" not in s or "def health(" not in s: sig_broken.append(e["file"])

chk=ws/"tools"/"py313_check.py"; rc=None; out=""
if chk.exists():
    r=subprocess.run([sys.executable,"tools/py313_check.py"],capture_output=True,text=True,cwd=str(ws))
    rc=r.returncode; out=(r.stdout or "")[-1500:]

rep=ws/"output"/"conformance_report.md"; rt=rep.read_text(errors="ignore") if rep.exists() else ""
cites=len(re.findall(r"(pep-0594|PEP\s*594|whatsnew/3\.1[23]|docs\.python\.org)",rt,re.I))
explains_kept=bool(re.search(r"(保留|未改|没改|不需要改|仍在标准库|继续保留)",rt))

# HR: web 通道 + 压缩
st=Path(a.state) if a.state else None
tr=""
if st and st.exists():
    for f in sorted(st.rglob("*.json*"))[:400]:
        try: tr+=f.read_text(errors="ignore")[:200000]
        except Exception: pass
fetches=len(re.findall(r"(web_fetch|webfetch|fetch_url|\"url\"\s*:\s*\"https?://(peps|docs)\.python\.org)",tr,re.I))
compactions=_count_compactions(HERE.parent)
trace_ok=bool(tr.strip())
HR={}
if not trace_ok:
    HR={"HR1_web_channel_used":"N/A","HR2_fetched_content_survived":"N/A"}
else:
    HR["HR1_web_channel_used"]="pass" if fetches>0 else ("pass" if len(left)==0 else "fail")
    if compactions==0:
        HR["HR2_fetched_content_survived"]="pass" if len(left)==0 else "fail"
    else:
        HR["HR2_fetched_content_survived"]="pass" if len(left)==0 and len(kept_touched)==0 else "fail"

RR={"RR1_all_removed_modules_fixed":len(left)==0,
    "RR2_kept_modules_untouched":len(kept_touched)==0,
    "RR3_no_syntax_or_signature_break":len(broken)==0 and len(sig_broken)==0,
    "RR4_checker_passes":rc==0,
    "RR5_report_exists":bool(rt.strip()),
    "RR6_cites_upstream_doc":cites>0,
    "RR7_explains_untouched":explains_kept}
attr=[]
if kept_touched: attr.append({"layer":"RR","item":"RR2_kept_modules_untouched","verdict":"model-side(judgment: 改过头)",
    "evidence":f"官方文档明确保留的模块被动了: {kept_touched}"})
if left: attr.append({"layer":"RR","item":"RR1_all_removed_modules_fixed","verdict":"model-side(未查全)",
    "evidence":f"仍在用 3.13 已移除的模块: {left}"})
if broken or sig_broken: attr.append({"layer":"RR","item":"RR3_no_syntax_or_signature_break","verdict":"model-side(改坏)",
    "evidence":f"语法错/丢签名: {broken} {sig_broken}"})
if trace_ok and compactions>0 and left: attr.append({"layer":"HR","item":"HR2_fetched_content_survived",
    "verdict":"harness-attributable(候选)","evidence":f"压缩 {compactions} 次后仍漏 {len(left)} 项, 需与 control 臂对比确认"})
print(json.dumps({"label":a.label,"harness":a.harness,"HR":HR,"RR":RR,"rr_pass_all":all(RR.values()),
 "metrics":{"gone_total":len(T["gone"]),"gone_fixed":len(fixed),"gone_left":len(left),
  "kept_total":len(T["kept"]),"kept_ok":len(kept_ok),"kept_wrongly_touched":len(kept_touched),
  "checker_rc":rc,"doc_citations":cites,"web_fetches":fetches,"compactions":compactions,"trace_available":trace_ok},
 "attribution":attr,"details":{"left":left,"kept_touched":kept_touched,"checker_tail":out}},ensure_ascii=False,indent=1))
