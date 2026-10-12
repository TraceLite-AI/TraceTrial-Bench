#!/usr/bin/env python3
"""Audit batch_2.txt (vouchers 101-200) against audit_spec.json checks."""

import re
import json
from collections import Counter
from datetime import date

# -- Parse input ----------------------------------------------------------------
with open("output/batch_2.txt") as f:
    lines = [l.strip() for l in f if l.strip()]

pattern = re.compile(
    r"ts=(?P<ts>\S+)\s+"
    r"voucher=(?P<voucher>V\d+)\s+"
    r"type=(?P<type>\S+)\s+"
    r"amount=(?P<amount>[\d.]+)\s+"
    r"approver=(?P<approver>\S+)\s+"
    r"stage=(?P<stage>\S+)\s+"
    r"attach=(?P<attach>\d+)\s+"
    r"period=(?P<period>\S+)\s+"
    r"dept=(?P<dept>\S+)\s+"
    r"trace=(?P<trace>\S+)"
)

vouchers = []
for line in lines:
    m = pattern.match(line)
    if m:
        d = m.groupdict()
        d["amount"] = float(d["amount"])
        d["attach"] = int(d["attach"])
        vouchers.append(d)

total = len(vouchers)
print(f"Parsed {total} vouchers from batch_2.txt")

# -- CK-001: Duplicate voucher IDs --------------------------------------------
voucher_by_id = {}
for v in vouchers:
    vid = v["voucher"]
    if vid not in voucher_by_id:
        voucher_by_id[vid] = []
    voucher_by_id[vid].append(v)

ck001_anomalies = []
for vid, recs in voucher_by_id.items():
    if len(recs) > 1:
        # Check if type/amount/dept differ
        types = set(r["type"] for r in recs)
        amounts = set(r["amount"] for r in recs)
        depts = set(r["dept"] for r in recs)
        if len(types) > 1 or len(amounts) > 1 or len(depts) > 1:
            ck001_anomalies.append((vid, recs))

print(f"\nCK-001 duplicate vouchers: {len(ck001_anomalies)}")
for vid, recs in ck001_anomalies:
    for r in recs:
        print(f"  {r['voucher']} type={r['type']} amount={r['amount']} dept={r['dept']} stage={r['stage']}")

# -- CK-002: Zero-attachment vouchers -----------------------------------------
ck002 = [v for v in vouchers if v["attach"] == 0]
print(f"\nCK-002 zero-attachment: {len(ck002)}")

# -- CK-003: Stage == submitted -----------------------------------------------
ck003 = [v for v in vouchers if v["stage"] == "submitted"]
print(f"CK-003 submitted stage: {len(ck003)}")

# -- CK-004: Large transactions (>400000) -------------------------------------
ck004 = [v for v in vouchers if v["amount"] > 400000]
print(f"CK-004 large transactions: {len(ck004)}")

# -- CK-005: Approver concentration (>15%) ------------------------------------
approver_counts = Counter(v["approver"] for v in vouchers)
threshold = total * 0.15
ck005 = {app: cnt for app, cnt in approver_counts.items() if cnt > threshold}
print(f"CK-005 approvers over threshold ({threshold:.0f} = 15% of {total}):")
for app, cnt in sorted(approver_counts.items(), key=lambda x: -x[1]):
    flag = " *** OVER THRESHOLD ***" if app in ck005 else ""
    print(f"  {app}: {cnt} ({cnt/total*100:.1f}%){flag}")

# -- CK-006: Amount reasonableness ---------------------------------------------
ck006 = [v for v in vouchers if v["amount"] < 0 or (0 < v["amount"] < 100)]
print(f"CK-006 unreasonable amounts: {len(ck006)}")
for v in ck006:
    print(f"  {v['voucher']} amount={v['amount']} type={v['type']} dept={v['dept']}")

# -- CK-007: Trace uniqueness --------------------------------------------------
trace_counts = Counter(v["trace"] for v in vouchers)
ck007 = [t for t, cnt in trace_counts.items() if cnt > 1]
print(f"CK-007 duplicate traces: {len(ck007)}")
for t in ck007:
    recs = [v for v in vouchers if v["trace"] == t]
    for r in recs:
        print(f"  {r['voucher']} trace={r['trace']}")

# -- Summary -------------------------------------------------------------------
ck001_count = sum(len(recs) for _, recs in ck001_anomalies)
total_anomalies = ck001_count + len(ck002) + len(ck003) + len(ck004) + len(ck005) + len(ck006) + len(ck007)
high_risk = ck001_count
medium_risk = len(ck002) + len(ck003)
low_risk = len(ck004) + len(ck005) + len(ck006) + len(ck007)

print(f"\n=== SUMMARY ===")
print(f"Total vouchers: {total}")
print(f"Total anomalies: {total_anomalies}")
print(f"High-risk: {high_risk} (CK-001)")
print(f"Medium-risk: {medium_risk} (CK-002 + CK-003)")
print(f"Low-risk: {low_risk} (CK-004 + CK-005 + CK-006 + CK-007)")
print(f"Audit conclusion: {'FAIL' if high_risk > 0 else 'PASS'}")

# -- Generate report -----------------------------------------------------------
today = date.today().isoformat()

def format_amount(a):
    return f"{a:,.2f}"

report = f"""# Batch 2 审计报告
**凭证区间**: 第101-200条 | **审计日期**: {today} | **审计结论**: {'FAIL' if high_risk > 0 else 'PASS'}

## 执行摘要
- 总凭证数: {total}
- 异常数: {total_anomalies}
- 高风险异常: {high_risk}
- 中风险异常: {medium_risk}
- 低风险异常: {low_risk}

## CK-001 重复凭证号 [HIGH]
"""

if ck001_anomalies:
    for vid, recs in ck001_anomalies:
        report += f"**{vid}** 出现 {len(recs)} 次:\n"
        for r in recs:
            report += f"- type={r['type']} amount={format_amount(r['amount'])} dept={r['dept']} stage={r['stage']} approver={r['approver']} attach={r['attach']}\n"
        report += "\n"
else:
    report += "未发现异常\n\n"

report += "## CK-002 零附件凭证 [MEDIUM]\n"
if ck002:
    report += "| voucher | type | amount | dept | stage |\n"
    report += "|---|---|---|---|---|\n"
    for v in ck002:
        report += f"| {v['voucher']} | {v['type']} | {format_amount(v['amount'])} | {v['dept']} | {v['stage']} |\n"
else:
    report += "未发现异常\n"

report += "\n## CK-003 状态异常 [MEDIUM]\n"
if ck003:
    report += "| voucher | type | amount | dept | approver |\n"
    report += "|---|---|---|---|---|\n"
    for v in ck003:
        report += f"| {v['voucher']} | {v['type']} | {format_amount(v['amount'])} | {v['dept']} | {v['approver']} |\n"
else:
    report += "未发现异常\n"

report += "\n## CK-004 大额交易 [LOW]\n"
if ck004:
    report += "| voucher | type | amount | dept |\n"
    report += "|---|---|---|---|\n"
    for v in sorted(ck004, key=lambda x: -x["amount"]):
        report += f"| {v['voucher']} | {v['type']} | {format_amount(v['amount'])} | {v['dept']} |\n"
else:
    report += "未发现异常\n"

report += f"\n## CK-005 审批人集中度 [LOW]\n"
report += f"批次凭证数: {total}, 阈值 15% = {threshold:.1f} 条\n\n"
report += "| approver | 频次 | 占比 | 标注 |\n"
report += "|---|---|---|---|\n"
for app, cnt in sorted(approver_counts.items(), key=lambda x: -x[1]):
    pct = cnt / total * 100
    flag = "⚠️ 超过阈值" if app in ck005 else ""
    report += f"| {app} | {cnt} | {pct:.1f}% | {flag} |\n"

report += "\n## CK-006 金额合理性 [LOW]\n"
if ck006:
    report += "| voucher | type | amount | dept |\n"
    report += "|---|---|---|---|\n"
    for v in ck006:
        report += f"| {v['voucher']} | {v['type']} | {format_amount(v['amount'])} | {v['dept']} |\n"
else:
    report += "未发现异常\n"

report += "\n## CK-007 trace 唯一性 [LOW]\n"
if ck007:
    for t in ck007:
        recs = [v for v in vouchers if v["trace"] == t]
        report += f"**trace: {t}** (出现 {len(recs)} 次)\n"
        for r in recs:
            report += f"- {r['voucher']} type={r['type']} amount={format_amount(r['amount'])} dept={r['dept']}\n"
        report += "\n"
else:
    report += "未发现异常\n"

report += "\n## 批次结论\n"
if high_risk > 0:
    report += f"批次2发现 {high_risk} 条高风险异常（重复凭证号），审计结论: **FAIL**\n"
elif medium_risk > 0:
    report += f"批次2发现 {medium_risk} 条中风险异常（零附件+状态异常），审计结论: **PASS**（中风险已记录）\n"
else:
    report += "批次2未发现高/中风险异常，审计结论: **PASS**\n"

with open("output/batch_2_report.md", "w") as f:
    f.write(report)

print("\nReport written to output/batch_2_report.md")
