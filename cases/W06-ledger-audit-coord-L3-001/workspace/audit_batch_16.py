#!/usr/bin/env python3
"""Run CK-001 through CK-007 audits on batch_16.txt (vouchers 1501-1600)."""

import re
from collections import Counter, defaultdict

INPUT_FILE = "output/batch_16.txt"
OUTPUT_FILE = "output/batch_16_report.md"
TOTAL = 100  # 100 vouchers in this batch
THRESHOLD_PCT = 15.0
HIGH_AMOUNT = 400_000

# ---- Parse ----
records = []
with open(INPUT_FILE) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r'ts=(\S+)\s+voucher=(\S+)\s+type=(\S+)\s+amount=(\S+)\s+'
            r'approver=(\S+)\s+stage=(\S+)\s+attach=(\S+)\s+period=(\S+)\s+'
            r'dept=(\S+)\s+trace=(\S+)',
            line
        )
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

N = len(records)
assert N == TOTAL, f"Expected {TOTAL} records, got {N}"

# ========== CK-001: Duplicate vouchers with different key fields ==========
voucher_groups = defaultdict(list)
for i, r in enumerate(records):
    voucher_groups[r["voucher"]].append((i + 1, r))  # 1-based line #

dup_vouchers_ck001 = {}
for v, entries in voucher_groups.items():
    if len(entries) > 1:
        # Check if type/amount/dept are inconsistent
        types = {e[1]["type"] for e in entries}
        amounts = {e[1]["amount"] for e in entries}
        depts = {e[1]["dept"] for e in entries}
        if len(types) > 1 or len(amounts) > 1 or len(depts) > 1:
            dup_vouchers_ck001[v] = entries

# ========== CK-002: Zero-attachment vouchers ==========
zero_attach = [(i + 1, r) for i, r in enumerate(records) if r["attach"] == 0]

# ========== CK-003: Submitted-stage vouchers ==========
submitted = [(i + 1, r) for i, r in enumerate(records) if r["stage"] == "submitted"]

# ========== CK-004: High-amount vouchers > 400,000 ==========
high_amount = [(i + 1, r) for i, r in enumerate(records) if r["amount"] > HIGH_AMOUNT]

# ========== CK-005: Approver concentration ==========
approver_counts = Counter(r["approver"] for r in records)
approver_concentration = {}
for a, cnt in approver_counts.items():
    pct = cnt / N * 100
    if pct > THRESHOLD_PCT:
        approver_concentration[a] = (cnt, pct)

# ========== CK-006: Negative amount or amount < 100 ==========
amount_anomalies = [(i + 1, r) for i, r in enumerate(records) if r["amount"] < 0 or r["amount"] < 100]

# ========== CK-007: Duplicate trace hashes ==========
trace_groups = defaultdict(list)
for i, r in enumerate(records):
    trace_groups[r["trace"]].append((i + 1, r))
dup_traces_ck007 = {t: entries for t, entries in trace_groups.items() if len(entries) > 1}

# ========== Summary stats ==========
types_dist = Counter(r["type"] for r in records)
dept_dist = Counter(r["dept"] for r in records)
stage_dist = Counter(r["stage"] for r in records)
total_amount = sum(r["amount"] for r in records)

# ========== Generate Markdown Report ==========
report = []
report.append("# batch_16 审计报告")
report.append(f"")
report.append(f"**审计批次**: batch_16.txt（第 1501–1600 条凭证）")
report.append(f"**审计期间**: 2026-Q3")
report.append(f"**凭证数量**: {N} 条")
report.append(f"**本批总额**: ¥{total_amount:,.2f}")
report.append(f"**审计规则**: CK-001 ~ CK-007")
report.append(f"")

# Overview
report.append("## 批次概览")
report.append("")
report.append(f"| 维度 | 分布 |")
report.append(f"|------|------|")
for t, c in sorted(types_dist.items()):
    report.append(f"| type/{t} | {c} ({c/N*100:.1f}%) |")
for d, c in sorted(dept_dist.items()):
    report.append(f"| dept/{d} | {c} ({c/N*100:.1f}%) |")
for s, c in sorted(stage_dist.items()):
    report.append(f"| stage/{s} | {c} ({c/N*100:.1f}%) |")
report.append(f"")

# ---- CK-001 ----
report.append("## CK-001 重复凭证号")
report.append("")
report.append(f"**规则**: 同一 voucher 号对应多条记录且关键字段（type/amount/dept）不一致则为异常")
report.append(f"**严重级别**: 🔴 HIGH")
report.append("")
if dup_vouchers_ck001:
    for v, entries in dup_vouchers_ck001.items():
        report.append(f"### 异常 voucher: {v}（{len(entries)} 条）")
        report.append("")
        report.append("| # | 行号 | 时间 | type | amount | dept | approver | stage | attach | trace |")
        report.append("|---|------|------|------|--------|------|----------|-------|--------|-------|")
        for line_no, r in entries:
            report.append(f"| | {line_no} | {r['ts']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['approver']} | {r['stage']} | {r['attach']} | {r['trace']} |")
        report.append("")
    report.append(f"**CK-001 结论**: ❌ **FAIL** — 发现 {len(dup_vouchers_ck001)} 组重复且不一致的凭证号")
else:
    report.append("**CK-001 结论**: ✅ **PASS** — 未发现任何不一致的重复凭证号")
report.append("")

# ---- CK-002 ----
report.append("## CK-002 零附件凭证")
report.append("")
report.append(f"**规则**: attach=0 的凭证缺少审计追踪附件，需逐条列出")
report.append(f"**严重级别**: 🟡 MEDIUM")
report.append("")
if zero_attach:
    report.append(f"共 {len(zero_attach)} 条零附件凭证：")
    report.append("")
    report.append("| 行号 | voucher | type | amount | dept | approver | stage |")
    report.append("|------|---------|------|--------|------|----------|-------|")
    for line_no, r in zero_attach:
        report.append(f"| {line_no} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['approver']} | {r['stage']} |")
    report.append("")
    report.append(f"**CK-002 结论**: ⚠️ **FAIL** — 发现 {len(zero_attach)} 条零附件凭证（占本批 {len(zero_attach)/N*100:.1f}%）")
else:
    report.append("**CK-002 结论**: ✅ **PASS** — 所有凭证均包含附件")
report.append("")

# ---- CK-003 ----
report.append("## CK-003 状态异常")
report.append("")
report.append(f"**规则**: stage=submitted 的凭证未经复核/审批即入抽样范围，视为流程未完成")
report.append(f"**严重级别**: 🟡 MEDIUM")
report.append("")
if submitted:
    report.append(f"共 {len(submitted)} 条未完成审批的凭证：")
    report.append("")
    report.append("| 行号 | voucher | type | amount | dept | approver |")
    report.append("|------|---------|------|--------|------|----------|")
    for line_no, r in submitted:
        report.append(f"| {line_no} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['approver']} |")
    report.append("")
    report.append(f"**CK-003 结论**: ⚠️ **FAIL** — 发现 {len(submitted)} 条 submitted 状态凭证（占本批 {len(submitted)/N*100:.1f}%）")
else:
    report.append("**CK-003 结论**: ✅ **PASS** — 无 submitted 状态凭证")
report.append("")

# ---- CK-004 ----
report.append("## CK-004 大额交易")
report.append("")
report.append(f"**规则**: amount > {HIGH_AMOUNT:,} 需单独标记，说明类型和部门分布")
report.append(f"**严重级别**: 🟢 LOW")
report.append("")
if high_amount:
    report.append(f"共 {len(high_amount)} 条大额交易（>¥{HIGH_AMOUNT:,}）：")
    report.append("")
    # Type distribution
    ha_types = Counter(r["type"] for _, r in high_amount)
    ha_depts = Counter(r["dept"] for _, r in high_amount)
    report.append(f"- **类型分布**: {', '.join(f'{t}={c}' for t, c in ha_types.items())}")
    report.append(f"- **部门分布**: {', '.join(f'{d}={c}' for d, c in ha_depts.items())}")
    report.append("")
    report.append("| 行号 | voucher | type | amount | dept | approver | stage |")
    report.append("|------|---------|------|--------|------|----------|-------|")
    for line_no, r in high_amount:
        report.append(f"| {line_no} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['approver']} | {r['stage']} |")
    report.append("")
    report.append(f"**CK-004 结论**: ⚠️ **FAIL** — 发现 {len(high_amount)} 条大额交易（占本批 {len(high_amount)/N*100:.1f}%）")
else:
    report.append("**CK-004 结论**: ✅ **PASS** — 无大额交易")
report.append("")

# ---- CK-005 ----
report.append("## CK-005 审批人集中度")
report.append("")
report.append(f"**规则**: 统计 approver 频次，同一审批人占比 >{THRESHOLD_PCT:.0f}% 需标注")
report.append(f"**严重级别**: 🟢 LOW")
report.append("")
report.append("### 审批人频次统计")
report.append("")
report.append("| approver | 次数 | 占比 | 标记 |")
report.append("|----------|------|------|------|")
for a, cnt in sorted(approver_counts.items()):
    pct = cnt / N * 100
    flag = "⚠️ 超阈值" if pct > THRESHOLD_PCT else ""
    report.append(f"| {a} | {cnt} | {pct:.1f}% | {flag} |")
report.append("")
if approver_concentration:
    report.append("### 超阈值审批人详情")
    report.append("")
    for a, (cnt, pct) in sorted(approver_concentration.items(), key=lambda x: -x[1][0]):
        report.append(f"- **{a}**: {cnt} 次（{pct:.1f}%）")
        # List their vouchers
        their_vouchers = [(i + 1, r) for i, r in enumerate(records) if r["approver"] == a]
        type_dist = Counter(r["type"] for _, r in their_vouchers)
        dept_dist2 = Counter(r["dept"] for _, r in their_vouchers)
        report.append(f"  - 类型分布: {', '.join(f'{t}={c}' for t, c in type_dist.items())}")
        report.append(f"  - 部门分布: {', '.join(f'{d}={c}' for d, c in dept_dist2.items())}")
    report.append("")
    report.append(f"**CK-005 结论**: ⚠️ **FAIL** — {len(approver_concentration)} 位审批人超过 {THRESHOLD_PCT:.0f}% 阈值")
else:
    report.append("**CK-005 结论**: ✅ **PASS** — 所有审批人占比均在阈值内")
report.append("")

# ---- CK-006 ----
report.append("## CK-006 金额合理性")
report.append("")
report.append(f"**规则**: 金额为负数或 <100 的小额异常需列出")
report.append(f"**严重级别**: 🟢 LOW")
report.append("")
if amount_anomalies:
    neg_count = sum(1 for _, r in amount_anomalies if r["amount"] < 0)
    small_count = sum(1 for _, r in amount_anomalies if r["amount"] >= 0)
    report.append(f"- 负金额: {neg_count} 条")
    report.append(f"- 小额（<100）: {small_count} 条")
    report.append("")
    report.append("| 行号 | voucher | amount | type | dept | 备注 |")
    report.append("|------|---------|--------|------|------|------|")
    for line_no, r in amount_anomalies:
        note = "负金额" if r["amount"] < 0 else "小额(<100)"
        report.append(f"| {line_no} | {r['voucher']} | {r['amount']:,.2f} | {r['type']} | {r['dept']} | {note} |")
    report.append("")
    report.append(f"**CK-006 结论**: ⚠️ **FAIL** — 发现 {len(amount_anomalies)} 条金额异常凭证")
else:
    report.append("**CK-006 结论**: ✅ **PASS** — 无金额异常")
report.append("")

# ---- CK-007 ----
report.append("## CK-007 trace 唯一性")
report.append("")
report.append(f"**规则**: trace 哈希重复视为数据异常")
report.append(f"**严重级别**: 🟢 LOW")
report.append("")
if dup_traces_ck007:
    for t, entries in dup_traces_ck007.items():
        report.append(f"### 重复 trace: `{t}`（{len(entries)} 条）")
        report.append("")
        report.append("| 行号 | voucher | type | amount | dept |")
        report.append("|------|---------|------|--------|------|")
        for line_no, r in entries:
            report.append(f"| {line_no} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} |")
        report.append("")
    report.append(f"**CK-007 结论**: ❌ **FAIL** — 发现 {len(dup_traces_ck007)} 组重复 trace 哈希")
else:
    report.append("**CK-007 结论**: ✅ **PASS** — 所有 trace 均唯一")
report.append("")

# ---- Overall Summary ----
report.append("---")
report.append("")
report.append("## 审计结论汇总")
report.append("")
checks_summary = {
    "CK-001": bool(dup_vouchers_ck001),
    "CK-002": bool(zero_attach),
    "CK-003": bool(submitted),
    "CK-004": bool(high_amount),
    "CK-005": bool(approver_concentration),
    "CK-006": bool(amount_anomalies),
    "CK-007": bool(dup_traces_ck007),
}
report.append("| 检查项 | 名称 | 结果 | 详情 |")
report.append("|--------|------|------|------|")
for ck_id in sorted(checks_summary.keys()):
    failed = checks_summary[ck_id]
    status = "❌ FAIL" if failed else "✅ PASS"
    if ck_id == "CK-001":
        detail = f"{len(dup_vouchers_ck001)} 组重复" if failed else "-"
    elif ck_id == "CK-002":
        detail = f"{len(zero_attach)} 条零附件" if failed else "-"
    elif ck_id == "CK-003":
        detail = f"{len(submitted)} 条 submitted" if failed else "-"
    elif ck_id == "CK-004":
        detail = f"{len(high_amount)} 条大额" if failed else "-"
    elif ck_id == "CK-005":
        detail = f"{len(approver_concentration)} 人超阈值" if failed else "-"
    elif ck_id == "CK-006":
        detail = f"{len(amount_anomalies)} 条异常" if failed else "-"
    elif ck_id == "CK-007":
        detail = f"{len(dup_traces_ck007)} 组重复" if failed else "-"
    name = {
        "CK-001": "重复凭证号",
        "CK-002": "零附件凭证",
        "CK-003": "状态异常",
        "CK-004": "大额交易",
        "CK-005": "审批人集中度",
        "CK-006": "金额合理性",
        "CK-007": "trace 唯一性",
    }[ck_id]
    report.append(f"| {ck_id} | {name} | {status} | {detail} |")

total_failed = sum(1 for v in checks_summary.values() if v)
report.append(f"")
report.append(f"**综合结果**: {total_failed}/7 项 **FAIL**")

# Write report
output = "\n".join(report)
with open(OUTPUT_FILE, "w") as f:
    f.write(output)

print(f"✅ Report written to {OUTPUT_FILE}")
print(f"   Total records: {N}")
print(f"   Failed checks: {total_failed}/7")
for ck_id in sorted(checks_summary.keys()):
    status = "FAIL" if checks_summary[ck_id] else "PASS"
    print(f"   {ck_id}: {status}")
