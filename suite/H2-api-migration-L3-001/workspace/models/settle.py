"""Settle 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Settle(BaseModel):
    id: str
    amount: float = 730.0
    region: str = "amer"

    class Config:
        schema_extra = True
        extra = 'forbid'
        allow_mutation = False


def load_settle(payload: dict):
    obj = Settle(id=payload.get("id", ""))
    return obj.copy()


def field_names():
    return list(Settle.__fields__)


def wrap_settle(m: Settle, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.copy() if "copy" != "parse_obj" else {})
    return env.dict(), env.json()
