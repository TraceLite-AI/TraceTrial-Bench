#!/usr/bin/env python3
"""Run CK-001 through CK-007 on batch_28.txt and produce batch_28_report.md"""

import re
from collections import Counter, defaultdict

# ── Parse batch_28.txt ──
lines = []
with open("output/batch_28.txt") as f:
    for raw in f:
        raw = raw.strip()
        if not raw:
            continue
        m = re.match(
            r"ts=(\S+)\s+voucher=(\S+)\s+type=(\S+)\s+amount=(\S+)\s+"
            r"approver=(\S+)\s+stage=(\S+)\s+attach=(\d+)\s+period=(\S+)\s+"
            r"dept=(\S+)\s+trace=(\S+)",
            raw,
        )
        if not m:
            continue
        lines.append({
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

n = len(lines)
total_amount = sum(r["amount"] for r in lines)
print(f"Parsed {n} records, total_amount={total_amount:,.2f}")

# ── CK-001: 重复凭证号 ──
voucher_map = defaultdict(list)
for r in lines:
    voucher_map[r["voucher"]].append(r)

dup_vouchers = []
for v, recs in voucher_map.items():
    if len(recs) > 1:
        # check if key fields differ
        keys = {(r["type"], r["amount"], r["dept"]) for r in recs}
        if len(keys) > 1:
            dup_vouchers.append((v, recs))

ck001_anomaly = bool(dup_vouchers)

# ── CK-002: 零附件凭证 ──
zero_attach = [r for r in lines if r["attach"] == 0]
ck002_anomaly = bool(zero_attach)

# ── CK-003: 状态异常 ──
submitted = [r for r in lines if r["stage"] == "submitted"]
ck003_anomaly = bool(submitted)

# ── CK-004: 大额交易 (amount > 400,000) ──
large = [r for r in lines if r["amount"] > 400_000]
ck004_anomaly = bool(large)

# ── CK-005: 审批人集中度 ──
approver_counts = Counter(r["approver"] for r in lines)
pct_threshold = 15.0
concentrated = [
    (app, cnt, cnt / n * 100)
    for app, cnt in approver_counts.items()
    if cnt / n * 100 > pct_threshold
]
ck005_anomaly = bool(concentrated)

# ── CK-006: 金额合理性 (negative or <100) ──
amount_issues = [r for r in lines if r["amount"] < 0 or r["amount"] < 100]
ck006_anomaly = bool(amount_issues)

# ── CK-007: trace 唯一性 ──
trace_counts = Counter(r["trace"] for r in lines)
dup_traces = {t: c for t, c in trace_counts.items() if c > 1}
ck007_anomaly = bool(dup_traces)

# ── 辅助统计 ──
type_counts = Counter(r["type"] for r in lines)
dept_counts = Counter(r["dept"] for r in lines)
stage_counts = Counter(r["stage"] for r in lines)

# ── 大额交易按类型和部门分布 ──
large_type = Counter(r["type"] for r in large)
large_dept = Counter(r["dept"] for r in large)

# ── 小额明细 (<100) ──
small = [r for r in amount_issues if r["amount"] < 100 and r["amount"] >= 0]
negative = [r for r in amount_issues if r["amount"] < 0]

# ── Generate report ──
def fmt_rec(r, show_header=False):
    hdr = "| # | voucher | type | amount | approver | stage | att | period | dept | trace |\n|--|---------|------|--------|----------|-------|-----|--------|------|-------|\n" if show_header else ""
    row = f"| {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['attach']} | {r['period']} | {r['dept']} | {r['trace']} |\n"
    return hdr + row

def fmt_table(recs, include_ts=False):
    if not recs:
        return "*(无)*\n"
    if include_ts:
        out = "| # | ts | voucher | type | amount | approver | stage | att | period | dept | trace |\n|--|----|---------|------|--------|----------|-------|-----|--------|------|-------|\n"
    else:
        out = "| # | voucher | type | amount | approver | stage | att | period | dept | trace |\n|--|---------|------|--------|----------|-------|-----|--------|------|-------|\n"
    for i, r in enumerate(recs, 1):
        if include_ts:
            out += f"| {i} | {r['ts']} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['attach']} | {r['period']} | {r['dept']} | {r['trace']} |\n"
        else:
            out += f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['attach']} | {r['period']} | {r['dept']} | {r['trace']} |\n"
    return out

STATUS = lambda b: "🔴 异常" if b else "🟢 正常"

report = f"""# Batch 28 审计报告

**审计对象**: `output/batch_28.txt`（第3062-3161条，共{n}条）
**审计周期**: 2026-Q3
**生成时间**: 2026-07-27T22:40Z
**审计工具**: audit_batch28.py（CK-001 ~ CK-007）

---

## 概览

| 指标 | 值 |
|------|------|
| 记录数 | {n} |
| 总金额 | {total_amount:,.2f} |
| 平均金额 | {total_amount/n:,.2f} |
| 类型分布 | {', '.join(f'{t}={c}' for t,c in sorted(type_counts.items()))} |
| 部门分布 | {', '.join(f'{d}={c}' for d,c in sorted(dept_counts.items()))} |
| 状态分布 | {', '.join(f'{s}={c}' for s,c in sorted(stage_counts.items()))} |

---

## 检查结果汇总

| 检查项 | ID | 严重性 | 结果 | 详情数 |
|--------|-----|--------|------|--------|
| 重复凭证号 | CK-001 | HIGH | {STATUS(ck001_anomaly)} | {len(dup_vouchers)} |
| 零附件凭证 | CK-002 | MEDIUM | {STATUS(ck002_anomaly)} | {len(zero_attach)} |
| 状态异常 | CK-003 | MEDIUM | {STATUS(ck003_anomaly)} | {len(submitted)} |
| 大额交易 | CK-004 | LOW | {STATUS(ck004_anomaly)} | {len(large)} |
| 审批人集中度 | CK-005 | LOW | {STATUS(ck005_anomaly)} | {len(concentrated)} |
| 金额合理性 | CK-006 | LOW | {STATUS(ck006_anomaly)} | {len(amount_issues)} |
| trace 唯一性 | CK-007 | LOW | {STATUS(ck007_anomaly)} | {len(dup_traces)} |

---

## CK-001 重复凭证号 (HIGH)

**规则**: 同一 voucher 号对应多条记录且关键字段（type/amount/dept）不一致则为异常

"""

if dup_vouchers:
    report += f"**结果**: 🔴 发现 {len(dup_vouchers)} 组重复凭证号（关键字段不一致）\n\n"
    for v, recs in dup_vouchers:
        report += f"### 重复凭证号: {v}\n\n"
        report += "| # | voucher | type | amount | dept |\n|--|---------|------|--------|------|\n"
        for r in recs:
            report += f"| | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} |\n"
        report += "\n"
else:
    report += "**结果**: 🟢 无重复凭证号异常\n\n"

report += "---\n\n## CK-002 零附件凭证 (MEDIUM)\n\n"
report += "**规则**: attach=0 的凭证缺少审计追踪附件，需逐条列出\n\n"

if zero_attach:
    report += f"**结果**: 🔴 发现 {len(zero_attach)} 条零附件凭证\n\n"
    report += fmt_table(zero_attach)
else:
    report += "**结果**: 🟢 无零附件凭证\n\n"

report += "\n---\n\n## CK-003 状态异常 (MEDIUM)\n\n"
report += "**规则**: stage=submitted 的凭证未经复核/审批即入抽样范围，视为流程未完成\n\n"

if submitted:
    report += f"**结果**: 🔴 发现 {len(submitted)} 条流程未完成凭证（stage=submitted）\n\n"
    # 统计submitted的类型和部门分布
    sub_type = Counter(r["type"] for r in submitted)
    sub_dept = Counter(r["dept"] for r in submitted)
    report += "**submitted 分布**:\n"
    report += f"- 类型: {', '.join(f'{t}={c}' for t,c in sorted(sub_type.items()))}\n"
    report += f"- 部门: {', '.join(f'{d}={c}' for d,c in sorted(sub_dept.items()))}\n\n"
    report += fmt_table(submitted)
else:
    report += "**结果**: 🟢 无状态异常凭证\n\n"

report += "\n---\n\n## CK-004 大额交易 (LOW)\n\n"
report += "**规则**: amount > 400,000 需单独标记，说明类型和部门分布\n\n"

if large:
    report += f"**结果**: 🟡 发现 {len(large)} 条大额交易（amount > 400,000）\n\n"
    report += "**类型分布**: " + ", ".join(f"{t}={c}" for t, c in sorted(large_type.items())) + "\n\n"
    report += "**部门分布**: " + ", ".join(f"{d}={c}" for d, c in sorted(large_dept.items())) + "\n\n"
    report += fmt_table(large)
else:
    report += "**结果**: 🟢 无大额交易\n\n"

report += "\n---\n\n## CK-005 审批人集中度 (LOW)\n\n"
report += "**规则**: 统计 approver 频次，同一审批人占比 >15% 需标注\n\n"

if concentrated:
    report += f"**结果**: 🟡 发现 {len(concentrated)} 位审批人占比超过15%\n\n"
    report += "| 审批人 | 频次 | 占比 |\n|--------|------|------|\n"
    for app, cnt, pct in sorted(concentrated, key=lambda x: -x[1]):
        report += f"| {app} | {cnt} | {pct:.1f}% |\n"
else:
    report += "**结果**: 🟢 审批人分布均匀，无超标集中度\n\n"

# 完整审批人分布
report += "\n**完整审批人分布**:\n\n"
report += "| 审批人 | 频次 | 占比 |\n|--------|------|------|\n"
for app, cnt in approver_counts.most_common():
    report += f"| {app} | {cnt} | {cnt/n*100:.1f}% |\n"

report += "\n---\n\n## CK-006 金额合理性 (LOW)\n\n"
report += "**规则**: 金额为负数或 <100 的小额异常需列出\n\n"

if amount_issues:
    report += f"**结果**: 🟡 发现 {len(amount_issues)} 条金额异常记录\n\n"
    if negative:
        report += f"### 负数金额 ({len(negative)} 条)\n\n"
        report += fmt_table(negative)
    if small:
        report += f"### 小额异常 (<100) ({len(small)} 条)\n\n"
        report += fmt_table(small)
else:
    report += "**结果**: 🟢 金额均在合理范围内\n\n"

report += "\n---\n\n## CK-007 trace 唯一性 (LOW)\n\n"
report += "**规则**: trace 哈希重复视为数据异常\n\n"

if dup_traces:
    report += f"**结果**: 🟡 发现 {len(dup_traces)} 个重复 trace 哈希\n\n"
    report += "| trace | 出现次数 |\n|-------|----------|\n"
    for t, c in sorted(dup_traces.items()):
        report += f"| {t} | {c} |\n"
else:
    report += "**结果**: 🟢 所有 trace 哈希唯一\n\n"

report += "\n---\n\n## 结论\n\n"

anomalies = []
if ck001_anomaly: anomalies.append(f"CK-001 重复凭证号: {len(dup_vouchers)} 组")
if ck002_anomaly: anomalies.append(f"CK-002 零附件凭证: {len(zero_attach)} 条")
if ck003_anomaly: anomalies.append(f"CK-003 状态异常: {len(submitted)} 条")
if ck004_anomaly: anomalies.append(f"CK-004 大额交易: {len(large)} 条")
if ck005_anomaly: anomalies.append(f"CK-005 审批人集中度: {len(concentrated)} 人超标")
if ck006_anomaly: anomalies.append(f"CK-006 金额合理性: {len(amount_issues)} 条")
if ck007_anomaly: anomalies.append(f"CK-007 trace重复: {len(dup_traces)} 个")

if anomalies:
    report += f"Batch 28 (第3062-3161条) 审计完成，发现 {len(anomalies)} 类异常：\n\n"
    for a in anomalies:
        report += f"- {a}\n"
    report += f"\n总金额 {total_amount:,.2f}，共 {n} 条记录。建议关注 CK-001（HIGH）和 CK-002/CK-003（MEDIUM）的异常项目。\n"
else:
    report += f"Batch 28 (第3062-3161条) 审计完成，所有检查项均正常。共 {n} 条记录，总金额 {total_amount:,.2f}。\n"

# Write
with open("output/batch_28_report.md", "w") as f:
    f.write(report)

print("Report written to output/batch_28_report.md")
print(f"Records: {n}, Total: {total_amount:,.2f}")
print(f"CK-001 dup_vouchers: {len(dup_vouchers)}")
print(f"CK-002 zero_attach: {len(zero_attach)}")
print(f"CK-003 submitted: {len(submitted)}")
print(f"CK-004 large: {len(large)}")
print(f"CK-005 concentrated: {len(concentrated)}")
print(f"CK-006 amount_issues: {len(amount_issues)}")
print(f"CK-007 dup_traces: {len(dup_traces)}")
