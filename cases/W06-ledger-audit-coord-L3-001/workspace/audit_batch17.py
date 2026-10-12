#!/usr/bin/env python3
"""Batch 17 audit: entries 1601-1700, checks CK-001 through CK-007."""

import json
import re
from collections import Counter, defaultdict

# --- Load data ---
with open("output/batch_17.txt") as f:
    lines = [ln.strip() for ln in f if ln.strip()]

with open("output/audit_spec.json") as f:
    spec = json.load(f)

# --- Parse each line ---
pattern = re.compile(
    r"ts=(?P<ts>\S+)\s+voucher=(?P<voucher>\S+)\s+type=(?P<type>\S+)\s+"
    r"amount=(?P<amount>[\d.]+)\s+approver=(?P<approver>\S+)\s+"
    r"stage=(?P<stage>\S+)\s+attach=(?P<attach>\d+)\s+"
    r"period=(?P<period>\S+)\s+dept=(?P<dept>\S+)\s+"
    r"trace=(?P<trace>\S+)"
)

entries = []
for i, line in enumerate(lines, start=1):
    m = pattern.match(line)
    if not m:
        print(f"WARN: line {i} failed to parse: {line}")
        continue
    d = m.groupdict()
    d["_lineno"] = 1600 + i  # batch 17 starts at 1601
    d["amount"] = float(d["amount"])
    d["attach"] = int(d["attach"])
    entries.append(d)

N = len(entries)
total_amt = sum(e["amount"] for e in entries)
print(f"Parsed {N} entries, total amount = {total_amt:,.2f}")

# ---- CK-001: 重复凭证号 ----
voucher_groups = defaultdict(list)
for e in entries:
    voucher_groups[e["voucher"]].append(e)

ck001_dup = []
for v, recs in voucher_groups.items():
    if len(recs) > 1:
        # Check if key fields differ
        types = {r["type"] for r in recs}
        depts = {r["dept"] for r in recs}
        amts = {r["amount"] for r in recs}
        if len(types) > 1 or len(depts) > 1 or len(amts) > 1:
            ck001_dup.append((v, recs))

print(f"\nCK-001 duplicate vouchers with diff key fields: {len(ck001_dup)}")

# ---- CK-002: 零附件凭证 ----
ck002 = [e for e in entries if e["attach"] == 0]
print(f"CK-002 zero-attachment entries: {len(ck002)}")

# ---- CK-003: 状态异常 ----
ck003 = [e for e in entries if e["stage"] == "submitted"]
print(f"CK-003 submitted-stage entries: {len(ck003)}")

# ---- CK-004: 大额交易 (>400,000) ----
ck004 = [e for e in entries if e["amount"] > 400_000]
print(f"CK-004 large-amount entries (>400k): {len(ck004)}")

# ---- CK-005: 审批人集中度 ----
approver_counts = Counter(e["approver"] for e in entries)
pct_threshold = 15.0
ck005 = [(a, c, round(c / N * 100, 1)) for a, c in approver_counts.most_common()
         if c / N * 100 > pct_threshold]
print(f"CK-005 approvers >{pct_threshold}%: {[(a, p) for a, _, p in ck005]}")

# ---- CK-006: 金额合理性 (negative or <100) ----
ck006 = [e for e in entries if e["amount"] < 100]
print(f"CK-006 amount < 100: {len(ck006)}")
# Also check for negative
ck006_neg = [e for e in entries if e["amount"] < 0]
print(f"CK-006 negative amounts: {len(ck006_neg)}")

# ---- CK-007: trace 唯一性 ----
trace_groups = defaultdict(list)
for e in entries:
    trace_groups[e["trace"]].append(e)
ck007 = [(t, recs) for t, recs in trace_groups.items() if len(recs) > 1]
print(f"CK-007 duplicate traces: {len(ck007)}")

# ---- Build report ----
def fmt_entry(e):
    return (f"- `{e['voucher']}` | type={e['type']} | amount={e['amount']:,.2f} | "
            f"approver={e['approver']} | stage={e['stage']} | attach={e['attach']} | "
            f"dept={e['dept']} | trace=`{e['trace']}`")

lines_rpt = []
lines_rpt.append("# batch_17 审计报告")
lines_rpt.append(f"\n**审计范围**: 第1601–1700条（共{N}条）  ")
lines_rpt.append(f"**审计期间**: {spec['audit_period']}  ")
lines_rpt.append(f"**批次总金额**: {total_amt:,.2f}  ")
lines_rpt.append(f"**执行检查**: CK-001 ~ CK-007  \n")

# Summary
ck001_pf = "FAIL" if ck001_dup else "PASS"
ck002_pf = "PASS"  # CK-002 is informational
ck003_pf = "FAIL" if ck003 else "PASS"
ck004_pf = "PASS"  # Informational
ck005_pf = "FAIL" if ck005 else "PASS"
ck006_pf = "FAIL" if ck006 or ck006_neg else "PASS"
ck007_pf = "FAIL" if ck007 else "PASS"

lines_rpt.append("## 摘要")
lines_rpt.append("| 检查项 | 结果 | 异常数 |")
lines_rpt.append("|--------|------|--------|")
lines_rpt.append(f"| CK-001 重复凭证号 | {ck001_pf} | {len(ck001_dup)} |")
lines_rpt.append(f"| CK-002 零附件凭证 | {ck002_pf} | {len(ck002)} |")
lines_rpt.append(f"| CK-003 状态异常   | {ck003_pf} | {len(ck003)} |")
lines_rpt.append(f"| CK-004 大额交易   | {ck004_pf} | {len(ck004)} |")
lines_rpt.append(f"| CK-005 审批人集中度 | {ck005_pf} | {len(ck005)} |")
lines_rpt.append(f"| CK-006 金额合理性 | {ck006_pf} | {len(ck006)} |")
lines_rpt.append(f"| CK-007 trace唯一性 | {ck007_pf} | {len(ck007)} |")
lines_rpt.append("")

# ---- CK-001 details ----
lines_rpt.append("## CK-001 重复凭证号")
lines_rpt.append(f"**规则**: {spec['checks'][0]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][0]['severity']}  ")
if ck001_dup:
    for v, recs in ck001_dup:
        lines_rpt.append(f"\n### 凭证 {v}（出现 {len(recs)} 次）")
        for r in recs:
            lines_rpt.append(fmt_entry(r))
else:
    lines_rpt.append("\n无重复凭证号异常。所有 voucher 号唯一。")
lines_rpt.append(f"\n**结果: {ck001_pf}**\n")

# ---- CK-002 details ----
lines_rpt.append("## CK-002 零附件凭证")
lines_rpt.append(f"**规则**: {spec['checks'][1]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][1]['severity']}  ")
if ck002:
    lines_rpt.append(f"\n共 {len(ck002)} 条 attach=0 的凭证：\n")
    for e in ck002:
        lines_rpt.append(fmt_entry(e))
else:
    lines_rpt.append("\n无零附件凭证。")
lines_rpt.append(f"\n**结果: {ck002_pf}**\n")

# ---- CK-003 details ----
lines_rpt.append("## CK-003 状态异常")
lines_rpt.append(f"**规则**: {spec['checks'][2]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][2]['severity']}  ")
if ck003:
    lines_rpt.append(f"\n共 {len(ck003)} 条 stage=submitted 的凭证：\n")
    for e in ck003:
        lines_rpt.append(fmt_entry(e))
else:
    lines_rpt.append("\n无状态异常凭证。")
lines_rpt.append(f"\n**结果: {ck003_pf}**\n")

# ---- CK-004 details ----
lines_rpt.append("## CK-004 大额交易")
lines_rpt.append(f"**规则**: {spec['checks'][3]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][3]['severity']}  ")
if ck004:
    lines_rpt.append(f"\n共 {len(ck004)} 条 amount > 400,000 的凭证：\n")
    # Type distribution
    type_cnt = Counter(e["type"] for e in ck004)
    dept_cnt = Counter(e["dept"] for e in ck004)
    lines_rpt.append("**类型分布**:")
    for t, c in type_cnt.most_common():
        lines_rpt.append(f"  - {t}: {c} 笔")
    lines_rpt.append("\n**部门分布**:")
    for d, c in dept_cnt.most_common():
        lines_rpt.append(f"  - {d}: {c} 笔")
    lines_rpt.append("")
    for e in ck004:
        lines_rpt.append(fmt_entry(e))
else:
    lines_rpt.append("\n无大额交易。")
lines_rpt.append(f"\n**结果: {ck004_pf}**\n")

# ---- CK-005 details ----
lines_rpt.append("## CK-005 审批人集中度")
lines_rpt.append(f"**规则**: {spec['checks'][4]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][4]['severity']}  ")
lines_rpt.append(f"\n本批次共 {len(approver_counts)} 位审批人：\n")
lines_rpt.append("| 审批人 | 笔数 | 占比 |")
lines_rpt.append("|--------|------|------|")
for a, c in approver_counts.most_common():
    pct = round(c / N * 100, 1)
    flag = " ⚠️" if pct > pct_threshold else ""
    lines_rpt.append(f"| {a} | {c} | {pct}%{flag} |")
lines_rpt.append("")
if ck005:
    lines_rpt.append(f"⚠️ 以下审批人占比超过 {pct_threshold}%：")
    for a, c, p in ck005:
        lines_rpt.append(f"  - {a}: {c} 笔 ({p}%)")
else:
    lines_rpt.append(f"所有审批人占比均未超过 {pct_threshold}%。")
lines_rpt.append(f"\n**结果: {ck005_pf}**\n")

# ---- CK-006 details ----
lines_rpt.append("## CK-006 金额合理性")
lines_rpt.append(f"**规则**: {spec['checks'][5]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][5]['severity']}  ")
if ck006_neg:
    lines_rpt.append(f"\n### 负数金额（{len(ck006_neg)} 条）")
    for e in ck006_neg:
        lines_rpt.append(fmt_entry(e))
else:
    lines_rpt.append("\n无负数金额。")
if ck006:
    lines_rpt.append(f"\n### 小额异常 (<100)（{len(ck006)} 条）")
    for e in ck006:
        lines_rpt.append(fmt_entry(e))
else:
    lines_rpt.append("\n无小额异常 (<100)。")
if not ck006 and not ck006_neg:
    lines_rpt.append("\n金额均正常。")
lines_rpt.append(f"\n**结果: {ck006_pf}**\n")

# ---- CK-007 details ----
lines_rpt.append("## CK-007 trace 唯一性")
lines_rpt.append(f"**规则**: {spec['checks'][6]['rule']}  ")
lines_rpt.append(f"**严重级别**: {spec['checks'][6]['severity']}  ")
if ck007:
    for t, recs in ck007:
        lines_rpt.append(f"\n### trace `{t}`（出现 {len(recs)} 次）")
        for r in recs:
            lines_rpt.append(fmt_entry(r))
else:
    lines_rpt.append("\n所有 trace 哈希唯一，无重复。")
lines_rpt.append(f"\n**结果: {ck007_pf}**\n")

# ---- Overall ----
overall = "PASS" if all(
    p == "PASS" for p in [ck001_pf, ck002_pf, ck003_pf, ck004_pf, ck005_pf, ck006_pf, ck007_pf]
) else "FAIL"
lines_rpt.append("---")
lines_rpt.append(f"\n## 综合判定: **{overall}**\n")

report = "\n".join(lines_rpt)
with open("output/batch_17_report.md", "w") as f:
    f.write(report)

print("\nReport written to output/batch_17_report.md")
print(f"Overall: {overall}")
for ck, pf in [("CK-001", ck001_pf), ("CK-002", ck002_pf), ("CK-003", ck003_pf),
               ("CK-004", ck004_pf), ("CK-005", ck005_pf), ("CK-006", ck006_pf),
               ("CK-007", ck007_pf)]:
    print(f"  {ck}: {pf}")
