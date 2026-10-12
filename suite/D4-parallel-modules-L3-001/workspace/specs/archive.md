# archive 规格

职责：
1. 生成归档行字符串 `"<event_id>,<tenant>,<kind>,<amount_cents>,<ts>,<trace 用分号连接>"`
2. 调用 `stamped("archive")` 后再生成归档行（即归档行里的 trace **包含 archive 自己**）
3. 把归档行写到 `output/archive.txt`（追加，一行一条）
4. 返回 stamped 后的 Envelope

**不得改动任何字段。**
