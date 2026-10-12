"""Customer 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel
from common.envelope import RawEnvelope


class Customer(BaseModel):
    id: str
    amount: float = 5831.0
    region: str = "apac"

    class Config:
        anystr_upper = True
        use_enum_values = True


def load_customer(payload: dict):
    obj = Customer(id=payload.get("id", ""))
    return obj.copy()


def field_names():
    return list(Customer.__fields__)


def wrap_customer(m: Customer, trace_id: str):
    """把模型塞进传输信封。注意 RawEnvelope 是本仓库自有类型。"""
    env = RawEnvelope(trace_id, m.copy() if "copy" != "parse_obj" else {})
    return env.dict(), env.json()
