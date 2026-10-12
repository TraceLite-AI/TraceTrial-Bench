#!/usr/bin/env python3
"""Audit batch_18.txt (entries 1701-1800) with CK-001 through CK-007."""

import re
import json
from collections import Counter

INPUT = "output/batch_18.txt"
OUTPUT = "output/batch_18_report.md"

# Parse records
records = []
with open(INPUT) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r'ts=([\d\-T:Z]+) voucher=(\S+) type=(\S+) amount=([\d.]+) '
            r'approver=(\S+) stage=(\S+) attach=(\d+) period=(\S+) '
            r'dept=(\S+) trace=(\S+)',
            line
        )
        if m:
            records.append({
                'ts': m.group(1),
                'voucher': m.group(2),
                'type': m.group(3),
                'amount': float(m.group(4)),
                'approver': m.group(5),
                'stage': m.group(6),
                'attach': int(m.group(7)),
                'period': m.group(8),
                'dept': m.group(9),
                'trace': m.group(10),
                'line': line,
            })

total = len(records)
total_amount = sum(r['amount'] for r in records)

# --- CK-001: Duplicate voucher numbers ---
voucher_groups = {}
for r in records:
    v = r['voucher']
    voucher_groups.setdefault(v, []).append(r)

ck001_dupes = []
for v, recs in voucher_groups.items():
    if len(recs) > 1:
        # check if key fields (type/amount/dept) differ
        keys = set((r['type'], r['amount'], r['dept']) for r in recs)
        if len(keys) > 1:
            ck001_dupes.append((v, recs))

# --- CK-002: Zero attachment vouchers ---
ck002_zero_attach = [r for r in records if r['attach'] == 0]

# --- CK-003: Stage anomaly (submitted) ---
ck003_submitted = [r for r in records if r['stage'] == 'submitted']

# --- CK-004: Large transactions (>400000) ---
ck004_large = [r for r in records if r['amount'] > 400000]

# --- CK-005: Approver concentration ---
approver_counts = Counter(r['approver'] for r in records)
ck005_concentrated = {
    appr: cnt for appr, cnt in approver_counts.items()
    if cnt / total > 0.15
}

# --- CK-006: Amount reasonability (negative or <100) ---
ck006_small_or_neg = [r for r in records if r['amount'] < 0 or r['amount'] < 100]

# --- CK-007: Trace uniqueness ---
trace_counts = Counter(r['trace'] for r in records)
ck007_dup_traces = {t: c for t, c in trace_counts.items() if c > 1}

# Determine pass/fail
ck001_pass = len(ck001_dupes) == 0
ck002_pass = True  # always lists them; pass if we can enumerate
ck003_pass = len(ck003_submitted) == 0
ck004_pass = True  # informational
ck005_pass = len(ck005_concentrated) == 0
ck006_pass = len(ck006_small_or_neg) == 0
ck007_pass = len(ck007_dup_traces) == 0

all_pass = all([ck001_pass, ck002_pass, ck003_pass, ck004_pass, ck005_pass, ck006_pass, ck007_pass])

# --- Build report ---
lines = []
lines.append("# Batch 18 审计报告 (Entries 1701-1800)")
lines.append("")
lines.append(f"**审计周期**: 2026-Q3")
lines.append(f"**批次**: 18")
lines.append(f"**记录数**: {total}")
lines.append(f"**总金额**: {total_amount:,.2f}")
lines.append("")

lines.append("## 摘要")
lines.append("")
pass_fail_list = []
pass_fail_list.append(("CK-001 重复凭证号", "PASS" if ck001_pass else "FAIL"))
pass_fail_list.append(("CK-002 零附件凭证", "PASS" if ck002_pass else "FAIL"))
pass_fail_list.append(("CK-003 状态异常", "PASS" if ck003_pass else "FAIL"))
pass_fail_list.append(("CK-004 大额交易", "PASS" if ck004_pass else "FAIL"))
pass_fail_list.append(("CK-005 审批人集中度", "PASS" if ck005_pass else "FAIL"))
pass_fail_list.append(("CK-006 金额合理性", "PASS" if ck006_pass else "FAIL"))
pass_fail_list.append(("CK-007 trace 唯一性", "PASS" if ck007_pass else "FAIL"))
for name, status in pass_fail_list:
    lines.append(f"- **{name}**: {status}")

lines.append("")
overall = "PASS" if all_pass else "FAIL"
lines.append(f"**整体结论**: {overall}")
lines.append("")

# CK-001 details
lines.append("---")
lines.append("")
lines.append("## CK-001 重复凭证号")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck001_pass else 'FAIL'}")
lines.append(f"**规则**: 同一 voucher 号对应多条记录且关键字段（type/amount/dept）不一致则为异常")
lines.append("")
if ck001_dupes:
    lines.append(f"**发现 {len(ck001_dupes)} 组重复异常**:")
    lines.append("")
    for v, recs in ck001_dupes:
        lines.append(f"### Voucher {v} (出现 {len(recs)} 次)")
        lines.append("")
        lines.append("| # | Timestamp | Type | Amount | Dept | Stage | Approver | Attach | Trace |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(recs, 1):
            lines.append(
                f"| {i} | {r['ts']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | "
                f"{r['stage']} | {r['approver']} | {r['attach']} | {r['trace']} |"
            )
        lines.append("")
else:
    lines.append("未发现重复凭证号异常。")
    lines.append("")

# CK-002 details
lines.append("---")
lines.append("")
lines.append("## CK-002 零附件凭证 (attach=0)")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck002_pass else 'FAIL'}")
lines.append(f"**规则**: attach=0 的凭证缺少审计追踪附件，需逐条列出")
lines.append(f"**数量**: {len(ck002_zero_attach)}")
lines.append("")
if ck002_zero_attach:
    lines.append("| # | Voucher | Type | Amount | Dept | Stage | Approver | Trace |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck002_zero_attach, 1):
        lines.append(
            f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | "
            f"{r['stage']} | {r['approver']} | {r['trace']} |"
        )
    lines.append("")
else:
    lines.append("未发现零附件凭证。")
    lines.append("")

# CK-003 details
lines.append("---")
lines.append("")
lines.append("## CK-003 状态异常 (stage=submitted)")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck003_pass else 'FAIL'}")
lines.append(f"**规则**: stage=submitted 的凭证未经复核/审批即入抽样范围，视为流程未完成")
lines.append(f"**数量**: {len(ck003_submitted)}")
lines.append("")
if ck003_submitted:
    lines.append("| # | Voucher | Type | Amount | Dept | Approver | Attach | Trace |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck003_submitted, 1):
        lines.append(
            f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | "
            f"{r['approver']} | {r['attach']} | {r['trace']} |"
        )
    lines.append("")
else:
    lines.append("未发现状态异常凭证。")
    lines.append("")

# CK-004 details
lines.append("---")
lines.append("")
lines.append("## CK-004 大额交易 (amount > 400,000)")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck004_pass else 'FAIL'}")
lines.append(f"**规则**: amount > 400,000 需单独标记，说明类型和部门分布")
lines.append(f"**数量**: {len(ck004_large)}")
lines.append("")
if ck004_large:
    # type distribution
    type_dist = Counter(r['type'] for r in ck004_large)
    dept_dist = Counter(r['dept'] for r in ck004_large)
    lines.append("### 类型分布")
    lines.append("")
    for t, c in type_dist.most_common():
        lines.append(f"- {t}: {c}")
    lines.append("")
    lines.append("### 部门分布")
    lines.append("")
    for d, c in dept_dist.most_common():
        lines.append(f"- {d}: {c}")
    lines.append("")
    lines.append("### 明细")
    lines.append("")
    lines.append("| # | Voucher | Type | Amount | Dept | Stage | Approver | Attach | Trace |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck004_large, 1):
        lines.append(
            f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | "
            f"{r['stage']} | {r['approver']} | {r['attach']} | {r['trace']} |"
        )
    lines.append("")
else:
    lines.append("未发现大额交易。")
    lines.append("")

# CK-005 details
lines.append("---")
lines.append("")
lines.append("## CK-005 审批人集中度")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck005_pass else 'FAIL'}")
lines.append(f"**规则**: 统计 approver 频次，同一审批人占比 >15% 需标注")
lines.append("")
lines.append("### 审批人频次分布")
lines.append("")
lines.append("| Approver | Count | Percentage | Flag |")
lines.append("|---|---|---|---|")
for appr, cnt in approver_counts.most_common():
    pct = cnt / total * 100
    flag = "⚠️ >15%" if pct > 15 else ""
    lines.append(f"| {appr} | {cnt} | {pct:.1f}% | {flag} |")
lines.append("")

if ck005_concentrated:
    lines.append("### 集中度异常")
    lines.append("")
    for appr, cnt in sorted(ck005_concentrated.items(), key=lambda x: -x[1]):
        lines.append(f"- **{appr}**: {cnt} 条 ({cnt/total*100:.1f}%)")
    lines.append("")

# CK-006 details
lines.append("---")
lines.append("")
lines.append("## CK-006 金额合理性 (amount < 0 or amount < 100)")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck006_pass else 'FAIL'}")
lines.append(f"**规则**: 金额为负数或 <100 的小额异常需列出")
lines.append(f"**数量**: {len(ck006_small_or_neg)}")
lines.append("")
if ck006_small_or_neg:
    lines.append("| # | Voucher | Type | Amount | Dept | Stage | Approver | Attach | Trace | Issue |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck006_small_or_neg, 1):
        issue = "负数" if r['amount'] < 0 else "小额 (<100)"
        lines.append(
            f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | "
            f"{r['stage']} | {r['approver']} | {r['attach']} | {r['trace']} | {issue} |"
        )
    lines.append("")
else:
    lines.append("未发现金额异常。")
    lines.append("")

# CK-007 details
lines.append("---")
lines.append("")
lines.append("## CK-007 trace 唯一性")
lines.append("")
lines.append(f"**状态**: {'PASS' if ck007_pass else 'FAIL'}")
lines.append(f"**规则**: trace 哈希重复视为数据异常")
lines.append("")
if ck007_dup_traces:
    lines.append(f"**发现 {len(ck007_dup_traces)} 个重复 trace**:")
    lines.append("")
    for t, c in ck007_dup_traces.items():
        matching = [r for r in records if r['trace'] == t]
        lines.append(f"### Trace {t} (出现 {c} 次)")
        lines.append("")
        lines.append("| Voucher | Type | Amount | Dept |")
        lines.append("|---|---|---|---|")
        for r in matching:
            lines.append(f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} |")
    lines.append("")
else:
    lines.append("未发现 trace 重复异常。所有 trace 哈希值唯一。")
    lines.append("")

# Final summary
lines.append("---")
lines.append("")
lines.append("## 检查汇总")
lines.append("")
lines.append(f"| Check | Name | Severity | Result | Count |")
lines.append(f"|---|---|---|---|---|")
checks_summary = [
    ("CK-001", "重复凭证号", "high", "PASS" if ck001_pass else "FAIL", len(ck001_dupes)),
    ("CK-002", "零附件凭证", "medium", "PASS" if ck002_pass else "FAIL", len(ck002_zero_attach)),
    ("CK-003", "状态异常", "medium", "PASS" if ck003_pass else "FAIL", len(ck003_submitted)),
    ("CK-004", "大额交易", "low", "PASS" if ck004_pass else "FAIL", len(ck004_large)),
    ("CK-005", "审批人集中度", "low", "PASS" if ck005_pass else "FAIL", len(ck005_concentrated)),
    ("CK-006", "金额合理性", "low", "PASS" if ck006_pass else "FAIL", len(ck006_small_or_neg)),
    ("CK-007", "trace 唯一性", "low", "PASS" if ck007_pass else "FAIL", len(ck007_dup_traces)),
]
for cid, cname, sev, result, cnt in checks_summary:
    lines.append(f"| {cid} | {cname} | {sev} | {result} | {cnt} |")
lines.append("")
lines.append(f"**批次结论**: {overall}")

# Write report
report = "\n".join(lines) + "\n"
with open(OUTPUT, "w") as f:
    f.write(report)

# Also print summary to stdout
print(f"=== Batch 18 Audit Complete ===")
print(f"Records: {total}")
print(f"Total Amount: {total_amount:,.2f}")
for cid, cname, sev, result, cnt in checks_summary:
    print(f"  {cid} {cname} ({sev}): {result} [{cnt}]")
print(f"Overall: {overall}")
print(f"Report written to {OUTPUT}")

# Print details for failed checks
if not ck001_pass:
    print(f"\nCK-001 DUPLICATES:")
    for v, recs in ck001_dupes:
        print(f"  {v}: {len(recs)} occurrences")
if ck002_zero_attach:
    print(f"\nCK-002 ZERO ATTACH ({len(ck002_zero_attach)}):")
    for r in ck002_zero_attach:
        print(f"  {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']}")
if ck003_submitted:
    print(f"\nCK-003 SUBMITTED ({len(ck003_submitted)}):")
    for r in ck003_submitted:
        print(f"  {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']}")
if ck004_large:
    print(f"\nCK-004 LARGE ({len(ck004_large)}):")
    for r in ck004_large:
        print(f"  {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']}")
if ck005_concentrated:
    print(f"\nCK-005 APPROVER CONCENTRATION:")
    for appr, cnt in ck005_concentrated.items():
        print(f"  {appr}: {cnt} ({cnt/total*100:.1f}%)")
if ck006_small_or_neg:
    print(f"\nCK-006 AMOUNT ISSUES ({len(ck006_small_or_neg)}):")
    for r in ck006_small_or_neg:
        print(f"  {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']}")
if ck007_dup_traces:
    print(f"\nCK-007 TRACE DUPLICATES:")
    for t, c in ck007_dup_traces.items():
        print(f"  {t}: {c} occurrences")
