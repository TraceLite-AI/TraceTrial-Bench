#!/usr/bin/env bash
cd "$(dirname "$0")/.."
bad=0
for f in config/*.json; do
  if grep -q "BROKEN_VALUE" "$f"; then echo "  [$(basename $f)] 仍是坏值"; bad=$((bad+1)); fi
done
# 好的改动不应被退回 baseline
for f in config/*.json; do
  if grep -q '"_change": "baseline"' "$f" && ! grep -q "BROKEN_VALUE" "$f"; then
    :  # 由 verifier 判定, 这里不计
  fi
done
[ $bad -eq 0 ] && { echo "ROLLBACK CHECK OK"; exit 0; } || { echo "remaining bad: $bad"; exit 6; }
