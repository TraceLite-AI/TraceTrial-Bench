# -*- coding: utf-8 -*-
"""语义判据的判官层：3 次投票 + 引文回查。

守卫（缺一不可）：
  votes=3         多数决 —— LLM 的地板约 5%，投票把它再压低
  quote_required  判 fail 必须逐字引原文
  quote_verified  程序把 quote 拿回证据包逐字回查
                  ★对不上标「未验证」，**不是「已证伪」** ——
                    实测过引文 0% 覆盖但结论为真的例子（判官引错了地方）。
                    两者处置不同：假的要删，未验证的要补证据。

回查的能力边界（必须写清，否则会被当成「已验证」）：
  它证明「这段话在证据里」，**不证明「这段话支持这个结论」**。
  「引了真话但答非所问」这一类回查抓不到，只能人看。
"""
import json, re, sys, collections
from pathlib import Path

R = Path.home() / "hb-cases"
sys.path.insert(0, str(R))
import llm_rubrics2 as LR
import anchors as ANC
from judge import call as ask          # 复用现成判官调用（凭据只从文件读，不落盘不打印）

TOK = re.compile(r"[A-Za-z0-9_]{3,}|[一-鿿]{2,}")


def _toks(s):
    return set(TOK.findall(s or ""))


def verify_quote(quote: str, evidence: str) -> float:
    """token 覆盖率 —— 不做字面全等，判官可能合法地跨行拼接。"""
    q = _toks(quote)
    if not q:
        return 0.0
    return len(q & _toks(evidence)) / len(q)


def one_vote(rub, evidence: str) -> dict:
    prompt = LR.prompt_for(rub, evidence)
    try:
        raw = ask(prompt, max_tokens=800, temperature=0.0)
        if isinstance(raw, tuple):        # judge.call 返回 (文本, usage)
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


def judge_case(case_name: str, component: str, votes: int = 3) -> dict:
    """每条 rubric 只看自己锚点截出的那一段轨迹。

    ★锚点命中 0 条 → 按该条声明的 on_empty 处置，**不调判官**。
      那是「这道题没出现这个机制」的确定事实，不是「拿不到证据」。
      实测：整包喂给判官时，它会跑去判模型算错数、题面歧义；
      锚点一收窄，这类误判直接消失 —— 证据范围是硬约束，提示词是软约束。
    """
    out, n_call = [], 0
    for rub in LR.for_case(component):
        rid, comp, what, failshape, anchor, on_empty = rub
        try:
            sl = ANC.slice_for(case_name, anchor)
        except Exception as e:
            out.append({"id": rid, "value": None, "note": "★取证失败: %s" % str(e)[:80]})
            continue

        if sl["empty"]:
            out.append({"id": rid, "value": on_empty if on_empty != "judge" else None,
                        "anchor_empty": True,
                        "note": ("锚点命中 0 条：该机制在这道题没出现，harness 无从做错"
                                 if on_empty == "pass" else
                                 "★锚点本不该为空（system_prompt / final），空了是装置问题")})
            continue

        vs = [one_vote(rub, sl["text"]) for _ in range(votes)]
        n_call += votes
        vals = [v["value"] for v in vs if v["value"]]
        if not vals:
            out.append({"id": rid, "value": None, "note": "★判官未给出可解析取值",
                        "errors": [v.get("error") or v.get("raw") for v in vs][:2]})
            continue
        cnt = collections.Counter(vals)
        final, _ = cnt.most_common(1)[0]
        pick = next(v for v in vs if v["value"] == final)
        row = {"id": rid, "value": final, "votes": dict(cnt), "unanimous": len(cnt) == 1,
               "reason": pick.get("reason", ""), "quote": pick.get("quote", ""),
               "evidence_chars": sl["chars"], "evidence_spans": sl["n_spans"]}
        if final == "fail":
            cov = verify_quote(pick.get("quote", ""), sl["text"])
            row["quote_coverage"] = round(cov, 3)
            row["quote_status"] = ("引文可回查" if cov >= 0.8 else
                                   "引文部分可回查" if cov >= 0.4 else
                                   "★引文回查不通过（未验证，非证伪）")
        out.append(row)
    return {"case": case_name, "component": component,
            "api_calls": n_call, "verdicts": out}


if __name__ == "__main__":
    name = sys.argv[1]
    comp = sys.argv[2] if len(sys.argv) > 2 else "C"
    nv = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    r = judge_case(name, comp, votes=nv)
    vs = r["verdicts"]
    empt = [v for v in vs if v.get("anchor_empty")]
    print("【%s】component=%s  判据 %d 条 | 锚点为空 %d 条(不调 API) | API 调用 %d 次\n"
          % (r["case"], r["component"], len(vs), len(empt), r["api_calls"]))
    for v in vs:
        tag = ""
        if v.get("anchor_empty"):
            tag = "  ← 锚点为空：该机制这道题没出现"
        elif not v.get("unanimous", True):
            tag = "  ⚠票不一致 %s" % v.get("votes")
        print("  %-40s %-6s%s" % (v["id"], v["value"], tag))
        if v.get("value") == "fail":
            print("      理由: %s" % (v.get("reason") or "")[:120])
            print("      证据: %d 字/%d 片段 | 回查: %s (%.0f%%)"
                  % (v.get("evidence_chars", 0), v.get("evidence_spans", 0),
                     v.get("quote_status", ""), 100 * v.get("quote_coverage", 0)))
    (R / ("SEM_%s.json" % name)).write_text(json.dumps(r, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
