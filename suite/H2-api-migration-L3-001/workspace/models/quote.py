"""Quote 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Quote(BaseModel):
    id: str
    amount: float = 8781.0
    region: str = "latam"

    class Config:
        validate_all = True
        use_enum_values = True


def load_quote(payload: dict):
    obj = Quote(id=payload.get("id", ""))
    return Quote.construct(**payload)


def field_names():
    return list(Quote.__fields__)


def wrap_quote(m: Quote, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.construct() if "construct" != "parse_obj" else {})
    return env.dict(), env.json()
