#!/usr/bin/env python3
"""Audit batch_23.txt (records 2201-2300) against audit_spec.json checks CK-001 ~ CK-007."""
import re
import json
from collections import Counter

# --- Load data ---
with open("output/batch_23.txt") as f:
    lines = [l.strip() for l in f if l.strip()]

records = []
pattern = re.compile(
    r'ts=(\S+)\s+voucher=(\S+)\s+type=(\S+)\s+amount=([\d.]+)\s+'
    r'approver=(\S+)\s+stage=(\S+)\s+attach=(\d+)\s+'
    r'period=(\S+)\s+dept=(\S+)\s+trace=(\S+)'
)

for i, line in enumerate(lines, start=2201):
    m = pattern.match(line)
    if not m:
        print(f"WARNING: line {i} did not match: {line}")
        continue
    records.append({
        "line": i,
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
total_amount = sum(r["amount"] for r in records)

print(f"Loaded {N} records from batch_23.txt")
print(f"Total amount: {total_amount:,.2f}")
print()

# ========== CK-001: 重复凭证号 ==========
print("=" * 60)
print("CK-001: 重复凭证号 (high)")
print("=" * 60)
voucher_groups = {}
for r in records:
    v = r["voucher"]
    voucher_groups.setdefault(v, []).append(r)

dup_issues = []
for v, grp in voucher_groups.items():
    if len(grp) > 1:
        # Check if any key fields differ
        keys = {(r["type"], r["amount"], r["dept"]) for r in grp}
        if len(keys) > 1:
            dup_issues.append((v, grp))
            print(f"  DUPLICATE voucher {v}: {len(grp)} occurrences with inconsistent fields")
            for r in grp:
                print(f"    line {r['line']}: type={r['type']} amount={r['amount']} dept={r['dept']} stage={r['stage']}")

if not dup_issues:
    print("  No duplicate voucher anomalies found.")

print(f"\n  CK-001 count: {len(dup_issues)}")
print()

# ========== CK-002: 零附件凭证 ==========
print("=" * 60)
print("CK-002: 零附件凭证 (medium)")
print("=" * 60)
zero_attach = [r for r in records if r["attach"] == 0]
if zero_attach:
    for r in zero_attach:
        print(f"  line {r['line']}: voucher={r['voucher']} type={r['type']} amount={r['amount']:,.2f} dept={r['dept']} stage={r['stage']}")
else:
    print("  No zero-attachment vouchers found.")

print(f"\n  CK-002 count: {len(zero_attach)}")
print()

# ========== CK-003: 状态异常 ==========
print("=" * 60)
print("CK-003: 状态异常 (medium)")
print("=" * 60)
submitted = [r for r in records if r["stage"] == "submitted"]
if submitted:
    for r in submitted:
        print(f"  line {r['line']}: voucher={r['voucher']} type={r['type']} amount={r['amount']:,.2f} dept={r['dept']} approver={r['approver']}")
else:
    print("  No submitted-stage vouchers found.")

print(f"\n  CK-003 count: {len(submitted)}")
print()

# ========== CK-004: 大额交易 ==========
print("=" * 60)
print("CK-004: 大额交易 (low)")
print("=" * 60)
large = [r for r in records if r["amount"] > 400_000]
if large:
    # By type distribution
    by_type = Counter(r["type"] for r in large)
    by_dept = Counter(r["dept"] for r in large)
    print(f"  Records with amount > 400,000: {len(large)}")
    print(f"  Type distribution: {dict(by_type)}")
    print(f"  Dept distribution: {dict(by_dept)}")
    print()
    for r in sorted(large, key=lambda x: x["amount"], reverse=True):
        print(f"  line {r['line']}: voucher={r['voucher']} type={r['type']} amount={r['amount']:,.2f} dept={r['dept']} stage={r['stage']}")
else:
    print("  No large transactions (>400k) found.")

print(f"\n  CK-004 count: {len(large)}")
print()

# ========== CK-005: 审批人集中度 ==========
print("=" * 60)
print("CK-005: 审批人集中度 (low)")
print("=" * 60)
approver_counts = Counter(r["approver"] for r in records)
threshold = N * 0.15
print(f"  Total records: {N}, 15% threshold: {threshold:.1f}")
print()
for approver, count in approver_counts.most_common():
    pct = count / N * 100
    flag = " *** CONCENTRATION > 15% ***" if pct > 15 else ""
    print(f"  {approver}: {count} records ({pct:.1f}%){flag}")

print()

# ========== CK-006: 金额合理性 ==========
print("=" * 60)
print("CK-006: 金额合理性 (low)")
print("=" * 60)
amount_issues = [r for r in records if r["amount"] < 0 or r["amount"] < 100]
if amount_issues:
    for r in sorted(amount_issues, key=lambda x: x["amount"]):
        reason = "NEGATIVE" if r["amount"] < 0 else "small (<100)"
        print(f"  line {r['line']}: voucher={r['voucher']} amount={r['amount']:,.2f} ({reason}) type={r['type']} dept={r['dept']}")
else:
    print("  No negative or small-amount anomalies found.")
# Also check for negative amounts specifically
neg = [r for r in records if r["amount"] < 0]
small = [r for r in records if 0 <= r["amount"] < 100]

print(f"\n  Negative amounts: {len(neg)}")
print(f"  Small amounts (<100): {len(small)}")
print(f"  CK-006 total issues: {len(amount_issues)}")
print()

# ========== CK-007: trace 唯一性 ==========
print("=" * 60)
print("CK-007: trace 唯一性 (low)")
print("=" * 60)
trace_counts = Counter(r["trace"] for r in records)
dup_traces = {t: c for t, c in trace_counts.items() if c > 1}
if dup_traces:
    print(f"  Found {len(dup_traces)} duplicate trace hash(es):")
    for t, c in dup_traces.items():
        print(f"    trace={t}: {c} occurrences")
        for r in records:
            if r["trace"] == t:
                print(f"      line {r['line']}: voucher={r['voucher']}")
else:
    print("  All trace hashes are unique. No duplicates found.")

print(f"\n  CK-007 duplicate traces: {len(dup_traces)}")

# === Summary ===
print()
print("=" * 60)
print("AUDIT SUMMARY for batch_23.txt (lines 2201-2300)")
print("=" * 60)
print(f"  Total records:     {N}")
print(f"  Total amount:      {total_amount:,.2f}")
print(f"  CK-001 duplicates: {len(dup_issues)}")
print(f"  CK-002 zero-attach:{len(zero_attach)}")
print(f"  CK-003 submitted:  {len(submitted)}")
print(f"  CK-004 large (>400k): {len(large)}")
# Count approvers > 15%
high_conc = [(a, c) for a, c in approver_counts.items() if c / N > 0.15]
print(f"  CK-005 concentration: {len(high_conc)} approver(s) > 15%")
print(f"  CK-006 amount issues: {len(amount_issues)}")
print(f"  CK-007 trace dups:    {len(dup_traces)}")
