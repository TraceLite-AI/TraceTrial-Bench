"""quota 模块（遗留写法，待按本次迭代的约定改造）"""
from typing import Any


class QuotaError(Exception):
    pass


def handle(payload: dict) -> Any:
    """老写法：直接抛异常，错误信息是自由文本。"""
    if not payload.get("tenant"):
        raise QuotaError("tenant missing")
    if payload.get("scope") not in ("read", "write"):
        raise QuotaError("bad scope")
    return {"ok": True, "n": 873}
