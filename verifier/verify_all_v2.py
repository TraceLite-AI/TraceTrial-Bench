# -*- coding: utf-8 -*-
"""HarnessBench 统一 verifier runner —— 74 道题一把跑完，输出规范化。

## 为什么需要它
题库里有**两代 scorer**，接口和输出形状都不同：

  A 代（M 系列 34 道）
     参数：--workspace
     轨迹：环境变量 HARNESSBENCH_PARENT_SESSION_FILE
     输出：{"metrics": {"harness_rubric": {"items": {...}}, "result_rubric": {...}}}
     取值：True / False / None

  B 代（其余 39 道）
     参数：--workspace --state [--harness]
     轨迹：--state 指向 <case>/state，自己 rglob
     输出：{"HR": {...}, "RR": {...}, "metrics": {...}, "attribution": [...]}
     取值："pass" / "fail" / "N/A"

两代都是好的。之前全库只跑出 34 道有结果，是因为
**没给 B 代传 --state**（它拿不到轨迹 → HR 全 N/A），
以及 runner 只认 A 代的输出形状。不是 verifier 坏了。

## 喂哪个 session 文件（踩过）
    <id>.jsonl              ★完整会话，实际发生的全部调用
    <id>.trajectory.jsonl   模型可见上下文快照（压缩后残存），只剩一部分
同一道题：94 次 vs 58 次。指错会让判据全部拿不到结果。
"""
import json, subprocess, sys, os, collections
from tracefp import trace_fp
import re
from pathlib import Path

R = Path.home() / "hb-cases"
TIMEOUT = 300


def session_file(case: Path):
    """完整会话：排除 .trajectory.jsonl。

    ★多会话时必须**全部合并**，不能取最大的那个 ——
      M-G2 的 12 次 books_submit 主要在较小的那个文件里，
      取最大会漏掉，导致 7/8 条判据返回 None。
      按文件名排序拼接，保证同一输入永远得到同一结果。
    """
    cands = sorted((p for p in case.glob("state/agents/*/sessions/*.jsonl")
                    if not p.name.endswith(".trajectory.jsonl")),
                   key=lambda p: p.name)
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    merged = case / ".merged_session.jsonl"
    parts = []
    for p in cands:
        try:
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    body = "\n".join(x.rstrip("\n") for x in parts if x.strip())
    try:
        if not merged.is_file() or merged.read_text(encoding="utf-8", errors="ignore") != body:
            merged.write_text(body, encoding="utf-8")
    except Exception:
        return cands[-1]
    return merged


def accepted_args(score_py: Path) -> set:
    """扫 score.py 声明了哪些参数，只传它认识的。"""
    try:
        t = score_py.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    import re
    return set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', t))


def norm(v):
    """两代取值归一化 → True(pass) / False(fail) / None(拿不到结果)"""
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in ("pass", "true", "ok", "1"):
        return True
    if s in ("fail", "false", "0"):
        return False
    return None            # "N/A" / "n/a" / 其它


# ── 归因型条目：不是判据，是 fail 的归因 ──
#   判据回答「harness 做对了吗」；归因回答「如果做错了，是谁的错」。
#   前者前件恒成立，后者只在 fail 时才谈得上 —— 混在一起会让
#   「没出错」被记成「无结果」。实测 HR8_loss_is_harness_side 在 10 道题上如此。
#   ★过滤要**窄**：我第一版用 `_cause` 匹配，把
#   `root_cause_codes_exact`（是否精确报出根因代码）和
#   `RR7_report_names_root_causes`（报告有没有点名根因）也抓走了——
#   那两条是正经判据，不是归因。宁可漏，不可宽。
ATTR_NAMES = ("loss_is_harness_side", "_is_harness_side", "loss_side")


def is_attribution(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in ATTR_NAMES)


# ── 准入门：坏 run 不产出判定 ──
def admission(case: Path, ncalls: int) -> list:
    """返回不合格原因列表；空列表 = 通过。"""
    import json as _j
    fails = []
    v = case / "verifier"

    # 计划轮数
    pt = 0
    tp = case / "turns.json"
    if tp.is_file():
        try:
            d = _j.loads(tp.read_text(encoding="utf-8", errors="ignore"))
            pt = len(d if isinstance(d, list) else d.get("turns", []))
        except Exception:
            pass

    if ncalls == 0:
        fails.append("轨迹解析不出任何工具调用")
    elif pt >= 5 and ncalls < pt:
        fails.append("调用数 %d < 计划轮数 %d，本次运行基本没跑起来" % (ncalls, pt))

    # oracle 指定的交付物必须存在
    op = v / "oracle.json"
    if op.is_file():
        try:
            art = _j.loads(op.read_text(encoding="utf-8")).get("artifact")
        except Exception:
            art = None
        if art and not (case / "workspace" / art).is_file():
            fails.append("交付物不存在: %s" % art)
    return fails


def extract(d: dict):
    """全库 8 种输出形状 -> 统一的 {"hr": {...}, "rr": {...}}

    ★排除三种**派生视图**，它们是从 items 算出来的，计进去会重复：
        metrics.*.gate_verdicts · metrics.*.gauge_readings · metrics.curves.*
    """
    hr, rr = {}, {}
    m = d.get("metrics") or {}

    attr = {}

    def take(src, bucket, prefix=""):
        if not isinstance(src, dict):
            return
        for k, v in src.items():
            if isinstance(v, (dict, list)):
                continue
            s = str(v).strip().lower()
            if isinstance(v, bool) or s in ("pass", "fail", "true", "false",
                                            "n/a", "na", "none", "null"):
                (attr if is_attribution(k) else bucket)[prefix + k] = norm(v)

    # (1) A 代：metrics.harness_rubric.items / metrics.result_rubric.items
    #     L1 变体：metrics.harness_rubric 本身就是判据字典（没有 .items）
    for key, bucket in (("harness_rubric", hr), ("result_rubric", rr)):
        sub = m.get(key) if isinstance(m, dict) else None
        if isinstance(sub, dict):
            take(sub["items"], bucket) if isinstance(sub.get("items"), dict) else take(sub, bucket)

    # (2) B 代：顶层 HR / RR
    take(d.get("HR"), hr)
    take(d.get("RR"), rr)

    # (3) N 系列：AR_rule_layer（交付物规则层）
    take(d.get("AR_rule_layer"), rr, prefix="ar.")

    # (4) W 系列：official.checks（官方 grader）
    off = d.get("official")
    if isinstance(off, dict):
        take(off.get("checks"), rr, prefix="official.")

    # (4.5) W 系列：harness 信号在 attribution 数组里
    #       [{"layer":"HR","item":"official_grader","verdict":"clean|harness-attributable(...)"}]
    for a in (d.get("attribution") or []):
        if not isinstance(a, dict):
            continue
        if str(a.get("layer", "")).upper() != "HR":
            continue
        vd = str(a.get("verdict", "")).lower()
        if "clean" in vd:
            val = True
        elif "harness-attributable" in vd or "harness_caused" in vd:
            val = False
        else:
            val = None            # inconclusive
        hr["attr." + str(a.get("item", "?"))] = val

    # (4.6) 条件型归因：题目带 attribution 机制、但本轮没检出可归给 harness 的问题
    #       → 那等于「这一维 harness 通过」，不是「没有判据」。
    #       ★只对确实带该机制的题生效（输出里有 attribution 键），
    #         否则会把「没测」伪装成「通过」。
    if not hr and isinstance(d.get("attribution"), list) and rr:
        hr["attr.no_harness_attribution"] = True

    # (5) 单判定型（L7 / R1）：整题一个 verdict
    if not hr and not rr and isinstance(d.get("verdict"), str):
        hr["verdict"] = norm(d["verdict"])

    # (6) 能力增益型（K1）：core_pass + capability_exercised
    if not hr:
        sub = m.get("harness_rubric") if isinstance(m, dict) else None
        if isinstance(sub, dict) and isinstance(sub.get("core_pass"), bool):
            hr["core_pass"] = sub["core_pass"]
        if d.get("capability_exercised") is False:
            # 该能力一次没被用 -> 本轮测不到增益，记「无结果」而不是 pass
            hr["capability_gain"] = None

    return hr, rr, attr


def run_one(case: Path) -> dict:
    v = case / "verifier"
    score = v / "score.py"
    ws = case / "workspace"
    row = {"case": case.name}
    if not score.is_file():
        return {**row, "state": "无 score.py"}
    if not ws.is_dir():
        return {**row, "state": "无 workspace"}

    args = accepted_args(score)
    cmd = [sys.executable, "score.py", "--workspace", str(ws.resolve())]
    if "--state" in args and (case / "state").is_dir():
        cmd += ["--state", str((case / "state").resolve())]
    if "--harness" in args:
        cmd += ["--harness", "openclaw"]

    env = dict(os.environ)
    sess = session_file(case)
    if sess:
        env["HARNESSBENCH_PARENT_SESSION_FILE"] = str(sess.resolve())

    # ★准入门：先看这次运行本身立不立得住，坏 run 不打分
    try:
        import sys as _s
        _s.path.insert(0, str(R))
        from hr_slices import _tool_events
        ncalls = len(_tool_events(str(case)))
    except Exception:
        ncalls = 0
    gate = admission(case, ncalls)
    if gate:
        return {**row, "state": "INVALID", "trace_fp": trace_fp(case), "gate_fails": gate,
                "tool_calls": ncalls, "hr": {}, "rr": {}, "attribution": {},
                "hr_n": 0, "hr_fail": [], "hr_none": [],
                "rr_n": 0, "rr_fail": [], "rr_none": []}

    try:
        out = subprocess.run(cmd, cwd=str(v), env=env, capture_output=True,
                             text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {**row, "state": "超时"}
    if out.returncode != 0:
        return {**row, "state": "崩溃", "err": (out.stderr or "")[-160:].strip()}
    try:
        d = json.loads(out.stdout)
    except Exception:
        return {**row, "state": "输出非 JSON", "err": (out.stdout or "")[:120]}

    hr, rr, attr = extract(d)

    # ★并入程序判据库的结果。
    #   来源：本题 verifier/rubric_harness.yaml 里声明、且 prog_criteria 里已实现的 id。
    #   为什么要这一步：分配表里程序侧 44 条判据、93 处，一条都没在跑；
    #   把实现放在库里、题目只声明 id，一份实现服务所有声明它的题，不会各题漂移。
    try:
        import prog_criteria as _PC
        _rub = case / "verifier" / "rubric_harness.yaml"
        if _rub.is_file():
            _ids = re.findall(r"(?m)^\s*-\s*id:\s*(\S+)", _rub.read_text(errors="ignore"))
            _want = [i for i in _ids if i in _PC.REGISTRY]
            if _want:
                for _i, (_v, _ev) in _PC.evaluate(case, _want).items():
                    hr[_i] = _v
                    row.setdefault("prog_evidence", {})[_i] = _ev
    except Exception as _e:
        row.setdefault("prog_error", str(_e)[:120])
    row.update({
        "state": "ok",
        "trace_fp": trace_fp(case),
        "gen": "A" if (d.get("metrics") or {}).get("harness_rubric") else "B",
        "has_session": bool(sess),
        "hr": hr, "rr": rr,
        "hr_n": len(hr), "hr_fail": [k for k, x in hr.items() if x is False],
        "hr_none": [k for k, x in hr.items() if x is None],
        "attribution": attr,
        "rr_n": len(rr), "rr_fail": [k for k, x in rr.items() if x is False],
        "rr_none": [k for k, x in rr.items() if x is None],
    })
    return row


def main(only=None):
    targets = sorted(p for p in R.iterdir()
                     if p.is_dir() and (p / "verifier" / "score.py").is_file())
    if only:
        targets = [p for p in targets if p.name in set(only)]
    rows = []
    for c in targets:
        r = run_one(c)
        rows.append(r)
        if r["state"] == "INVALID":
            print("%-40s ★作废  %s" % (r["case"][:40], "; ".join(r["gate_fails"])[:52]))
        elif r["state"] != "ok":
            print("%-40s %-10s %s" % (r["case"][:40], r["state"], r.get("err", "")[:50]))
        else:
            print("%-40s [%s] HR %d条(fail %d, 无结果 %d)  RR %d条(fail %d, 无结果 %d)%s"
                  % (r["case"][:40], r["gen"],
                     r["hr_n"], len(r["hr_fail"]), len(r["hr_none"]),
                     r["rr_n"], len(r["rr_fail"]), len(r["rr_none"]),
                     "" if r["has_session"] else "  ★无 session"))
    (R / "VERIFY_ALL.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    ok = [r for r in rows if r["state"] == "ok"]
    inval = [r for r in rows if r["state"] == "INVALID"]
    live = [r for r in ok if r["hr_n"] or r["rr_n"]]
    print("\n" + "=" * 70)
    print("run %d | ★准入不合格 %d（作废待重跑）| 有效 %d | 产出判据 %d | 异常 %d"
          % (len(rows), len(inval), len(ok), len(live),
             len(rows) - len(ok) - len(inval)))
    if inval:
        print("\n★ 作废待重跑:")
        for r in inval:
            print("   %-40s %s" % (r["case"][:40], "; ".join(r["gate_fails"])[:60]))
    for lay in ("hr", "rr"):
        n = sum(r[lay + "_n"] for r in live)
        f = sum(len(r[lay + "_fail"]) for r in live)
        u = sum(len(r[lay + "_none"]) for r in live)
        print("%s 判据 %3d 条：pass %3d · fail %3d · 无结果 %3d (%.0f%%)"
              % (lay.upper(), n, n - f - u, f, u, 100 * u / n if n else 0))
    g = collections.Counter(r["gen"] for r in live)
    print("两代分布: %s" % dict(g))
    bad = collections.Counter(r["state"] for r in rows if r["state"] != "ok")
    if bad:
        print("异常: %s" % dict(bad))


if __name__ == "__main__":
    main(sys.argv[1:] or None)
