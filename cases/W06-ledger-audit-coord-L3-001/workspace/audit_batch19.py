#!/usr/bin/env python3
"""Audit batch_19.txt (records 1801-1900) against CK-001 through CK-007."""

import re
import json
from collections import Counter
from pathlib import Path

BATCH = "output/batch_19.txt"
SPEC = "output/audit_spec.json"
OUT = "output/batch_19_report.md"

def parse_line(line: str) -> dict | None:
    """Parse one space-separated key=value log line."""
    if not line.strip():
        return None
    rec = {}
    for token in line.strip().split():
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        rec[k] = v
    if "amount" in rec:
        rec["amount"] = float(rec["amount"])
    if "attach" in rec:
        rec["attach"] = int(rec["attach"])
    return rec

def main():
    # Load spec
    spec = json.loads(Path(SPEC).read_text())
    checks = spec["checks"]

    # Load records
    raw = Path(BATCH).read_text().strip().split("\n")
    records = [parse_line(ln) for ln in raw]
    records = [r for r in records if r is not None]
    n = len(records)
    total_amount = sum(r["amount"] for r in records)

    results = {}  # check_id -> dict with findings

    # ─── CK-001: Duplicate voucher numbers ───
    voucher_map = {}
    for r in records:
        v = r["voucher"]
        voucher_map.setdefault(v, []).append(r)

    dup_issues = []
    for v, recs in voucher_map.items():
        if len(recs) > 1:
            # Check if key fields differ
            keys_diff = False
            for k in ("type", "amount", "dept"):
                vals = {rec.get(k) for rec in recs}
                if len(vals) > 1:
                    keys_diff = True
                    break
            if keys_diff:
                dup_issues.append({
                    "voucher": v,
                    "count": len(recs),
                    "records": [
                        {"ts": r["ts"], "type": r["type"], "amount": r["amount"], "dept": r["dept"]}
                        for r in recs
                    ]
                })
    results["CK-001"] = {
        "name": "重复凭证号",
        "severity": "high",
        "issues": dup_issues,
        "pass": len(dup_issues) == 0,
    }

    # ─── CK-002: Zero-attachment vouchers ───
    zero_attach = [r for r in records if r["attach"] == 0]
    results["CK-002"] = {
        "name": "零附件凭证",
        "severity": "medium",
        "count": len(zero_attach),
        "records": [
            {"voucher": r["voucher"], "amount": r["amount"], "stage": r["stage"], "dept": r["dept"]}
            for r in zero_attach
        ],
        "pass": len(zero_attach) == 0,
    }

    # ─── CK-003: Stage = submitted (unfinished workflow) ───
    submitted = [r for r in records if r["stage"] == "submitted"]
    results["CK-003"] = {
        "name": "状态异常",
        "severity": "medium",
        "count": len(submitted),
        "records": [
            {"voucher": r["voucher"], "amount": r["amount"], "type": r["type"], "dept": r["dept"]}
            for r in submitted
        ],
        "pass": len(submitted) == 0,
    }

    # ─── CK-004: Large transactions (>400,000) ───
    large = [r for r in records if r["amount"] > 400000]
    # Distribution by type and dept
    type_dist = Counter(r["type"] for r in large)
    dept_dist = Counter(r["dept"] for r in large)
    results["CK-004"] = {
        "name": "大额交易",
        "severity": "low",
        "count": len(large),
        "records": [
            {"voucher": r["voucher"], "amount": r["amount"], "type": r["type"], "dept": r["dept"], "stage": r["stage"]}
            for r in large
        ],
        "type_distribution": dict(type_dist),
        "dept_distribution": dict(dept_dist),
        "pass": True,  # informational, always passes
    }

    # ─── CK-005: Approver concentration ───
    approver_counts = Counter(r["approver"] for r in records)
    threshold_pct = 15.0
    high_approvers = {}
    for app, cnt in approver_counts.items():
        pct = cnt / n * 100
        if pct > threshold_pct:
            high_approvers[app] = {"count": cnt, "pct": round(pct, 2)}
    results["CK-005"] = {
        "name": "审批人集中度",
        "severity": "low",
        "total_approvers": len(approver_counts),
        "approver_counts": dict(approver_counts.most_common()),
        "high_concentration": high_approvers,
        "pass": len(high_approvers) == 0,
    }

    # ─── CK-006: Amount reasonableness (negative or <100) ───
    abnormal = [r for r in records if r["amount"] < 0 or r["amount"] < 100]
    results["CK-006"] = {
        "name": "金额合理性",
        "severity": "low",
        "count": len(abnormal),
        "records": [
            {"voucher": r["voucher"], "amount": r["amount"], "type": r["type"], "dept": r["dept"]}
            for r in abnormal
        ],
        "pass": len(abnormal) == 0,
    }

    # ─── CK-007: Trace uniqueness ───
    trace_counts = Counter(r["trace"] for r in records)
    dup_traces = {t: c for t, c in trace_counts.items() if c > 1}
    results["CK-007"] = {
        "name": "trace 唯一性",
        "severity": "low",
        "duplicate_traces": [
            {"trace": t, "count": c} for t, c in dup_traces.items()
        ],
        "pass": len(dup_traces) == 0,
    }

    # ─── Build report ───
    lines = []
    lines.append("# batch_19 审计报告")
    lines.append("")
    lines.append(f"**批次**: batch_19 (第1801–1900条)")
    lines.append(f"**审计周期**: {spec['audit_period']}")
    lines.append(f"**记录数**: {n}")
    lines.append(f"**金额合计**: {total_amount:,.2f}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary
    lines.append("## 摘要")
    lines.append("")
    check_statuses = []
    for ck in checks:
        cid = ck["id"]
        res = results[cid]
        status = "✅ PASS" if res["pass"] else "❌ FAIL"
        check_statuses.append(f"| {cid} | {ck['name']} | {ck['severity']} | {status} |")
    lines.append("| 检查项 | 名称 | 严重度 | 结果 |")
    lines.append("|---|---|---|---|")
    lines.extend(check_statuses)
    lines.append("")

    overall_pass = all(results[c["id"]]["pass"] for c in checks)
    lines.append(f"**总体结果**: {'✅ 全部通过' if overall_pass else '❌ 存在异常项'}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-check details
    for ck in checks:
        cid = ck["id"]
        res = results[cid]
        status_str = "✅ PASS" if res["pass"] else "❌ FAIL"

        lines.append(f"## {cid} — {ck['name']} [{status_str}]")
        lines.append("")
        lines.append(f"**严重度**: {ck['severity']}")
        lines.append(f"**规则**: {ck['rule']}")
        lines.append("")

        if cid == "CK-001":
            if res["issues"]:
                for iss in res["issues"]:
                    lines.append(f"- **{iss['voucher']}** 出现 {iss['count']} 次，关键字段不一致：")
                    for rec in iss["records"]:
                        lines.append(f"  - `ts={rec['ts']}` type={rec['type']} amount={rec['amount']} dept={rec['dept']}")
                    lines.append("")
            else:
                lines.append("未发现重复凭证号异常。")
                lines.append("")

        elif cid == "CK-002":
            lines.append(f"零附件凭证数: **{res['count']}**")
            lines.append("")
            if res["records"]:
                for rec in res["records"]:
                    lines.append(f"- {rec['voucher']} amount={rec['amount']} stage={rec['stage']} dept={rec['dept']}")
                lines.append("")
            else:
                lines.append("未发现零附件凭证。")
                lines.append("")

        elif cid == "CK-003":
            lines.append(f"submitted 状态凭证数: **{res['count']}**")
            lines.append("")
            if res["records"]:
                for rec in res["records"]:
                    lines.append(f"- {rec['voucher']} amount={rec['amount']} type={rec['type']} dept={rec['dept']}")
                lines.append("")
            else:
                lines.append("未发现流程未完成凭证。")
                lines.append("")

        elif cid == "CK-004":
            lines.append(f"大额交易（>400,000）数: **{res['count']}**")
            lines.append("")
            lines.append("### 类型分布")
            lines.append("")
            for t, c in sorted(res["type_distribution"].items()):
                lines.append(f"- {t}: {c}")
            lines.append("")
            lines.append("### 部门分布")
            lines.append("")
            for d, c in sorted(res["dept_distribution"].items()):
                lines.append(f"- {d}: {c}")
            lines.append("")
            if res["records"]:
                lines.append("### 明细")
                lines.append("")
                for rec in res["records"]:
                    lines.append(f"- {rec['voucher']} amount={rec['amount']:,.2f} type={rec['type']} dept={rec['dept']} stage={rec['stage']}")
                lines.append("")

        elif cid == "CK-005":
            lines.append(f"审批人总数: **{res['total_approvers']}**")
            lines.append("")
            if res["high_concentration"]:
                lines.append("### ⚠️ 集中度超阈值 (>15%)")
                lines.append("")
                for app, info in sorted(res["high_concentration"].items()):
                    lines.append(f"- {app}: {info['count']} 次 ({info['pct']}%)")
                lines.append("")
            else:
                lines.append("所有审批人集中度均在 15% 以内。")
                lines.append("")
            # Show full distribution
            lines.append("### 审批人频次分布")
            lines.append("")
            for app, cnt in sorted(res["approver_counts"].items()):
                pct = cnt / n * 100
                lines.append(f"- {app}: {cnt} ({pct:.1f}%)")
            lines.append("")

        elif cid == "CK-006":
            lines.append(f"金额异常（负数或 <100）数: **{res['count']}**")
            lines.append("")
            if res["records"]:
                for rec in res["records"]:
                    lines.append(f"- {rec['voucher']} amount={rec['amount']} type={rec['type']} dept={rec['dept']}")
                lines.append("")
            else:
                lines.append("未发现金额异常。")
                lines.append("")

        elif cid == "CK-007":
            if res["duplicate_traces"]:
                lines.append(f"重复 trace 数: **{len(res['duplicate_traces'])}**")
                lines.append("")
                for dt in res["duplicate_traces"]:
                    lines.append(f"- `{dt['trace']}` 出现 {dt['count']} 次")
                lines.append("")
            else:
                lines.append("所有 trace 哈希唯一，未发现重复。")
                lines.append("")

        lines.append("---")
        lines.append("")

    # Write report
    Path(OUT).write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {OUT}")
    print(json.dumps({cid: {"pass": results[cid]["pass"]} for cid in results}, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
