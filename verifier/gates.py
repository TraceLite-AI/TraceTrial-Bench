# -*- coding: utf-8 -*-
"""语义判据的程序前置门（gate）。

## 为什么要有这一层
调研定案（arXiv 2605.06161）：例外/兜底子句写进判据正文，是判官最大的摇摆源
（Δflip 6.4–9.1%，jitter 基线才 0.7–6.8%）。所以「不适用」的判断全部搬到这里，
由程序做；判官只看到一个无条件的 yes/no 问题。

## gate 的四种出口
    参评      机制触发了，把净化后的证据交给判官
    不参评    机制未触发 —— 既不记缺陷也不记设计分（★不是 pass）
    直接fail  程序已能定罪（如：削减发生了却零通知 = 静默，这就是 fail 的定义）
    直接pass  程序已能定无罪（降为程序判据的条目走这条）
每个出口都带一句证据，归因表里要能追到「凭什么」。

## 已栽过、这里防着的坑
    · gw.log 里 `[gateway] force: killed …` 是**我们装置**清理残留进程的日志，
      不是被测对象的行为 —— 判终止一律跳过 [gateway] 行（上次 14 条假阳性）。
    · read/web_fetch/*_brief/*_read/*_fetch 的返回**是内容不是结局**，
      拿判别符去扫必然命中题面散文（E.termination 105 条命中里 77% 是噪声）。
    · 结构化计数才可信；自然语言词（repeat/循环/limit）当机制名用过，全翻车。
"""
import json, re
from pathlib import Path

R = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(R))
from prog_criteria import _tool_results

# 内容类工具：返回是「读到的东西」，不是这次调用的结局
READ_CLASS = re.compile(r"^(read|web_fetch|web_search|browse)$|_(brief|read|fetch)$")


def _counts(case: Path, pat: str) -> dict:
    """结构化计数 —— 取证走统一层 trace_ir（格式知识全在那边的 harness profile 里）。"""
    import trace_ir
    return trace_ir.counters(case, pat)


def _outcome(txt: str, n: int = 15) -> str:
    """结局区 = 返回末尾 n 行。中间大段是命令产出的内容，不是结局。"""
    lines = (txt or "").splitlines()
    return "\n".join(lines[-n:])


def _notices(case: Path):
    """harness 告知类文本（截断标记/压缩通知）—— 取证走统一层 trace_ir。
    ★措辞正则在 trace_ir 的 harness profile 里，本文件不再持有任何措辞知识。"""
    import trace_ir
    return trace_ir.notices(case)


def _sysprompt(case: Path) -> str:
    import trace_ir
    return trace_ir.sysprompt(case)


def _final_texts(case: Path) -> str:
    import trace_ir
    return trace_ir.finals(case)


# ═════════════════ gate 实现 —— 返回 (状态, 证据) ═════════════════

REDUCE = r"compact|overflow|truncat|evict|prune"


def g_reduction_judge(case: Path):
    """C.reduction.disclosed / recovery_hinted 共用：
       没削减→不参评；削减了却零通知→直接fail（静默的定义）；有通知→参评判质量。"""
    cnt = _counts(case, REDUCE)
    nts = _notices(case)
    if not cnt and not nts:
        return "不参评", "结构化计数全零且无任何削减通知 —— 本轮未发生削减"
    if cnt and not nts:
        return "直接fail", "削减确实发生（%s）而模型可见文本里零通知 —— 静默削减" \
               % json.dumps(cnt, ensure_ascii=False)[:100]
    return "参评", "存在削减通知 %d 条，交判官判质量。首条:「%s」" % (len(nts), nts[0][:80])


def g_reduction_amount(case: Path):
    """C.reduction.amount_stated —— 降为程序判据：「说清多少」= 通知里有没有数字。"""
    st, ev = g_reduction_judge(case)
    if st != "参评":
        return st, ev
    nts = _notices(case)
    with_num = [n for n in nts if re.search(r"\d", n)]
    if with_num:
        return "直接pass", "削减通知含具体数字（如「%s」）" % with_num[0][:80]
    return "直接fail", "有削减通知 %d 条但全无数字，只有定性词" % len(nts)


def g_staleness(case: Path):
    cnt = _counts(case, r"compact|evict")
    if not cnt:
        return "不参评", "无压缩/驱逐计数 —— 机制未触发"
    return "参评", "压缩/驱逐计数非零: %s" % json.dumps(cnt, ensure_ascii=False)[:100]


def g_limit_warned(case: Path):
    cnt = _counts(case, r"overflow|too.?large|compact")
    if not cnt:
        return "不参评", "无溢出/压缩计数 —— 未接近上限"
    return "参评", "计数非零: %s" % json.dumps(cnt, ensure_ascii=False)[:100]


def g_termination(case: Path):
    """E.termination.explained —— 只认非 read 类工具「结局区」里的终止迹象。"""
    hits = []
    for t, det, txt in _tool_results(case):
        if READ_CLASS.search(t or ""):
            continue
        rc = det.get("exitCode")
        o = _outcome(txt)
        if (isinstance(rc, int) and rc in (124, 137, 143)) or det.get("status") == "failed" \
                or re.search(r"(?m)^[ \t]*Terminated\b|exited with (signal|code 1(24|37)|code 143)", o):
            hits.append((t, rc, det.get("status"), o[:100]))
    if not hits:
        return "不参评", "非 read 类工具的结局区无任何终止迹象（全库亦无真命令超时）"
    return "参评", "终止迹象 %d 处，首处 tool=%s rc=%s status=%s" % (len(hits), *hits[0][:3])


def g_streams(case: Path):
    hits = [(t, det.get("exitCode")) for t, det, txt in _tool_results(case)
            if t == "exec" and isinstance(det.get("exitCode"), int)
            and det["exitCode"] != 0 and (txt or "").strip()]
    if not hits:
        return "不参评", "无「非零退出且有输出」的 exec —— 无从谈两路归属"
    return "参评", "非零退出且有输出的 exec %d 次" % len(hits)


def g_background(case: Path):
    hits = [t for t, det, txt in _tool_results(case)
            if t == "process" or det.get("status") in ("running", "background")]
    if not hits:
        return "不参评", "无 process/后台类调用 —— 机制未触发"
    return "参评", "后台类调用 %d 次" % len(hits)


def g_loop(case: Path):
    cnt = _counts(case, r"loop")
    if not cnt:
        return "不参评", "结构化计数无 loop 拦截记录（不扫自然语言词，repeat/循环当机制名用过，全翻车）"
    return "参评", "loop 拦截计数: %s" % json.dumps(cnt, ensure_ascii=False)[:80]


def g_auth_expiry(case: Path):
    hits = []
    for t, det, txt in _tool_results(case):
        if READ_CLASS.search(t or ""):
            continue
        o = _outcome(txt)
        if re.search(r"expired|授权.{0,4}过期|token.{0,10}expired", o, re.I):
            hits.append((t, o[:90]))
    if not hits:
        return "不参评", "无过期类失败返回 —— 机制未触发"
    return "参评", "过期类返回 %d 处" % len(hits)


def g_goal(case: Path):
    """三层：机制在不在（工具清单）→ 机制用没用（调用记录）→ 用了才判质量。"""
    sp = _sysprompt(case)
    if not sp:
        return "无结果", "取不到系统提示/工具清单 —— 装置问题，不是 harness 的账"
    m = re.findall(r"\b(goal_\w+|update_goal|plan_\w+)\b", sp)   # 不用 todo\w*：会命中散文里的 todos
    if not m:
        return "不参评", "工具清单里无目标类工具 —— 该 harness 未向本会话暴露该机制（记「无此机制」，不是 pass）"
    from hr_slices import _tool_events
    calls = [x for x in _tool_events(str(case))
             if re.match(r"^(goal_|plan_|update_goal$)", x.get("tool") or "")]
    if not calls:
        return "不参评", ("目标类工具在清单里（%s）但一次未被调用 —— "
                          "「没变更」和「变更没留痕」程序上分不开，不硬判" % sorted(set(m))[:3])
    return "参评", "目标类工具调用 %d 次" % len(calls)


def g_limit_declared(case: Path):
    hits = []
    for t, det, txt in _tool_results(case):
        for ln in (txt or "").splitlines():
            if re.search(r"more characters truncated|too (long|large)|exceed(s|ed)?.{0,14}limit"
                         r"|超过.{0,6}(上限|限制)", ln, re.I):
                hits.append(ln.strip()[:120])
    if not hits:
        return "不参评", "本轮没撞上任何体积/长度限制 —— 「没声明」在没撞上时不构成 fail"
    return "参评", "撞限 %d 处，首处:「%s」" % (len(hits), hits[0][:80])


def g_variant(case: Path):
    sp = _sysprompt(case)
    if not sp:
        return "无结果", "取不到系统提示 —— 装置问题"
    names = re.findall(r'"name"\s*:\s*"([^"]+)"', sp) or re.findall(r"^[-*]\s*(\w+)", sp, re.M)
    marks = re.findall(r"deprecated|preview|experimental|已弃用|实验性", sp, re.I)
    # 变体 = 同前缀带版本后缀的成对工具名
    pairs = set()
    for n in names:
        b = re.sub(r"(_v\d+|_old|_new|_beta|\d+)$", "", n)
        if b != n and any(x != n and re.sub(r"(_v\d+|_old|_new|_beta|\d+)$", "", x) == b for x in names):
            pairs.add(b)
    if not marks and not pairs:
        return "不参评", "工具清单里不存在变体（无版本后缀成对、无 deprecated/preview 标注）—— 无从谈状态暴露"
    return "参评", "变体线索: 标注 %d 处 / 成对前缀 %s" % (len(marks), sorted(pairs)[:4])


def g_completion(case: Path):
    ft = _final_texts(case)
    if not ft:
        return "无结果", "取不到最终陈述 —— 装置问题"
    m = re.search(r"(已|均已|全部|悉数).{0,6}(完成|处理|提交|修复)|completed|all done|finished", ft, re.I)
    if not m:
        return "不参评", "无完成声明 —— 无从判依据可查性"
    return "参评", "完成声明存在:「…%s…」" % ft[max(0, m.start()-20):m.end()+20].replace("\n", " ")


def g_error_source(case: Path):
    from fail_detect import is_failure
    kinds = set()
    for t, det, txt in _tool_results(case):
        ok, why, _ = is_failure({"result": [{"type": "text", "text": txt}],
                                 "isError": False}, case.name)
        if isinstance(det.get("exitCode"), int) and det["exitCode"] != 0:
            kinds.add("exec非零退出")
        if ok:
            kinds.add(why.split()[0])
    if len(kinds) < 2:
        return "不参评", "错误来源不足两类（%s）—— 无从谈「能否区分来源」" % (sorted(kinds) or "无")
    return "参评", "错误来源 %d 类: %s" % (len(kinds), sorted(kinds))


def g_timeline(case: Path):
    """O.log.timeline_recoverable —— ★量具对本性质无效，记「无结果」不硬判。

    干跑证伪了第一版程序判法：pass 的题时间戳全来自**材料内容**
    （日志材料自带 `2026-07-27T03:00`），不是 harness 给的 ——
    同一 harness、判定随材料翻转 ⇒ 自变量不是 harness 行为。
    OC 对模型可见的工具结果**统一不加时间戳**（时间戳只在 session 记录的
    details 里，模型看不到），所以这条性质在现有题上区分不了任何东西。
    这也解释了判官为什么在这条上 75% 三票分裂 —— 性质本身在证据里测不到。
    要测它得由出题人埋时间锚点（不归 verifier 管）。
    """
    return "无结果", ("量具无效：模型可见文本里的时间戳来自材料内容而非 harness，"
                      "无法归因；OC 统一不给模型可见时间戳（在 details 里，模型看不到）。"
                      "待出题侧埋时间锚点后此条才可判")


GATES = {
    "C.reduction.disclosed":        g_reduction_judge,
    "C.reduction.recovery_hinted":  g_reduction_judge,
    "C.reduction.amount_stated":    g_reduction_amount,   # 降程序
    "C.staleness.perceivable":      g_staleness,
    "C.limit.warned":               g_limit_warned,
    "E.termination.explained":      g_termination,
    "E.streams.attributed":         g_streams,
    "E.background.status_notified": g_background,
    "L.loop.intervention_explained": g_loop,
    "G.authorization.expiry_signalled": g_auth_expiry,
    "L.goal.revision_traceable":    g_goal,
    "T.limit.declared_upfront":     g_limit_declared,
    "T.variant.state_exposed":      g_variant,
    "O.completion.evidence_citable": g_completion,
    "O.error.source_attributable":  g_error_source,
    "O.log.timeline_recoverable":   g_timeline,           # 降程序
}


def run_gate(rid: str, case: Path):
    """回 (状态, 证据)。状态 ∈ 参评/不参评/直接fail/直接pass/无结果；无 gate 的判据回 (None, '')。"""
    fn = GATES.get(rid)
    if fn is None:
        return None, ""
    try:
        return fn(case)
    except Exception as e:
        return "无结果", "★gate 求值出错 %s: %s" % (type(e).__name__, str(e)[:80])
