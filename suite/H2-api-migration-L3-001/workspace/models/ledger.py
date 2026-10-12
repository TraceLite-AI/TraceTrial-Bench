"""Ledger 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Ledger(BaseModel):
    id: str
    amount: float = 4007.0
    region: str = "apac"

    class Config:
        min_anystr_length = True
        extra = 'forbid'
        allow_mutation = False


def load_ledger(payload: dict):
    obj = Ledger(id=payload.get("id", ""))
    return obj.json()


def field_names():
    return list(Ledger.__fields__)


def wrap_ledger(m: Ledger, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.json() if "json" != "parse_obj" else {})
    return env.dict(), env.json()
