# -*- coding: utf-8 -*-
"""语义判官层 v3 = gate（程序）→ ask（判官）→ quote 回查（程序，且卡分）。

## 和 v2 判官层的三个差别
  1. **gate 前置**：有 gate 的判据先跑程序门。不参评/直接fail/直接pass 不调判官。
     判官看到的问句没有任何兜底分支（兜底全在 gate 里）。
  2. **evidence gate 卡分**：引文回查不过 → 不允许维持原判。
       fail 无可回查引文 → 降为 None（未验证，非证伪 —— 两者处置不同）
       ★pass 无可回查引文 → 也降为 None（v2 只查 fail 不查 pass，白做一半；
         调研 Rulers 消融：去掉这一步 QWK 0.7276→0.6904）
       只对 gate 管辖的判据启用 pass 侧卡分 —— 其余 21 条问句还是旧形态，
       「没找到问题所以 pass」类回答没有可引对象，硬卡会误伤。
  3. **gate 结论进账**：直接fail/直接pass 记 program=True，证据是 gate 的那句话，
     可追溯，且 0% 翻转。
"""
import json, re, sys, collections
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
import llm_rubrics3 as LR3
import anchors as ANC
import gates as G
from judge_sem import one_vote as _one_vote_v2, verify_quote
from judge import call as ask


def one_vote(rub, evidence: str) -> dict:
    prompt = LR3.prompt_for(rub, evidence)
    try:
        raw = ask(prompt, max_tokens=800, temperature=0.0)
        if isinstance(raw, tuple):
            raw = raw[0]
    except Exception as e:
        return {"value": None, "error": str(e)[:120]}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"value": None, "parse_failed": True, "raw": raw[:160]}
    try:
        d = json.loads(m.group(0))
    except Exception:
        return {"value": None, "parse_failed": True, "raw": raw[:160]}
    v = str(d.get("value", "")).strip().lower()
    return {"value": v if v in ("pass", "fail") else None,
            "reason": d.get("reason", ""), "quote": d.get("quote", "")}


# 降为程序判据、但仍要在报表里占位的两条（gate 直接出结论）
PROGRAM_ONLY = [
    ("O.log.timeline_recoverable", "O"),
    ("C.reduction.amount_stated", "C"),
]


def judge_case(case_name: str, component: str, votes: int = 3) -> dict:
    case = R / case_name
    out, n_call = [], 0

    rows = list(LR3.for_case(component))
    # 程序 only 的两条按 component 补进来占位
    for rid, comp in PROGRAM_ONLY:
        if comp == component:
            rows.append((rid, comp, None, None, None, None))

    for rub in rows:
        rid = rub[0]
        st, gev = G.run_gate(rid, case)

        if st == "不参评":
            out.append({"id": rid, "value": None, "gated": True,
                        "gate": st, "gate_evidence": gev})
            continue
        if st == "直接fail":
            out.append({"id": rid, "value": "fail", "gated": True, "program": True,
                        "gate": st, "gate_evidence": gev})
            continue
        if st == "直接pass":
            out.append({"id": rid, "value": "pass", "gated": True, "program": True,
                        "gate": st, "gate_evidence": gev})
            continue
        if st == "无结果":
            out.append({"id": rid, "value": None, "gated": True,
                        "gate": st, "gate_evidence": gev,
                        "note": "★装置取不到判定材料，记装置账不记 harness 账"})
            continue

        # st == "参评" 或无 gate → 走判官
        anchor = rub[4]
        if anchor is None:            # program-only 不该走到这
            out.append({"id": rid, "value": None, "note": "★program-only 判据缺 gate 结论"})
            continue
        try:
            sl = ANC.slice_for(case_name, anchor)
        except Exception as e:
            out.append({"id": rid, "value": None, "note": "★取证失败: %s" % str(e)[:80]})
            continue

        if sl["empty"]:
            if st == "参评":
                # gate 说机制触发了，锚点却截不到证据 —— 装置错位，不是 harness 的账
                out.append({"id": rid, "value": None, "gated": True, "gate": st,
                            "gate_evidence": gev,
                            "note": "★gate 判机制已触发但锚点为空 —— 锚点与 gate 错位，记装置账"})
            else:
                on_empty = rub[5]
                out.append({"id": rid, "value": "pass" if on_empty == "pass" else None,
                            "anchor_empty": True})
            continue

        vs = [one_vote(rub, sl["text"]) for _ in range(votes)]
        n_call += votes
        vals = [v["value"] for v in vs if v["value"]]
        if not vals:
            out.append({"id": rid, "value": None, "note": "★判官未给出可解析取值"})
            continue
        cnt = collections.Counter(vals)
        final, _ = cnt.most_common(1)[0]
        pick = next(v for v in vs if v["value"] == final)
        row = {"id": rid, "value": final, "votes": dict(cnt),
               "unanimous": len(cnt) == 1,
               "reason": pick.get("reason", ""), "quote": pick.get("quote", ""),
               "evidence_chars": sl["chars"], "evidence_spans": sl["n_spans"]}
        if st == "参评":
            row["gate"] = st
            row["gate_evidence"] = gev

        # ── evidence gate：引文回查不过 → 不允许维持原判 ──
        cov = verify_quote(pick.get("quote", ""), sl["text"])
        row["quote_coverage"] = round(cov, 3)
        if final == "fail" and cov < 0.4:
            row["value"] = None
            row["note"] = "★fail 引文回查不过（未验证，非证伪）—— 降为无结果"
        elif final == "pass" and st == "参评" and cov < 0.4:
            row["value"] = None
            row["note"] = "★pass 无可回查引文（evidence gate 卡分）—— 降为无结果"
        out.append(row)

    return {"case": case_name, "component": component,
            "api_calls": n_call, "verdicts": out}
