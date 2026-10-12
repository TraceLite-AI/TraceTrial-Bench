# ledger.db 结构

- `accounts(acct_id, name, kind, opened)`
- `entries(entry_id, txn_id, acct_id, kind, amount_cents, booked_at, memo)`
- `batches(batch_id, closed, note)`

账套规则：
- 每笔 `txn_id` 至少两条分录，`debit` 记正数、`credit` 记负数
- 同一 `txn_id` 下 `sum(amount_cents)` 必须为 0
- `entries.acct_id` 必须存在于 `accounts`
- `booked_at` 不得为空
