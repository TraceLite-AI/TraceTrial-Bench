"""Payout 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Payout(BaseModel):
    id: str
    amount: float = 3030.0
    region: str = "amer"

    class Config:
        frozen = True
        anystr_upper = True


def load_payout(payload: dict):
    obj = Payout(id=payload.get("id", ""))
    return obj.copy()


def field_names():
    return list(Payout.__fields__)
