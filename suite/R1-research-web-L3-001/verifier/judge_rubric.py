#!/usr/bin/env python3
"""LLM-as-judge 给 RR 层打分。纪律: 判官≠被测模型 · 必须给证据span · 3票取多数 · 报分歧率 ·
   与确定性判定并行输出做交叉核对。"""
import argparse,json,os,re,subprocess,sys,tempfile
from pathlib import Path

RR_SPEC = """RR1_coverage: 24 个数据点(6 项目 × 4 字段)是否齐全
RR2_format_valid: 是否 JSON 可解析且每条含 project/field/value/source_url/quote
RR3_value_grounded: 每个取值是否确实被它所引来源页面支持(语义等价即可, 不要求逐字, 例: 页面写 "Apache License 2.0" 而取值 "Apache-2.0" 算支持)
RR4_source_authoritative: 来源是否为该字段的合理权威出处(license→LICENSE/package.json/官方API; version→releases/tags/官方API; 第三方博客不算权威)
RR5_values_plausible: 取值类型是否合理(版本号形如 vX.Y; 语言为已知编程语言; mcp_support 为 yes/no)
RR6_release_line: 是否误把非正式发布线(cloud/nightly/beta/-vscode 等)当作最新正式版本
RR7_consistent: 同一(项目,字段)是否存在自相矛盾取值
RR8_uncertainty_declared: 对查不到/不确定的项, 是否自发做了说明(不是被要求, 而是自己披露)"""

PROMPT_TMPL = """你是评测判官。依据下面的 rubric 定义, 对一份调研产出逐项判定 pass/fail。

## rubric 定义
{spec}

## 待评产出(JSON)
{artifact}

## 来源页面证据(我已代抓, 用于核实取值是否被页面支持)
{evidence}

## 输出要求
只输出 JSON, 不要任何解释文字:
{{"RR1_coverage":{{"verdict":"pass|fail","evidence":"<支持你判定的具体证据, 引用产出或页面中的原文片段>"}},
 "RR2_format_valid":{{...}}, "RR3_value_grounded":{{...}}, "RR4_source_authoritative":{{...}},
 "RR5_values_plausible":{{...}}, "RR6_release_line":{{...}}, "RR7_consistent":{{...}},
 "RR8_uncertainty_declared":{{...}}}}

判定纪律: 每项必须给出 evidence(具体片段, 不能写"符合要求"这类空话); 证据不足时判 fail 并说明缺什么。"""

def norm(s): return re.sub(r"\s+"," ",re.sub(r"<[^>]+>"," ",str(s))).strip()

def build_evidence(rows, max_excerpt=260):
    """为每个数据点抓来源页, 取与 value 最相关的片段(判官据此判语义等价)"""
    cache={}; out=[]
    for r in rows:
        u=str(r.get("source_url","")); v=str(r.get("value",""))
        if u not in cache:
            try:
                p=subprocess.run(["curl","-sL","--max-time","20","-A","Mozilla/5.0",u],
                                 capture_output=True,text=True)
                cache[u]=norm(p.stdout)
            except Exception: cache[u]=""
        pg=cache[u]
        if not pg: out.append(f"- {r.get('project')}/{r.get('field')} url={u} → 【页面抓取失败】"); continue
        i=pg.lower().find(v.lower()[:40]) if v else -1
        exc = pg[max(0,i-100):i+160] if i>=0 else pg[:max_excerpt]
        out.append(f"- {r.get('project')}/{r.get('field')} value={v!r} url={u}\n    页面片段: {exc}")
    return "\n".join(out)

def call_judge(prompt, model, timeout=600):
    with tempfile.NamedTemporaryFile("w",suffix=".txt",delete=False) as f:
        f.write(prompt); path=f.name
    try:
        r=subprocess.run(["claude","-p",prompt,"--model",model,
                          "--permission-mode","bypassPermissions","--output-format","json"],
                         capture_output=True,text=True,timeout=timeout)
        d=json.loads(r.stdout or "{}")
        txt=str(d.get("result") or "")
        m=re.search(r"\{.*\}",txt,re.S)
        return json.loads(m.group(0)) if m else {"_err":"判官输出无 JSON","_raw":txt[:200]}
    except subprocess.TimeoutExpired: return {"_err":"判官超时"}
    except Exception as e: return {"_err":str(e)[:120]}
    finally: os.unlink(path)

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--workspace",required=True)
    # ★必须钉死完整模型 ID: 别名 "opus" 在本机解析成 claude-opus-4-5-20251101(Opus 4.5),
    #   别名随环境/时间漂移 -> 跨机器跨时间不可复现。判官一律用全名。
    ap.add_argument("--judge-model",default="claude-opus-5")   # ≠ 被测模型(claude-sonnet-5 / deepseek-v4-pro)
    ap.add_argument("--votes",type=int,default=3)
    ap.add_argument("--label",default="")
    a=ap.parse_args()
    art=Path(a.workspace)/"output"/"research.json"
    d=json.loads(art.read_text()); rows=d.get("findings",d if isinstance(d,list) else [])
    ev=build_evidence(rows)
    prompt=PROMPT_TMPL.format(spec=RR_SPEC,artifact=json.dumps(d,ensure_ascii=False)[:6000],evidence=ev[:12000])
    votes=[call_judge(prompt,a.judge_model) for _ in range(a.votes)]
    ok=[v for v in votes if "_err" not in v]
    items=["RR1_coverage","RR2_format_valid","RR3_value_grounded","RR4_source_authoritative",
           "RR5_values_plausible","RR6_release_line","RR7_consistent","RR8_uncertainty_declared"]
    res={};disagree=0
    for it in items:
        vs=[str((v.get(it) or {}).get("verdict","")).lower() for v in ok]
        vs=[x for x in vs if x in ("pass","fail")]
        if not vs: res[it]={"verdict":None,"note":"判官未给出"}; continue
        npass=sum(1 for x in vs if x=="pass")
        maj="pass" if npass*2>len(vs) else "fail"
        if 0<npass<len(vs): disagree+=1
        evd=next((str((v.get(it) or {}).get("evidence",""))[:200] for v in ok
                  if str((v.get(it) or {}).get("verdict","")).lower()==maj),"")
        res[it]={"verdict":maj,"votes":f"{npass}/{len(vs)} pass","evidence":evd}
    print(json.dumps({"label":a.label,"judge_model":a.judge_model,
      "valid_votes":len(ok),"total_votes":a.votes,
      "disagreement_items":disagree,"disagreement_rate":round(disagree/len(items),3),
      "judge_rr":res,
      "judge_errors":[v.get("_err") for v in votes if "_err" in v],
      "reproducibility":{"model_id_pinned":a.judge_model,
        "warning":"禁止使用别名(如 opus/sonnet): 本机 opus→claude-opus-4-5-20251101, 别名解析随环境漂移"}},
      ensure_ascii=False,indent=1))
