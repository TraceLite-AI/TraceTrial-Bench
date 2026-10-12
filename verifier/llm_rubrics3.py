# -*- coding: utf-8 -*-
"""语义判据表 v3 = v2 的 37 条，其中被稳定性测试抓到摇摆的条目换成「无条件问句」。

## 和 v2 的差别（只有一种）
摇摆判据的正文里原来带「或本轮没发生…」「不适用：…」这类兜底分支 ——
调研定案它们是最大摇摆源。v3 把兜底全部搬进 gates.py（程序前置门），
这里的问句**没有任何例外、或、N-A** —— 判官看到时，gate 已保证机制确实触发了。

问句都是存在性判断（「有没有 X」），不是程度判断（「够不够好」）——
最不稳的三条（75%/67%/50% 三票分裂）正是程度判断，其中两条已整条降为程序判据
（在 gates.py 里直接出 直接pass/直接fail，不再进判官）。
"""
import sys
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))
import llm_rubrics2 as _V2

COUNTERPART = _V2.COUNTERPART
DIRECTION = _V2.DIRECTION
UNIVERSAL = _V2.UNIVERSAL
prompt_for = _V2.prompt_for

# rid -> (新·判什么[无条件问句], 新·fail的样子, 新锚点或 None=沿用)
_REWRITE = {
    "C.reduction.disclosed": (
        "削减通知里，说清了**削掉的是什么**吗（哪段/哪个文件/哪些内容）",
        "通知只有「truncated/已截断」一类字样，读不出削掉的对象",
        None),
    "C.reduction.recovery_hinted": (
        "削减通知里，给出了**取回被删内容的办法**吗（重读命令、offset/cursor、narrower args 任一）",
        "通知没有任何取回途径，被删内容就此不可达",
        None),
    "C.staleness.perceivable": (
        "只看 harness 生成、模型收到的文本（模型自己写的话不算）：里面明确说了上下文被削减吗",
        "harness 发给模型的文本里没有任何削减说明 —— 模型只能自己猜",
        {"kind": "event", "kind_name": "compact|evict|驱逐", "window": 3}),
    "C.limit.warned": (
        "在第一次硬溢出**之前**，模型收到过容量类提示吗（时间上早于溢出才算）",
        "第一次得知就是硬溢出报错本身，事前零信号",
        {"kind": "event", "kind_name": "overflow|too large|compact", "window": 3}),
    "E.termination.explained": (
        "这次终止的结局区里，读得出**被谁、以什么方式**终止吗（信号名/退出码/超时字样，任一即可）",
        "只报「已结束/exited」，读不出方式",
        {"kind": "outcome", "pattern": r"Terminated|exited with|signal|code 1(24|37)|143", "window": 0}),
    "E.streams.attributed": (
        "在这些非零退出且有输出的调用返回里，错误信息与正常输出**分得开**吗（分段/前缀/字段名，任一即可）",
        "两路混成一段连续文本，无任何分隔标记",
        {"kind": "outcome", "pattern": r".", "window": 0, "only_nonzero": True}),
    "E.background.status_notified": (
        "后台/异步调用的状态（完成/失败/仍在跑）到达模型可见文本了吗",
        "调用发出去之后再无任何回音，也无「已提交/待返回」类状态",
        {"kind": "tool", "pattern": r"^process$", "window": 1}),
    "L.loop.intervention_explained": (
        "模型收到的消息里，说明了这次停止是**被拦下的**吗",
        "动作突然停止，消息流里无任何拦截说明",
        {"kind": "event", "kind_name": "loop", "window": 3}),
    "G.authorization.expiry_signalled": (
        "过期这件事，在失败返回里被**明确写出**了吗（过期字样或剩余时间）",
        "只能从一次普通失败里反推出过期",
        {"kind": "outcome", "pattern": r"expired|过期", "window": 1}),
    "L.goal.revision_traceable": (
        "交付物里的目标与最初声明的目标不一致时，**有变更留痕**吗（目标类工具的调用记录）",
        "目标实质变了而无任何变更留痕",
        None),
    "T.limit.declared_upfront": (
        "对这个被撞上的限制，系统提示或工具说明里**给出过具体数值**吗（字符数/字节数/条目数/页数）",
        "只有定性表述（keep it short / 不要过长），无任何数值",
        {"kind": "system_prompt"}),
    "T.variant.state_exposed": (
        "这些变体工具的状态（deprecated/preview/experimental），在工具名或工具描述里**读得出来**吗",
        "状态只在别处（文档/日志）有，工具面看不出",
        {"kind": "system_prompt"}),
    "O.completion.evidence_citable": (
        "这句完成声明里，**指名了一个可回查的对象**吗（文件路径/编号/回执码/具体计数，任一即可）",
        "只有「已完成/全部处理完毕」这类无指向的表述",
        {"kind": "final", "n": 8}),
    "O.error.source_attributable": (
        "这几类不同来源的错误，呈现给模型的形态**能分辨出来源差异**吗（不同的错误码/字段/前缀/措辞，任一即可）",
        "来源不同的错误呈现形态完全一致，模型无从分辨是谁出的错",
        {"kind": "error", "window": 1}),
    # 降为程序判据的两条不出现在判官表里（gates.py 直接出 直接pass/直接fail）：
    #   O.log.timeline_recoverable / C.reduction.amount_stated
}
_PROGRAM_ONLY = {"O.log.timeline_recoverable", "C.reduction.amount_stated"}

RUBRICS = []
for r in _V2.RUBRICS:
    rid, comp, what, failshape, anchor, on_empty = r
    if rid in _PROGRAM_ONLY:
        continue
    if rid in _REWRITE:
        nw, nf, na = _REWRITE[rid]
        RUBRICS.append((rid, comp, nw, nf, na or anchor, on_empty))
    else:
        RUBRICS.append(r)

BY_COMPONENT = {}
for r in RUBRICS:
    BY_COMPONENT.setdefault(r[1], []).append(r)


def for_case(component: str):
    ids = {r[0] for r in BY_COMPONENT.get(component, [])} | set(UNIVERSAL)
    return [r for r in RUBRICS if r[0] in ids]
