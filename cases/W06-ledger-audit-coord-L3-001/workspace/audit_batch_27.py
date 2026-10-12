#!/usr/bin/env python3
"""Audit batch_27.txt against audit_spec.json checks CK-001 through CK-007."""

import re
import json
from collections import Counter

# --- Load batch data ---
with open("output/batch_27.txt", "r") as f:
    raw = f.read().strip()

# Parse records
records = []
for line in raw.split("\n"):
    line = line.strip()
    if not line:
        continue
    m = re.match(
        r'ts=(\S+)\s+voucher=(\S+)\s+type=(\S+)\s+amount=([\d.]+)\s+approver=(\S+)\s+stage=(\S+)\s+attach=(\d+)\s+period=(\S+)\s+dept=(\S+)\s+trace=(\S+)',
        line
    )
    if m:
        rec = {
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
        }
        records.append(rec)

print(f"Parsed {len(records)} records")

# ============================================================
# CK-001: 重复凭证号
# ============================================================
ck001_findings = []
voucher_groups = {}
for r in records:
    voucher_groups.setdefault(r["voucher"], []).append(r)

for voucher, entries in voucher_groups.items():
    if len(entries) > 1:
        types_set = set(e["type"] for e in entries)
        amounts_set = set(e["amount"] for e in entries)
        depts_set = set(e["dept"] for e in entries)
        if len(types_set) > 1 or len(amounts_set) > 1 or len(depts_set) > 1:
            ck001_findings.append({
                "voucher": voucher,
                "count": len(entries),
                "types": list(types_set),
                "amounts": list(amounts_set),
                "depts": list(depts_set),
                "records": entries,
            })

print(f"\nCK-001 (Duplicate voucher): {len(ck001_findings)} anomalies")

# ============================================================
# CK-002: 零附件凭证 (attach=0)
# ============================================================
ck002_findings = [r for r in records if r["attach"] == 0]
print(f"CK-002 (Zero attachments): {len(ck002_findings)} records")

# ============================================================
# CK-003: 状态异常 (stage=submitted)
# ============================================================
ck003_findings = [r for r in records if r["stage"] == "submitted"]
print(f"CK-003 (Submitted stage): {len(ck003_findings)} records")

# ============================================================
# CK-004: 大额交易 (amount > 400,000)
# ============================================================
ck004_findings = [r for r in records if r["amount"] > 400_000]
print(f"CK-004 (Large amount >400k): {len(ck004_findings)} records")

# ============================================================
# CK-005: 审批人集中度
# ============================================================
approver_counter = Counter(r["approver"] for r in records)
total_records = len(records)
ck005_findings = {}
for approver, count in approver_counter.items():
    pct = count / total_records * 100
    if pct > 15:
        ck005_findings[approver] = {"count": count, "pct": round(pct, 2)}
print(f"CK-005 (Approver concentration >15%): {len(ck005_findings)} approvers flagged")

# ============================================================
# CK-006: 金额合理性 (negative or <100)
# ============================================================
ck006_findings = [r for r in records if r["amount"] < 0 or r["amount"] < 100]
print(f"CK-006 (Amount <100 or negative): {len(ck006_findings)} records")

# ============================================================
# CK-007: trace 唯一性
# ============================================================
trace_counter = Counter(r["trace"] for r in records)
ck007_findings = {t: c for t, c in trace_counter.items() if c > 1}
print(f"CK-007 (Duplicate trace): {len(ck007_findings)} duplicates")

# ============================================================
# Summary stats
# ============================================================
type_dist = Counter(r["type"] for r in records)
dept_dist = Counter(r["dept"] for r in records)
stage_dist = Counter(r["stage"] for r in records)
amount_sum = sum(r["amount"] for r in records)
amount_avg = amount_sum / total_records
amount_min = min(r["amount"] for r in records)
amount_max = max(r["amount"] for r in records)

print("\n--- Summary ---")
print(f"Total records: {total_records}")
print(f"Total amount: {amount_sum:,.2f}")
print(f"Avg amount: {amount_avg:,.2f}")
print(f"Min amount: {amount_min:,.2f}")
print(f"Max amount: {amount_max:,.2f}")
print(f"Type dist: {dict(type_dist)}")
print(f"Dept dist: {dict(dept_dist)}")
print(f"Stage dist: {dict(stage_dist)}")

# ============================================================
# Generate report
# ============================================================
report = []
report.append("# Batch 27 Audit Report")
report.append("")
report.append("**Audit Period:** 2026-Q3")
report.append(f"**Batch Range:** Lines 2962–3061 ({total_records} records)")
ts_min = min(r["ts"] for r in records)
ts_max = max(r["ts"] for r in records)
report.append(f"**Timestamp Range:** {ts_min} – {ts_max}")
report.append("")
report.append("## Executive Summary")
report.append("")
report.append(f"| Metric | Value |")
report.append(f"|---|---|")
report.append(f"| Total Vouchers | {total_records} |")
report.append(f"| Total Amount | {amount_sum:,.2f} |")
report.append(f"| Average Amount | {amount_avg:,.2f} |")
report.append(f"| Min Amount | {amount_min:,.2f} |")
report.append(f"| Max Amount | {amount_max:,.2f} |")
report.append(f"| CK-001 (Duplicate Voucher) | {len(ck001_findings)} anomalies |")
report.append(f"| CK-002 (Zero Attachments) | {len(ck002_findings)} records |")
report.append(f"| CK-003 (Unprocessed/Submitted) | {len(ck003_findings)} records |")
report.append(f"| CK-004 (Large Amount >400K) | {len(ck004_findings)} records |")
report.append(f"| CK-005 (Approver Concentration) | {len(ck005_findings)} flagged |")
report.append(f"| CK-006 (Amount <100) | {len(ck006_findings)} records |")
report.append(f"| CK-007 (Trace Duplicate) | {len(ck007_findings)} duplicates |")
report.append("")

report.append("---")
report.append("")
report.append("## CK-001: 重复凭证号 (HIGH)")
report.append("")
if ck001_findings:
    for f in ck001_findings:
        report.append(f"### Voucher `{f['voucher']}` — {f['count']} occurrences")
        report.append("")
        report.append(f"- **Types:** {', '.join(f['types'])}")
        report.append(f"- **Amounts:** {', '.join(f'{a:,.2f}' for a in f['amounts'])}")
        report.append(f"- **Departments:** {', '.join(f['depts'])}")
        report.append("")
        report.append("| # | Timestamp | Type | Amount | Dept | Stage | Trace |")
        report.append("|---|---|---|---|---|---|---|")
        for r in f['records']:
            report.append(f"| | {r['ts']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['stage']} | `{r['trace']}` |")
        report.append("")
else:
    report.append("✅ **No duplicate voucher anomalies detected.** All voucher IDs are unique within this batch.")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-002: 零附件凭证 (MEDIUM)")
report.append("")
if ck002_findings:
    report.append(f"**{len(ck002_findings)} records with attach=0** — these vouchers lack audit-trail attachments:")
    report.append("")
    report.append("| # | Voucher | Type | Amount | Stage | Dept | Timestamp |")
    report.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck002_findings, 1):
        report.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['stage']} | {r['dept']} | {r['ts']} |")
    report.append("")
    zero_dept = Counter(r["dept"] for r in ck002_findings)
    zero_type = Counter(r["type"] for r in ck002_findings)
    report.append("**Breakdown by Dept:** " + ", ".join(f"{k}={v}" for k, v in zero_dept.items()))
    report.append("")
    report.append("**Breakdown by Type:** " + ", ".join(f"{k}={v}" for k, v in zero_type.items()))
    report.append("")
else:
    report.append("✅ **No zero-attachment records.** All vouchers have at least 1 attachment.")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-003: 状态异常 — stage=submitted (MEDIUM)")
report.append("")
if ck003_findings:
    report.append(f"**{len(ck003_findings)} records** are still in `submitted` stage (not yet reviewed/approved/posted):")
    report.append("")
    report.append("| # | Voucher | Type | Amount | Dept | Approver | Timestamp |")
    report.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck003_findings, 1):
        report.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['approver']} | {r['ts']} |")
    report.append("")
    sub_dept = Counter(r["dept"] for r in ck003_findings)
    sub_type = Counter(r["type"] for r in ck003_findings)
    report.append("**Breakdown by Dept:** " + ", ".join(f"{k}={v}" for k, v in sub_dept.items()))
    report.append("")
    report.append("**Breakdown by Type:** " + ", ".join(f"{k}={v}" for k, v in sub_type.items()))
    report.append("")
else:
    report.append("✅ **No unprocessed vouchers.** All records have progressed beyond `submitted`.")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-004: 大额交易 (LOW)")
report.append("")
if ck004_findings:
    report.append(f"**{len(ck004_findings)} records** with amount > 400,000:")
    report.append("")
    report.append("| # | Voucher | Type | Amount | Dept | Stage | Timestamp |")
    report.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck004_findings, 1):
        report.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['stage']} | {r['ts']} |")
    report.append("")
    big_dept = Counter(r["dept"] for r in ck004_findings)
    big_type = Counter(r["type"] for r in ck004_findings)
    report.append("**By Dept:** " + ", ".join(f"{k}={v}" for k, v in big_dept.items()))
    report.append("")
    report.append("**By Type:** " + ", ".join(f"{k}={v}" for k, v in big_type.items()))
    report.append("")
else:
    report.append("✅ **No large-amount vouchers** exceeding 400,000.")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-005: 审批人集中度 (LOW)")
report.append("")
report.append("### Full Approver Distribution")
report.append("")
report.append("| Approver | Count | % | Flagged |")
report.append("|---|---|---|---|")
for a, c in sorted(approver_counter.items()):
    p = c / total_records * 100
    flag = "⚠️ >15%" if a in ck005_findings else ""
    report.append(f"| {a} | {c} | {p:.1f}% | {flag} |")
report.append("")

if ck005_findings:
    report.append("### ⚠️ Concentration Warnings")
    report.append("")
    for a, d in ck005_findings.items():
        report.append(f"- **{a}**: {d['count']} approvals ({d['pct']}%) — exceeds 15% threshold")
    report.append("")
else:
    report.append("✅ **No approver exceeds the 15% concentration threshold.**")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-006: 金额合理性 (LOW)")
report.append("")
if ck006_findings:
    report.append(f"**{len(ck006_findings)} records** with amount < 100 (no negative amounts):")
    report.append("")
    report.append("| # | Voucher | Type | Amount | Dept | Stage | Timestamp |")
    report.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(ck006_findings, 1):
        report.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['dept']} | {r['stage']} | {r['ts']} |")
    report.append("")
else:
    report.append("✅ **No small-amount anomalies.** All amounts ≥ 100.")
    report.append("")

report.append("---")
report.append("")
report.append("## CK-007: Trace 哈希唯一性 (LOW)")
report.append("")
if ck007_findings:
    report.append(f"**{len(ck007_findings)} duplicate trace hash(es)** detected:")
    report.append("")
    report.append("| Trace Hash | Occurrences | Affected Vouchers |")
    report.append("|---|---|---|")
    for t, c in ck007_findings.items():
        affected = [r["voucher"] for r in records if r["trace"] == t]
        report.append(f"| `{t}` | {c} | {', '.join(affected)} |")
    report.append("")
else:
    report.append("✅ **All trace hashes are unique.** No duplicates found.")
    report.append("")

report.append("---")
report.append("")
report.append("## Appendix: Batch Statistics")
report.append("")
report.append("### Type Distribution")
report.append("")
report.append("| Type | Count | % | Total Amount |")
report.append("|---|---|---|---|")
for t, c in sorted(type_dist.items()):
    p = c / total_records * 100
    ta = sum(r["amount"] for r in records if r["type"] == t)
    report.append(f"| {t} | {c} | {p:.1f}% | {ta:,.2f} |")
report.append("")

report.append("### Department Distribution")
report.append("")
report.append("| Dept | Count | % |")
report.append("|---|---|---|")
for d, c in sorted(dept_dist.items()):
    p = c / total_records * 100
    report.append(f"| {d} | {c} | {p:.1f}% |")
report.append("")

report.append("### Stage Distribution")
report.append("")
report.append("| Stage | Count | % |")
report.append("|---|---|---|")
for s, c in sorted(stage_dist.items()):
    p = c / total_records * 100
    report.append(f"| {s} | {c} | {p:.1f}% |")
report.append("")

report_text = "\n".join(report)

with open("output/batch_27_report.md", "w") as f:
    f.write(report_text)

print("\n✅ Report written to output/batch_27_report.md")
print(f"Report size: {len(report_text)} chars, {len(report_text.splitlines())} lines")
