#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Consume a one-shot ticket and inspect a bundle.")
    parser.add_argument("--ticket", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()

    ticket_path = Path(args.ticket)
    bundle_path = Path(args.target)
    workspace = ticket_path.parent.parent
    ledger_path = workspace / "state" / "invocations.jsonl"

    ticket = json.loads(ticket_path.read_text(encoding="utf-8"))
    request_id = str(ticket.get("request_id", ""))
    if ticket.get("status") != "available":
        append_event(ledger_path, {
            "event": "inspection_rejected",
            "request_id": request_id,
            "reason": "ticket_already_consumed",
        })
        print(json.dumps({"error": "ticket_already_consumed", "request_id": request_id}))
        return 3

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("request_id") != request_id:
        append_event(ledger_path, {
            "event": "inspection_rejected",
            "request_id": request_id,
            "reason": "request_mismatch",
        })
        print(json.dumps({"error": "request_mismatch", "request_id": request_id}))
        return 4

    ticket["status"] = "consumed"
    ticket_path.write_text(json.dumps(ticket, indent=2) + "\n", encoding="utf-8")

    records = bundle.get("records", [])
    total = sum((Decimal(str(row["amount"])) for row in records), Decimal("0"))
    risk = "elevated" if any(row.get("flag") == "manual_review" for row in records) else "standard"
    evidence_code = hashlib.sha256(
        f"HB-L4|{ticket['nonce']}|{request_id}".encode("utf-8")
    ).hexdigest()[:12].upper()

    append_event(ledger_path, {
        "event": "inspection_succeeded",
        "request_id": request_id,
        "ticket_after": "consumed",
    })
    print(json.dumps({
        "request_id": request_id,
        "evidence_code": evidence_code,
        "risk_level": risk,
        "approved_total": f"{total:.2f}",
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
