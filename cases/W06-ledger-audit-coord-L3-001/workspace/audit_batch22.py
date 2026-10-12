#!/usr/bin/env python3
"""Audit batch_22.txt (vouchers 2101-2200) against CK-001 through CK-007."""

import re
from collections import Counter

def parse_batch(path):
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = {}
            rec['ts'] = re.search(r'ts=(\S+)', line).group(1)
            rec['voucher'] = re.search(r'voucher=(\S+)', line).group(1)
            rec['type'] = re.search(r'type=(\S+)', line).group(1)
            rec['amount'] = float(re.search(r'amount=([\d.]+)', line).group(1))
            rec['approver'] = re.search(r'approver=(\S+)', line).group(1)
            rec['stage'] = re.search(r'stage=(\S+)', line).group(1)
            rec['attach'] = int(re.search(r'attach=(\d+)', line).group(1))
            rec['period'] = re.search(r'period=(\S+)', line).group(1)
            rec['dept'] = re.search(r'dept=(\S+)', line).group(1)
            rec['trace'] = re.search(r'trace=(\S+)', line).group(1)
            rec['line'] = line
            records.append(rec)
    return records

def ck001_duplicate_vouchers(records):
    """CK-001: 重复凭证号 — same voucher with inconsistent key fields"""
    results = []
    seen = {}
    for r in records:
        v = r['voucher']
        key = (r['type'], r['amount'], r['dept'])
        if v in seen:
            prev_key, prev_line = seen[v]
            if key != prev_key:
                results.append({
                    'voucher': v,
                    'record_1': prev_line,
                    'record_2': r['line'],
                    'diff': f"type={prev_key[0]}/{r['type']} amount={prev_key[1]}/{r['amount']} dept={prev_key[2]}/{r['dept']}"
                })
        else:
            seen[v] = (key, r['line'])
    return results

def ck002_zero_attach(records):
    """CK-002: 零附件凭证 — attach=0"""
    return [r for r in records if r['attach'] == 0]

def ck003_submitted_stage(records):
    """CK-003: 状态异常 — stage=submitted"""
    return [r for r in records if r['stage'] == 'submitted']

def ck004_large_amount(records):
    """CK-004: 大额交易 — amount > 400,000"""
    return [r for r in records if r['amount'] > 400000]

def ck005_approver_concentration(records):
    """CK-005: 审批人集中度 — approver freq > 15%"""
    total = len(records)
    if total == 0:
        return [], Counter()
    counter = Counter(r['approver'] for r in records)
    threshold = 0.15
    over = {a: c for a, c in counter.items() if c / total > threshold}
    return over, counter

def ck006_amount_anomalies(records):
    """CK-006: 金额合理性 — negative or < 100"""
    return [r for r in records if r['amount'] < 0 or r['amount'] < 100]

def ck007_trace_duplicates(records):
    """CK-007: trace 唯一性 — duplicate trace hashes"""
    counter = Counter(r['trace'] for r in records)
    dups = {t: c for t, c in counter.items() if c > 1}
    # Get the records with duplicate traces
    dup_records = {}
    for r in records:
        if r['trace'] in dups:
            dup_records.setdefault(r['trace'], []).append(r)
    return dups, dup_records

def main():
    records = parse_batch('output/batch_22.txt')
    total = len(records)
    total_amount = sum(r['amount'] for r in records)

    # CK-001
    dup_vouchers = ck001_duplicate_vouchers(records)

    # CK-002
    zero_attach = ck002_zero_attach(records)

    # CK-003
    submitted = ck003_submitted_stage(records)

    # CK-004
    large = ck004_large_amount(records)

    # CK-005
    approver_over, approver_counter = ck005_approver_concentration(records)

    # CK-006
    amount_anomalies = ck006_amount_anomalies(records)

    # CK-007
    trace_dups, trace_dup_records = ck007_trace_duplicates(records)

    # --- Build report ---
    lines = []
    lines.append("# Batch 22 审计报告 (Audit Report)")
    lines.append("")
    lines.append("**审计批次:** batch_22.txt (第2101-2200条凭证)")
    lines.append(f"**审计期间:** 2026-Q3")
    lines.append(f"**审计时间:** 2026-07-27T22:30Z")
    lines.append(f"**凭证总数:** {total}")
    lines.append(f"**总金额:** {total_amount:,.2f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- CK-001 ---
    lines.append("## CK-001 重复凭证号 (severity: high)")
    lines.append("")
    if not dup_vouchers:
        lines.append("✅ **通过** — 未发现重复凭证号异常。")
    else:
        lines.append(f"❌ **异常** — 发现 {len(dup_vouchers)} 组重复凭证号不一致:")
        lines.append("")
        for i, d in enumerate(dup_vouchers, 1):
            lines.append(f"### 异常 #{i}: {d['voucher']}")
            lines.append(f"- 记录1: `{d['record_1']}`")
            lines.append(f"- 记录2: `{d['record_2']}`")
            lines.append(f"- 差异: {d['diff']}")
            lines.append("")
    lines.append("")

    # --- CK-002 ---
    lines.append("## CK-002 零附件凭证 (severity: medium)")
    lines.append("")
    if not zero_attach:
        lines.append("✅ **通过** — 未发现零附件凭证。")
    else:
        lines.append(f"⚠️ **发现** — 共 {len(zero_attach)} 条 attach=0 的凭证:")
        lines.append("")
        lines.append("| # | 凭证号 | 类型 | 金额 | 审批人 | 状态 | 部门 | 时间戳 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(zero_attach, 1):
            lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['stage']} | {r['dept']} | {r['ts']} |")
        lines.append("")
    lines.append("")

    # --- CK-003 ---
    lines.append("## CK-003 状态异常 (severity: medium)")
    lines.append("")
    if not submitted:
        lines.append("✅ **通过** — 未发现 stage=submitted 的凭证。")
    else:
        lines.append(f"⚠️ **发现** — 共 {len(submitted)} 条 stage=submitted 的凭证（流程未完成）:")
        lines.append("")
        lines.append("| # | 凭证号 | 类型 | 金额 | 审批人 | 部门 | 附件数 | 时间戳 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(submitted, 1):
            lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['dept']} | {r['attach']} | {r['ts']} |")
        lines.append("")
    lines.append("")

    # --- CK-004 ---
    lines.append("## CK-004 大额交易 (severity: low)")
    lines.append("")
    if not large:
        lines.append("✅ **通过** — 未发现 amount > 400,000 的大额交易。")
    else:
        lines.append(f"📊 **标记** — 共 {len(large)} 条大额交易 (amount > 400,000):")
        lines.append("")
        # Type distribution
        type_dist = Counter(r['type'] for r in large)
        dept_dist = Counter(r['dept'] for r in large)
        lines.append(f"- 类型分布: {dict(type_dist)}")
        lines.append(f"- 部门分布: {dict(dept_dist)}")
        lines.append("")
        lines.append("| # | 凭证号 | 类型 | 金额 | 审批人 | 部门 | 状态 | 时间戳 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(large, 1):
            lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['dept']} | {r['stage']} | {r['ts']} |")
        lines.append("")
    lines.append("")

    # --- CK-005 ---
    lines.append("## CK-005 审批人集中度 (severity: low)")
    lines.append("")
    lines.append("**审批人频次统计 (共100条):**")
    lines.append("")
    lines.append("| 审批人 | 次数 | 占比 | 标记 |")
    lines.append("|---|---|---|---|")
    for approver, count in approver_counter.most_common():
        pct = count / total * 100
        flag = "⚠️ >15%" if count / total > 0.15 else ""
        lines.append(f"| {approver} | {count} | {pct:.1f}% | {flag} |")
    lines.append("")
    if approver_over:
        lines.append(f"⚠️ **注意:** 以下审批人占比超过 15%:")
        for a, c in approver_over.items():
            lines.append(f"- {a}: {c}次 ({c/total*100:.1f}%)")
    else:
        lines.append("✅ 无审批人占比超过15%。")
    lines.append("")
    lines.append("")

    # --- CK-006 ---
    lines.append("## CK-006 金额合理性 (severity: low)")
    lines.append("")
    if not amount_anomalies:
        lines.append("✅ **通过** — 未发现金额异常（负数或 <100）。")
    else:
        lines.append(f"⚠️ **发现** — 共 {len(amount_anomalies)} 条金额异常:")
        lines.append("")
        lines.append("| # | 凭证号 | 类型 | 金额 | 审批人 | 部门 | 状态 | 附件数 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for i, r in enumerate(amount_anomalies, 1):
            reason = "负数" if r['amount'] < 0 else f"小额 ({r['amount']})"
            lines.append(f"| {i} | {r['voucher']} | {r['type']} | {r['amount']:,.2f} | {r['approver']} | {r['dept']} | {r['stage']} | {r['attach']} |")
        lines.append("")
    lines.append("")

    # --- CK-007 ---
    lines.append("## CK-007 Trace 唯一性 (severity: low)")
    lines.append("")
    if not trace_dups:
        lines.append("✅ **通过** — 所有 trace 哈希值唯一。")
    else:
        lines.append(f"❌ **异常** — 发现 {len(trace_dups)} 个重复 trace 哈希:")
        lines.append("")
        for trace, count in trace_dups.items():
            lines.append(f"### Trace: `{trace}` (出现 {count} 次)")
            recs = trace_dup_records[trace]
            for j, r in enumerate(recs, 1):
                lines.append(f"- 记录{j}: voucher={r['voucher']} type={r['type']} amount={r['amount']:,.2f} dept={r['dept']} ts={r['ts']}")
            lines.append("")
    lines.append("")

    # --- Summary ---
    lines.append("---")
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append(f"| 检查项 | 结果 | 异常数 |")
    lines.append(f"|---|---|---|")
    lines.append(f"| CK-001 重复凭证号 | {'❌ FAIL' if dup_vouchers else '✅ PASS'} | {len(dup_vouchers)} |")
    lines.append(f"| CK-002 零附件凭证 | {'⚠️ WARN' if zero_attach else '✅ PASS'} | {len(zero_attach)} |")
    lines.append(f"| CK-003 状态异常 | {'⚠️ WARN' if submitted else '✅ PASS'} | {len(submitted)} |")
    lines.append(f"| CK-004 大额交易 | {'📊 INFO' if large else '✅ PASS'} | {len(large)} |")
    lines.append(f"| CK-005 审批人集中度 | {'⚠️ WARN' if approver_over else '✅ PASS'} | {len(approver_over)} |")
    lines.append(f"| CK-006 金额合理性 | {'⚠️ WARN' if amount_anomalies else '✅ PASS'} | {len(amount_anomalies)} |")
    lines.append(f"| CK-007 Trace唯一性 | {'❌ FAIL' if trace_dups else '✅ PASS'} | {len(trace_dups)} |")

    report = "\n".join(lines)
    print(report)

    # Write to file
    with open('output/batch_22_report.md', 'w') as f:
        f.write(report)
    print("\n\nReport written to output/batch_22_report.md")

if __name__ == '__main__':
    main()
