"""N 系列共用: 从 runner 传进来的会话文件里取「模型说了什么、调了哪些工具」。
这些是 HR 层的客观事实来源, 不在这里下判断 —— 判断交给两层判官。"""
import json, os, re
from pathlib import Path


def session_facts():
    """返回 (助手全文, 工具调用名列表, 工具结果文本列表)。取不到就给空, 由上层标 N-A。"""
    f = os.environ.get("HARNESSBENCH_PARENT_SESSION_FILE", "")
    text, calls, results = [], [], []
    if not f or not Path(f).is_file():
        return "", [], [], False
    for line in Path(f).read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        for o in _walk(e):
            if not isinstance(o, dict):
                continue
            t = o.get("type") or o.get("role")
            if t in ("assistant", "text") and isinstance(o.get("text"), str):
                text.append(o["text"])
            elif isinstance(o.get("content"), str) and t == "assistant":
                text.append(o["content"])
            if o.get("type") == "tool_use" or "toolName" in o or "tool_name" in o:
                nm = o.get("name") or o.get("toolName") or o.get("tool_name")
                if isinstance(nm, str):
                    calls.append(nm)
            if o.get("type") == "tool_result":
                c = o.get("content")
                results.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:4000])
    return "\n".join(text), calls, results, True


def _walk(o):
    yield o
    if isinstance(o, dict):
        for v in o.values():
            for x in _walk(v):
                yield x
    elif isinstance(o, list):
        for v in o:
            for x in _walk(v):
                yield x


def gw_counts(here):
    g = here.parent / "gw.log"
    if not g.is_file():
        return {}
    s = g.read_text(errors="ignore")
    return {k: len(re.findall(p, s, re.I)) for k, p in (
        ("overflow", r"context overflow detected"), ("compact_ok", r"auto-compaction succeeded"),
        ("blocked", r"already_compacted_recently"), ("hard", r"prompt too large"),
        ("truncate", r"truncate_tool_results"))}


def load_json(p):
    if not p.exists():
        return None, "文件不存在"
    try:
        return json.loads(p.read_text(errors="ignore")), ""
    except Exception as e:
        return None, "解析失败: %s" % e


def pick(obj, keys):
    """交付物形状容错: 顶层直接是 {id: {...}}, 或包在 items/data/result 里, 或列表带 id 字段。"""
    if not isinstance(obj, (dict, list)):
        return {}
    out = {}
    def rec(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in keys and isinstance(v, dict):
                    out.setdefault(k, v)
                else:
                    rec(v)
            i = o.get("id") or o.get("service") or o.get("dep") or o.get("subscription")
            if isinstance(i, str) and i in keys:
                out.setdefault(i, o)
        elif isinstance(o, list):
            for v in o:
                rec(v)
    rec(obj)
    return out
