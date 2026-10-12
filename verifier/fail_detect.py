# -*- coding: utf-8 -*-
"""失败返回的**窄**检测器 —— 给 a_error 锚点用，替掉只看 isError 的做法。

## 为什么要换
`isError` 只有 9/57 道题设过。真实形态是：**失败以正常结果送达**
（M-L2a 的 `wf_do` 返回 `RETRY_LATER item=M03 transient error, attempt=1`，isError=False）。
只看 flag 的锚点抓不到这类，于是 `E.failure.reason_legible` / `O.error.source_attributable`
被静默记成 pass。

## 为什么不能用宽正则
我先试过宽扫（`\berror\b|failed|denied|...`），假阳性极多，实测三类：
  1. **路径名**  —— "wrote 3862 bytes to .../N3-tool-**denied**-L3-001/..." 命中 denied
  2. **题面材料** —— "代付 M08 要出现在这种载体里：错误信息里回显" 命中 错误
  3. **文档内容** —— changelog 里 "reports the offending value in its error message"
共同点：命中的词出现在**散文中间**，不是这次调用的结局判别符。

## 窄规则：只认「结局判别符」
一条返回算失败，当且仅当满足任一：
  A. isError 为真
  B. 结果里有**结构化**的失败字段：exit_code/status/code 非零或为 error 类值
  C. 失败判别符出现在**行首**（或整条结果的开头），即它是这次调用的结论，
     而不是被引用的内容。判别符本身取全大写协议词与固定前缀，不取自然语言词。
★并且：先把 case 目录名从文本里剔掉，杜绝路径自命中。
"""
import json, re
from pathlib import Path

# 行首判别符：全大写协议词 + 固定前缀。刻意不含 error/failed 这类自然语言词。
# ★用 [ \t]* 不用 \s* —— \s* 配 re.M 能跨换行，会让匹配起点落在空行上，
#   截出来的「行」是空的（M-O3b 的两条 ENOENT 就这样给出了空证据片段）。
LEAD = re.compile(
    r"^[ \t]*(?:"
    r"RETRY_LATER|PERMANENT_FAILURE|PERMANENT_ERROR|TRANSIENT_ERROR|RATE_LIMIT"
    r"|DENIED|BLOCKED|FORBIDDEN|UNAUTHORIZED|TIMEOUT|TIMED_OUT|KILLED|CANCELLED"
    r"|NOT_FOUND|UNAVAILABLE|UNKNOWN_TOOL|INVALID_ARGUMENT|MALFORMED"
    r"|Error|Exception|Traceback|FATAL|PANIC"
    r"|command not found|No such file or directory|Permission denied"
    r"|操作被拒绝|工具不可用|执行超时"
    r")\b",
    re.M)

# 结构化字段：键名 -> 什么值算失败
STRUCT = {
    "exit_code": lambda v: isinstance(v, int) and v != 0,
    "exitCode": lambda v: isinstance(v, int) and v != 0,
    "returncode": lambda v: isinstance(v, int) and v != 0,
    "status": lambda v: isinstance(v, str) and v.lower() in
              ("error", "failed", "failure", "denied", "timeout", "killed"),
    "ok": lambda v: v is False,
    "success": lambda v: v is False,
    "error": lambda v: bool(v),
}


def _text(r) -> str:
    if r is None:
        return ""
    if isinstance(r, str):
        return r
    if isinstance(r, list):
        out = []
        for it in r:
            if isinstance(it, dict) and isinstance(it.get("text"), str):
                out.append(it["text"])
            else:
                out.append(json.dumps(it, ensure_ascii=False))
        return "\n".join(out)
    return json.dumps(r, ensure_ascii=False)


def _struct_fail(r):
    """在结果的 dict 层找结构化失败字段。回 (是否失败, 命中的字段说明)。"""
    stack = [r]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            for k, v in x.items():
                f = STRUCT.get(k)
                if f:
                    try:
                        if f(v):
                            return True, "%s=%r" % (k, v)
                    except Exception:
                        pass
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(x, list):
            stack.extend(x)
    return False, ""


def is_failure(call: dict, case_name: str = ""):
    """回 (是否失败, 依据, 命中的原文片段)。"""
    if call.get("isError"):
        return True, "isError 标记", _text(call.get("result"))[:120]
    ok, why = _struct_fail(call.get("result"))
    if ok:
        return True, "结构化字段 " + why, _text(call.get("result"))[:120]
    t = _text(call.get("result"))
    if case_name:
        t = t.replace(case_name, "⟦case⟧")          # ★剔掉路径自命中
    m = LEAD.search(t)
    if m:
        line = t[m.start():t.find("\n", m.start()) if t.find("\n", m.start()) > 0 else len(t)]
        return True, "行首判别符 " + m.group(0).strip(), line[:120]
    return False, "", ""
