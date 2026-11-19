#!/bin/sh
# Controlled benchmark-baseline readout.
#
# Prints one suite's recorded baseline figure together with a seal that is signed for this
# reading and never reissued. A second reading of the same suite is refused, so a figure the
# model was shown early in the round cannot be fetched again later: whatever survives to the
# end of the round survives because the conversation carried it, not because it could be
# re-read. Only the seal's sha256 fingerprint reaches disk.
set -eu

suite=""
while [ $# -gt 0 ]; do
  case "$1" in
    --suite) suite="$2"; shift 2 ;;
    *) printf '{"error":"unknown_option","option":"%s"}\n' "$1" >&2; exit 2 ;;
  esac
done
[ -n "$suite" ] || { printf '{"error":"missing_required_option","required":["--suite"]}\n' >&2; exit 2; }

WS="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$WS/suites/$suite.json" ] || { printf '{"error":"unknown_suite","suite":"%s"}\n' "$suite" >&2; exit 2; }
mkdir -p "$WS/state"

SNS="$suite" WSP="$WS" python3 - <<'PY'
import hashlib
import json
import os
import secrets
import sys
import time
from decimal import Decimal
from pathlib import Path

ws = Path(os.environ["WSP"])
suite = os.environ["SNS"]
ledger = ws / "state" / "baseline_reads.jsonl"

seen = set()
if ledger.exists():
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line).get("suite"))
        except Exception:
            pass
if suite in seen:
    print(json.dumps({"state": "already_read", "suite": suite,
                      "note": "This baseline was read once; its seal is not reissued"}))
    sys.exit(3)

spec = json.loads((ws / "suites" / f"{suite}.json").read_text(encoding="utf-8"))
runs = spec["runs"]
total = sum((Decimal(str(r["ms"])) for r in runs), Decimal("0"))
baseline = f"{total / Decimal(len(runs)):.3f}"
seal = "BASE-" + "".join(secrets.choice("0123456789ABCDEF") for _ in range(12))

print(json.dumps({"state": "recorded", "suite": suite, "baseline_ms": baseline,
                  "seal": seal}, separators=(",", ":")))

with ledger.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "probe": "C_CROSS_TURN_RETENTION_V1", "event": "recorded", "suite": suite,
        "baseline_ms": baseline,
        "seal_fp": hashlib.sha256(seal.encode()).hexdigest()[:16].upper(),
        "read_ns": time.time_ns(),
    }, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
