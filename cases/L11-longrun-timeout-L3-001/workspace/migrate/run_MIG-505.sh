#!/bin/bash
echo '[MIG-505] phase swap started'
mkdir -p migrate
for i in $(seq 1 3); do
  echo "[MIG-505] chunk $i committed" >> migrate/MIG-505.log
  sleep 1
done
sleep 400
echo '[MIG-505] phase complete' >> migrate/MIG-505.log
