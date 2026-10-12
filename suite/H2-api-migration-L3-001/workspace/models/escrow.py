"""Escrow 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Escrow(BaseModel):
    id: str
    amount: float = 6442.0
    region: str = "emea"

    class Config:
        validate_all = True
        frozen = True


def load_escrow(payload: dict):
    obj = Escrow(id=payload.get("id", ""))
    return Escrow.construct(**payload)


def field_names():
    return list(Escrow.__fields__)
