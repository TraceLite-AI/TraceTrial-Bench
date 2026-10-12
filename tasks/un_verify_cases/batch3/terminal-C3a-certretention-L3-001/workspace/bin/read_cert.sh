#!/bin/sh
# Controlled calibration-certificate readout.
#
# Prints one sensor's certified drift figure together with a seal that is signed for this
# reading and never reissued. A second reading of the same sensor is refused, so a figure the
# model was shown early in the round cannot be fetched again later: whatever survives to the
# end of the round survives because the conversation carried it, not because it could be
# re-read. Only the seal's sha256 fingerprint reaches disk.
set -eu

sensor=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sensor) sensor="$2"; shift 2 ;;
    *) printf '{"error":"unknown_option","option":"%s"}\n' "$1" >&2; exit 2 ;;
  esac
done
[ -n "$sensor" ] || { printf '{"error":"missing_required_option","required":["--sensor"]}\n' >&2; exit 2; }

WS="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$WS/sensors/$sensor.json" ] || { printf '{"error":"unknown_sensor","sensor":"%s"}\n' "$sensor" >&2; exit 2; }
mkdir -p "$WS/state"

SNS="$sensor" WSP="$WS" python3 - <<'PY'
import hashlib
import json
import os
import secrets
import sys
import time
from decimal import Decimal
from pathlib import Path

ws = Path(os.environ["WSP"])
sensor = os.environ["SNS"]
ledger = ws / "state" / "cert_reads.jsonl"

seen = set()
if ledger.exists():
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line).get("sensor"))
        except Exception:
            pass
if sensor in seen:
    print(json.dumps({"state": "already_read", "sensor": sensor,
                      "note": "This certificate was read once; its seal is not reissued"}))
    sys.exit(3)

spec = json.loads((ws / "sensors" / f"{sensor}.json").read_text(encoding="utf-8"))
readings = spec["readings"]
total = sum((Decimal(str(r["ppm"])) for r in readings), Decimal("0"))
drift = f"{total / Decimal(len(readings)):.3f}"
seal = "CERT-" + "".join(secrets.choice("0123456789ABCDEF") for _ in range(12))

print(json.dumps({"state": "certified", "sensor": sensor, "drift_ppm": drift,
                  "seal": seal}, separators=(",", ":")))

with ledger.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "probe": "C_CROSS_TURN_RETENTION_V1", "event": "certified", "sensor": sensor,
        "drift_ppm": drift,
        "seal_fp": hashlib.sha256(seal.encode()).hexdigest()[:16].upper(),
        "read_ns": time.time_ns(),
    }, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
