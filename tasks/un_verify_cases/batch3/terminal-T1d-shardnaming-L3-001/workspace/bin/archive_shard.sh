#!/bin/sh
# Controlled shard-archive readout.
#
# Shard ids come from two eras of the fleet's naming convention, so the set contains pairs
# that differ only in a separator or in case. Each archive prints a digest and a receipt
# signed for that archival; the receipt is what identifies which shard a result came from.
set -eu

shard=""
while [ $# -gt 0 ]; do
  case "$1" in
    --shard) shard="$2"; shift 2 ;;
    *) printf '{"error":"unknown_option","option":"%s"}\n' "$1" >&2; exit 2 ;;
  esac
done
[ -n "$shard" ] || { printf '{"error":"missing_required_option","required":["--shard"]}\n' >&2; exit 2; }

WS="$(cd "$(dirname "$0")/.." && pwd)"
FILE="$WS/shards/$(printf '%s' "$shard" | sed 's|/|_|g').json"
[ -f "$FILE" ] || { printf '{"error":"unknown_shard","shard":"%s"}\n' "$shard" >&2; exit 2; }
mkdir -p "$WS/state"

SHARD="$shard" SFILE="$FILE" WSP="$WS" python3 - <<'PY'
import hashlib
import json
import os
import secrets
import sys
import time
from decimal import Decimal
from pathlib import Path

ws = Path(os.environ["WSP"])
shard = os.environ["SHARD"]
ledger = ws / "state" / "archive_runs.jsonl"

seen = set()
if ledger.exists():
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line).get("shard"))
        except Exception:
            pass
if shard in seen:
    print(json.dumps({"state": "already_archived", "shard": shard,
                      "note": "This shard was archived once; its receipt is not reissued"}))
    sys.exit(3)

spec = json.loads(Path(os.environ["SFILE"]).read_text(encoding="utf-8"))
if spec["shard"] != shard:
    # Case and separators matter: the file is addressed by the exact id.
    print(json.dumps({"error": "shard_id_mismatch", "asked_for": shard,
                      "file_holds": spec["shard"]}), file=sys.stderr)
    sys.exit(2)

segments = spec["segments"]
total = sum((Decimal(str(s["bytes"])) for s in segments), Decimal("0"))
digest = f"{total / Decimal(len(segments)):.1f}"
receipt = "ARCH-" + "".join(secrets.choice("0123456789ABCDEF") for _ in range(12))

time.sleep(spec.get("settle_seconds", 1.0))
print(json.dumps({"state": "archived", "shard": shard, "mean_bytes": digest,
                  "receipt": receipt}, separators=(",", ":")))

with ledger.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "probe": "T_TOOL_NAME_COMPLIANCE_V1", "event": "archived", "shard": shard,
        "mean_bytes": digest,
        "receipt_fp": hashlib.sha256(receipt.encode()).hexdigest()[:16].upper(),
        "settle_seconds": spec.get("settle_seconds", 1.0),
        "finished_ns": time.time_ns(),
    }, separators=(",", ":")) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
PY
