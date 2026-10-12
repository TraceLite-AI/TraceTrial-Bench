"""Shipment 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Shipment(BaseModel):
    id: str
    amount: float = 9217.0
    region: str = "latam"

    class Config:
        frozen = True
        anystr_lower = True


def load_shipment(payload: dict):
    obj = Shipment(id=payload.get("id", ""))
    return obj.json()


def field_names():
    return list(Shipment.__fields__)
