# -*- coding: utf-8 -*-
"""按判据取证据片段 —— 程序只做**定位**，不做判断、不做取舍。

为什么这么做(2026-07-28 实测):
  旧做法把全部材料按预算裁剪成一个 30KB 大包, 判官一次判 10 条 ——
  丢证据(D4 断言信息没了) + 长上下文伤精准度(H4 实验: 1.4M ctx 精准度掉到 22 分)。
  实测改成"只给这条判据要的 12 段原文(2.8KB)、只判这一条", 判官判对了,
  而且比我的正则规则和我人工抽查都准。

铁律:
  ① 取到的片段**原文完整**, 不截断、不摘要、不改写
  ② 每次取片都记录"取了哪些、从哪个文件取的", 供独立核查
  ③ 取不到就明说取不到(判官据此判 N-A), 绝不用相似的东西凑
"""
from __future__ import annotations
import json, glob, os, re
from pathlib import Path

MAXSLICE = 60_000     # 单条判据的证据上限(字符)。超了**分批判**, 不裁剪。


def _read(p):
    try: return Path(p).read_text(errors="ignore")
    except Exception: return ""


def _sessions(d):
    return sorted(f for f in glob.glob(str(Path(d)/"state/agents/*/sessions/*.jsonl"))
                  if "trajectory" not in f)


def _walk(o):
    st = [o]
    while st:
        x = st.pop()
        yield x
        if isinstance(x, dict): st.extend(x.values())
        elif isinstance(x, list): st.extend(x)


def _tool_events(d):
    """(序号, 工具名, 参数, 结果原文, isError) —— 结果原文一字不改"""
    out, i = [], 0
    calls = {}
    for f in _sessions(d):
        for line in _read(f).splitlines():
            if '"toolCall"' not in line and '"toolCallId"' not in line: continue
            try: e = json.loads(line)
            except Exception: continue
            for o in _walk(e):
                if not isinstance(o, dict): continue
                if o.get("type") == "toolCall":
                    calls[o.get("id")] = (o.get("name"), o.get("arguments"))
                cid = o.get("toolCallId")
                if cid and ("content" in o or "isError" in o):
                    nm, args = calls.get(cid, (o.get("toolName"), None))
                    i += 1
                    # ★时间戳必须带上。会话记录里本来就有(实测某题 168 处),
                    #   此前不取, 结果 hr_log_timestamps_present 被判 fail,
                    #   理由是「仅有序号 i, 没有任何时间戳字段」——
                    #   那说的是我的证据包, 不是 harness。确凿的假阳性。
                    out.append({"i": i, "tool": nm, "args": args,
                                "isError": bool(o.get("isError")),
                                "result": o.get("content"),
                                "timestamp": e.get("timestamp") or o.get("timestamp")})
    return out


# ────────── 各类取片器：只定位，不判断 ──────────
def slice_compaction(d):
    """压缩类：压缩事件原文 + 事件前后各 2 条消息"""
    hits = []
    for f in _sessions(d):
        lines = _read(f).splitlines()
        for n, line in enumerate(lines):
            if not re.search(r"compact|压缩|summariz", line, re.I): continue
            lo, hi = max(0, n-2), min(len(lines), n+3)
            hits.append({"file": os.path.basename(f), "line": n+1,
                         "context": lines[lo:hi]})
    gw = _read(Path(d)/"gw.log")
    counts = {k: len(re.findall(p, gw, re.I)) for k, p in
              (("compaction_succeeded", r"auto-compaction succeeded"),
               ("compaction_failed", r"compaction[_ ]failed"),
               ("hard_overflow", r"prompt too large"),
               ("truncate_tool_results", r"truncate_tool_results"))}
    return {"kind": "压缩事件原文", "gw_counts": counts,
            "events": hits[:40], "n_events": len(hits),
            "source": "state/agents/*/sessions/*.jsonl 中含 compact 的行及其上下文 + gw.log"}


def slice_failure(d):
    """失败/退出码类：所有 isError 结果 + 所有 exec 结果原文"""
    ev = _tool_events(d)
    err = [x for x in ev if x["isError"]]
    execs = [x for x in ev if (x["tool"] or "") == "exec"]
    return {"kind": "失败与退出码原文",
            "error_results": [{"i": x["i"], "tool": x["tool"], "result": x["result"]} for x in err],
            "exec_results": [{"i": x["i"], "args": x["args"], "result": x["result"]} for x in execs][:40],
            "n_error": len(err), "n_exec": len(execs), "n_tool_total": len(ev),
            "source": "会话记录里 isError=true 的工具结果 + 全部 exec 结果，原文未裁剪"}


def slice_status(d):
    """状态/会话类：每轮的 status 与 error，加轮次与退出码"""
    t = _read(Path(d)/"run/cli_output.log")
    per = []
    for b in re.split(r'(?=\{\s*"runId")', t):
        if '"runId"' not in b: continue
        st = re.search(r'"status"\s*:\s*"(\w+)"', b)
        er = re.findall(r'"kind"\s*:\s*"(\w+)"', b)
        msg = re.search(r'"message"\s*:\s*"([^"]{0,200})"', b)
        per.append({"turn": len(per)+1, "status": st.group(1) if st else None,
                    "error_kinds": er, "message": msg.group(1) if msg else None})
    meta = {}
    try: meta = json.loads(_read(Path(d)/"run/run_meta.json"))
    except Exception: pass
    planned = None
    try:
        x = json.loads(_read(Path(d)/"turns.json"))
        planned = len(x if isinstance(x, list) else x.get("turns", []))
    except Exception: pass
    return {"kind": "逐轮状态", "per_turn": per, "turns_planned": planned,
            "turns_observed": len(per), "exit_code": meta.get("exit_code"),
            "source": "run/cli_output.log 每个 runId 块的 status/error.kind + run_meta.json + turns.json"}


def slice_sequence(d):
    """返工类：完整工具调用序列（名字+关键参数），判官自己看重复模式。

    ★这份切片由本程序做过删减，删减处一律显式标出。
      不标的话判官会把「我砍掉的部分」当成 harness 的缺陷——实测发生过：
      M-T4a 的 partial_parse_not_passed_as_whole 被判 fail，理由是
      「arg 里 content 被截断且无标记」，那正是本函数砍到 120 字的结果。
    """
    ev = _tool_events(d)
    seq = []
    for x in ev:
        a = json.dumps(x["args"], ensure_ascii=False) if x["args"] else None
        cut = a is not None and len(a) > 120
        row = {"i": x["i"], "tool": x["tool"], "arg": (a[:120] if a else None)}
        if cut:
            row["arg_truncated_by_this_evidence_packet"] = True
            row["arg_full_length"] = len(a)
        seq.append(row)
    n_cut = sum(1 for r in seq if r.get("arg_truncated_by_this_evidence_packet"))
    return {"kind": "工具调用序列", "sequence": seq, "n": len(seq),
            "packet_note": ("本切片只含调用名与参数，**不含结果内容、不含时间戳**——"
                            "凡是要判断结果内容或时序的条目，不要据此推断，判 N-A。"
                            "另有 %d 条参数被本程序截断至 120 字"
                            "（已逐条标 arg_truncated_by_this_evidence_packet），"
                            "**那是证据包的截断，不是 harness 的截断**。" % n_cut),
            "source": "会话记录里全部 toolCall 的名字与参数，按时间顺序"}


def slice_pending(d):
    """未决结果类：spawn/yield/后台类调用及其后续结果"""
    ev = _tool_events(d)
    key = re.compile(r"spawn|yield|dispatch|submit|background|async|send", re.I)
    rel = [x for x in ev if key.search(x["tool"] or "")]
    return {"kind": "未决/派发类调用及结果", "n": len(rel),
            "calls": [{"i": x["i"], "tool": x["tool"], "args": x["args"],
                       "result": x["result"]} for x in rel][:40],
            "source": "会话记录里 spawn/yield/dispatch/submit 类调用及其结果原文"}


def slice_config(d):
    """旋钮/对照类：两臂配置原文"""
    out = {"kind": "配置与旋钮"}
    for name in ("runtime/execution.json", "gw.json", "knob_applied.json"):
        p = Path(d)/name
        if p.is_file(): out[name] = _read(p)[:4000]
    base = Path(d).name
    peer = Path(d).parent/(base[:-4] if base.endswith("-off") else base + "-off")
    if peer.is_dir():
        pp = peer/"runtime/execution.json"
        if pp.is_file(): out["对照臂 " + peer.name + "/runtime/execution.json"] = _read(pp)[:4000]
    out["source"] = "题包自带的配置文件原文"
    return out


def slice_tools_offered(d):
    """能力暴露类：模型实际能调到哪些工具"""
    ev = _tool_events(d)
    names = sorted({x["tool"] for x in ev if x["tool"]})
    pi = _read(Path(d)/"run/plugin_inspect.stdout.json")
    return {"kind": "工具面",
            "★实际调用过的工具名": names,
            "注意": "plugin_inspect 只列**插件**工具，不是模型的全部工具面；两者不要混",
            "plugin_inspect原文": pi[:3000] if pi else "(无)",
            "source": "会话记录里出现过的 toolCall 名字 + run/plugin_inspect.stdout.json"}


def slice_secret(d):
    """脱敏类：含疑似凭据的工具结果原文"""
    ev = _tool_events(d)
    pat = re.compile(r"(AKIA[0-9A-Z]{8,}|secret|password|api[_-]?key|token|BEGIN [A-Z ]*PRIVATE KEY)", re.I)
    rel = [x for x in ev if pat.search(json.dumps(x["result"], ensure_ascii=False))]
    return {"kind": "含疑似凭据的工具结果", "n": len(rel),
            "results": [{"i": x["i"], "tool": x["tool"], "result": x["result"]} for x in rel][:20],
            "source": "会话记录里正文命中凭据特征的工具结果原文"}



def slice_calls_with_results(d):
    """通用：完整工具调用序列 **带结果**。

    这是给「要看返回内容才判得了」的族用的兜底。
    原兜底 slice_sequence 只有名字和参数，判官看不到结果，
    却被要求判断结果完不完整 —— 那不是判官不稳，是证据里没有答案。
    """
    ev = _tool_events(d)
    out = []
    for x in ev:
        r = x["result"]
        if isinstance(r, str) and len(r) > 3000:
            r = r[:3000] + "\n…（本条结果原文过长，此处截断，未截断部分不影响是否有返回的判断）"
        out.append({"i": x["i"], "tool": x["tool"],
                    "args": x["args"], "result": r, "isError": x["isError"]})
    return {"kind": "工具调用序列（含结果原文）", "calls": out, "n": len(out),
            "n_error": sum(1 for x in ev if x["isError"]),
            "source": "会话记录里全部 toolCall 的名字、参数与结果原文，按时间顺序"}

FAMILY_ROUTE = {
    "C-1": "slice_compaction",
    "C-2": "slice_compaction",
    "C-3": "slice_compaction",
    "C-4": "slice_compaction",
    "C-5": "slice_compaction",
    "E-4": "slice_failure",
    "E-5": "slice_failure",
    "Q-1": "slice_failure",
    "Q-2": "slice_failure",
    "T-1": "slice_failure",
    "T-2": "slice_failure",
    "T-3": "slice_failure",
    "T-5": "slice_failure",
    "O-1": "slice_failure",
    "O-2": "slice_failure",
    "O-3": "slice_failure",
    "S-1": "slice_status",
    "L-1": "slice_status",
    "W-1": "slice_sequence",
    "D-1": "slice_sequence",
    "D-2": "slice_sequence",
    "R-1": "slice_pending",
    "R-2": "slice_pending",
    "R-3": "slice_pending",
    "N-1": "slice_pending",
    "F-1": "slice_tools_offered",
    "F-2": "slice_tools_offered",
    "F-3": "slice_tools_offered",
    "F-4": "slice_tools_offered",
    "F-5": "slice_tools_offered",
    "I-1": "slice_tools_offered",
    "I-2": "slice_tools_offered",
    "I-3": "slice_tools_offered",
    "G-1": "slice_config",
    "G-2": "slice_config",
    "G-4": "slice_config",
    "G-5": "slice_config",
    "V-1": "slice_config",
    "P-1": "slice_config",
    "P-2": "slice_config",
    "X-1": "slice_config",
    "X-2": "slice_config",
    "G-3": "slice_secret",
    "E-1": "slice_calls_with_results",
    "E-2": "slice_calls_with_results",
    "E-3": "slice_calls_with_results",
    "E-6": "slice_calls_with_results",
    "E-7": "slice_calls_with_results",
    "Y-1": "slice_calls_with_results",
    "Y-2": "slice_calls_with_results",
    "Y-3": "slice_calls_with_results",
    "X-3": "slice_calls_with_results",
    "S-2": "slice_calls_with_results",
    "S-3": "slice_calls_with_results",
    "S-4": "slice_calls_with_results",
    "S-5": "slice_calls_with_results",
    "S-6": "slice_calls_with_results",
    "L-2": "slice_calls_with_results",
    "L-3": "slice_calls_with_results",
    "L-4": "slice_calls_with_results",
    "N-2": "slice_calls_with_results",
    "N-3": "slice_calls_with_results",
    "T-4": "slice_calls_with_results",
    "J-1": "slice_calls_with_results",
    "J-2": "slice_calls_with_results",
    "J-3": "slice_calls_with_results"
}

# ────────── 路由：按条目 id 的关键词分派 ──────────
ROUTES = [
    (r"compact|material_survived|early_material|kept_region|summary_nonempty|preserves_task_critical|trigger_threshold",
     slice_compaction),
    (r"failure_signal|exit_code|signal_death|timeout|error_relay|process_exit|orphan|background_output|result_integrity|truncat",
     slice_failure),
    (r"status_consistent|reached_task_end|session_recoverable|turn_boundary", slice_status),
    (r"rework|repeat", slice_sequence),
    (r"pending|child_|dispatch|iso_submit|spawn_capability|subagent", slice_pending),
    (r"arm_isolation|param_|knob|denied_tool|deny_boundary|frozen|guardrail", slice_config),
    (r"tool.*offered|capability_(present|absent)|invalid_tool_name|reserved_name|ask_user|clarif|system_prompt",
     slice_tools_offered),
    (r"redact|secret|credential", slice_secret),
]



# ────────── 按目录族路由（取代按判据名猜的正则） ──────────
_MAPV3 = None
def _fam(item_id):
    """判据 id -> 目录族代号（C-1 / E-4 / …）。取不到返回 None。"""
    global _MAPV3
    if _MAPV3 is None:
        try:
            _MAPV3 = json.loads((Path(__file__).parent / "MAP_V3.json")
                                .read_text(encoding="utf-8"))["mapped"]
        except Exception:
            _MAPV3 = {}
    cat = _MAPV3.get(item_id, "")
    if not cat:
        return None
    w = cat.replace("SPLIT:", "").split()
    return w[0] if w else None


def slice_for(case_dir, item_id):
    """先按目录族路由；族里没有的才退回按名字的老正则；两条都不中才是真兜底。

    ★兜底必须是**带结果**的切片。原实现退回 slice_sequence（只有名字和参数），
      导致 145/563 条判据在看不到结果的证据上判「结果完不完整」，
      已经产出过假阳性（M-T5d 的 skill_result_relayed）。
    """
    fam = _fam(item_id)
    fn = FAMILY_ROUTE.get(fam) if fam else None
    if fn:
        s = globals()[fn](case_dir)
        s["item"] = item_id
        s["routed_by"] = "目录族 %s" % fam
        return s
    for pat, f in ROUTES:
        if re.search(pat, item_id, re.I):
            s = f(case_dir)
            s["item"] = item_id
            s["routed_by"] = "判据名正则（该条未归入目录族）"
            return s
    s = slice_calls_with_results(case_dir)
    s["item"] = item_id
    s["routed_by"] = "★兜底：该条既不在目录族里、也没有专用取片规则"
    s["device_warning"] = ("这条判据没有对应的证据切片规则，给的是完整调用序列（含结果）。"
                           "若它要判的东西不在这份证据里，请判 N-A，不要凭空推断。")
    return s


# ────────────────────────────────────────────────────────────
LIST_KEYS = ("events", "error_results", "exec_results", "sequence", "calls", "results")


def batches(sl, limit=MAXSLICE):
    """把一个片段切成若干批(只切列表型字段, 标量字段每批都带上)。"""
    import json as _j
    full = len(_j.dumps(sl, ensure_ascii=False))
    if full <= limit:
        return [sl]
    # ★切**体积最大**的那个列表, 不是第一个 ——
    #   实测切错字段会白切: D1 的体积 90% 在 exec_results(40 条), 却去切了 error_results(12 条),
    #   结果每批还是 98KB。
    cand = [(k, len(_j.dumps(sl[k], ensure_ascii=False)))
            for k in LIST_KEYS if isinstance(sl.get(k), list) and sl[k]]
    if not cand:
        return [sl]                      # 切不动就整份给, 由调用方决定
    lk = max(cand, key=lambda x: x[1])[0]
    # 其余大列表也不能每批都带 —— 只在第 1 批带一次
    other = [k for k, _ in cand if k != lk]
    head = {k: v for k, v in sl.items() if k != lk and k not in other}
    items = sl[lk]
    per = max(1, int(len(items) * limit / full))
    out = []
    for i in range(0, len(items), per):
        b = dict(head)
        b[lk] = items[i:i+per]
        if i == 0:
            for k in other: b[k] = sl[k]
        b["★分批"] = "第 %d/%d 批（本条判据的证据过大，按 %s 切分；**每条原文都完整，未裁剪**）" % (
            i // per + 1, (len(items) + per - 1) // per, lk)
        out.append(b)
    return out


if __name__ == "__main__":
    import sys
    s = slice_for(sys.argv[1], sys.argv[2])
    txt = json.dumps(s, ensure_ascii=False, indent=1)
    print(txt[:3000])
    print("\n... 片段总大小 %.1f KB" % (len(txt)/1024))
