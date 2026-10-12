# -*- coding: utf-8 -*-
"""程序判据库 —— 可复用的 harness 观测，题目在 rubric_harness.yaml 里按 id 声明即生效。

## 为什么做成库而不是每题手写
分配表里程序侧有 44 条判据、93 处，同一条在多道题重复出现
（`writes_persisted` 4 道、`exit_code_delivered` 3 道…）。
每题手写一份必然漂移；做成库，一条实现服务所有声明它的题。

## 每条判据的契约
    def 判据(case: Path) -> (值, 证据)
      值 = True(pass) / False(fail) / None(不参评：机制未触发)
      证据 = 一句话，写明凭什么这么判 —— 归因表里要能追到这句
★None 不是 pass。机制没触发就是没测到，不该记设计分也不该记缺陷。
"""
import json, re
from pathlib import Path

# ── 取材：模型可见的工具结果 + harness 侧的结构化字段 ──
_VISIBLE_RC = re.compile(r"exited with code\s*(\d+)|exit(?:ed)?[ _]?code[:= ]*(\d+)"
                         r"|退出码[:= ]*(\d+)|\bEXIT[:= ]*(\d+)", re.I)


def _tool_results(case: Path):
    """回 [(toolName, outcome, content 全文)] —— ★取证走统一层 trace_ir，
    本文件不再知道任何 harness 的 trace 格式（收口见 trace_ir.py 文件头）。"""
    import trace_ir
    return trace_ir.tool_results(Path(case))


# ═══════════════════════════ 判据实现 ═══════════════════════════

def exit_code_visible_to_model(case: Path):
    """E1a 退出码保真：非零退出码有没有到达**模型可见的 content**。

    gate：本轮存在 details.exitCode 非零的调用。
    ★复合命令（`x.py; echo $?`）把失败屏蔽成 0 是 shell 语义正确、且由模型写法造成，
      exitCode==0 自然不进 gate —— 不需要额外过滤（多写过滤反而制造过假阴性）。
    """
    nz = [(t, det["exitCode"], txt) for t, det, txt in _tool_results(case)
          if isinstance(det.get("exitCode"), int) and det["exitCode"] != 0]
    if not nz:
        return None, "本轮无非零退出码，该机制未触发"
    bad = []
    for t, rc, txt in nz:
        m = _VISIBLE_RC.search(txt)
        if (m and str(rc) in m.group(0)) or re.search(r"\b%d\b" % rc, txt):
            continue
        bad.append((t, rc, txt[:120].replace("\n", " ")))
    if not bad:
        return True, "非零退出 %d 次，全部以明文出现在模型可见 content（如 `(Command exited with code %s)`）" \
                     % (len(nz), nz[0][1])
    t, rc, frag = bad[0]
    return False, "非零退出 %d 次，其中 %d 次模型面读不出。首例 tool=%s rc=%s content=「%s」" \
                  % (len(nz), len(bad), t, rc, frag)


def exec_output_survives_truncation(case: Path):
    """截断有没有吃掉结局判别符。

    判的不是「有没有截断」（截断本身正常，且 OpenClaw 会告知），
    而是**截断之后退出码还读不读得出** —— 结局判别符是结果里最要紧的一段，
    应当优先于内容保留。H1 的 rc=7 就是被截断吃掉的。
    """
    hits = [(det.get("exitCode"), txt) for t, det, txt in _tool_results(case)
            if re.search(r"more characters truncated|\[\.\.\.", txt or "")]
    if not hits:
        return None, "本轮没有被截断的工具结果，该机制未触发"
    lost = [rc for rc, txt in hits
            if isinstance(rc, int) and rc != 0 and not re.search(r"\b%d\b" % rc, txt)]
    if not lost:
        return True, "被截断的结果 %d 条，非零退出码均未被截掉" % len(hits)
    return False, "被截断的结果 %d 条，其中 %d 条把非零退出码一并截掉（rc=%s），模型无从判断成败" \
                  % (len(hits), len(lost), lost[0])


REGISTRY = {
    "E1a.exit_code_visible_to_model": exit_code_visible_to_model,
    "E1a.exec_output_survives_truncation": exec_output_survives_truncation,
}


def evaluate(case: Path, ids):
    """按 id 列表求值，回 {id: (值, 证据)}。未注册的 id 报错而不静默跳过。"""
    out = {}
    for i in ids:
        fn = REGISTRY.get(i)
        if fn is None:
            out[i] = (None, "★程序判据库里没有 %s —— 声明了但没实现" % i)
            continue
        try:
            out[i] = fn(Path(case))
        except Exception as e:
            out[i] = (None, "★求值出错 %s: %s" % (type(e).__name__, str(e)[:80]))
    return out
