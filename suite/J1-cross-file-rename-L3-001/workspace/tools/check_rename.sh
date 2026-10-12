#!/usr/bin/env bash
cd "$(dirname "$0")/.."
bad=0
# 代码标识符应已改名
if grep -rn "LegacyOrderCode" --include="*.py" src/ | grep -v "LEGACY_WIRE_FIELD" | grep -v "^.*#" | grep -q .; then
  echo "  仍有未改名的代码标识符:"; grep -rn "LegacyOrderCode" --include="*.py" src/ | grep -v LEGACY_WIRE_FIELD | grep -v "#" | head -5; bad=$((bad+1))
fi
# 对外协议字段名必须保持原样
if ! grep -rq 'LEGACY_WIRE_FIELD = "LegacyOrderCode"' src/; then
  echo "  ✗ 对外协议字段名被改动(下游会解析失败)"; bad=$((bad+1))
fi
[ $bad -eq 0 ] && { echo "RENAME CHECK OK"; exit 0; } || { echo "issues: $bad"; exit 4; }
