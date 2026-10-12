"""Voucher 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Voucher(BaseModel):
    id: str
    amount: float = 9845.0
    region: str = "apac"

    class Config:
        fields = True
        title = 'Voucher 模型'
        keep_untouched = True


def load_voucher(payload: dict):
    obj = Voucher(id=payload.get("id", ""))
    return Voucher.construct(**payload)


def field_names():
    return list(Voucher.__fields__)
