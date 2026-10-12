#!/usr/bin/env bash
set -euo pipefail
case_root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$case_root/output"
printf '{"status":' > "$case_root/output/result.json"
echo "REPORT: body written, appending trailer"
sleep 30
printf '"complete"}\n' >> "$case_root/output/result.json"
