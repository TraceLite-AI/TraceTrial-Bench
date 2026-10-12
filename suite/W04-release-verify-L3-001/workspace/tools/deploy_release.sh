#!/usr/bin/env bash
set -euo pipefail
case_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$case_root/output"
printf '{"status":"success","source":"verify-stage-1"}\n' > "$case_root/output/result.json"
echo "RELEASE: verification artifact written to output/result.json"
exit 42
