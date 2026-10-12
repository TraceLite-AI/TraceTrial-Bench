#!/usr/bin/env python
"""D2 verifier: 判**代码实际内容** —— 只在第 2 轮口头说过的错误码，后期有没有用对。"""
import argparse, ast, json, re, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(); ap.add_argument("--workspace", required=True)
ap.add_argument("--harness", default="openclaw"); ap.add_argument("--state", default="")
ap.add_argument("--exit-code", type=int, default=0); ap.add_argument("--label", default="")
a = ap.parse_args(); ws = Path(a.workspace)
T = json.loads((HERE/"truth.json").read_text())
if a.exit_code in (124, 137, 143):
    print(json.dumps({"label": a.label, "verdict": "inconclusive",
        "reason": "外部终止 exit=%d" % a.exit_code}, ensure_ascii=False, indent=1)); raise SystemExit

def _gw(k):
    g = HERE.parent/"gw.log"
    return len(re.findall(k, g.read_text(errors="ignore"), re.I)) if g.is_file() else 0
gw = {"overflow": _gw(r"context overflow detected"), "compact_ok": _gw(r"auto-compaction succeeded"),
      "blocked": _gw(r"already_compacted_recently"), "hard": _gw(r"prompt too large"),
      "truncate": _gw(r"truncate_tool_results")}

allsrc = ""
for p in sorted((ws/"ops").glob("*.py")) + sorted((ws/"tests").glob("*.py")):
    try: allsrc += "\n### " + p.name + "\n" + p.read_text(errors="ignore")
    except Exception: pass

# ① 中途用到的 4 个码：应出现
mid_ok = [c for c in T["mid_codes"] if c in allsrc]
# ② ★只口头说过的目标码：后期必须用对
tgt = {}
for c, cond, rt in T["target_codes"]:
    used = c in allsrc
    # 该码是否配了正确的 retryable
    ok_rt = bool(re.search(re.escape(c) + r"[\"'\s,)]*.{0,60}retryable\s*=\s*" + ("True" if rt else "False"), allsrc, re.S)) \
            or bool(re.search(r"OpsError\(\s*[\"']" + re.escape(c) + r"[\"']\s*,\s*" + ("True" if rt else "False"), allsrc))
    tgt[c] = {"条件": cond, "用到": used, "retryable正确": ok_rt}
# ③ 有没有发明码表外的错误码
invented = sorted(set(re.findall(r"[\"']E-\d{4}[\"']", allsrc)))
invented = [x.strip("\"'") for x in invented if x.strip("\"'") not in T["all_codes"]]
# ④ 三元组形状：handle 是否 return 三元组且不再抛自定义异常
# ★用 AST 判, 不用正则: `return (False, None, e)` 和 `return False, None, e` 都是三元组,
#   后者是 Python 惯用写法; 正则硬要括号会把完全正确的实现判成没改(实测踩中)。
import ast as _ast
tuple_ok = 0; raise_left = 0; nmod = 0
for p in sorted((ws/"ops").glob("*.py")):
    if p.name in ("__init__.py", "errors.py"): continue
    nmod += 1
    try: _tree = _ast.parse(p.read_text(errors="ignore"))
    except SyntaxError: continue
    _fn = next((n for n in _ast.walk(_tree)
                if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and n.name == "handle"), None)
    _scope = _fn if _fn is not None else _tree
    _rets = [n for n in _ast.walk(_scope) if isinstance(n, _ast.Return) and n.value is not None]
    if _fn is not None and _rets and all(isinstance(r.value, _ast.Tuple) and len(r.value.elts) == 3 for r in _rets):
        tuple_ok += 1
    if any(isinstance(n, _ast.Raise) for n in _ast.walk(_scope)): raise_left += 1
# ⑤ 测试
r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                   capture_output=True, text=True, cwd=str(ws), timeout=300)
tout = (r.stderr or "") + (r.stdout or "")
m = re.search(r"Ran (\d+) tests?", tout); ran = int(m.group(1)) if m else 0
green = r.returncode == 0 and ran > 0

RR = {"RR1_tests_green": green,
      "RR2_tuple_shape_adopted": nmod > 0 and tuple_ok == nmod,
      "RR3_no_legacy_raise": raise_left == 0,
      "RR4_mid_codes_used": len(mid_ok) >= len(T["mid_codes"]) - 1,
      "RR5_target_codes_used": all(v["用到"] for v in tgt.values()),
      "RR6_target_retryable_correct": all(v["retryable正确"] for v in tgt.values()),
      "RR7_no_invented_codes": len(invented) == 0}
attr = []
lost = [c for c, v in tgt.items() if not v["用到"]]
if lost:
    verdict = ("harness-attributable(候选: 压缩摘掉早期口头契约)"
               if (gw["hard"] > 0 or gw["blocked"] > 0 or gw["truncate"] > 0)
               else "model-side(未遵守早期约定)")
    attr.append({"layer": "HR" if verdict.startswith("harness") else "RR",
        "item": "RR5_target_codes_used", "verdict": verdict,
        "evidence": "只在第 2 轮口头说过的码未被使用: %s（盘上任何文件都没出现过这些码）| harness侧 %s" % (lost, gw)})
if invented:
    attr.append({"layer": "RR", "item": "RR7_no_invented_codes", "verdict": "model-side(自创码表)",
        "evidence": "使用了约定外的错误码: %s" % invented})
if not green:
    attr.append({"layer": "RR", "item": "RR1_tests_green", "verdict": "model-side(测试未绿)",
        "evidence": tout.strip().splitlines()[-1] if tout.strip() else "无输出"})
print(json.dumps({"label": a.label, "harness": a.harness, "RR": RR,
    "rr_pass_all": all(RR.values()),
    "metrics": {"modules": nmod, "tuple_shape_ok": tuple_ok, "legacy_raise_left": raise_left,
                "mid_codes_used": len(mid_ok), "mid_codes_total": len(T["mid_codes"]),
                "target_codes": tgt, "invented_codes": invented,
                "tests_ran": ran, "tests_green": green, "gw": gw},
    "attribution": attr}, ensure_ascii=False, indent=1))
