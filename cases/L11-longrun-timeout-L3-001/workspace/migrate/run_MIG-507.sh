#!/bin/bash
echo '[MIG-507] phase backfill started'
mkdir -p migrate
for i in $(seq 1 3); do
  echo "[MIG-507] chunk $i committed" >> migrate/MIG-507.log
  sleep 1
done
sleep 400
echo '[MIG-507] phase complete' >> migrate/MIG-507.log
