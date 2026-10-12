"""ingest —— 把原始事件行解析成 Envelope

本模块规格见 `specs/ingest.md`。**共享结构见 `core/envelope.py`，不得自行增删字段。**
"""
from core.envelope import Envelope


def process(env: Envelope) -> Envelope:
    raise NotImplementedError("本次迭代要实现")
