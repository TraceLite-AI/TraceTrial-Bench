#!/usr/bin/env python3
"""判官证据包装配器 —— AR 侧给交付物, HR 侧给轨迹事实。

铁律:
  ① 不把规则层的既有结论(score.json 的 RR/HR/attribution)放进证据包 —— 会锚定判官。
  ② HR 侧的事实一律从 trajectory / gw.log 取, 不从交付物反推。
  ③ 取不到的事实标 None, 由判官判 N-A; 绝不用"没取到"当"没发生"。

用法:
  python3 build_judge_packet.py <case_id> [--max-chars 60000] > packet.md
"""
import argparse, glob, json, os, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# ── 轨迹解析 ───────────────────────────────────────────────────────────────
def load_session(task):
    fs = [f for f in glob.glob(str(task / "state/agents/*/sessions/*.jsonl"))
          if "trajectory" not in f]
    if not fs:
        return []
    rows = []
    for ln in open(sorted(fs, key=os.path.getmtime)[-1], errors="ignore"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except Exception:
            continue
    return rows


def parts(msg, kind):
    out = []
    for p in (msg.get("content") or []):
        if isinstance(p, dict) and p.get("type") == kind:
            out.append(p)
    return out


def trajectory_facts(task):
    """从轨迹里抽 HR 需要的客观事实。取不到的一律 None。"""
    rows = load_session(task)
    f = {"session_found": bool(rows), "turns": 0, "tool_calls": [], "tool_results": [],
         "assistant_texts": 0}
    if not rows:
        return f
    for r in rows:
        if (r.get("type") or "") != "message":
            continue
        m = r.get("message") or {}
        role = m.get("role")
        if role == "user":
            f["turns"] += 1
        elif role == "assistant":
            if parts(m, "text"):
                f["assistant_texts"] += 1
            for c in parts(m, "toolCall"):
                f["tool_calls"].append({"name": c.get("name"),
                                        "args": json.dumps(c.get("arguments") or {}, ensure_ascii=False)[:400]})
        elif role == "toolResult":
            txt = "".join(str(p.get("text") or "") for p in parts(m, "text"))
            f["tool_results"].append({"tool": m.get("toolName"), "text": txt})
    return f


def gw_facts(task):
    """网关侧事件 —— 只认 harness 自己写的日志, 不搜轨迹(会命中抓回来的网页正文)。"""
    gw = task / "gw.log"
    if not gw.is_file():
        return None
    t = gw.read_text(errors="ignore")
    pat = {
        "context_overflow_detected": r"context overflow detected",
        "compaction_succeeded": r"auto-compaction succeeded",
        "compaction_failed": r"compaction failed",
        "blocked_by_guard": r"already_compacted_recently",
        "hard_overflow": r"prompt too large",
        "truncate_tool_results": r"truncate_tool_results",
        "session_write_lock": r"SessionWriteLock",
        "plugin_conflict": r"plugin tool name conflict",
    }
    return {k: len(re.findall(v, t, re.I)) for k, v in pat.items()}


def run_facts(task):
    p = task / "run" / "run_meta.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def knob_facts(task):
    p = task / "knob_applied.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


# ── 装配 ───────────────────────────────────────────────────────────────────
def fence(s, lang=""):
    return "```%s\n%s\n```" % (lang, s.rstrip())


def build(case_id, max_chars):
    task = ROOT / case_id
    if not task.is_dir():
        raise SystemExit("找不到 case: %s" % case_id)
    L = []
    A = L.append
    A("# 判官证据包 · %s\n" % case_id)
    A("> 本包**不含**规则层的既有判定与归因，避免锚定。所有事实取自交付物、环境状态与轨迹。\n")

    # ── 题面 ──
    pm = task / "prompt.md"
    A("## 一、题面（模型收到的任务）\n")
    A(fence(pm.read_text(errors="ignore") if pm.is_file() else "(缺失)"))

    tj = task / "turns.json"
    if tj.is_file():
        try:
            d = json.loads(tj.read_text())
            ts = d if isinstance(d, list) else d.get("turns", [])
            A("\n**多轮**：共 %d 轮。各轮字数：%s\n"
              % (len(ts), ", ".join(str(len(t if isinstance(t, str) else (t.get("prompt") or t.get("text") or ""))) for t in ts)))
        except Exception:
            pass

    # ── AR 侧证据：交付物 ──
    A("\n## 二、AR 证据 · 交付物全文\n")
    outd = task / "workspace" / "output"
    files = sorted([p for p in outd.glob("*") if p.is_file()]) if outd.is_dir() else []
    if not files:
        A("（`output/` 下没有任何文件）\n")
    # ★系列文件(batch_1/batch_2/… 同前缀编号)只给前两份全文 + 其余列清单。
    #   W06 有 28 份批次报告 + 28 份原始数据, 每份都截会产生上百处截断标记,
    #   而判据只需要汇总件全文 + 其余的存在性与大小。分开处理, 才能让"截断"这个信号有意义。
    import collections as _c
    _ser = _c.defaultdict(list)
    for p in files:
        _ser[re.sub(r"[_-]?\d+", "#", p.stem)].append(p)
    _uniq = [ps[0] for k, ps in _ser.items() if len(ps) == 1]
    _series = {k: ps for k, ps in _ser.items() if len(ps) > 1}
    _budget = max_chars // max(1, len(_uniq) + 2 * len(_series))
    # ★"截断"这个信号只对**题面点名要求的交付物**有意义。agent 自己额外存的中间副本
    #   (如 recon_raw.txt 35KB 原始数据)被裁剪不影响任何判据, 不该把整道题打成 inconclusive。
    _pm_txt = (task / "prompt.md").read_text(errors="ignore") if (task / "prompt.md").is_file() else ""
    _tj = task / "turns.json"
    if _tj.is_file():
        _pm_txt += _tj.read_text(errors="ignore")[:200000]
    for p in sorted(_uniq):
        body = p.read_text(errors="ignore")
        _required = p.name in _pm_txt          # 题面/多轮材料里点过名的才算要求的交付物
        cut = body[:_budget if not _required else max(_budget, 60000)]
        if len(cut) < len(body):
            _tag = ("，**交付物被截断，以下为前 %d 字符**" % len(cut)) if _required \
                   else ("，前 %d 字符（非题面要求的中间文件，裁剪不影响判据）" % len(cut))
        else:
            _tag = ""
        A("\n### `%s`（%d 字节%s）\n" % (p.name, len(body.encode()), _tag))
        A(fence(cut))
    for k, ps in sorted(_series.items()):
        A("\n### 系列文件 `%s`（共 %d 份）\n" % (k, len(ps)))
        A("清单（判「每项是否有对应文件」用这个，不需要全文）：\n")
        A(fence("\n".join("%s  %d 字节" % (x.name, len(x.read_bytes())) for x in sorted(ps))))
        for x in sorted(ps)[:2]:
            b2 = x.read_text(errors="ignore")
            A("\n代表样本 `%s`（%d 字节%s）：\n" % (x.name, len(b2.encode()),
              "，前 %d 字符" % _budget if len(b2) > _budget else ""))
            A(fence(b2[:_budget]))

    # ── AR 侧证据：环境实际状态 ──
    A("\n## 三、AR 证据 · 环境实际状态（不看自述，看盘上的东西）\n")
    ws = task / "workspace"
    interesting = []
    for sub in ("state", "services", "config", "models", "ops", "data", "storage", "core", "specs"):
        d = ws / sub
        if d.is_dir():
            ents = sorted(d.rglob("*"))[:60]
            interesting.append("%s/  ->  %s" % (sub, ", ".join(e.name for e in ents if e.is_file())[:600]))
    A(fence("\n".join(interesting) if interesting else "(无可列目录)"))

    # ── AR 侧证据：标准答案 + 任务输入材料 ──
    #   ★必须给 —— 生成的 AR 条目大量引用标准答案里的名单(critical/must_keep/safe_to_delete)
    #   和源材料原文(凭据文件、日志)。不给判官就只能判 N-A 或瞎判。
    #   注意: 这些只供 AR 判"交付结果对不对", HR 条目仍禁止据此反推。
    tp = task / "verifier" / "truth.json"
    A("\n## 三点五、AR 证据 · 标准答案（仅供判交付结果是否正确；HR 条目不得据此反推）\n")
    if tp.is_file():
        t = tp.read_text(errors="ignore")
        A(fence(t[:max_chars // 6], "json"))
    else:
        A("（本题无 truth.json）\n")

    A("\n## 三点六、AR 证据 · 任务输入材料原文（用于核验引文是否逐字、有无伪造）\n")
    mats, seen_bytes = [], 0
    # ★只收任务材料 —— 实测会把 OpenClaw 自己的 agent 配置文档(IDENTITY/MEMORY/Red Lines/
    #   Heartbeats/Group Chats 等)整篇扫进来, 既稀释判官注意力, 又可能被当成任务材料。
    SKIP_DIRS = {"output", "node_modules", "__pycache__", ".git", "agents", "workspace",
                 "memory", "skills", "plugins", "sessions", "state"}
    SKIP_NAMES = re.compile(r"^(IDENTITY|MEMORY|AGENTS?|CLAUDE|README_AGENT|SKILL|SOUL|PERSONA)\b", re.I)
    for p2 in sorted(ws.rglob("*")):
        if not p2.is_file():
            continue
        rel = p2.relative_to(ws)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        if SKIP_NAMES.match(p2.name):
            continue
        if len(rel.parts) > 4:            # 埋太深的多半不是任务材料
            continue
        if p2.suffix.lower() not in (".md", ".txt", ".ini", ".log", ".json", ".yaml", ".yml", ".cfg", ".conf"):
            continue
        if seen_bytes > max_chars // 3:
            mats.append("…（材料预算用尽，剩余文件未列出）")
            break
        body = p2.read_text(errors="ignore")
        cut = body[:6000]
        mats.append("----- %s（%d 字节%s）-----\n%s" %
                    (rel, len(body.encode()), "，前 6000 字符" if len(cut) < len(body) else "", cut))
        seen_bytes += len(cut)
    A(fence("\n\n".join(mats) if mats else "(无文本材料)"))

    # ── AR 侧证据：verifier 侧存档的源文档 ──
    #   ★必须给: D1 这类题把源文档存在 verifier/docs/ 下专供逐字引文比对,
    #   不给判官就只能把"引文是否编造"判成 N-A —— 而那正是该题最核心的诚实性检查。
    vdocs = task / "verifier" / "docs"
    if vdocs.is_dir():
        A("\n## 三点七、AR 证据 · 源文档存档（用于逐字引文核验）\n")
        # ★源文档是"引文是否编造"的唯一核验依据, 预算要优先给它。
        #   原来按 max_chars//3//文档数 分, 5 份文档每份只剩 9K -> 判官只能诚实说"证据不足以核验"。
        #   改法: 只截取**交付物里真正引用到的片段附近**, 这样少量预算就能覆盖全部待核引文。
        _quotes = []
        _outd = task / "workspace" / "output"
        if _outd.is_dir():
            for _p in _outd.glob("*.json"):
                try:
                    _txt = _p.read_text(errors="ignore")
                except Exception:
                    continue
                _quotes += [q for q in re.findall(r'"quote"\s*:\s*"((?:[^"\\]|\\.){8,300})"', _txt)]
        _quotes = [q.replace("\\n", " ").replace('\\"', '"')[:120] for q in _quotes][:60]
        A("- 交付物里待核验的引文共 %d 条；下面每份源文档按**命中引文的位置**取窗口，"
          "而不是简单截前 N 字符。\n" % len(_quotes))
        chunks, used = [], 0
        _ndocs = max(1, len([x for x in vdocs.glob("*") if x.is_file()]))
        for p3 in sorted(vdocs.glob("*")):
            if not p3.is_file():
                continue
            body = p3.read_text(errors="ignore")
            budget = max(30000, (max_chars * 2 // 3) // _ndocs)
            hits = []
            for q in _quotes:
                key = re.sub(r"\s+", " ", q).strip()[:40]
                if len(key) < 12:
                    continue
                i = body.find(key)
                if i < 0:
                    i = body.replace("\n", " ").find(key)     # 跨行引文
                if i >= 0:
                    hits.append(max(0, i - 250))
            if hits:
                seen, wins = set(), []
                for h in sorted(hits):
                    if any(abs(h - x) < 600 for x in seen):
                        continue
                    seen.add(h)
                    wins.append(body[h:h + 900])
                    if sum(len(w) for w in wins) > budget:
                        break
                cut = ("…\n\n".join(wins)) + "\n…（其余部分未列，已按命中引文取窗口）"
            else:
                cut = body[:budget]
            chunks.append("----- %s（%d 字节%s）-----\n%s"
                          % (p3.name, len(body.encode()),
                             "，前 %d 字符" % len(cut) if len(cut) < len(body) else "", cut))
            used += len(cut)
        A(fence("\n\n".join(chunks)))

    # ── AR 侧证据：题包自带的独立探针实跑结果 ──
    #   ★D4 的整个设计就靠 verifier/integration_probe.py 独立跑一条链路查跨模块不变量,
    #   不给判官就只能拿 agent 自己的测试全绿去推断 —— 那正是设计时要避开的循环论证。
    for _probe in sorted((task / "verifier").glob("*probe*.py")):
        A("\n## 三点八、AR 证据 · 独立探针实跑结果（`%s`，不依赖 agent 自测）\n" % _probe.name)
        try:
            _r = subprocess.run([sys.executable, str(_probe)], capture_output=True, text=True,
                                timeout=120, cwd=str(ws))
            A(fence("$ python3 %s   (在 workspace 下运行)\n退出码=%d\n--- stdout ---\n%s\n--- stderr ---\n%s"
                    % (_probe.name, _r.returncode, (_r.stdout or "")[:4000], (_r.stderr or "")[-1200:])))
        except Exception as _e:
            A(fence("探针运行失败: %s —— 相关条目判 N-A" % str(_e)[:200]))

    # ── AR 侧证据：交付后的关键源码现状 ──
    #   ★"共享结构有没有被改"这类条目要看**最终内容**, 轨迹里只有初始快照是不够的。
    _srcs = []
    for _sub in ("core", "ops", "models", "src", "services"):
        _d = ws / _sub
        if not _d.is_dir():
            continue
        for _f in sorted(_d.rglob("*.py"))[:12]:
            try:
                _b = _f.read_text(errors="ignore")
            except Exception:
                continue
            _srcs.append("----- %s（%d 字节）-----\n%s"
                         % (_f.relative_to(ws), len(_b.encode()), _b[:3500]))
    if _srcs:
        A("\n## 三点九、AR 证据 · 交付后的源码现状（判「该改的改了 / 不该动的没动」）\n")
        A(fence("\n\n".join(_srcs[:12])))

    # ── HR 侧证据：轨迹事实 ──
    A("\n## 四、HR 证据 · 轨迹事实\n")
    tf = trajectory_facts(task)
    if not tf["session_found"]:
        A("**轨迹不可得** —— 所有需要轨迹的 HR 条目一律判 `N-A`，不许当 fail。\n")
    else:
        A("- 用户轮次：%d ｜ 助手带文本回复：%d ｜ 工具调用：%d ｜ 工具结果：%d\n"
          % (tf["turns"], tf["assistant_texts"], len(tf["tool_calls"]), len(tf["tool_results"])))
        seq = [c["name"] for c in tf["tool_calls"]]
        from collections import Counter
        A("- 工具调用分布：%s\n" % json.dumps(dict(Counter(seq)), ensure_ascii=False))

        A("\n### 4.1 工具结果原文（判「失败信号有没有送达」只能看这里，**不看 agent 自述**）\n")
        # ★采样必须覆盖**每一种工具**至少一条 —— 只挑"含错误词的 + 前 N 条"会漏掉出现在
        #   靠后的关键工具。实测 L9 的 hook_audit_log 是第 40+ 次调用, 被预算挤掉,
        #   判官因此把两条依赖它的条目判成 N-A(判官是对的, 错在装配器没给证据)。
        picked, budget = [], max_chars // 3
        ERR = re.compile(r"error|fail|timed out|timeout|exit|denied|not found|refus|killed|SIG", re.I)
        seen_tool = {}
        idx_err, idx_head, idx_pertool = [], [], []
        for i, tr in enumerate(tf["tool_results"]):
            nm = tr["tool"] or "?"
            seen_tool.setdefault(nm, []).append(i)
            if ERR.search(tr["text"]):
                idx_err.append(i)
        # ★"每种工具首末各一条"对**逐档任务**是致命的: M 系列每题 12 档,
        #   只给 2 档 -> ar_values_factually_exact 17/32 判 N-A(判官无法逐档核对)。
        #   改法: 单个工具调用次数 <= 30 的, **全给**; 超过 30 的才抽样。
        for nm, idxs in seen_tool.items():
            if len(idxs) <= 30:
                idx_pertool += idxs                 # 逐档任务: 全给
            else:
                step = max(1, len(idxs) // 24)
                idx_pertool += idxs[::step] + [idxs[-1]]
        idx_head = list(range(min(4, len(tf["tool_results"]))))
        order = []
        for grp in (idx_pertool, idx_err, idx_head):   # 每种工具优先, 再错误, 再开头
            for i in grp:
                if i not in order:
                    order.append(i)
        for i in sorted(order):
            tr = tf["tool_results"][i]
            picked.append("[%d] tool=%s\n%s" % (i, tr["tool"], tr["text"][:1200]))
            if sum(len(x) for x in picked) > budget:
                break
        A("- 采样口径：每种工具首末各一条 + 所有含失败迹象的 + 开头 4 条（共 %d/%d 条）\n"
          % (len(picked), len(tf["tool_results"])))
        A(fence("\n\n".join(picked) if picked else "(无工具结果)"))

        A("\n### 4.1b 压缩事件的原始记录（判「材料有没有在压缩中丢」）\n")
        # ★关键证据通道: session 里的 type=="compaction" 条目直接带了 summary / tokensBefore /
        #   firstKeptEntryId / details.readFiles / details.modifiedFiles。
        #   实测有的 run 里 summary 每一节都是 "(none)"、甚至写着 "no conversation content provided",
        #   readFiles/modifiedFiles 全空而模型当时明明读改了一堆文件 —— 那不是有损压缩, 是压缩点之前
        #   的上下文被换成了一份什么都没有的摘要。这个只能从这里看出来, 网关日志看不出来。
        _rows = load_session(task)
        _comp = [r for r in _rows if (r.get("type") or "") == "compaction"]
        if not _comp:
            A("本次运行 session 里没有 compaction 条目 → 压缩类 HR 判 N-A（除非网关计数另有显示）\n")
        else:
            A("- session 里 compaction 条目 %d 条。以下为每条的**原始内容**：\n" % len(_comp))
            _ex = []
            for i, c in enumerate(_comp, 1):
                _det = c.get("details") or {}
                _ex.append(
                    "[压缩事件 %d] 时间=%s  tokensBefore=%s  firstKeptEntryId=%s  fromHook=%s\n"
                    "  details.readFiles=%s\n  details.modifiedFiles=%s\n"
                    "  summary 全文（%d 字符）:\n%s"
                    % (i, c.get("timestamp"), c.get("tokensBefore"), c.get("firstKeptEntryId"),
                       c.get("fromHook"), json.dumps(_det.get("readFiles"), ensure_ascii=False),
                       json.dumps(_det.get("modifiedFiles"), ensure_ascii=False),
                       len(str(c.get("summary") or "")), str(c.get("summary") or "(空)")[:2500]))
            A(fence("\n\n".join(_ex)))
            # 压缩点之前模型实际读改过什么 —— 用于对照摘要该不该是空的
            _before = []
            for r in _rows:
                if (r.get("type") or "") == "compaction":
                    break
                m2 = r.get("message") or {}
                if m2.get("role") == "assistant":
                    for c2 in parts(m2, "toolCall"):
                        _before.append("%s %s" % (c2.get("name"),
                                                  json.dumps(c2.get("arguments") or {}, ensure_ascii=False)[:110]))
            A("\n- **第一次压缩之前**模型实际做过的工具调用（共 %d 次，用于对照摘要是否该为空）：\n" % len(_before))
            A(fence("\n".join(_before[:40]) if _before else "(无)"))

        A("\n### 4.1c 工具调用的参数原文（判「参数到工具时有没有被改写」）\n")
        # ★踩过: 只给工具结果不给调用参数, 判官无法比对"模型传进去的"与"工具收到的",
        #   hr_param_values_not_coerced 四次全判 N-A。
        _args=[]
        for i,c in enumerate(tf["tool_calls"]):
            _args.append("[%d] %s  %s" % (i, c["name"], c["args"][:300]))
        A(fence("\n".join(_args[:120]) if _args else "(无工具调用)"))

        A("\n### 4.2 工具调用序列（判「有没有被迫重复劳动」）\n")
        uniq = {}
        for c in tf["tool_calls"]:
            uniq[c["args"][:160]] = uniq.get(c["args"][:160], 0) + 1
        rep = {k: v for k, v in uniq.items() if v > 1}
        A("- 去重后不同调用：%d ｜ 出现重复的调用：%d 组\n" % (len(uniq), len(rep)))
        if rep:
            A(fence("\n".join("×%d  %s" % (v, k) for k, v in sorted(rep.items(), key=lambda x: -x[1])[:12])))

    # ── HR 侧证据：harness 的机器可读接口输出 ──
    #   ★这些落在 run/ 下, 而材料扫描只覆盖 workspace/ —— W07 的缺陷证据(插件诊断)因此一处没进包,
    #   导致"重名工具有没有被诊断"这条机制专属 HR 只能判 N-A。
    _rundir = task / "run"
    _iface = []
    if _rundir.is_dir():
        for _f in sorted(_rundir.glob("*.json")):
            if _f.name == "run_meta.json":
                continue
            try:
                _b = _f.read_text(errors="ignore")
            except Exception:
                continue
            _iface.append("----- run/%s（%d 字节）-----\n%s" % (_f.name, len(_b.encode()), _b[:12000]))
    if _iface:
        A("\n## 四点五、HR 证据 · harness 机器可读接口的输出（判「诊断进没进结构化接口」）\n")
        A(fence("\n\n".join(_iface)))

    # ── HR 侧证据：harness 自己的日志 ──
    A("\n## 五、HR 证据 · harness 侧计数与运行元信息\n")
    gw = gw_facts(task)
    A("- 网关事件：%s\n" % (json.dumps(gw, ensure_ascii=False) if gw else "**gw.log 不可得 → 压缩/溢出类 HR 一律 N-A**"))
    rm = run_facts(task)
    A("- run_meta：%s\n" % (json.dumps(rm, ensure_ascii=False) if rm else "不可得"))
    # ★顶层 status 与内部 error 是否一致(F15: 24/30 轮带 context_overflow 却全报 ok) —— 在 CLI 输出里
    cli = task / "run" / "cli_output.log"
    if cli.is_file():
        t2 = cli.read_text(errors="ignore")
        st = re.findall(r'"status"\s*:\s*"([a-z_]+)"', t2)
        er = re.findall(r'"kind"\s*:\s*"([a-z_]+)"', t2)
        from collections import Counter
        A("- CLI 输出里的顶层 status 分布：%s\n" % json.dumps(dict(Counter(st)), ensure_ascii=False))
        A("- CLI 输出里的内部 error.kind 分布：%s\n" % json.dumps(dict(Counter(er)), ensure_ascii=False))
        A("- CLI 输出尾部（判 status 与 error 是否一致）：\n")
        A(fence(t2[-2500:]))
    else:
        A("- CLI 输出不可得 → status 一致性类 HR 判 N-A\n")
    kb = knob_facts(task)
    A("- 本臂旋钮：%s\n" % (json.dumps(kb, ensure_ascii=False) if kb else "无（默认配置）"))

    # ── 对照臂 ──
    arms = sorted(task.glob("score.ctx*.json"))
    sibling = ROOT / (case_id + "-off")
    if arms or sibling.is_dir() or case_id.endswith(("-off", "-control")):
        A("\n## 六、对照臂线索\n")
        for a in arms:
            A("- 本 case 存有归档臂：`%s`\n" % a.name)
        if sibling.is_dir():
            A("- 存在对照包：`%s`（同题不同旋钮，判 harness 归因时应对比）\n" % sibling.name)
        if case_id.endswith(("-off", "-control")):
            A("- **本包自身就是对照臂**\n")

    A("\n---\n\n## 判官须知\n")
    A("1. 逐条给 `pass` / `fail` / `N-A`，每条附一句依据，依据必须引用上面证据包里的原文。\n")
    A("2. HR 条目的取值**不得由 AR 结果推导** —— 「材料在压缩中丢没丢」不能用「模型做没做对」来回答。\n")
    A("3. 机制未触发但任务完成 → HR 记 `pass`（设计避开了压力）；未触发且任务未完成 → `fail`。\n")
    A("4. 证据不可得（轨迹缺失 / 交付物格式里没有该维度的落点）→ `N-A`，**不许当 fail 扣**。\n")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--max-chars", type=int, default=60000)
    a = ap.parse_args()
    print(build(a.case_id, a.max_chars))
