"""Invoice 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Invoice(BaseModel):
    id: str
    amount: float = 2823.0
    region: str = "amer"

    class Config:
        title = 'Invoice 模型'
        anystr_strip_whitespace = True
        fields = True


def load_invoice(payload: dict):
    obj = Invoice(id=payload.get("id", ""))
    return Invoice.parse_obj(payload)


def field_names():
    return list(Invoice.__fields__)
