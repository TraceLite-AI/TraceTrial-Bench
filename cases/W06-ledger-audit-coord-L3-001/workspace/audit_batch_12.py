#!/usr/bin/env python3
"""Audit batch_12.txt (records 1101-1200) - 7 checks per audit_spec.json"""

import re
import json
from collections import Counter

# Read input
with open("output/batch_12.txt") as f:
    raw = f.read().strip()

# Parse lines
records = []
pattern = r'ts=(\S+) voucher=(\S+) type=(\S+) amount=([\d.]+) approver=(\S+) stage=(\S+) attach=(\d+) period=(\S+) dept=(\S+) trace=(\S+)'
for line in raw.split("\n"):
    line = line.strip()
    if not line:
        continue
    m = re.match(pattern, line)
    if m:
        records.append({
            "ts": m.group(1),
            "voucher": m.group(2),
            "type": m.group(3),
            "amount": float(m.group(4)),
            "approver": m.group(5),
            "stage": m.group(6),
            "attach": int(m.group(7)),
            "period": m.group(8),
            "dept": m.group(9),
            "trace": m.group(10),
        })

n = len(records)
print(f"Parsed {n} records from batch_12.txt")

# ============================================================
# CK-001: Duplicate voucher numbers (same voucher but type/amount/dept differ)
# ============================================================
voucher_map = {}
for r in records:
    v = r["voucher"]
    if v not in voucher_map:
        voucher_map[v] = []
    voucher_map[v].append(r)

ck001_dup = []
for v, recs in voucher_map.items():
    if len(recs) > 1:
        # check if any key fields differ
        keys = set((r["type"], r["amount"], r["dept"]) for r in recs)
        if len(keys) > 1:
            ck001_dup.append((v, recs))

print(f"CK-001: {len(ck001_dup)} duplicate voucher groups")

# ============================================================
# CK-002: Zero-attachment vouchers (attach=0)
# ============================================================
ck002 = [r for r in records if r["attach"] == 0]
print(f"CK-002: {len(ck002)} zero-attachment vouchers")

# ============================================================
# CK-003: Abnormal status (stage=submitted)
# ============================================================
ck003 = [r for r in records if r["stage"] == "submitted"]
print(f"CK-003: {len(ck003)} submitted-stage vouchers")

# ============================================================
# CK-004: Large transactions (amount > 400000)
# ============================================================
ck004 = [r for r in records if r["amount"] > 400000]
print(f"CK-004: {len(ck004)} large transactions (>400k)")

# ============================================================
# CK-005: Approver concentration (>15% of batch)
# ============================================================
approver_counts = Counter(r["approver"] for r in records)
threshold_15 = n * 0.15
ck005 = {app: cnt for app, cnt in approver_counts.items() if cnt > threshold_15}
print(f"CK-005: {len(ck005)} approvers exceeding 15% threshold ({threshold_15:.1f})")

# ============================================================
# CK-006: Amount reasonableness (negative or 0 < amount < 100)
# ============================================================
ck006 = [r for r in records if r["amount"] < 0 or (0 < r["amount"] < 100)]
print(f"CK-006: {len(ck006)} unreasonable amounts")

# ============================================================
# CK-007: Trace duplicates
# ============================================================
trace_counts = Counter(r["trace"] for r in records)
ck007 = {t: cnt for t, cnt in trace_counts.items() if cnt > 1}
print(f"CK-007: {len(ck007)} duplicate traces")

# ============================================================
# Generate Markdown Report
# ============================================================

report = []
report.append("# Batch-12 审计报告 (第1101-1200条)")
report.append("")
report.append(f"**审计期间**: 2026-Q3")
report.append(f"**批次记录数**: {n}")
report.append(f"**数据时间范围**: {records[0]['ts']} ~ {records[-1]['ts']}")
report.append("")

report.append("## 执行摘要")
report.append("")
report.append("| 检查项 | 编号 | 异常计数 | 严重级别 |")
report.append("|--------|------|----------|----------|")
report.append(f"| 重复凭证号 | CK-001 | {len(ck001_dup)} | high |")
report.append(f"| 零附件凭证 | CK-002 | {len(ck002)} | medium |")
report.append(f"| 状态异常 | CK-003 | {len(ck003)} | medium |")
report.append(f"| 大额交易 | CK-004 | {len(ck004)} | low |")
report.append(f"| 审批人集中度 | CK-005 | {len(ck005)} | low |")
report.append(f"| 金额合理性 | CK-006 | {len(ck006)} | low |")
report.append(f"| trace重复 | CK-007 | {len(ck007)} | low |")
report.append("")

# ── CK-001 ──
report.append("## CK-001: 重复凭证号")
report.append("")
report.append(f"**规则**: 同一 voucher 号对应多条记录且 type/amount/dept 任一不同")
report.append(f"**异常组数**: {len(ck001_dup)}")
report.append("")
if ck001_dup:
    for v, recs in ck001_dup:
        report.append(f"### Voucher: {v} (出现 {len(recs)} 次)")
        report.append("")
        report.append("| # | ts | type | amount | approver | stage | attach | dept | trace |")
        report.append("|---|-----|------|--------|----------|-------|--------|------|-------|")
        for i, r in enumerate(recs, 1):
            report.append(f"| {i} | {r['ts']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['stage']} | {r['attach']} | {r['dept']} | {r['trace']} |")
        report.append("")
else:
    report.append("*无异常 — 所有凭证号唯一*")
    report.append("")

# ── CK-002 ──
report.append("## CK-002: 零附件凭证 (attach=0)")
report.append("")
report.append(f"**规则**: attach=0 的凭证缺少审计追踪附件")
report.append(f"**异常数**: {len(ck002)}")
report.append("")
if ck002:
    report.append("| # | ts | voucher | type | amount | approver | stage | dept | trace |")
    report.append("|---|-----|---------|------|--------|----------|-------|------|-------|")
    for i, r in enumerate(ck002, 1):
        report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['trace']} |")
    report.append("")
    # summary by type and dept
    type_dist = Counter(r["type"] for r in ck002)
    dept_dist = Counter(r["dept"] for r in ck002)
    report.append(f"**类型分布**: {dict(type_dist)}")
    report.append(f"**部门分布**: {dict(dept_dist)}")
    report.append("")
else:
    report.append("*无异常*")
    report.append("")

# ── CK-003 ──
report.append("## CK-003: 状态异常 (stage=submitted)")
report.append("")
report.append(f"**规则**: stage=submitted 的凭证流程未完成")
report.append(f"**异常数**: {len(ck003)}")
report.append("")
if ck003:
    report.append("| # | ts | voucher | type | amount | approver | dept | trace |")
    report.append("|---|-----|---------|------|--------|----------|------|-------|")
    for i, r in enumerate(ck003, 1):
        report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['dept']} | {r['trace']} |")
    report.append("")
else:
    report.append("*无异常*")
    report.append("")

# ── CK-004 ──
report.append("## CK-004: 大额交易 (amount > 400,000)")
report.append("")
report.append(f"**规则**: amount > 400,000 需单独标记")
report.append(f"**异常数**: {len(ck004)}")
report.append("")
if ck004:
    report.append("| # | ts | voucher | type | amount | approver | stage | dept | trace |")
    report.append("|---|-----|---------|------|--------|----------|-------|------|-------|")
    for i, r in enumerate(ck004, 1):
        report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['trace']} |")
    report.append("")
    type_dist = Counter(r["type"] for r in ck004)
    dept_dist = Counter(r["dept"] for r in ck004)
    stage_dist = Counter(r["stage"] for r in ck004)
    report.append(f"**类型分布**: {dict(type_dist)}")
    report.append(f"**部门分布**: {dict(dept_dist)}")
    report.append(f"**状态分布**: {dict(stage_dist)}")
    report.append("")
else:
    report.append("*无异常*")
    report.append("")

# ── CK-005 ──
report.append("## CK-005: 审批人集中度")
report.append("")
report.append(f"**规则**: 同一审批人占比 >15% (阈值: {threshold_15:.1f}，即 >{int(threshold_15)} 条)")
report.append(f"**异常审批人**: {len(ck005)}")
report.append("")
if ck005:
    report.append("| approver | 出现次数 | 占比 |")
    report.append("|----------|----------|------|")
    for app, cnt in sorted(ck005.items(), key=lambda x: -x[1]):
        pct = cnt / n * 100
        report.append(f"| {app} | {cnt} | {pct:.1f}% |")
    report.append("")
    # list associated vouchers
    for app, cnt in sorted(ck005.items(), key=lambda x: -x[1]):
        app_recs = [r for r in records if r["approver"] == app]
        pct = cnt / n * 100
        report.append(f"### {app} ({cnt}条, {pct:.1f}%)")
        report.append("")
        report.append("| # | ts | voucher | type | amount | stage | dept |")
        report.append("|---|-----|---------|------|--------|-------|------|")
        for i, r in enumerate(app_recs, 1):
            report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['stage']} | {r['dept']} |")
        report.append("")
else:
    report.append("*无异常 — 所有审批人占比均在15%以内*")
    report.append("")
    report.append("### 审批人频次分布")
    report.append("")
    report.append("| approver | 出现次数 | 占比 |")
    report.append("|----------|----------|------|")
    for app, cnt in approver_counts.most_common():
        pct = cnt / n * 100
        report.append(f"| {app} | {cnt} | {pct:.1f}% |")
    report.append("")

# ── CK-006 ──
report.append("## CK-006: 金额合理性")
report.append("")
report.append(f"**规则**: 金额为负数或 0 < amount < 100")
report.append(f"**异常数**: {len(ck006)}")
report.append("")
if ck006:
    report.append("| # | ts | voucher | type | amount | approver | stage | dept | trace |")
    report.append("|---|-----|---------|------|--------|----------|-------|------|-------|")
    for i, r in enumerate(ck006, 1):
        report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['trace']} |")
    report.append("")
else:
    report.append("*无异常*")
    report.append("")

# ── CK-007 ──
report.append("## CK-007: trace 重复")
report.append("")
report.append(f"**规则**: trace 哈希重复视为数据异常")
report.append(f"**异常 trace 数**: {len(ck007)}")
report.append("")
if ck007:
    report.append("| trace | 重复次数 |")
    report.append("|-------|----------|")
    for t, cnt in sorted(ck007.items(), key=lambda x: -x[1]):
        report.append(f"| {t} | {cnt} |")
    report.append("")
    # list all records with duplicate traces
    for t, cnt in sorted(ck007.items(), key=lambda x: -x[1]):
        trecs = [r for r in records if r["trace"] == t]
        report.append(f"### trace: {t} (出现 {cnt} 次)")
        report.append("")
        report.append("| # | ts | voucher | type | amount | approver | stage | dept |")
        report.append("|---|-----|---------|------|--------|----------|-------|------|")
        for i, r in enumerate(trecs, 1):
            report.append(f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:.2f} | {r['approver']} | {r['stage']} | {r['dept']} |")
        report.append("")
else:
    report.append("*无异常 — 所有 trace 唯一*")
    report.append("")

# ── Overall assessment ──
report.append("## 批次结论")
report.append("")
# Determine PASS/FAIL: CK-001 (high) or CK-002/CK-003 (medium) with non-zero count -> FAIL
# Actually, let's look at the pattern: high severity anomalies typically cause FAIL
has_high = len(ck001_dup) > 0
has_medium = len(ck002) > 0 or len(ck003) > 0
has_low = len(ck004) > 0 or len(ck005) > 0 or len(ck006) > 0 or len(ck007) > 0

anomaly_counts = {
    "CK-001 (重复凭证号)": len(ck001_dup),
    "CK-002 (零附件凭证)": len(ck002),
    "CK-003 (状态异常)": len(ck003),
    "CK-004 (大额交易)": len(ck004),
    "CK-005 (审批人集中)": len(ck005),
    "CK-006 (金额不合理)": len(ck006),
    "CK-007 (trace重复)": len(ck007),
}

total_anomalies = sum(anomaly_counts.values())

if has_high or (has_medium and (len(ck002) >= 5 or len(ck003) >= 5)):
    verdict = "**FAIL** ❌"
    reason_parts = []
    if has_high:
        reason_parts.append(f"存在 {len(ck001_dup)} 组 CK-001 高危异常")
    if len(ck002) >= 5:
        reason_parts.append(f"CK-002 零附件凭证达到 {len(ck002)} 条")
    if len(ck003) >= 5:
        reason_parts.append(f"CK-003 状态异常达到 {len(ck003)} 条")
    reason = "；".join(reason_parts)
    report.append(f"**结论**: {verdict}")
    report.append(f"**原因**: {reason}")
else:
    verdict = "**PASS** ✅"
    report.append(f"**结论**: {verdict}")
    if total_anomalies > 0:
        report.append(f"**备注**: 存在 {total_anomalies} 个低/中级别异常，但均在可接受范围内")

report.append(f"**总异常数**: {total_anomalies} (CK-001~CK-007 条目加总)")
report.append("")
report.append("| 检查项 | 计数 |")
report.append("|--------|------|")
for name, cnt in anomaly_counts.items():
    flag = " ⚠️" if cnt > 0 else ""
    report.append(f"| {name} | {cnt}{flag} |")

# Write report
report_text = "\n".join(report)
with open("output/batch_12_report.md", "w") as f:
    f.write(report_text)

print("\nReport written to output/batch_12_report.md")
print(f"Total anomalies: {total_anomalies}")
print(f"Verdict: {verdict}")
