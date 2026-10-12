"""archive —— 把 Envelope 落到归档行

本模块规格见 `specs/archive.md`。**共享结构见 `core/envelope.py`，不得自行增删字段。**
"""
from core.envelope import Envelope


def process(env: Envelope) -> Envelope:
    raise NotImplementedError("本次迭代要实现")
