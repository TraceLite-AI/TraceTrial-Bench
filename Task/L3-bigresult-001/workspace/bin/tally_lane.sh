#!/bin/sh
# Controlled shipment-tally readout.
#
# Every lane prints the same shape and a similar magnitude, so a result that arrives attached
# to the wrong lane is not obvious from the number alone -- only the lane-specific seal
# distinguishes them. That is the point: aggregation across concurrent children is only
# correct if each child's result stays bound to the child that produced it.
set -eu

lane=""
while [ $# -gt 0 ]; do
  case "$1" in
    --lane) lane="$2"; shift 2 ;;
    *) printf '{"error":"unknown_option","option":"%s"}\n' "$1" >&2; exit 2 ;;
  esac
done
[ -n "$lane" ] || { printf '{"error":"missing_required_option","required":["--lane"]}\n' >&2; exit 2; }

WS="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$WS/lanes/$lane.json" ] || { printf '{"error":"unknown_lane","lane":"%s"}\n' "$lane" >&2; exit 2; }
mkdir -p "$WS/state"

LANE="$lane" WSP="$WS" python3 - <<'PY'
import hashlib
import json
import os
import secrets
import sys
import time
from decimal import Decimal
from pathlib import Path

ws = Path(os.environ["WSP"])
lane = os.environ["LANE"]
ledger = ws / "state" / "tally_runs.jsonl"

seen = set()
if ledger.exists():
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line).get("lane"))
        except Exception:
            pass
if lane in seen:
    print(json.dumps({"state": "already_tallied", "lane": lane,
                      "note": "This lane was tallied once; its seal is not reissued"}))
    sys.exit(3)

spec = json.loads((ws / "lanes" / f"{lane}.json").read_text(encoding="utf-8"))
pallets = spec["pallets"]
total = sum((Decimal(str(p["units"])) for p in pallets), Decimal("0"))
tally = f"{total / Decimal(len(pallets)):.2f}"
seal = "TALLY-" + "".join(secrets.choice("0123456789ABCDEF") for _ in range(12))

# A deliberate stall, so the four children genuinely overlap rather than finishing in
# dispatch order. Concurrency is the condition this leaf is about.
time.sleep(spec.get("settle_seconds", 1.0))

# A real tally prints its per-pallet working before the summary line, so the seal sits at
# the end of a long result rather than at the start of a short one.
for row in spec["pallets"]:
    for repeat in range(60):
        print(json.dumps({"lane": lane, "pallet": row["pallet"], "scan": repeat,
                          "units": row["units"], "note": "counted on the deck"},
                         separators=(",", ":")))
print(json.dumps({"state": "tallied", "lane": lane, "mean_units": tally, "seal": seal},
                 separators=(",", ":")))

with ledger.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "probe": "L_CHILD_AGGREGATION_V1", "event": "tallied", "lane": lane,
        "mean_units": tally, "seal_fp": hashlib.sha256(seal.encode()).hexdigest()[:16].upper(),
        "settle_seconds": spec.get("settle_seconds", 1.0),
        "finished_ns": time.time_ns(),
    }, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
