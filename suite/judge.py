#!/usr/bin/env python3
"""LLM-as-judge 执行器 —— qianfan / deepseek-v4-pro。

  packet(证据) + rubric(AR/HR 条目) -> 逐条 pass/fail/N-A + 依据(必须引证据原文)

约定:
  · 证据包里**不含**规则层旧结论, 防锚定(见 build_judge_packet.py)
  · dsv4 是推理模型, reasoning token 占大头(实测 99/102) -> max_tokens 给足, 否则 content 为空
  · 判官只输出 JSON; 解析失败重试一次并降温
  · 凭据只从 ~/.config/harnessbench/models.toml 读, 永不落盘/打印
"""
import argparse, json, os, re, sys, threading, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 判官后端 —— 优先 DeepSeek 官方(RPM 高得多), 没配则退回 qianfan。
# 实测 qianfan 那把 key: 30 RPM 撞限流, 降到 10 RPM 仍撞; 判官每题发 2-5 次请求, 49 题跑不动。
_DS_ENV = Path(os.path.expanduser("~/.config/harnessbench/deepseek.env"))
_QF_TOML = Path(os.path.expanduser("~/.config/harnessbench/models.toml"))


def _backend():
    if _DS_ENV.is_file():
        m = re.search(r"(?:DEEPSEEK_API_KEY|API_KEY)\s*=\s*\"?([^\"\s]+)", _DS_ENV.read_text())
        if m:
            return ("https://api.deepseek.com/v1", os.environ.get("HB_JUDGE_MODEL", "deepseek-reasoner"),
                    m.group(1), "deepseek-official")
    if _QF_TOML.is_file():
        m = re.search(r'api_key\s*=\s*"([^"]+)"', _QF_TOML.read_text())
        if m:
            return ("https://qianfan.baidubce.com/v2", "deepseek-v4-pro", m.group(1), "qianfan")
    raise SystemExit("找不到判官凭据: 需要 ~/.config/harnessbench/deepseek.env 或 models.toml")


# ★惰性解析凭据 —— import 时就读会让"只想解析 rubric"这类纯本地操作也需要凭据。
_BK = [None]


def _bk():
    if _BK[0] is None:
        _BK[0] = _backend()
    return _BK[0]


class _Lazy(str):
    """BASE/MODEL/BACKEND 用到时才解析，未配置凭据时不阻塞导入。"""
    def __new__(cls, idx):
        o = super().__new__(cls, "")
        o._i = idx
        return o
    def __str__(self):
        return str(_bk()[self._i])
    __repr__ = lambda self: repr(str(self))
    def __eq__(self, other):
        return str(self) == other
    def __hash__(self):
        return hash(str(self))
    def __add__(self, other):
        return str(self) + other
    def __radd__(self, other):
        return other + str(self)


BASE, MODEL, BACKEND = _Lazy(0), _Lazy(1), _Lazy(3)


def _key():
    return _bk()[2]


# ★全局请求节流 —— 实测 qianfan 瞬时 12 并发没问题, 但**持续 RPM 有上限**:
#   8 并发连跑 49 题(每题 2-5 次请求)会撞 rpm_rate_limit_exceeded。
#   所以不靠退避兜, 直接在客户端限速。
_RATE_LOCK = threading.Lock()
_LAST_CALL = [0.0]
# 实测: 2.0s(≈30 RPM) 仍撞 rpm_rate_limit_exceeded -> 起步放到 6s(≈10 RPM), 且**撞一次就永久放慢**
_INTERVAL = [0.5]      # 起步值; 选中 qianfan 时在首次调用处上调
MIN_INTERVAL_SEC = 0.5


def _throttle():
    with _RATE_LOCK:
        if _BK[0] and _BK[0][3] == "qianfan" and _INTERVAL[0] < 6.0:
            _INTERVAL[0] = 6.0        # qianfan RPM 极低, 选中它就自动放慢
        wait = _INTERVAL[0] - (time.time() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[0] = time.time()


def _slow_down(why=""):
    """撞限流就永久放慢 —— 靠退避只能救当前这次, 后面还会接着撞。"""
    with _RATE_LOCK:
        _INTERVAL[0] = min(_INTERVAL[0] * 1.6, 30.0)
        print("  [限速] 全局请求间隔 -> %.1fs %s" % (_INTERVAL[0], why), flush=True)


def call(prompt, max_tokens=4000, temperature=0.0, retries=8):
    key = _key()
    last = None
    for i in range(retries):
        body = json.dumps({"model": str(MODEL), "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": max_tokens, "temperature": temperature}).encode()
        req = urllib.request.Request(str(BASE) + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + key})
        _throttle()
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            msg = d["choices"][0]["message"]
            txt = msg.get("content") or ""
            if not txt.strip():           # reasoning 吃光了预算
                max_tokens = int(max_tokens * 1.8)
                last = "content 为空(reasoning 占满), 提高 max_tokens 重试"
                continue
            return txt, d.get("usage", {})
        except urllib.error.HTTPError as e:
            body_txt = e.read()[:200].decode(errors="ignore")
            last = "HTTP %s %s" % (e.code, body_txt)
            if e.code == 429:
                _slow_down("(429)")
                time.sleep(60 * (i + 1))      # RPM 限流: 退避拉长 + 永久放慢
                continue
            if e.code in (500, 502, 503, 504):
                time.sleep(20 * (i + 1))
                continue
            break
        except Exception as e:
            last = str(e)[:200]
            time.sleep(10 * (i + 1))
    raise RuntimeError("judge 调用失败: %s" % last)


JUDGE_TMPL = """你是 TraceTrial_Bench 的评审官。任务：**区分模型问题与 harness 问题**。

下面给你一份证据包和一组评审条目。逐条判定。

## 判定规则（严格遵守）
1. 取值只能是 `pass` / `fail` / `N-A`。
2. **每条必须引用证据包里的原文片段作为依据**，不许凭印象、不许推测。
3. `N-A` 用于**证据不可得**：轨迹缺失、日志缺失、或交付物格式里根本没有该维度的落点。
   `N-A` 不是 fail，不许当扣分。
4. 机制未被触发但任务完成 → `pass`（设计避开了压力，记功）；未触发且任务未完成 → `fail`。
5. **HR 条目的取值绝不能由交付结果反推** —— 「材料在压缩中丢没丢」不能用「模型做没做对」来回答，
   必须从轨迹/日志的直接证据判定；没有直接证据就判 `N-A`。
6. 标了「场景项·非归因」的条目只作事实记录，不构成对 harness 的指控。

## 证据包
{packet}

## 待判条目（{layer} 层）
{items}

## 输出
只输出 JSON，不要任何其他文字：
{{"verdicts":[{{"id":"条目id","value":"pass|fail|N-A","evidence":"引证据包原文的一句依据"}}]}}
"""


def parse_json(txt):
    txt = txt.strip()
    m = re.search(r"```(?:json)?\s*(.+?)```", txt, re.S)
    if m:
        txt = m.group(1).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i >= 0 and j > i:
        txt = txt[i:j + 1]
    return json.loads(txt)


def judge_layer(packet, rubric, layer, max_tokens=4000):
    items = rubric.get("items") or []
    if not items:
        return []
    lines = []
    for it in items:
        s = "- `%s`: %s" % (it["id"], it["criterion"])
        if it.get("evidence"):
            s += "（取证位置：%s）" % it["evidence"]
        if it.get("note"):
            s += "（注：%s）" % it["note"]
        lines.append(s)
    prompt = JUDGE_TMPL.format(packet=packet, layer=layer, items="\n".join(lines))
    txt, usage = call(prompt, max_tokens=max_tokens)
    try:
        d = parse_json(txt)
    except Exception:
        txt, usage = call(prompt + "\n\n上次输出无法解析为 JSON，请**只输出 JSON**。",
                          max_tokens=int(max_tokens * 1.5), temperature=0.1)
        d = parse_json(txt)
    got = {v["id"]: v for v in d.get("verdicts", []) if isinstance(v, dict) and v.get("id")}
    # ★不许降级: 判官漏返的条目**必须逐条补判**, 绝不静默记成 N-A。
    #   "判官没答" 与 "判官判了证据不可得" 是两件完全不同的事, 混起来会让缺陷凭空消失。
    VALID = {"pass", "fail", "N-A", "n-a", "na", "NA"}
    for attempt in range(3):
        missing = [it for it in items
                   if it["id"] not in got or str(got[it["id"]].get("value", "")).strip() not in VALID]
        if not missing:
            break
        lines2 = ["- `%s`: %s" % (it["id"], it["criterion"]) for it in missing]
        sub = JUDGE_TMPL.format(packet=packet, layer=layer, items="\n".join(lines2))
        sub += "\n\n注意：上一轮你漏了这 %d 条或取值非法，**必须逐条给出 pass/fail/N-A**。" % len(missing)
        t2, u2 = call(sub, max_tokens=max_tokens, temperature=0.1 * (attempt + 1))
        try:
            d2 = parse_json(t2)
        except Exception:
            continue
        for v in d2.get("verdicts", []):
            if isinstance(v, dict) and v.get("id"):
                got[v["id"]] = v
        for k, n in (u2 or {}).items():
            if isinstance(n, int):
                usage[k] = usage.get(k, 0) + n
    still = [it["id"] for it in items
             if it["id"] not in got or str(got[it["id"]].get("value", "")).strip() not in VALID]
    if still:
        raise RuntimeError("判官三轮仍未给出有效取值, 拒绝降级: %s" % still)
    out = []
    for it in items:
        v = got[it["id"]]
        val = str(v.get("value", "")).strip()
        val = "N-A" if val.lower() in ("n-a", "na") else val
        out.append({"id": it["id"], "criterion": it["criterion"], "value": val,
                    "evidence": v.get("evidence", "")})
    return out, usage


def run(case_id, max_chars=140000):
    task = ROOT / case_id
    import build_judge_packet as bjp
    packet = bjp.build(case_id, max_chars)
    # ★只有**交付物**被截断才不许下结论。批量输入材料/源码/工具结果按上限裁剪是正常的,
    #   把它们也算进去会让 W06 这种有 60+ 交付文件的题永远 inconclusive。
    trunc = re.findall(r"\*\*交付物被截断", packet)
    res = {"case": case_id, "judge_model": str(BACKEND) + "/" + str(MODEL), "layers": {},
           "evidence_complete": not trunc,
           "evidence_truncated_files": len(trunc)}
    total_usage = {}
    for layer, fn in (("AR", "rubric_ar.yaml"), ("HR", "rubric_hr.yaml")):
        p = task / "verifier" / fn
        if not p.is_file():
            # ★不许降级: 缺一层 rubric 就不许出归因, 硬失败让人去补
            raise RuntimeError("%s 缺 %s —— 两层都必须走判官, 不接受只判一层" % (case_id, fn))
        rubric = load_yaml(p)
        v, usage = judge_layer(packet, rubric, layer)
        res["layers"][layer] = {"verdicts": v,
                                "pass": sum(1 for x in v if x["value"] == "pass"),
                                "fail": sum(1 for x in v if x["value"] == "fail"),
                                "na": sum(1 for x in v if x["value"] == "N-A"),
                                "total": len(v)}
        for k, n in (usage or {}).items():
            if isinstance(n, int):
                total_usage[k] = total_usage.get(k, 0) + n
    res["usage"] = total_usage
    # 归因: HR 有 fail -> harness 侧; HR 全过而 AR 有 fail -> 模型侧
    hr = res["layers"].get("HR", {})
    ar = res["layers"].get("AR", {})
    if not res["evidence_complete"]:
        res["attribution"] = "inconclusive(证据被截断 %d 处, 不许当完整证据下结论)" % res["evidence_truncated_files"]
    elif hr.get("fail"):
        res["attribution"] = "harness-attributable"
    elif hr.get("total") and hr.get("fail") == 0 and ar.get("fail"):
        res["attribution"] = "model-side"
    elif hr.get("total") and ar.get("total") and not hr.get("fail") and not ar.get("fail"):
        res["attribution"] = "clean"
    else:
        res["attribution"] = "inconclusive"
    return res


def load_yaml(p):
    """极简 YAML 读取 —— 支持本项目 rubric 的形状, **含折叠标量** `key: >` + 缩进续行。

    ★踩过的坑: 原版把 `criterion: >` 的值读成字符串 ">" , 续行全丢 ——
      判官会拿着一堆空判据去判, 而且照样返回看起来正常的结果。跑判官前必须自检。
    """
    items, cur, fold_key, fold_buf = [], None, None, []

    def flush_fold():
        nonlocal fold_key, fold_buf
        if cur is not None and fold_key:
            cur[fold_key] = " ".join(x.strip() for x in fold_buf if x.strip())
        fold_key, fold_buf = None, []

    for raw in p.read_text().splitlines():
        if not raw.strip():
            continue
        if raw.lstrip().startswith("#"):
            continue
        m_item = re.match(r"^\s*-\s+id:\s*(.+)$", raw)
        if m_item:
            flush_fold()
            if cur:
                items.append(cur)
            cur = {"id": m_item.group(1).strip()}
            continue
        m_kv = re.match(r"^(\s+)(\w+):\s*(.*)$", raw)
        if m_kv and cur is not None:
            key, val = m_kv.group(2), m_kv.group(3).strip()
            if val in (">", "|", ">-", "|-"):     # 折叠/字面标量: 值在后续缩进行里
                flush_fold()
                fold_key = key
                continue
            flush_fold()
            cur[key] = val
            continue
        if fold_key and cur is not None:          # 折叠标量的续行
            fold_buf.append(raw)
    flush_fold()
    if cur:
        items.append(cur)
    # 自检: 判据为空或退化成标量记号的条目一律拒绝, 不许让判官拿空判据去判
    bad = [it.get("id") for it in items
           if not str(it.get("criterion", "")).strip() or str(it.get("criterion")).strip() in (">", "|")]
    if bad:
        raise RuntimeError("%s 里这些条目的 criterion 解析为空, 拒绝继续: %s" % (p.name, bad))
    return {"items": items}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--conc", type=int, default=8)
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--max-chars", type=int, default=140000)
    ap.add_argument("--out", default="judge")
    a = ap.parse_args()
    outd = ROOT / a.out
    outd.mkdir(exist_ok=True)
    cases = a.cases
    if cases == ["--all"] or a.all:
        import glob as _g
        # ★绝对路径 glob 的 parts[0] 是 "/" 不是题名 —— 踩过, 49 个全判失败
        cases = sorted(Path(f).parent.parent.name for f in _g.glob(str(ROOT / "*/verifier/rubric_ar.yaml")))
    if a.skip_existing:
        cases = [c for c in cases if not (outd / (c + ".json")).is_file()]

    def one(cid):
        r = run(cid, a.max_chars)
        (outd / (cid + ".json")).write_text(json.dumps(r, ensure_ascii=False, indent=1))
        return cid, r

    import concurrent.futures as cf
    with cf.ThreadPoolExecutor(min(a.conc, max(1, len(cases)))) as ex:
        futs = {ex.submit(one, c): c for c in cases}
        for fut in cf.as_completed(futs):
            cid = futs[fut]
            try:
                cid, r = fut.result()
            except Exception as e:
                print("%-44s ★失败: %s" % (cid, str(e)[:140]), flush=True)
                continue
            ar, hr = r["layers"].get("AR", {}), r["layers"].get("HR", {})
            print("%-44s AR %s/%s(na%s)  HR %s/%s(na%s)  -> %s" % (
                cid, ar.get("pass", "-"), ar.get("total", "-"), ar.get("na", "-"),
                hr.get("pass", "-"), hr.get("total", "-"), hr.get("na", "-"), r["attribution"]), flush=True)
