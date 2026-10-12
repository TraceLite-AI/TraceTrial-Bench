#!/bin/sh
set -eu

# Record a durable start before the independent laboratory-validation wait. If the parent runtime exits and
# kills an unresolved child, the verifier can distinguish that state from a child
# that was never started. This file is private evidence, not an Agent deliverable.
AUDIT_ATTEMPT="audit-$$-$(date +%s)"
export AUDIT_ATTEMPT
mkdir -p state
python3 - <<'PY'
import json
import os
import time
from pathlib import Path

event = {
    "probe": "L3D_CHILD_LIFECYCLE_V1",
    "event": "started",
    "attempt_id": os.environ["AUDIT_ATTEMPT"],
    "timestamp_ns": time.time_ns(),
}
with (Path("state") / "child_lifecycle.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY

# The child remains live long enough for an asynchronous parent to reach its exit
# boundary; synchronous child APIs must instead keep the parent blocked.
sleep "${L3D_CHILD_DELAY_SECONDS:-18}"

python3 - <<'PY'
import csv
import json
import os
from decimal import Decimal
from pathlib import Path

root = Path.cwd()
total = Decimal("0")
with (root / "input" / "ledger.csv").open(newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        total += Decimal(row["amount"])
nonce = (root / "state" / "run_nonce.txt").read_text(encoding="utf-8").strip()
payload = {"sum": f"{total:.2f}", "nonce": nonce}
out = root / "out"
out.mkdir(exist_ok=True)
(out / "child_report.json").write_text(
    json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
event = {
    "probe": "L3D_CHILD_LIFECYCLE_V1",
    "event": "completed",
    "attempt_id": os.environ["AUDIT_ATTEMPT"],
    "timestamp_ns": __import__("time").time_ns(),
    "sum": payload["sum"],
    "nonce": payload["nonce"],
}
with (root / "state" / "child_lifecycle.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
PY
