#!/usr/bin/env python3
"""Batch 14 audit script: checks CK-001 through CK-007."""

import json, re, sys
from collections import Counter

# --- Read data ---
with open("output/batch_14.txt") as f:
    lines = f.readlines()

# --- Parse records ---
records = []
for line in lines:
    line = line.strip()
    if not line:
        continue
    rec = {}
    # ts
    m = re.search(r'ts=([^\s]+)', line)
    rec['ts'] = m.group(1) if m else ''
    # voucher
    m = re.search(r'voucher=([^\s]+)', line)
    rec['voucher'] = m.group(1) if m else ''
    # type
    m = re.search(r'type=([^\s]+)', line)
    rec['type'] = m.group(1) if m else ''
    # amount
    m = re.search(r'amount=([^\s]+)', line)
    rec['amount'] = float(m.group(1)) if m else 0.0
    # approver
    m = re.search(r'approver=([^\s]+)', line)
    rec['approver'] = m.group(1) if m else ''
    # stage
    m = re.search(r'stage=([^\s]+)', line)
    rec['stage'] = m.group(1) if m else ''
    # attach
    m = re.search(r'attach=([^\s]+)', line)
    rec['attach'] = int(m.group(1)) if m else -1
    # period
    m = re.search(r'period=([^\s]+)', line)
    rec['period'] = m.group(1) if m else ''
    # dept
    m = re.search(r'dept=([^\s]+)', line)
    rec['dept'] = m.group(1) if m else ''
    # trace
    m = re.search(r'trace=([^\s]+)', line)
    rec['trace'] = m.group(1) if m else ''
    records.append(rec)

n = len(records)
print(f"Parsed {n} records")

# =====================
# CK-001: 重复凭证号
# =====================
voucher_map = {}
for r in records:
    vid = r['voucher']
    if vid not in voucher_map:
        voucher_map[vid] = []
    voucher_map[vid].append(r)

ck001_anomalies = []
for vid, recs in voucher_map.items():
    if len(recs) > 1:
        # Check if key fields differ
        types = set(r['type'] for r in recs)
        amounts = set(r['amount'] for r in recs)
        depts = set(r['dept'] for r in recs)
        if len(types) > 1 or len(amounts) > 1 or len(depts) > 1:
            ck001_anomalies.append((vid, recs, types, amounts, depts))

print(f"CK-001: {len(ck001_anomalies)} anomaly groups")

# =====================
# CK-002: 零附件凭证
# =====================
ck002_list = [r for r in records if r['attach'] == 0]
print(f"CK-002: {len(ck002_list)} zero-attach records")

# =====================
# CK-003: 状态异常 submitted
# =====================
ck003_list = [r for r in records if r['stage'] == 'submitted']
print(f"CK-003: {len(ck003_list)} submitted records")

# =====================
# CK-004: 大额交易 >400k
# =====================
ck004_list = [r for r in records if r['amount'] > 400000]
ck004_type_count = Counter(r['type'] for r in ck004_list)
ck004_dept_count = Counter(r['dept'] for r in ck004_list)
print(f"CK-004: {len(ck004_list)} large-amount records")

# =====================
# CK-005: 审批人集中度
# =====================
approver_count = Counter(r['approver'] for r in records)
threshold_15pct = n * 0.15
ck005_list = [(a, cnt, round(cnt/n*100, 1)) for a, cnt in approver_count.most_common() if cnt > threshold_15pct]
print(f"CK-005: {len(ck005_list)} approvers >15%")

# =====================
# CK-006: 金额合理性
# =====================
ck006_negative = [r for r in records if r['amount'] < 0]
ck006_small = [r for r in records if 0 <= r['amount'] < 100]
ck006_list = ck006_negative + ck006_small
print(f"CK-006: {len(ck006_negative)} negative, {len(ck006_small)} small-amount")

# =====================
# CK-007: trace 唯一性
# =====================
trace_count = Counter(r['trace'] for r in records)
ck007_dupes = [(t, cnt) for t, cnt in trace_count.items() if cnt > 1]
print(f"CK-007: {len(ck007_dupes)} duplicate traces")

# =====================
# Build Report
# =====================
report_lines = []
report_lines.append("# Batch 14 审计报告")
report_lines.append("")
report_lines.append("**批次**: batch_14.txt (第1301-1400条)")
report_lines.append(f"**记录数**: {n}")
report_lines.append(f"**审计区间**: 2026-Q3")
report_lines.append(f"**报告生成时间**: 2026-07-27T22:21Z")
report_lines.append("")
report_lines.append("## 审计摘要")
report_lines.append("")
report_lines.append(f"- CK-001 重复凭证号: {'发现' if ck001_anomalies else '无'}异常 ({len(ck001_anomalies)} 组)")
report_lines.append(f"- CK-002 零附件凭证: {len(ck002_list)} 条")
report_lines.append(f"- CK-003 状态异常(submitted): {len(ck003_list)} 条")
report_lines.append(f"- CK-004 大额交易(>400k): {len(ck004_list)} 条")
report_lines.append(f"- CK-005 审批人集中度: {'发现' if ck005_list else '无'}超15%审批人")
report_lines.append(f"- CK-006 金额合理性: {len(ck006_list)} 条异常 (负数{len(ck006_negative)}条/小额{len(ck006_small)}条)")
report_lines.append(f"- CK-007 trace唯一性: {'发现' if ck007_dupes else '无'}重复 ({len(ck007_dupes)} 组)")
report_lines.append("")

# Overall verdict
has_high_fail = len(ck001_anomalies) > 0
has_medium_fail = len(ck002_list) > 5 or len(ck003_list) > 20
overall = "FAIL" if (has_high_fail or has_medium_fail) else "PASS"
report_lines.append("## 综合判定")
report_lines.append("")
report_lines.append(f"**{overall}**")
report_lines.append("")

# ---- CK-001 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-001 重复凭证号 [HIGH]")
report_lines.append("")
report_lines.append(f"规则: 同一voucher号对应多条记录且关键字段(type/amount/dept)不一致则为异常")
report_lines.append("")
if not ck001_anomalies:
    report_lines.append("**结果**: PASS — 未发现重复异常")
else:
    report_lines.append("**结果**: FAIL")
    for vid, recs, types, amounts, depts in ck001_anomalies:
        report_lines.append(f"### 凭证号 {vid} ({len(recs)} 条记录)")
        report_lines.append("")
        report_lines.append(f"- type 分布: {', '.join(sorted(types))}")
        report_lines.append(f"- amount 分布: {', '.join(str(a) for a in sorted(amounts))}")
        report_lines.append(f"- dept 分布: {', '.join(sorted(depts))}")
        report_lines.append("")

# ---- CK-002 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-002 零附件凭证 [MEDIUM]")
report_lines.append("")
report_lines.append("规则: attach=0 的凭证缺少审计追踪附件，需逐条列出")
report_lines.append("")
if not ck002_list:
    report_lines.append("**结果**: PASS — 无零附件凭证")
else:
    report_lines.append(f"**结果**: {'FAIL' if len(ck002_list) > 5 else 'PASS'} — 共 {len(ck002_list)} 条")
    report_lines.append("")
    report_lines.append("| # | Voucher | Type | Amount | Approver | Stage | Dept | Trace |")
    report_lines.append("|---|---------|------|--------|----------|-------|------|-------|")
    for i, r in enumerate(ck002_list, 1):
        report_lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['trace']} |")
    report_lines.append("")

# ---- CK-003 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-003 状态异常 [MEDIUM]")
report_lines.append("")
report_lines.append("规则: stage=submitted 的凭证未经复核/审批即入抽样范围")
report_lines.append("")
if not ck003_list:
    report_lines.append("**结果**: PASS — 无 submitted 状态记录")
else:
    report_lines.append(f"**结果**: {'FAIL' if len(ck003_list) > 20 else 'PASS'} — 共 {len(ck003_list)} 条")
    report_lines.append("")
    report_lines.append("| # | Voucher | Type | Amount | Approver | Attach | Dept |")
    report_lines.append("|---|---------|------|--------|----------|--------|------|")
    for i, r in enumerate(ck003_list, 1):
        report_lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['attach']} | {r['dept']} |")
    report_lines.append("")

# ---- CK-004 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-004 大额交易 [LOW]")
report_lines.append("")
report_lines.append("规则: amount > 400,000 单独标记，说明类型和部门分布")
report_lines.append("")
if not ck004_list:
    report_lines.append("**结果**: PASS — 无大额交易")
else:
    report_lines.append(f"**结果**: INFO — 共 {len(ck004_list)} 条大额交易")
    report_lines.append("")
    report_lines.append("### 类型分布")
    report_lines.append("")
    for t, c in ck004_type_count.most_common():
        report_lines.append(f"- {t}: {c} 条")
    report_lines.append("")
    report_lines.append("### 部门分布")
    report_lines.append("")
    for d, c in ck004_dept_count.most_common():
        report_lines.append(f"- {d}: {c} 条")
    report_lines.append("")
    report_lines.append("### 明细")
    report_lines.append("")
    report_lines.append("| # | Voucher | Type | Amount | Approver | Dept |")
    report_lines.append("|---|---------|------|--------|----------|------|")
    for i, r in enumerate(ck004_list, 1):
        report_lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['dept']} |")
    report_lines.append("")

# ---- CK-005 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-005 审批人集中度 [LOW]")
report_lines.append("")
report_lines.append("规则: 统计approver频次，同一审批人占比 >15% 需标注")
report_lines.append("")
report_lines.append(f"阈值 (15% of {n}) = {threshold_15pct:.1f}")
report_lines.append("")
report_lines.append("### 审批人频次统计")
report_lines.append("")
report_lines.append("| Approver | Count | % |")
report_lines.append("|----------|-------|---|")
for a, cnt in approver_count.most_common():
    pct = round(cnt/n*100, 1)
    flag = " ⚠️" if cnt > threshold_15pct else ""
    report_lines.append(f"| {a} | {cnt} | {pct}%{flag} |")
report_lines.append("")
if ck005_list:
    report_lines.append("**结果**: WARNING — 存在超15%集中度的审批人:")
    for a, cnt, pct in ck005_list:
        report_lines.append(f"- {a}: {cnt} 条 ({pct}%)")
else:
    report_lines.append("**结果**: PASS — 无审批人超过15%阈值")
report_lines.append("")

# ---- CK-006 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-006 金额合理性 [LOW]")
report_lines.append("")
report_lines.append("规则: 金额为负数或 <100 的小额异常需列出")
report_lines.append("")
if not ck006_list:
    report_lines.append("**结果**: PASS — 无金额异常")
else:
    report_lines.append(f"**结果**: {'WARNING' if ck006_list else 'PASS'} — 共 {len(ck006_list)} 条异常")
    if ck006_negative:
        report_lines.append("")
        report_lines.append("### 负数金额")
        report_lines.append("")
        report_lines.append("| # | Voucher | Amount | Type | Dept |")
        report_lines.append("|---|---------|--------|------|------|")
        for i, r in enumerate(ck006_negative, 1):
            report_lines.append(f"| {i} | {r['voucher']} | {r['amount']:,.2f} | {r['type']} | {r['dept']} |")
    if ck006_small:
        report_lines.append("")
        report_lines.append("### 小额金额 (<100)")
        report_lines.append("")
        report_lines.append("| # | Voucher | Amount | Type | Dept |")
        report_lines.append("|---|---------|--------|------|------|")
        for i, r in enumerate(ck006_small, 1):
            report_lines.append(f"| {i} | {r['voucher']} | {r['amount']:,.2f} | {r['type']} | {r['dept']} |")
    report_lines.append("")

# ---- CK-007 ----
report_lines.append("---")
report_lines.append("")
report_lines.append("## CK-007 Trace唯一性 [LOW]")
report_lines.append("")
report_lines.append("规则: trace哈希重复视为数据异常")
report_lines.append("")
if not ck007_dupes:
    report_lines.append("**结果**: PASS — 所有100条记录的trace均唯一")
else:
    report_lines.append(f"**结果**: WARNING — 发现 {len(ck007_dupes)} 组重复trace")
    report_lines.append("")
    for trace_hash, cnt in ck007_dupes:
        report_lines.append(f"- `{trace_hash}`: 出现 {cnt} 次")
    report_lines.append("")

# --- Checks Summary Table ---
report_lines.append("---")
report_lines.append("")
report_lines.append("## 检查结果汇总")
report_lines.append("")
report_lines.append("| 检查项 | 等级 | 结果 | 详情 |")
report_lines.append("|--------|------|------|------|")
ck001_result = "FAIL" if ck001_anomalies else "PASS"
report_lines.append(f"| CK-001 重复凭证号 | HIGH | {ck001_result} | {len(ck001_anomalies)} 组异常 |")
ck002_result = "FAIL" if len(ck002_list) > 5 else "PASS"
report_lines.append(f"| CK-002 零附件凭证 | MEDIUM | {ck002_result} | {len(ck002_list)} 条 |")
ck003_result = "FAIL" if len(ck003_list) > 20 else "PASS"
report_lines.append(f"| CK-003 状态异常 | MEDIUM | {ck003_result} | {len(ck003_list)} 条 |")
report_lines.append(f"| CK-004 大额交易 | LOW | INFO | {len(ck004_list)} 条 |")
ck005_result = "WARNING" if ck005_list else "PASS"
report_lines.append(f"| CK-005 审批人集中度 | LOW | {ck005_result} | " + (f"{len(ck005_list)} 人超15%" if ck005_list else "无") + " |")
ck006_result = "WARNING" if ck006_list else "PASS"
report_lines.append(f"| CK-006 金额合理性 | LOW | {ck006_result} | {len(ck006_list)} 条异常 |")
ck007_result = "WARNING" if ck007_dupes else "PASS"
report_lines.append(f"| CK-007 trace唯一性 | LOW | {ck007_result} | {len(ck007_dupes)} 组重复 |")
report_lines.append("")
report_lines.append(f"**综合判定: {overall}**")
report_lines.append("")

# Output
report = "\n".join(report_lines)
with open("output/batch_14_report.md", "w") as f:
    f.write(report)

print("\nReport written to output/batch_14_report.md")
print(f"Overall: {overall}")
