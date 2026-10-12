# -*- coding: utf-8 -*-
"""core 判定与 HR 清洗的**单一来源**。主表和一切下钻脚本都从这里取，避免两处口径漂移。

三件事：
  clean_hr()  从 HR 里剔掉「不是判据的东西」
                · excluded：场景说明（SCENARIO_*）与元信息（attr.no_harness_attribution 等）
                · attribution 回声：`attr.X` 与 `X` 同现时丢掉 `attr.X`（同一条判据被抽了两次）
  core_of()   该 run 里算 core 的 key
                · v3 族：读 rubric_harness.yaml 的 core: true
                · 旧族：查 core_map_old.yaml（尺子从 v3 族 82 条读出来的，见该文件头）
  unmapped()  map 没覆盖到的 key —— 漏标会被静默当成非 core，必须报出来
"""
import re, sys
from pathlib import Path
import yaml

R = Path(__file__).resolve().parent
MAP = yaml.safe_load((R / "core_map_old.yaml").read_text(encoding="utf-8"))

# ── 旧族整改表：逐条读过 score.py 之后定的处置，人读版见 fix_old34.yaml ──
FIX = yaml.safe_load((R / "old_fix.yaml").read_text(encoding="utf-8"))


def _match(case_name: str, spec: str):
    """spec 形如 "E1-config-remediation:HR4_run_completed"，前缀匹配目录名。"""
    pre, _, key = spec.partition(":")
    return case_name.startswith(pre), key


def _dropped(case_name: str) -> set:
    """该题要从 HR 删掉的 key。"""
    out = set()
    for spec in (FIX.get("drop") or []):
        ok, k = _match(case_name, spec)
        if ok:
            out.add(k)
    return out


def _facts(case_name: str) -> set:
    """真观测但题目明确不当缺陷判的 key（保留在 HR，不进 core）。"""
    out = set()
    for spec in (FIX.get("fact") or []):
        ok, k = _match(case_name, spec)
        if ok:
            out.add(k)
    return out


def _fix_core(case_name: str):
    """整改表里给该题声明的 core 集合；没声明则回 None（落回启发式 map）。"""
    for pre, keys in (FIX.get("core") or {}).items():
        if case_name.startswith(pre):
            return set(keys)
    return None
OLD_CORE = set(MAP.get("core") or {})
OLD_NONCORE = set(MAP.get("non_core") or {})
EXCLUDED = set(MAP.get("excluded") or {})
DEDUPE = bool(MAP.get("dedupe_attr_echo"))
_OFFICIAL = "attr.official_grader"


def clean_hr(hr: dict, case_name: str = "") -> dict:
    """从 HR 里剔掉「不是 harness 判据」的东西。

    除 EXCLUDED（场景说明/元信息）与 attribution 回声之外，
    还剔掉旧族整改表 drop 里的三类（逐条依据见 fix_old34.yaml）：
      ① 恒真常量      HR*_run_completed 写死 True（11 道）、clean_n>=0（E4）
      ② 从交付物反读  H1/H2 的 HR2 取自模型交付物，冒充 harness 观测
      ③ 与 RR 重复    E1 HR3 == RR6、L11 HR2 == RR3，同一布尔量算两层
    """
    out = {k: v for k, v in hr.items() if k not in EXCLUDED}
    if case_name:
        for k in _dropped(case_name):
            out.pop(k, None)
            # ★删掉的 key 若还有 attribution 回声（attr.<key>），一并删 ——
            #   X 既然不是合格的 harness 判据，它的回声副本也不是。
            out.pop("attr." + k, None)
    if DEDUPE:
        for k in list(out):
            if k.startswith("attr.") and k[5:] in out:
                del out[k]
    return out


def _v3_core(case_dir: Path):
    """v3 族返回 core id 集合；旧族返回 None。"""
    p = case_dir / "verifier" / "rubric_harness.yaml"
    if not p.is_file():
        return None
    return {b.split("\n")[0].strip()
            for b in re.split(r"(?m)^  - id:", p.read_text(errors="ignore"))[1:]
            if re.search(r"core:\s*true", b)}


def core_of(case_dir: Path, hr: dict) -> set:
    v3 = _v3_core(case_dir)
    if v3 is not None:
        return v3 & set(hr)
    # ★整改表优先：题目自己在 score.py 里声明过 core 的（如 L1 的 core_HR），以它为准
    fixed = _fix_core(case_dir.name)
    if fixed is not None:
        return (fixed & set(hr)) - _facts(case_dir.name)
    out = {k for k in hr if k in OLD_CORE}
    # 官方 grader 背书的一条：verify 层已保证 verdict 含 harness-attributable 才给 True/False，
    # inconclusive 给 None。取值非 None 即为一条有效的 core 判据。
    if hr.get(_OFFICIAL) is not None:
        out.add(_OFFICIAL)
    return out


def unmapped(case_dir: Path, hr: dict) -> set:
    if _v3_core(case_dir) is not None:
        return set()
    return set(hr) - OLD_CORE - OLD_NONCORE - EXCLUDED - {_OFFICIAL}
