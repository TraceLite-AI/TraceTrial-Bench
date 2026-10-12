# ingest 规格

输入：原始事件行 `<kind>|<amount_cents>|<ts>`（由调用方先构造成 Envelope 传入，tenant 为 None）
职责：
1. 校验 `kind` 在允许集合内，否则原样返回（不抛异常）
2. 校验 `ts` 以 `Z` 结尾，否则原样返回
3. 通过校验则调用 `env.stamped("ingest")` 返回

**不得改动 amount_cents、不得填 tenant。**
