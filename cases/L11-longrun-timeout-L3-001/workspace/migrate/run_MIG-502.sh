#!/bin/bash
echo '[MIG-502] phase backfill started'
mkdir -p migrate
for i in $(seq 1 3); do
  echo "[MIG-502] chunk $i committed" >> migrate/MIG-502.log
  sleep 1
done
sleep 400
echo '[MIG-502] phase complete' >> migrate/MIG-502.log
