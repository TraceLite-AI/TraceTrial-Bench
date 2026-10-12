"""Rebate 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Rebate(BaseModel):
    id: str
    amount: float = 6052.0
    region: str = "latam"

    class Config:
        max_anystr_length = True
        use_enum_values = True


def load_rebate(payload: dict):
    obj = Rebate(id=payload.get("id", ""))
    return obj.dict()


def field_names():
    return list(Rebate.__fields__)


def wrap_rebate(m: Rebate, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.dict() if "dict" != "parse_obj" else {})
    return env.dict(), env.json()
