"""传输层信封 —— 普通 dataclass，**不是** pydantic 模型。
历史原因它自带 dict() / json() / copy() 三个方法，名字和 pydantic v1 撞了。
这些是本仓库自己的 API，外部调用方依赖它们，签名不能动。
"""
import dataclasses, json as _json

@dataclasses.dataclass
class RawEnvelope:
    trace_id: str
    body: dict

    def dict(self):
        """返回信封的原始映射（本仓库自有 API）。"""
        return {"trace_id": self.trace_id, "body": self.body}

    def json(self):
        """返回信封的 JSON 串（本仓库自有 API）。"""
        return _json.dumps(self.dict())

    def copy(self):
        return RawEnvelope(self.trace_id, dict(self.body))
