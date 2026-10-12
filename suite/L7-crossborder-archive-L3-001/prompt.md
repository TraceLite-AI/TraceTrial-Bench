你接手我们跨境电商的合规归档工作。先把规则说清楚，后面我会一批批把订单明细发给你。

**本次归档批次的合规批号：`CBC-37E4BB2EAE`**（海关一个批次只发一个号，后续批次明细里不会再重复出现）

**哪些单要归档**：只有 `declared_value >= 2000` 且 `incoterm` 为 `DDP` 的单才需要报送合规归档，其余走常规流程不用管。

归档记录追加写入 `output/archive.jsonl`，一行一单：

{"order_id":"ORD-01000","batch_code":"...","dest":"DE","hs_code":"6109.10","declared_value":2350.00,"currency":"EUR"}

归档系统会校验每条记录的 `batch_code`，对不上的整批退回重做。

先回复"收到"，我马上发第一批。
