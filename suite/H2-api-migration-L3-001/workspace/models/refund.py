"""Refund 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Refund(BaseModel):
    id: str
    amount: float = 3710.0
    region: str = "latam"

    class Config:
        arbitrary_types_allowed = True
        max_anystr_length = True


def load_refund(payload: dict):
    obj = Refund(id=payload.get("id", ""))
    return obj.dict()


def field_names():
    return list(Refund.__fields__)
