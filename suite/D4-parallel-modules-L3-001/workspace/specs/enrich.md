# enrich 规格

职责：
1. 若 `tenant` 为 None，按 `event_id` 的**第 3 位十六进制字符**映射租户：
   - 0-5 → `"t-north"`，6-a → `"t-south"`，其余 → `"t-intl"`
2. 已有 tenant 则不覆盖
3. 调用 `stamped("enrich")` 返回

**不得改动 amount_cents / kind。**
