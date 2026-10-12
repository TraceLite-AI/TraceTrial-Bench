#!/usr/bin/env python3
"""把批评者(review_rubrics.py)的改法应用到 rubric 文件, 并产出可读 diff 供人工复核。

为什么要产出 diff 而不是静默应用:
  盲信批评者和盲信生成器是同一个毛病。批评者也会判错 —— 它可能把一条**正确的严格条目**
  当成"发明约束"给放宽, 那等于把缺陷放过去。所以: 应用 + 全量 diff 落盘 + 我逐条读。

用法:
  python3 apply_rubric_fixes.py --dry            # 只出 diff 不改
  python3 apply_rubric_fixes.py                  # 应用 + 写 RUBRIC_FIXES.md
  python3 apply_rubric_fixes.py --only A         # 只应用某一类
"""
import argparse, difflib, glob, json, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_criterion(text, item_id, new_criterion):
    """在 yaml 文本里就地替换某条目的 criterion（本项目 rubric 是固定两/三行形状）。"""
    lines = text.splitlines()
    out, i, hit = [], 0, False
    while i < len(lines):
        out.append(lines[i])
        if re.match(r"^\s*-\s+id:\s*%s\s*$" % re.escape(item_id), lines[i]):
            i += 1
            while i < len(lines) and not re.match(r"^\s*-\s+id:", lines[i]):
                if re.match(r"^\s+criterion:", lines[i]):
                    out.append("    criterion: %s" % new_criterion.replace("\n", " ").strip())
                    hit = True
                else:
                    out.append(lines[i])
                i += 1
            continue
        i += 1
    return ("\n".join(out) + "\n"), hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--only", default="ABCD")
    a = ap.parse_args()
    report = ["# rubric 改法复核 · 批评者标出的问题与应用结果\n",
              "> 批评者也会判错 —— 它可能把一条**正确的严格条目**当成「发明约束」给放宽，",
              "> 那等于把缺陷放过去。所以这里逐条列出 diff，必须人工读一遍。\n"]
    stat = {"A": 0, "B": 0, "C": 0, "D": 0, "applied": 0, "miss": 0}
    for f in sorted(glob.glob(str(ROOT / "rubric_review" / "*.json"))):
        d = json.loads(Path(f).read_text())
        cid = d["case"]
        for layer, x in (d.get("layers") or {}).items():
            flags = [fl for fl in (x.get("flags") or []) if fl.get("kind", "B") in a.only and fl.get("fix")]
            if not flags:
                continue
            p = ROOT / cid / "verifier" / ("rubric_%s.yaml" % layer.lower())
            if not p.is_file():
                continue
            text = p.read_text()
            for fl in flags:
                stat[fl.get("kind", "B")] = stat.get(fl.get("kind", "B"), 0) + 1
                old_m = re.search(r"^\s*-\s+id:\s*%s\s*$\n((?:\s+\w+:.*\n)+)" % re.escape(fl["id"]),
                                  text, re.M)
                old_crit = ""
                if old_m:
                    cm = re.search(r"criterion:\s*(.+)", old_m.group(1))
                    old_crit = cm.group(1).strip() if cm else ""
                new_text, hit = replace_criterion(text, fl["id"], fl["fix"])
                if not hit:
                    stat["miss"] += 1
                    report.append("\n## ⚠ %s · %s · `%s` —— **没找到该条目，未应用**\n" % (cid, layer, fl["id"]))
                    continue
                text = new_text
                stat["applied"] += 1
                report.append("\n## [%s] %s · %s · `%s`\n" % (fl.get("kind"), cid, layer, fl["id"]))
                report.append("**批评者认为**：%s\n" % fl.get("why", ""))
                report.append("```diff")
                for ln in difflib.unified_diff([old_crit], [fl["fix"].strip()],
                                               fromfile="原", tofile="改后", lineterm="", n=0):
                    report.append(ln)
                report.append("```")
            if not a.dry:
                p.write_text(text)
    report.insert(3, "\n**合计**：A 发明约束 %d · B 歧义 %d · C 格式琐事 %d · ★D 范围锚错 %d ｜ 已应用 %d，未命中 %d\n"
                  % (stat["A"], stat["B"], stat["C"], stat["D"], stat["applied"], stat["miss"]))
    out = ROOT / "RUBRIC_FIXES.md"
    if not a.dry:
        out.write_text("\n".join(report))
    print("\n".join(report[:6]))
    print("\n%s → %s" % ("（dry-run，未改文件）" if a.dry else "已应用并写入 RUBRIC_FIXES.md", out.name))


if __name__ == "__main__":
    main()
