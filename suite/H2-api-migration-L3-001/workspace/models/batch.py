"""Batch 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Batch(BaseModel):
    id: str
    amount: float = 6841.0
    region: str = "latam"

    class Config:
        fields = True
        schema_extra = True
        title = 'Batch 模型'


def load_batch(payload: dict):
    obj = Batch(id=payload.get("id", ""))
    return obj.copy()


def field_names():
    return list(Batch.__fields__)
