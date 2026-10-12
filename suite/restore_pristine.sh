#!/usr/bin/env bash
# 还原破坏性 case 的干净 workspace（删文件/改库这类题跑过就回不去了）
# 用法: ./restore_pristine.sh <case-id> [case-id ...]   或  ./restore_pristine.sh --list
set -e
cd "$(dirname "$0")"
if [ "$1" = "--list" ] || [ -z "$1" ]; then
  echo "可还原的 case:"; ls _pristine/*.tgz 2>/dev/null | sed 's|_pristine/||; s|\.tgz$||; s|^|  |'
  exit 0
fi
for c in "$@"; do
  f="_pristine/$c.tgz"
  [ -f "$f" ] || { echo "  ★ 没有备份: $c"; continue; }
  rm -rf "$c/workspace"
  tar xzf "$f"
  rm -f "$c/score.json"; rm -rf "$c/state" "$c/run"
  echo "  ✓ 已还原 $c（workspace + 清掉 score/state/run）"
done
