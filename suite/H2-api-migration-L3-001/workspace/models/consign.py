"""Consign 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Consign(BaseModel):
    id: str
    amount: float = 5138.0
    region: str = "latam"

    class Config:
        smart_union = True
        validate_assignment = True
        min_anystr_length = True


def load_consign(payload: dict):
    obj = Consign(id=payload.get("id", ""))
    return obj.json()


def field_names():
    return list(Consign.__fields__)
