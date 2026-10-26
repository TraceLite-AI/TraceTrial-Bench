#!/bin/bash
# TraceTrial_Bench verifier 一键跑批 —— 新 trace 落地后执行这一个脚本。
#
# 顺序（每步失败即停）：
#   1. 程序判据层跑 3 遍，逐字节比对 —— 不确定就不出数
#   2. gate 干跑（零 API）—— 人先看门的行为，直判逐条过眼
#   3. 主表（只用程序层 + gate，直接可看）
#   4. 仅当传 --judge 才调判官（花 API 钱，且要求前三步已绿）
#
# 单例：flock。已有跑批在进行时直接退出，不排队（防叠进程）。
set -e
cd "$(dirname "$0")"
exec 9>.pipeline.lock
flock -n 9 || { echo "★已有跑批在进行（.pipeline.lock 被持有），退出"; exit 1; }

echo "── ① 程序判据层 ×3 确定性 ──────────────────────────"
for i in 1 2 3; do
  python3 verify_all_v2.py > /tmp/hb_verify_$i.out 2>&1
  cp VERIFY_ALL.json /tmp/hb_VERIFY_$i.json
done
cmp -s /tmp/hb_VERIFY_1.json /tmp/hb_VERIFY_2.json && \
cmp -s /tmp/hb_VERIFY_2.json /tmp/hb_VERIFY_3.json || {
  echo "★程序层三遍不一致 —— 不确定就不出数，停"; exit 1; }
echo "三遍逐字节相同 OK"
tail -4 /tmp/hb_verify_1.out

echo
echo "── ② gate 干跑（零 API）────────────────────────────"
python3 gates_dry.py

echo
echo "── ③ 主表（程序层 + gate）──────────────────────────"
python3 main_table2.py | head -22

if [ "$1" = "--judge" ]; then
  echo
  echo "── ④ 语义判官层（花 API）───────────────────────────"
  python3 run_sem3.py
else
  echo
  echo "（未传 --judge：判官层没跑。前三步确认无误后 ./run_all.sh --judge）"
fi
