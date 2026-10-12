#!/usr/bin/env python3
"""Oracle arm: simulate the IDEAL harness — the delegated child runs to completion
before exit (drain), its report lands, the completion is delivered, the parent
aggregates, and the process exit is honest. Proves the task is solvable and the
verifier passable when the harness holds up its end."""
import csv, json, sys
from decimal import Decimal
from pathlib import Path

ws = Path(sys.argv[1])
total = sum(Decimal(r["amount"]) for r in csv.DictReader(open(ws / "input" / "ledger.csv", newline="")))
nonce = (ws / "state" / "run_nonce.txt").read_text().strip()
(ws / "out").mkdir(exist_ok=True)
# child executed to completion (drain) -> report lands
(ws / "out" / "child_report.json").write_text(json.dumps({"sum": f"{total:.2f}", "nonce": nonce}))
# completion delivered -> parent aggregates
(ws / "out" / "final_status.json").write_text(json.dumps(
    {"delegation": "completed", "sum": f"{total:.2f}", "nonce": nonce}))
print("oracle: child drained + delivered + aggregated")
