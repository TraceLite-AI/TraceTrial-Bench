"""跨模块共享的信封结构。**四个模块都必须严格按这个结构收发，不得自行增删字段。**

字段契约（顺序即位置参数顺序）：
    event_id   : str          必填，形如 "EV-<8位十六进制大写>"
    tenant     : str | None   ingest 阶段为 None，enrich 阶段填入
    kind       : str          取值只能是 "order" / "refund" / "adjust"
    amount_cents: int         必填，可为负（退款/冲销）
    ts         : str          必填，ISO8601 且以 "Z" 结尾
    trace      : tuple        每经过一个模块 append 一个模块名（不可原地修改）

**不变量（集成测试会逐条查）**：
  I1  event_id 全程不变
  I2  trace 按经过顺序累积，长度 == 经过的模块数
  I3  amount_cents 只有 adjust 类允许被改写，其余模块不得改动
  I4  kind 一旦确定不得变更
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class Envelope:
    event_id: str
    tenant: Optional[str]
    kind: str
    amount_cents: int
    ts: str
    trace: Tuple[str, ...] = field(default_factory=tuple)

    def stamped(self, module: str) -> "Envelope":
        """返回 trace 追加了 module 的新实例（frozen，不可原地改）。"""
        raise NotImplementedError("本次迭代要实现")
