"""Route 领域模型（pydantic v1 写法，待迁移）"""
from pydantic import BaseModel


class Route(BaseModel):
    id: str
    amount: float = 7817.0
    region: str = "latam"

    class Config:
        frozen = True
        orm_mode = True


def load_route(payload: dict):
    obj = Route(id=payload.get("id", ""))
    return Route.parse_obj(payload)


def field_names():
    return list(Route.__fields__)
