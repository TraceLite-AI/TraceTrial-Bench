# -*- coding: utf-8 -*-
"""证据锚点：每条 rubric 声明自己要看轨迹的哪一段。

## 为什么要这样
三种做法的差别：

    老做法    判据名 → 正则 → 9 个切片器之一    运行时**猜**，实测 26% 猜错掉兜底
    大包做法  一份 66K 字的包喂给所有判据        不猜，但噪声大 —— 判官会看到
                                                 模型算错数、题面歧义，然后去判那些
    ★锚点     rubric 自带锚点声明                写死在判据里，不猜也不散

实测证据：给整包时，10 条判据出 4 个 fail，其中 2 个判错对象
（把「模型该统计 errors 却去数行数」判成了 harness 的错误归因缺陷）。
**证据范围本身就是约束判官不跑题的手段** —— 提示词是软约束，拦不住。

## 锚点词汇表（7 种，写死，不可运行时推断）
"""
import json, re, sys
from pathlib import Path

R = Path.home() / "hb-cases"
sys.path.insert(0, str(R))
from hr_slices import _tool_events, _sessions, _read
from mv_text2 import _text, system_prompt

MAX_SPAN = 2500      # 单条证据片段封顶
MAX_TOTAL = 30000    # 一条 rubric 的证据总量封顶


def _fmt(x, cap=MAX_SPAN):
    args = json.dumps(x.get("args"), ensure_ascii=False) if x.get("args") else ""
    res = _text(x.get("result"))
    if len(res) > cap:
        res = res[:cap] + " ⟦⟧"
    if len(args) > 400:
        args = args[:400] + " ⟦⟧"
    return "[%d] %s(%s)\n[%d] 返回%s: %s" % (
        x["i"], x.get("tool"), args, x["i"],
        "（错误）" if x.get("isError") else "", res)


# ── 七种锚点 ──
def a_tool(case, ev, pattern, window=0):
    """特定工具的调用与返回。window=N 时前后各带 N 条。"""
    idx = [i for i, x in enumerate(ev) if re.search(pattern, x.get("tool") or "", re.I)]
    return _collect(ev, idx, window)


def a_error(case, ev, window=1):
    """所有失败返回。

    ★不能只看 isError —— 全库 57 道题里只有 9 道设过这个 flag。
      真实形态多是「失败以正常结果送达」：wf_do 回 `RETRY_LATER item=M03 transient error`、
      exec 回 `Traceback (most recent call last):`、edit 回 `{"status":"error"}`、
      read 回 `ENOENT`，isError 一律 False。
      只看 flag 会漏掉 36/55 = 65% 的真失败，把该判的判据静默记成 pass
      （E.failure.reason_legible / O.error.source_attributable 各 48 条被误记为「无失败返回」）。
      判别规则见 fail_detect.is_failure —— 窄规则（结构化字段 + 行首判别符），
      刻意不用宽正则：宽正则会命中路径名（.../N3-tool-denied-.../）与题面散文。
    """
    from fail_detect import is_failure
    name = case.name if hasattr(case, "name") else str(case)
    idx = [i for i, x in enumerate(ev) if is_failure(x, name)[0]]
    return _collect(ev, idx, window)


def a_result_match(case, ev, pattern, window=1):
    """返回内容匹配某模式的调用（截断/拒绝/超时/掩码…）。"""
    idx = [i for i, x in enumerate(ev) if re.search(pattern, _text(x.get("result"))[:6000], re.I)]
    return _collect(ev, idx, window)


def a_event(case, ev, kind_name, window=2):
    """harness 侧事件（压缩/溢出）及其前后 N 条调用。"""
    out = []
    import trace_ir
    for p in trace_ir.event_files(case):   # ★事件文件清单来自 harness profile
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="ignore")
        hits = [m for m in re.finditer(kind_name, txt, re.I)]
        for m in hits[:20]:
            s = max(0, m.start() - 600)
            out.append("【%s】…%s…" % (name, txt[s:m.end() + 900]))
    return out


def a_system_prompt(case, ev):
    """系统提示（工具清单、能力声明都在这）。"""
    sp = system_prompt(case)
    return ["【系统提示】\n" + sp] if sp else []


def _assistant_texts(case, n=6):
    """模型自己写的文本（最终报告就在这）。

    ★V 维判「自述 vs 实际」必须要它。只收工具调用会丢掉自述，
      实测导致一个已被程序判据交叉验证过的真 fail 变成 pass。
    """
    import json as _j
    out = []
    for f in _sessions(str(case)):
        for line in _read(f).splitlines():
            if '"assistant"' not in line and '"text"' not in line:
                continue
            try:
                row = _j.loads(line)
            except Exception:
                continue
            for node in _walk_any(row):
                if not isinstance(node, dict):
                    continue
                if node.get("role") != "assistant":
                    continue
                c = node.get("content")
                s = ""
                if isinstance(c, str):
                    s = c
                elif isinstance(c, list):
                    s = "\n".join(i.get("text", "") for i in c
                                   if isinstance(i, dict) and i.get("type") in (None, "text"))
                if s and len(s.strip()) > 40:
                    out.append(s.strip())
    # 去重保序，取末尾 n 条
    seen, uniq = set(), []
    for s in out:
        k = s[:200]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
    return uniq[-n:]


def _walk_any(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk_any(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_any(v)


def a_final(case, ev, n=6):
    """模型末尾动作 + **模型自己写的报告** + 交付物 —— 判「自述 vs 实际」用。"""
    out = [_fmt(x) for x in ev[-n:]]

    says = _assistant_texts(case, n=4)
    for i, s in enumerate(says, 1):
        out.append("【模型自述 %d/%d】\n%s" % (i, len(says), s[:3000] + (" ⟦⟧" if len(s) > 3000 else "")))

    outdir = case / "workspace" / "output"
    if outdir.is_dir():
        for f in sorted(outdir.iterdir())[:6]:
            try:
                out.append("【交付物 %s】\n%s" % (f.name, f.read_text(encoding="utf-8",
                                                                    errors="ignore")[:2500]))
            except Exception:
                pass
    return out


def a_turns(case, ev, which="all", cap=1200):
    """各轮用户消息（注入的材料只留头部——它是压力源不是判据对象）。"""
    p = case / "turns.json"
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        ts = d if isinstance(d, list) else d.get("turns", [])
    except Exception:
        return []
    if which == "last":
        ts = ts[-1:]
    out = []
    for i, t in enumerate(ts, 1):
        s = _text(t)
        out.append("【第 %d 轮用户消息】\n%s" % (i, s[:cap] + (" ⟦⟧" if len(s) > cap else "")))
    return out


def _collect(ev, idx, window):
    keep = set()
    for i in idx:
        for j in range(max(0, i - window), min(len(ev), i + window + 1)):
            keep.add(j)
    return [_fmt(ev[j]) for j in sorted(keep)]



def a_call_digest(case, ev, cap=120):
    """全程调用摘要：每次调用一行（序号 · 工具 · 关键参数 · 结果首段）。

    ★判「自述 vs 实际」必须看**全程**，不能只看末尾。
      实测：M-G5a 的 M06 在 [18] 提交成功拿到回执，[58] 又错提了一次返回 MISMATCH。
      `final` 锚点只取末尾 10 条 → 判官只看到 MISMATCH，
      判「模型自述 M06 通过是撒谎」——**而它没撒谎**，早期确实成功了。
      一行一条的摘要足够核对声称，又足够紧凑（94 次调用约 8K 字）。
    """
    out = []
    for x in ev:
        args = json.dumps(x.get("args"), ensure_ascii=False) if x.get("args") else ""
        res = _text(x.get("result")).replace("\n", " ")
        out.append("[%d] %s(%s) -> %s%s"
                   % (x["i"], x.get("tool"), args[:80],
                      "（错误）" if x.get("isError") else "", res[:cap]))
    return ["【全程调用摘要 %d 条】\n" % len(out) + "\n".join(out)]

def a_outcome(case, ev, pattern, window=0, only_nonzero=False):
    """结局区匹配 —— result_match 的净化版，证据净化三条规则的落点：
       ① read/web_fetch/*_brief/*_read/*_fetch 的返回是**内容**不是结局，一律不看
          （E.termination 曾在 105 条命中里吃进 77% 题面散文/文档噪声）
       ② 只扫返回**末尾 15 行**（结局区）；中间大段是命令产出的内容
       ③ 不扫 args（模型自己写的命令不是 harness 的报告）
       only_nonzero=True 时只保留失败形态的 exec（配合 E.streams 的 gate）。"""
    import re as _re
    from fail_detect import is_failure as _isf
    _READ = _re.compile(r"^(read|web_fetch|web_search|browse)$|_(brief|read|fetch)$")
    pat = _re.compile(pattern, _re.I)
    name = case.name if hasattr(case, "name") else str(case)
    idx = []
    for i, x in enumerate(ev):
        t = x.get("tool") or ""
        if _READ.search(t):
            continue
        txt = _text(x.get("result"))
        tail = "\n".join(txt.splitlines()[-15:])
        if only_nonzero and not (_isf(x, name)[0] or "exited with code" in tail):
            continue
        if pat.search(tail):
            idx.append(i)
    return _collect(ev, idx, window)


ANCHORS = {"outcome": a_outcome,
           "tool": a_tool, "error": a_error, "result_match": a_result_match,
           "event": a_event, "system_prompt": a_system_prompt,
           "final": a_final, "call_digest": a_call_digest, "turns": a_turns}


def slice_for(case_name: str, anchor: dict) -> dict:
    """按锚点截出这条 rubric 需要的那一段轨迹。

    ★锚点命中 0 条 ≠ 掉兜底。它是一个**确定的事实**：
      这条 rubric 关心的那件事在这道题里没有发生。怎么处置由判据规则决定，
      而不是塞一份别的证据给判官凑合。
    """
    case = R / case_name
    ev = _tool_events(str(case))
    kind = anchor["kind"]
    fn = ANCHORS[kind]
    kw = {k: v for k, v in anchor.items() if k != "kind"}
    spans = fn(case, ev, **kw)

    total, kept = 0, []
    for s in spans:
        if total + len(s) > MAX_TOTAL:
            kept.append("⟦⟧")
            break
        kept.append(s)
        total += len(s)
    return {"anchor": anchor, "n_spans": len(spans), "kept_spans": len(kept),
            "chars": total, "text": "\n\n".join(kept),
            "empty": len(spans) == 0}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "M-C2b-gameops-L3-001"
    tests = [
        {"kind": "result_match", "pattern": r"truncat|omitted|省略|截断|compact"},
        {"kind": "error"},
        {"kind": "event", "kind_": None} if False else {"kind": "event", "kind": "compaction"},
        {"kind": "system_prompt"},
        {"kind": "final"},
        {"kind": "tool", "pattern": r"submit"},
    ]
    for a in tests:
        try:
            d = slice_for(name, a)
            print("%-46s 片段 %3d → 留 %3d | %6d 字 %s"
                  % (json.dumps(a, ensure_ascii=False)[:46], d["n_spans"],
                     d["kept_spans"], d["chars"], "★空" if d["empty"] else ""))
        except Exception as e:
            print("%-46s ★失败 %s" % (json.dumps(a, ensure_ascii=False)[:46], str(e)[:50]))
