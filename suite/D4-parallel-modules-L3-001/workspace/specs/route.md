# route 规格

职责：
1. 依据 `kind` 与 `amount_cents` 决定队列名，写入 trace 之前不产生副作用
2. 队列名规则：`order` → `q-order`；`refund` → `q-refund`；`adjust` → `q-adjust`
3. 若 `amount_cents` 绝对值 ≥ 100000，队列名追加 `-big`
4. 队列名通过 `stamped("route:" + 队列名)` 记入 trace

**不得改动任何金额或类型字段。**
