"""Tariff 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Tariff(BaseModel):
    id: str
    amount: float = 4420.0
    region: str = "emea"

    class Config:
        arbitrary_types_allowed = True
        anystr_lower = True


def load_tariff(payload: dict):
    obj = Tariff(id=payload.get("id", ""))
    return obj.json()


def field_names():
    return list(Tariff.__fields__)
