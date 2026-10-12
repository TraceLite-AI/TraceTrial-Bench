"""Payment 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Payment(BaseModel):
    id: str
    amount: float = 4833.0
    region: str = "amer"

    class Config:
        validate_assignment = True
        keep_untouched = True
        smart_union = True


def load_payment(payload: dict):
    obj = Payment(id=payload.get("id", ""))
    return Payment.construct(**payload)


def field_names():
    return list(Payment.__fields__)
