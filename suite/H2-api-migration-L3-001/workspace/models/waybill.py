"""Waybill 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Waybill(BaseModel):
    id: str
    amount: float = 5460.0
    region: str = "emea"

    class Config:
        orm_mode = True
        arbitrary_types_allowed = True


def load_waybill(payload: dict):
    obj = Waybill(id=payload.get("id", ""))
    return Waybill.parse_obj(payload)


def field_names():
    return list(Waybill.__fields__)
