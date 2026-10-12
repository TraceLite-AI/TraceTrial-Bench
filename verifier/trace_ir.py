# -*- coding: utf-8 -*-
"""统一取证层（IR）—— verifier 里**唯一**允许知道 harness trace 格式的地方。

## 为什么收口
此前三个读取器（hr_slices / prog_criteria / verify_all_v2）各自解析 OC 会话格式，
截断标记措辞、退出码字段路径、工具名族散落在 gates/anchors 的正则里 ——
换一个 harness 要改一片，而且漏改一处不会报错，只会静默给错数。

## 契约：8 个访问器，所有消费方只准调这里
    calls(case)            调用序列（锚点层形状：{i,tool,args,result,isError}）
    tool_results(case)     [(tool, outcome{exitCode,status}, 模型可见文本)]
    counters(case, pat)    结构化计数（压缩/溢出/截断/loop）——只信 JSON 不扫日志散文
    notices(case)          harness 告知类文本（截断标记/压缩通知），命中点为中心取窗
    sysprompt(case)        系统提示 + 工具清单文本
    finals(case)           模型最终陈述
    event_files(case)      harness 事件文件列表（a_event 锚点扫这些）
    scorer_session_file(case)  给题内 score.py 的会话文件（多会话合并）

## 后端规则
    · OC 后端 = 现有读取逻辑**原样委托**（hr_slices/anchors 的实现就是 OC 后端的
      组成部分），保证收口前后输出逐字节相同 —— 收口不许改变任何判定。
    · 新 harness 照 PROFILE + Backend 填。★不凭记忆编格式：
      必须拿一份真 trace 对着写（Hermes 后端现在是带说明的占位，喂它会显式报错，
      不会静默给「不参评」）。
"""
import json, re, sys
from pathlib import Path

R = Path(__file__).resolve().parent
sys.path.insert(0, str(R))

# ───────────────────────── harness profile ─────────────────────────
# 措辞/字段路径/工具名族 —— 每个 harness 一份，正则不许出现在本文件之外。
OC_PROFILE = {
    "name": "openclaw",
    # 截断标记：OC 把它贴在被截长行末尾，含被截字符数
    "truncation_marker": re.compile(r"more characters truncated|rerun with narrower", re.I),
    # 压缩通知：出现在非 assistant 角色消息里才算 harness 告知（模型自述不算）
    "compaction_notice": re.compile(r"compacted|compaction summary|上下文已压缩|\[Conversation compacted", re.I),
    # 工具名族
    "content_tools": re.compile(r"^(read|web_fetch|web_search|browse)$|_(brief|read|fetch)$"),
    "exec_tools": re.compile(r"^exec$"),
    "background_tools": re.compile(r"^process$"),
    "goal_tools": re.compile(r"^(goal_|plan_|update_goal$)"),
    # 结构化计数与事件文件
    "counter_files": ("gw.json", "harness_events.json"),
    "event_files": ("gw.log", "gw.json", "harness_events.json"),
}


class OCBackend:
    """OpenClaw 后端 —— 委托现有已验证的读取实现，不重写。"""
    profile = OC_PROFILE

    @staticmethod
    def detect(case: Path) -> bool:
        return any(case.glob("state/agents/*/sessions/*.jsonl"))

    # ---- 调用序列（锚点层形状）----
    @staticmethod
    def calls(case: Path):
        from hr_slices import _tool_events            # OC 会话解析的既有实现
        return _tool_events(str(case))

    # ---- (tool, outcome, 模型可见文本) ----
    @staticmethod
    def tool_results(case: Path):
        out = []
        for f in case.glob("state/agents/*/sessions/*.jsonl"):
            if f.name.endswith(".trajectory.jsonl"):
                continue
            for line in f.read_text(errors="ignore").splitlines():
                line = line.strip()
                if not line or '"toolResult"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message") or {}
                if m.get("role") != "toolResult":
                    continue
                txt = "\n".join(x.get("text", "") for x in (m.get("content") or [])
                                if isinstance(x, dict))
                out.append((m.get("toolName"), m.get("details") or {}, txt))
        return out

    # ---- 结构化计数 ----
    @staticmethod
    def counters(case: Path, pat: str) -> dict:
        p_re = re.compile(pat, re.I)
        out = {}
        for fn in OC_PROFILE["counter_files"]:
            p = case / fn
            if not p.is_file():
                continue
            try:
                d = json.loads(p.read_text(errors="ignore"))
            except Exception:
                continue
            stack = [(d, "")]
            while stack:
                x, pre = stack.pop()
                if isinstance(x, dict):
                    for k, v in x.items():
                        if isinstance(v, (int, float)) and v and p_re.search(str(k)):
                            out[pre + k] = v
                        elif isinstance(v, (dict, list)):
                            stack.append((v, pre + k + "."))
                elif isinstance(x, list):
                    for i in x:
                        stack.append((i, pre))
        return out

    # ---- harness 告知类文本 ----
    @staticmethod
    def notices(case: Path):
        out = []
        marker = OC_PROFILE["truncation_marker"]
        for t, det, txt in OCBackend.tool_results(case):
            for ln in (txt or "").splitlines():
                m = marker.search(ln)
                if m:
                    # 命中点为中心取窗：OC 标记贴在长行末尾，存行首会把数字切掉（踩过）
                    out.append(ln[max(0, m.start() - 60):m.end() + 60].strip()[:200])
        comp = OC_PROFILE["compaction_notice"]
        for f in case.glob("state/agents/*/sessions/*.jsonl"):
            if f.name.endswith(".trajectory.jsonl"):
                continue
            for line in f.read_text(errors="ignore").splitlines():
                if "compact" not in line.lower():
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get("message") or {}
                if m.get("role") == "assistant":       # 模型自述不算 harness 告知
                    continue
                txt = "\n".join(x.get("text", "") for x in (m.get("content") or [])
                                if isinstance(x, dict)) if isinstance(m.get("content"), list) \
                      else str(m.get("content") or "")
                mm = comp.search(txt)
                if mm:
                    out.append(("压缩通知: " + txt[max(0, mm.start() - 30):mm.end() + 80]).strip()[:160])
        return out

    # ---- 系统提示 / 最终陈述（委托 anchors 的既有 OC 实现）----
    @staticmethod
    def sysprompt(case: Path) -> str:
        import anchors as ANC
        try:
            return ANC.slice_for(case.name, {"kind": "system_prompt"}).get("text") or ""
        except Exception:
            return ""

    @staticmethod
    def finals(case: Path) -> str:
        import anchors as ANC
        try:
            return ANC.slice_for(case.name, {"kind": "final", "n": 10}).get("text") or ""
        except Exception:
            return ""

    @staticmethod
    def event_files(case: Path):
        return [case / fn for fn in OC_PROFILE["event_files"] if (case / fn).is_file()]

    @staticmethod
    def scorer_session_file(case: Path):
        from verify_all_v2 import session_file       # 多会话合并的既有实现
        return session_file(case)


class HermesBackend:
    """★占位：等一份真 Hermes trace 再填（不凭记忆编格式）。

    需要从真 trace 里确认后写进 HERMES_PROFILE 的东西：
      会话文件路径与 JSON 形状 / 退出码字段路径 / 截断标记措辞 /
      压缩通知措辞与角色 / 工具名族（exec类/后台类/目标类/内容类）/
      hook 产物里哪几个键当结构化计数（Hermes 有 23 种 hook kinds，够用）
    """
    profile = {"name": "hermes"}

    @staticmethod
    def detect(case: Path) -> bool:
        return False                                  # 有真 trace 之前永不匹配

    def __getattr__(self, _):
        raise NotImplementedError(
            "Hermes 后端未实现：需要一份真 Hermes trace 对着填 PROFILE，"
            "拒绝静默给「不参评」")


_BACKENDS = [OCBackend, HermesBackend]


def backend_for(case: Path):
    for b in _BACKENDS:
        if b.detect(case):
            return b
    raise RuntimeError("识别不出 %s 是哪个 harness 的 trace（不猜，显式报错）" % case)


# ───────────── 消费方只准用下面这 8 个 ─────────────
def calls(case: Path):
    return backend_for(case).calls(case)


def tool_results(case: Path):
    return backend_for(case).tool_results(case)


def counters(case: Path, pat: str) -> dict:
    return backend_for(case).counters(case, pat)


def notices(case: Path):
    return backend_for(case).notices(case)


def sysprompt(case: Path) -> str:
    return backend_for(case).sysprompt(case)


def finals(case: Path) -> str:
    return backend_for(case).finals(case)


def event_files(case: Path):
    return backend_for(case).event_files(case)


def scorer_session_file(case: Path):
    return backend_for(case).scorer_session_file(case)


def profile(case: Path) -> dict:
    return backend_for(case).profile


# ═════════════ wire 通道：API 流量捕获（harness 无关）═════════════
# api_tap.py 落盘的 api_capture.jsonl。「模型看到了什么」以此为权威 ——
# harness 伪造不了发给模型 API 的请求。有捕获文件才启用；没有时一切照旧
# （现有判定不受影响）。新 harness 配上 tap 后，下面这些不需要任何后端代码。

def has_wire(case: Path) -> bool:
    return (case / "api_capture.jsonl").is_file()


def wire_exchanges(case: Path):
    """全部 API 交换，按发生顺序。"""
    out = []
    p = case / "api_capture.jsonl"
    if not p.is_file():
        return out
    for line in p.read_text(errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return sorted(out, key=lambda r: r.get("seq", 0))


def wire_sysprompt(case: Path) -> str:
    """系统提示 + 工具清单 —— 取自第一次请求原文（模型可见面的权威）。"""
    for r in wire_exchanges(case):
        req = r.get("request") or {}
        parts = []
        if req.get("system"):
            parts.append(str(req["system"]))
        for m in (req.get("messages") or []):
            if m.get("role") == "system":
                parts.append(str(m.get("content")))
        if req.get("tools"):
            parts.append(json.dumps(req["tools"], ensure_ascii=False))
        if parts:
            return "\n".join(parts)
    return ""


def wire_finals(case: Path, n: int = 6) -> str:
    """模型最终陈述 —— 最后 n 条 assistant 产出。"""
    texts = []
    for r in wire_exchanges(case):
        resp = r.get("response") or {}
        t = resp.get("text") or ((resp.get("json") or {}).get("choices") or [{}])[0] \
            .get("message", {}).get("content")
        if t:
            texts.append(t)
    return "\n---\n".join(texts[-n:])


def wire_reduction_events(case: Path, drop: float = 0.30):
    """削减事件 —— 相邻请求的上下文骤降，**直接观测**，不必信 harness 自报计数。

    量法：每次请求 messages 的总字符量。第 k+1 次比第 k 次缩水超过 drop
    （且绝对量 > 4KB，滤掉短对话的正常波动）即记一次削减事件。
    """
    sizes = []
    for r in wire_exchanges(case):
        msgs = (r.get("request") or {}).get("messages") or []
        sizes.append(sum(len(json.dumps(m, ensure_ascii=False)) for m in msgs))
    ev = []
    for i in range(1, len(sizes)):
        if sizes[i - 1] > 4096 and sizes[i] < sizes[i - 1] * (1 - drop):
            ev.append({"at_request": i + 1, "before_chars": sizes[i - 1],
                       "after_chars": sizes[i]})
    return ev
