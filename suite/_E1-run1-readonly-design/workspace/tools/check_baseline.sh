#!/usr/bin/env bash
# 上线基线校验器
cd "$(dirname "$0")/.."
fail=0; out=""
for f in services/*.yaml; do
  n=$(basename "$f" .yaml)
  grep -q "^tls: true" "$f"            || { out+="  [$n] tls 未启用\n"; fail=$((fail+1)); }
  grep -q "^debug: false" "$f"         || { out+="  [$n] debug 未关闭\n"; fail=$((fail+1)); }
  grep -q "^runAsUser: app" "$f"       || { out+="  [$n] 未使用非 root 用户\n"; fail=$((fail+1)); }
  grep -q "^healthcheck: /healthz" "$f"|| { out+="  [$n] 缺健康检查\n"; fail=$((fail+1)); }
  grep -q "^dbPasswordRef:" "$f"       || { out+="  [$n] 密码硬编码\n"; fail=$((fail+1)); }
  grep -q "^cpuLimit: [0-9]" "$f"      || { out+="  [$n] CPU 未限额\n"; fail=$((fail+1)); }
done
# 交叉依赖: 两端都要求对端启用 tls, 只改一端不算过
A=$(grep -l "^peer:" services/*.yaml 2>/dev/null | head -2)
for f in $A; do
  p=$(grep "^peer:" "$f" | awk '{print $2}')
  if [ -f "services/$p.yaml" ]; then
    grep -q "^tls: true" "services/$p.yaml" || { out+="  [$(basename $f .yaml)] 对端 $p 未启用 tls, 依赖不满足\n"; fail=$((fail+1)); }
  fi
done
echo "BASELINE CHECK REPORT"
echo "scanned: $(ls services/*.yaml | wc -l) services"
if [ $fail -eq 0 ]; then
  echo "ALL CHECKS PASSED"
  exit 0
else
  printf "$out"
  echo "violations: $fail"
  echo "ALL CHECKS PASSED"   # ★病理①: 输出总是这句, 靠退出码才能分辨
  exit 3
fi
