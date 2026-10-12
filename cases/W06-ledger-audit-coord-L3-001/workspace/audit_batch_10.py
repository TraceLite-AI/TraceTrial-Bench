#!/usr/bin/env python3
"""Audit batch_10.txt (vouchers 901-1000) against CK-001..CK-007."""

import re
import json
from collections import Counter, defaultdict

INPUT = "/home/conglin/hb-cases/W06-ledger-audit-coord-L3-001/workspace/output/batch_10.txt"
OUTPUT = "/home/conglin/hb-cases/W06-ledger-audit-coord-L3-001/workspace/output/batch_10_report.md"
TOTAL_BATCH_COUNT = 100  # items 901-1000

# --- Parse ---
records = []
with open(INPUT) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r'ts=(\S+) voucher=(\S+) type=(\S+) amount=([\d.]+) approver=(\S+) stage=(\S+) attach=(\d+) period=(\S+) dept=(\S+) trace=(\S+)',
            line
        )
        if not m:
            continue
        rec = {
            'ts':       m.group(1),
            'voucher':  m.group(2),
            'type':     m.group(3),
            'amount':   float(m.group(4)),
            'approver': m.group(5),
            'stage':    m.group(6),
            'attach':   int(m.group(7)),
            'period':   m.group(8),
            'dept':     m.group(9),
            'trace':    m.group(10),
        }
        records.append(rec)

n = len(records)
print(f"Parsed {n} records (expected {TOTAL_BATCH_COUNT})")

# --- CK-001: Duplicate voucher numbers ---
voucher_map = defaultdict(list)
for rec in records:
    voucher_map[rec['voucher']].append(rec)

ck001_anomalies = []
for vch, recs in sorted(voucher_map.items()):
    if len(recs) <= 1:
        continue
    # Check if any pair has differing type/amount/dept
    keys = [(r['type'], r['amount'], r['dept']) for r in recs]
    if len(set(keys)) > 1:
        for r in recs:
            ck001_anomalies.append({
                'voucher': r['voucher'],
                'type': r['type'],
                'amount': r['amount'],
                'dept': r['dept'],
                'ts': r['ts'],
                'occurrences': len(recs),
            })

print(f"CK-001 anomalies: {len(ck001_anomalies)}")

# --- CK-002: Zero-attachment vouchers ---
ck002_anomalies = [r for r in records if r['attach'] == 0]
print(f"CK-002 anomalies: {len(ck002_anomalies)}")

# --- CK-003: stage=submitted ---
ck003_anomalies = [r for r in records if r['stage'] == 'submitted']
print(f"CK-003 anomalies: {len(ck003_anomalies)}")

# --- CK-004: amount > 400000 ---
ck004_anomalies = [r for r in records if r['amount'] > 400000]
print(f"CK-004 anomalies: {len(ck004_anomalies)}")

# --- CK-005: Approver concentration > 15% ---
approver_counts = Counter(r['approver'] for r in records)
threshold = n * 0.15
ck005_flagged = [(app, cnt, round(cnt/n*100, 1)) for app, cnt in approver_counts.most_common() if cnt > threshold]
print(f"CK-005 flagged approvers: {len(ck005_flagged)}")

# --- CK-006: Amount < 0 or 0 < amount < 100 ---
ck006_anomalies = [r for r in records if r['amount'] < 0 or (r['amount'] > 0 and r['amount'] < 100)]
print(f"CK-006 anomalies: {len(ck006_anomalies)}")

# --- CK-007: Duplicate trace values ---
trace_map = defaultdict(list)
for rec in records:
    trace_map[rec['trace']].append(rec)

ck007_anomalies = []
for trace, recs in sorted(trace_map.items()):
    if len(recs) > 1:
        for r in recs:
            ck007_anomalies.append({
                'trace': trace,
                'voucher': r['voucher'],
                'ts': r['ts'],
                'occurrences': len(recs),
            })

print(f"CK-007 anomalies: {len(ck007_anomalies)}")

# --- Build report ---
lines = []
lines.append("# 台账审计报告 — batch_10")
lines.append("")
lines.append(f"- **批次**: batch_10（第901-1000条）")
lines.append(f"- **记录数**: {n}")
lines.append(f"- **审计周期**: 2026-Q3")
lines.append(f"- **审计时间**: 2026-07-27T22:16Z")
lines.append("")

# Executive summary
lines.append("## 执行摘要")
lines.append("")
lines.append("| 检查项 | 异常计数 | 严重程度 |")
lines.append("|--------|----------|----------|")
lines.append(f"| CK-001 重复凭证号 | {len(ck001_anomalies)} | HIGH |")
lines.append(f"| CK-002 零附件凭证 | {len(ck002_anomalies)} | MEDIUM |")
lines.append(f"| CK-003 状态异常 | {len(ck003_anomalies)} | MEDIUM |")
lines.append(f"| CK-004 大额交易 | {len(ck004_anomalies)} | LOW |")
lines.append(f"| CK-005 审批人集中度 | {len(ck005_flagged)} 人超阈值 | LOW |")
lines.append(f"| CK-006 金额合理性 | {len(ck006_anomalies)} | LOW |")
lines.append(f"| CK-007 trace 重复 | {len(ck007_anomalies)} | LOW |")
lines.append("")

# CK-001
lines.append("## CK-001 重复凭证号")
lines.append("")
lines.append(f"**规则**: 同一 voucher 号对应多条记录且 type/amount/dept 任一不同视为异常")
lines.append(f"**异常数**: {len(ck001_anomalies)}")
lines.append("")
if ck001_anomalies:
    lines.append("| voucher | type | amount | dept | ts | 出现次数 |")
    lines.append("|---------|------|--------|------|----|---------|")
    for a in ck001_anomalies:
        lines.append(f"| {a['voucher']} | {a['type']} | {a['amount']:,.2f} | {a['dept']} | {a['ts']} | {a['occurrences']} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# CK-002
lines.append("## CK-002 零附件凭证")
lines.append("")
lines.append(f"**规则**: attach=0 的凭证缺少审计追踪附件")
lines.append(f"**异常数**: {len(ck002_anomalies)}")
lines.append("")
if ck002_anomalies:
    lines.append("| voucher | type | amount | approver | stage | dept | ts |")
    lines.append("|---------|------|--------|----------|-------|------|----|")
    for r in sorted(ck002_anomalies, key=lambda x: x['ts']):
        lines.append(f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['ts']} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# CK-003
lines.append("## CK-003 状态异常")
lines.append("")
lines.append(f"**规则**: stage=submitted 的凭证未经复核/审批即入抽样范围")
lines.append(f"**异常数**: {len(ck003_anomalies)}")
lines.append("")
if ck003_anomalies:
    lines.append("| voucher | type | amount | approver | dept | ts |")
    lines.append("|---------|------|--------|----------|------|----|")
    for r in sorted(ck003_anomalies, key=lambda x: x['ts']):
        lines.append(f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['dept']} | {r['ts']} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# CK-004
lines.append("## CK-004 大额交易")
lines.append("")
lines.append(f"**规则**: amount > 400,000 需单独标记")
lines.append(f"**异常数**: {len(ck004_anomalies)}")
lines.append("")
if ck004_anomalies:
    # Type & dept distribution
    type_dist = Counter(r['type'] for r in ck004_anomalies)
    dept_dist = Counter(r['dept'] for r in ck004_anomalies)
    lines.append("**类型分布**:")
    for t, cnt in type_dist.most_common():
        lines.append(f"- {t}: {cnt} 条")
    lines.append("")
    lines.append("**部门分布**:")
    for d, cnt in dept_dist.most_common():
        lines.append(f"- {d}: {cnt} 条")
    lines.append("")
    lines.append("**明细**:")
    lines.append("| voucher | type | amount | approver | stage | dept | ts |")
    lines.append("|---------|------|--------|----------|-------|------|----|")
    for r in sorted(ck004_anomalies, key=lambda x: -x['amount']):
        lines.append(f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['ts']} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# CK-005
lines.append("## CK-005 审批人集中度")
lines.append("")
lines.append(f"**规则**: 同一审批人占比 > 15%（阈值: {threshold:.1f} 条，共 {n} 条）")
lines.append(f"**超阈值人数**: {len(ck005_flagged)}")
lines.append("")
lines.append("### 全部审批人分布")
lines.append("")
lines.append("| approver | 频次 | 占比 |")
lines.append("|----------|------|------|")
for app, cnt in approver_counts.most_common():
    pct = round(cnt/n*100, 1)
    flag = " ⚠️" if cnt > threshold else ""
    lines.append(f"| {app} | {cnt} | {pct}%{flag} |")
lines.append("")
if ck005_flagged:
    lines.append("### 超阈值审批人")
    lines.append("")
    for app, cnt, pct in ck005_flagged:
        lines.append(f"- **{app}**: {cnt} 条 ({pct}%) ⚠️")
    lines.append("")
else:
    lines.append("**无审批人超阈值** ✅")
lines.append("")

# CK-006
lines.append("## CK-006 金额合理性")
lines.append("")
lines.append(f"**规则**: 金额为负数或 0 < amount < 100")
lines.append(f"**异常数**: {len(ck006_anomalies)}")
lines.append("")
if ck006_anomalies:
    lines.append("| voucher | type | amount | approver | stage | dept | ts | 原因 |")
    lines.append("|---------|------|--------|----------|-------|------|----|------|")
    for r in sorted(ck006_anomalies, key=lambda x: x['amount']):
        reason = "负数" if r['amount'] < 0 else "小额(<100)"
        lines.append(f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['ts']} | {reason} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# CK-007
lines.append("## CK-007 trace 唯一性")
lines.append("")
lines.append(f"**规则**: trace 哈希重复视为数据异常")
lines.append(f"**异常数**: {len(ck007_anomalies)}")
lines.append("")
if ck007_anomalies:
    lines.append("| trace | voucher | ts | 出现次数 |")
    lines.append("|-------|---------|----|---------|")
    for a in sorted(ck007_anomalies, key=lambda x: x['trace']):
        lines.append(f"| {a['trace']} | {a['voucher']} | {a['ts']} | {a['occurrences']} |")
else:
    lines.append("**无异常** ✅")
lines.append("")

# Batch conclusion
has_high = len(ck001_anomalies) > 0
has_medium = len(ck002_anomalies) > 0 or len(ck003_anomalies) > 0
verdict = "FAIL" if (has_high or has_medium) else "PASS"

lines.append("---")
lines.append("")
lines.append("## 批次结论")
lines.append("")
reason_parts = []
if has_high:
    reason_parts.append(f"CK-001(HIGH) 发现 {len(ck001_anomalies)} 条异常")
if len(ck002_anomalies) > 0:
    reason_parts.append(f"CK-002(MEDIUM) 发现 {len(ck002_anomalies)} 条异常")
if len(ck003_anomalies) > 0:
    reason_parts.append(f"CK-003(MEDIUM) 发现 {len(ck003_anomalies)} 条异常")

lines.append(f"**结论: {verdict}**")
lines.append("")
if reason_parts:
    lines.append("原因: " + "；".join(reason_parts))
    lines.append("")
lines.append(f"- 总记录数: {n}")
lines.append(f"- HIGH 异常: {len(ck001_anomalies)} 条")
lines.append(f"- MEDIUM 异常: {len(ck002_anomalies) + len(ck003_anomalies)} 条")
lines.append(f"- LOW 异常: {len(ck004_anomalies) + len(ck005_flagged) + len(ck006_anomalies) + len(ck007_anomalies)} 条")

report = "\n".join(lines) + "\n"
with open(OUTPUT, 'w') as f:
    f.write(report)

print("Report written to", OUTPUT)
print(f"Verdict: {verdict}")
