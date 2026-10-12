#!/usr/bin/env python3
"""把不同 harness 的 trace 归一成同一套事件流, 供归因用。
事件: {"kind":"fetch","url":...} / {"kind":"search","q":...} / {"kind":"compaction","summary":...}
     / {"kind":"subagent"} / {"kind":"write","path":...}
"""
import json,glob,os
def _iter_lines(p):
    for ln in open(p,errors="ignore"):
        ln=ln.strip()
        if ln:
            try: yield json.loads(ln)
            except Exception: pass

def extract_openclaw(state_dir):
    fs=[f for f in glob.glob(os.path.join(state_dir,"agents/*/sessions/*.jsonl")) if "trajectory" not in f]
    if not fs: return []
    ev=[]
    for r in _iter_lines(sorted(fs,key=os.path.getmtime)[-1]):
        if (r.get("type") or "")=="compaction":
            ev.append({"kind":"compaction","summary":str(r.get("summary",""))[:300]}); continue
        for c in (r.get("message"),(r.get("data") or {}).get("message"),r):
            if not isinstance(c,dict): continue
            ct=c.get("content")
            if isinstance(ct,list):
                for it in ct:
                    if isinstance(it,dict) and it.get("type") in ("toolCall","tool_use"):
                        nm=str(it.get("name") or it.get("toolName") or "")
                        a=it.get("arguments") or it.get("input") or {}
                        if nm=="web_fetch": ev.append({"kind":"fetch","url":str(a.get("url",""))})
                        elif nm=="web_search": ev.append({"kind":"search","q":str(a.get("query",""))[:80]})
                        elif nm in ("sessions_spawn","subagents"): ev.append({"kind":"subagent"})
                        elif nm in ("write","file_write","edit"): ev.append({"kind":"write","path":str(a.get("path") or a.get("file_path") or "")})
    return ev

def extract_claudecode(workspace):
    pats=[os.path.expanduser("~/.claude/projects/*%s*/*.jsonl"%os.path.basename(workspace))]
    cands=[]
    for p in pats: cands+=glob.glob(p)
    if not cands: return []
    ev=[]
    for r in _iter_lines(sorted(cands,key=os.path.getmtime)[-1]):
        s=json.dumps(r,ensure_ascii=False)
        if r.get("isCompactSummary") or '"compact' in s.lower():
            ev.append({"kind":"compaction","summary":s[:300]})
        m=r.get("message") or {}
        ct=m.get("content") if isinstance(m,dict) else None
        if isinstance(ct,list):
            for it in ct:
                if isinstance(it,dict) and it.get("type")=="tool_use":
                    nm=str(it.get("name","")); a=it.get("input") or {}
                    if nm=="WebFetch": ev.append({"kind":"fetch","url":str(a.get("url",""))})
                    elif nm=="WebSearch": ev.append({"kind":"search","q":str(a.get("query",""))[:80]})
                    elif nm=="Task": ev.append({"kind":"subagent"})
                    elif nm in ("Write","Edit"): ev.append({"kind":"write","path":str(a.get("file_path",""))})
    return ev
