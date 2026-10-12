#!/usr/bin/env python3
"""rubric 批评者 —— 用同一个模型做二次意见, 专挑三类会污染结论的毛病。

为什么需要这一道:
  实测生成器会**发明题面里没有的约束**。E2 第一版写出"某步 incomplete 则其后不得 applied",
  题面从无此规定, 而实际正是第6步未完成、第7-10步已生效 —— 这条会把**完全正确的交付物判成 fail**,
  凭空造出一个模型缺陷。这类条目必须在过判官之前拦掉。

三类毛病:
  A 发明约束   —— 要求在题面/多轮材料/标准答案里指不出原句
  B 歧义       —— 两个判官按字面读会得出不同结论(实测 hr_no_rework_induced 就因此被判反)
  C 格式琐事   —— 抠大小写/标题层级/换行, 与任务成败无关

用法: python3 review_rubrics.py <case>... | --all [--conc 8]
输出: rubric_review/<case>.json  + 终端汇总
"""
import argparse, glob, json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from judge import call, load_yaml          # noqa: E402
from gen_rubrics import brief              # noqa: E402

TMPL = """你在审查一份 agent 评测基准的**评审条目**。你的任务不是判题，而是找出**会污染结论的条目**。

## 这道题的全部材料
### 题面
{prompt}

### 多轮补充材料摘要
{turns}

### 标准答案的键
{truth}

### ★材料规模（程序从材料里数出来的客观事实）
{scale}

## 待审查的条目
{items}

## 找这三类毛病
**A 发明约束**：该条目要求的东西，在上面的材料里**指不出原句**。
  真实反例：一道迁移题的条目写「某步骤为 incomplete 则其后所有步骤不得为 applied」——
  题面从未规定步骤间有此依赖，而实际情况正是第 6 步未完成、第 7-10 步已生效。
  这种条目会把**完全正确的交付物判成 fail**，凭空造出一个模型缺陷。**这是最严重的一类。**

**B 歧义**：两个评审者严格按字面读，会得出不同结论。
  真实反例：「是否存在被迫重复劳动」——评审者把模型主动复核算成了"被迫重复"，判成 fail，整个归因翻错。

**C 格式琐事**：抠大小写、标题层级、换行分段、字段顺序，与任务成败无关。

**D 范围锚错（最隐蔽，会让判据对真实缺陷失明）**：条目里"应完成多少"的基准，
  取自**交付物自己声称的范围**，而不是上面「材料规模」给的客观数字。
  真实反例：一道审计题里 agent 自行把范围切成 28 个批次并全部完成，条目写成
  「报告须包含所有批次（28 批）的结论」→ 三条完整性条目全部通过，
  而材料实际发出 3600 条凭证、它只覆盖 2761 条，**839 条缺口对判据完全不可见**。
  凡涉及"是否做全"的条目，基准必须是「材料规模」里的数字。

## 输出
只输出合法 JSON。对每个有问题的条目给出类别、理由、以及修正建议（改写后的 criterion）。
没问题的条目不要列出。criterion 内部禁止英文双引号，用「」代替。

{{"flags":[{{"id":"条目id","kind":"A|B|C|D","why":"为什么有问题","fix":"改写后的完整 criterion"}}],
 "clean_count":没问题的条目数}}
"""


def review(case_id):
    task = ROOT / case_id
    prompt, turns, truth, _, _, scale = brief(task)
    out = {"case": case_id, "layers": {}}
    for layer, fn in (("AR", "rubric_ar.yaml"), ("HR", "rubric_hr.yaml")):
        p = task / "verifier" / fn
        if not p.is_file():
            continue
        items = load_yaml(p)["items"]
        lines = "\n".join("- `%s`: %s" % (i["id"], i["criterion"]) for i in items)
        txt, _ = call(TMPL.format(prompt=prompt, turns=turns, truth=truth, scale=scale, items=lines), max_tokens=5000)
        m = re.search(r"```(?:json)?\s*(.+?)```", txt, re.S)
        if m:
            txt = m.group(1)
        i, j = txt.find("{"), txt.rfind("}")
        try:
            d = json.loads(txt[i:j + 1])
        except Exception as e:
            d = {"flags": [], "parse_error": str(e)[:120]}
        d["total"] = len(items)
        out["layers"][layer] = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--conc", type=int, default=8)
    a = ap.parse_args()
    cases = a.cases
    if a.all:
        cases = sorted(Path(f).parts[0] for f in glob.glob("*/verifier/rubric_ar.yaml"))
    outd = ROOT / "rubric_review"
    outd.mkdir(exist_ok=True)
    import concurrent.futures as cf
    tot_flag = {"A": 0, "B": 0, "C": 0, "D": 0}
    with cf.ThreadPoolExecutor(min(a.conc, max(1, len(cases)))) as ex:
        futs = {ex.submit(review, c): c for c in cases}
        for fut in cf.as_completed(futs):
            cid = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                print("%-44s ★失败: %s" % (cid, str(e)[:100]), flush=True)
                continue
            (outd / (cid + ".json")).write_text(json.dumps(r, ensure_ascii=False, indent=1))
            bits = []
            for layer, d in r["layers"].items():
                fl = d.get("flags") or []
                for f in fl:
                    tot_flag[f.get("kind", "B")] = tot_flag.get(f.get("kind", "B"), 0) + 1
                bits.append("%s %d/%d 有问题" % (layer, len(fl), d.get("total", 0)))
            print("%-44s %s" % (cid, " | ".join(bits)), flush=True)
    print("\n合计: A 发明约束 %d · B 歧义 %d · C 格式琐事 %d · ★D 范围锚错 %d" %
          (tot_flag.get("A", 0), tot_flag.get("B", 0), tot_flag.get("C", 0), tot_flag.get("D", 0)))


if __name__ == "__main__":
    main()
