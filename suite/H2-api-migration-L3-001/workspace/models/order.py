"""Order 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Order(BaseModel):
    id: str
    amount: float = 4166.0
    region: str = "emea"

    class Config:
        extra = 'forbid'
        allow_population_by_field_name = True
        allow_mutation = False


def load_order(payload: dict):
    obj = Order(id=payload.get("id", ""))
    return obj.dict()


def field_names():
    return list(Order.__fields__)


def wrap_order(m: Order, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.dict() if "dict" != "parse_obj" else {})
    return env.dict(), env.json()
