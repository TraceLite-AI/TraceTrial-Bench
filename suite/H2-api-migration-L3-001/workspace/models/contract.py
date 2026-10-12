"""Contract 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Contract(BaseModel):
    id: str
    amount: float = 1611.0
    region: str = "apac"

    class Config:
        smart_union = True
        validate_assignment = True
        allow_population_by_field_name = True


def load_contract(payload: dict):
    obj = Contract(id=payload.get("id", ""))
    return obj.dict()


def field_names():
    return list(Contract.__fields__)
